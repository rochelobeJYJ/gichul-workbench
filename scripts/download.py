# -*- coding: utf-8 -*-
"""`gw download` — 문제지·정답·해설을 내려받아 workspace/<slug>/sources/<exam_id>/ 에 쌓는다.

프로바이더가 둘이다. 왜 둘인지는 scripts/providers/__init__.py 머리말과 docs/PROVIDERS.md 참조.
요약하면 **해설은 EBSi 에만 있고, 문제·정답 원본은 평가원이 낫다.**

이 모듈이 지는 책임은 셋뿐이다.
  1. --years/--exams 를 exam_id 목록으로 바꾼다 (common.ids.make_exam_id 로만).
  2. 자료 종류별로 프로바이더를 골라 후보를 모으고, 판형 전략으로 하나를 고른다.
  3. 받은 바이트를 검증해 저장하고 회차별 manifest.json 과 리포트 하나를 남긴다.
실제 사이트 지식은 전부 providers/ 안에 있다. 여기에는 URL 이 한 줄도 없어야 한다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from common import Report, Space, load_subject, make_exam_id, normalize_exam
from common.ids import GRADE_BEARING
from providers import (KINDS, Candidate, ExamTarget, Http, PROVIDER_NAMES,
                       get_provider, provider_chain, verify_bytes)

# 회차 이름이 가리키는 '예정' 시행월. 실제 시행일이 여기서 벗어나면 리포트로 알린다.
# (2021학년도 수능 12월, 2023학년도 9월모평 8월 — 목록이 시행일 기준이라 생기는 함정이다.)
NOMINAL_MONTH = {"수능": 11, "6월모평": 6, "9월모평": 9,
                 "3월학평": 3, "4월학평": 4, "7월학평": 7, "10월학평": 10}


# --------------------------------------------------------------------------- 판형 전략

def _pick_single(cands: list[Candidate], chain: list[str]) -> list[Candidate]:
    """탐구 판형: 회차·과목당 파일이 하나다. 프로바이더 우선순위대로 줄만 세운다.

    탐구에는 수학의 가/나형, 국어의 홀/짝수형 같은 '같은 회차의 다른 판본'이 없다.
    그래서 후보가 여럿이면 그건 우선순위 문제이지 선택 문제가 아니다.
    """
    def rank(c: Candidate) -> tuple:
        prov = chain.index(c.provider) if c.provider in chain else len(chain)
        alias = 0 if c.extra.get("alias_match") else 1
        return (prov, alias, c.url)
    return sorted(cands, key=rank)


def _pick_not_ready(layout: str):
    """아직 규칙이 없는 판형. **조용히 아무거나 고르지 않는다.**

    수학은 한 회차에 가형/나형, 국어·영어는 홀수형/짝수형이 따로 올라온다. 어떤 판본을 정본으로
    삼을지는 과목 정의(subject.json)에 없는 정보라 지금 결정할 수 없다. 자리를 비워두고
    무엇이 후보였는지만 정확히 알려준다.
    """
    def picker(cands: list[Candidate], chain: list[str]) -> list[Candidate]:
        titles = ", ".join(sorted({c.title or c.url for c in cands}))[:400]
        raise NotImplementedError(
            f"판형 '{layout}' 은 한 회차에 여러 판본(가/나형·홀/짝수형)이 있어 선택 규칙이 필요하다. "
            f"docs/PROVIDERS.md 의 '판형별 변형' 절을 채우고 download.py 의 LAYOUT_PICKER 에 "
            f"전략 함수를 등록하라. 이번 후보: {titles}")
    return picker


# 판형 → 후보 선택 전략. 새 판형이 들어오면 여기 한 줄만 늘어나야 한다.
LAYOUT_PICKER = {
    "tamgu-1q1block": _pick_single,
    "passage-group": _pick_not_ready("passage-group"),
    "math-mixed": _pick_not_ready("math-mixed"),
}


# --------------------------------------------------------------------------- 인자 해석

def parse_years(value: str) -> list[int]:
    """'2020-2026', '2024', '2020-2022,2025' 를 연도 목록으로."""
    years: set[int] = set()
    for part in [p.strip() for p in str(value).split(",") if p.strip()]:
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            years.update(range(min(lo, hi), max(lo, hi) + 1))
        else:
            years.add(int(part))
    if not years:
        raise ValueError("--years 에 연도가 없다")
    return sorted(years)


def parse_list(value: str, allowed: tuple[str, ...], what: str) -> list[str]:
    items = [p.strip() for p in str(value).split(",") if p.strip()]
    bad = [i for i in items if i not in allowed]
    if not items or bad:
        raise ValueError(f"{what} 값이 잘못됐다: {', '.join(bad) or '(비어 있음)'} "
                         f"(가능: {', '.join(allowed)})")
    return list(dict.fromkeys(items))


def build_targets(years: list[int], exams: list[str], grade: int | None) -> list[ExamTarget]:
    out: list[ExamTarget] = []
    for year in years:
        for raw in exams:
            exam = normalize_exam(raw)
            g = grade if exam in GRADE_BEARING else None
            out.append(ExamTarget(exam_id=make_exam_id(year, exam, g),
                                  year=year, exam=exam, grade=g))
    return out


# --------------------------------------------------------------------------- 저장

def existing_file(dirpath: Path, kind: str) -> Path | None:
    if not dirpath.exists():
        return None
    for p in sorted(dirpath.glob(f"{kind}.*")):
        if p.suffix.lower() in {".pdf", ".png", ".jpg"} and p.stat().st_size > 1024:
            return p
    return None


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def load_manifest(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


# --------------------------------------------------------------------------- CLI

def register(parser) -> None:
    parser.add_argument("--subject", help="과목 슬러그 (--probe 일 때만 생략 가능)")
    parser.add_argument("--years", help="'2020-2026' 또는 '2024,2025'. 수능·모평은 학년도, 학평은 달력연도")
    parser.add_argument("--exams", default="수능,6월모평,9월모평",
                        help="쉼표 구분. 수능/6월모평/9월모평/3월학평/4월학평/7월학평/10월학평")
    parser.add_argument("--kinds", default="problem,answer,solution",
                        help=f"쉼표 구분. {', '.join(KINDS)}")
    parser.add_argument("--grade", type=int, choices=[1, 2, 3],
                        help="학평일 때만. 같은 달에 학년별 시험이 따로 있다")
    parser.add_argument("--provider", default="auto",
                        choices=["auto", *PROVIDER_NAMES],
                        help="auto: 해설은 EBSi, 문제·정답은 평가원 공식 우선")
    parser.add_argument("--only", help="특정 회차만. exam_id 를 쉼표로 (예 2024_수능,2025_수능)")
    parser.add_argument("--dry-run", action="store_true",
                        help="목록 조회까지만 하고 파일은 받지 않는다(계획이 현실과 맞는지 확인)")
    parser.add_argument("--force", action="store_true", help="이미 있는 파일도 다시 받는다")
    parser.add_argument("--quiet", action="store_true", help="리포트 경로만 출력")
    parser.add_argument("--fast", action="store_true",
                        help="요청 사이 딜레이를 줄인다. 소량 재시도용 — 대량 수집에는 쓰지 마라")
    # --- 실측 도구 ---
    parser.add_argument("--probe", action="store_true",
                        help="영역 안의 과목 목록과 subject_id 를 사이트에서 실측한다")
    parser.add_argument("--area", help="--probe 대상 영역 (사회탐구/과학탐구/국어 …)")
    parser.add_argument("--year", type=int, help="--probe 시 자료 유무까지 확인할 학년도")
    parser.add_argument("--exam", help="--probe 시 자료 유무까지 확인할 시험 (예 수능)")


def run(args) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    http = Http(delay=(0.05, 0.15) if args.fast else (0.5, 1.5),
                list_pause=0.05 if args.fast else 0.2)
    if args.probe:
        return _run_probe(args, http)
    return _run_download(args, http)


# --------------------------------------------------------------------------- probe

def _run_probe(args, http: Http) -> int:
    """`gw download --probe --area 사회탐구 --year 2024 --exam 수능`

    사탐 9과목의 subject_id 를 아무도 모른다. 표를 코드에 박아두면 교육과정이 바뀌는 순간
    거짓말이 된다. 그래서 사이트에서 직접 읽어 표로 남긴다 — 이 저장소를 새 과목으로
    넓히는 사람이 제일 먼저 돌리는 명령이다.
    """
    slug = args.subject or "_probe"
    space = Space(slug)
    report = Report("download_probe", slug, space)
    report.extra["area"] = args.area
    try:
        exam = normalize_exam(args.exam) if args.exam else None
    except ValueError as exc:
        report.note("--exam", str(exc), "error")
        report.next = f"python scripts/gw.py download --probe --area {args.area or '사회탐구'} --exam 수능"
        return _finish(report, args)

    if not args.area:
        report.note("--area", "--probe 에는 --area 가 필요하다 (예: --area 사회탐구)", "error")
        report.next = "python scripts/gw.py download --probe --area 사회탐구 --year 2024 --exam 수능"
        return _finish(report, args)

    rows: list[dict] = []
    try:
        ebsi = get_provider("ebsi", http)
        rows = ebsi.probe(args.area, args.year, exam, args.grade)
        for ident, why, sev in ebsi.notes:
            report.note(ident, why, sev)
    except Exception as exc:
        report.note("ebsi", f"EBSi 실측 실패: {exc}", "error")

    subjects = [r for r in rows if r.get("kind") == "subject"]
    report.extra["ebsi_subjects"] = subjects
    report.extra["ebsi_bundles"] = [r for r in rows if r.get("kind") != "subject"]
    report.count(found=len(subjects))

    if args.year and exam:
        empty = [r["label"] for r in subjects if r.get("rows_found") == 0]
        if empty:
            report.note("ebsi", f"{args.year} {exam} 자료가 목록에 없는 과목: {', '.join(empty)}",
                        "info")
        try:
            kice = get_provider("kice-official", http)
            report.extra["kice_official_posts"] = kice.probe(args.area, args.year, exam, args.grade)
            for ident, why, sev in kice.notes:
                report.note(ident, why, sev)
        except Exception as exc:
            report.note("kice-official", f"평가원 실측 실패: {exc}", "warn")

    report.note("시행일", "EBSi·평가원 목록은 모두 **시행일** 기준이다. 2021학년도 수능은 2020-12-03, "
                          "2023학년도 9월모평은 2022-08-31 시행이라 목록의 월이 12·08 로 나온다. "
                          "학년도와 시행월을 혼동하면 회차를 통째로 놓친다.", "info")
    report.next = ("subjects/<slug>/subject.json 의 providers 에 위 subject_json 조각을 붙여 넣고 "
                   "python scripts/gw.py download --subject <slug> --years <연도> --dry-run")
    report.artifact(space.rel(space.report("download_probe")))
    return _finish(report, args)


# --------------------------------------------------------------------------- download

def _run_download(args, http: Http) -> int:
    if not args.subject:
        print("[FAIL] --subject 가 필요하다. (과목 목록: python scripts/gw.py subjects)")
        return 2
    if not args.years:
        print("[FAIL] --years 가 필요하다. 예: --years 2020-2026")
        return 2

    try:
        subject = load_subject(args.subject)
    except (FileNotFoundError, ValueError) as exc:
        # 리포트를 쓸 작업 공간조차 정할 수 없는 단계라 여기서만 stdout 으로 끝낸다.
        print(f"[FAIL] {exc}")
        return 2
    space = Space(subject.slug)
    space.ensure()
    report = Report("download", subject.slug, space)

    try:
        years = parse_years(args.years)
        kinds = parse_list(args.kinds, KINDS, "--kinds")
        exams = [normalize_exam(e) for e in
                 [p.strip() for p in args.exams.split(",") if p.strip()]]
    except ValueError as exc:
        report.note("args", str(exc), "error")
        report.next = (f"python scripts/gw.py download --subject {subject.slug} "
                       f"--years 2020-2026 --exams 수능,6월모평,9월모평 --kinds problem,answer,solution")
        return _finish(report, args)

    if any(e in GRADE_BEARING for e in exams) and not args.grade:
        report.note("args", "학력평가는 같은 달에 학년별 시험이 따로 있다. --grade 1|2|3 을 붙여라.",
                    "error")
        report.next = (f"python scripts/gw.py download --subject {subject.slug} "
                       f"--years {args.years} --exams {args.exams} --grade 3")
        return _finish(report, args)

    targets = build_targets(years, exams, args.grade)
    if args.only:
        wanted = {p.strip() for p in args.only.split(",") if p.strip()}
        targets = [t for t in targets if t.exam_id in wanted]
        unknown = wanted - {t.exam_id for t in targets}
        for u in unknown:
            report.note(u, "--only 로 지정했으나 --years/--exams 범위 밖이라 무시했다", "warn")
    if not targets:
        report.note("args", "받을 회차가 없다. --only 가 --years/--exams 범위 안에 있어야 한다", "error")
        report.next = (f"python scripts/gw.py download --subject {subject.slug} "
                       f"--years {args.years} --exams {args.exams}   # --only 를 빼고 다시")
        return _finish(report, args)

    picker = LAYOUT_PICKER.get(subject.layout)
    if picker is None:
        report.note(subject.slug,
                    f"판형 '{subject.layout}' 의 후보 선택 전략이 없다. "
                    f"download.py 의 LAYOUT_PICKER 에 등록하라. docs/PROVIDERS.md '판형별 변형' 참조.",
                    "error")
        report.next = "docs/PROVIDERS.md 의 '판형별 변형' 절을 읽고 LAYOUT_PICKER 에 전략을 추가한다"
        return _finish(report, args)

    if not subject.providers:
        report.note(subject.slug,
                    "subject.json 의 providers 가 비어 있다. "
                    "`gw download --probe --area <영역>` 으로 실측해 채운다.", "error")
        report.next = (f"python scripts/gw.py download --probe --area {subject.area} "
                       f"--year {years[0]} --exam {exams[0]}")
        return _finish(report, args)

    # --- 1. 프로바이더별로 필요한 (회차, 종류) 를 모아 한 번씩만 조회한다 ---
    need: dict[str, set[str]] = {}
    chains: dict[tuple[str, str], list[str]] = {}
    for t in targets:
        for kind in kinds:
            chain = provider_chain(kind, t.exam, args.provider)
            chains[(t.exam_id, kind)] = chain
            for name in chain:
                need.setdefault(name, set()).add(kind)

    found: dict[tuple[str, str], list[Candidate]] = {}
    for name, want_kinds in need.items():
        try:
            provider = get_provider(name, http)
        except Exception as exc:
            report.note(name, f"프로바이더를 불러오지 못했다: {exc}", "error")
            continue
        # 프로바이더가 못 다루는 조합은 아예 조회하지 않는다.
        # (평가원에 학평을 물어보거나 EBSi 아닌 곳에 해설을 물어보는 헛질의를 막는다)
        usable = {k for k in want_kinds if k in provider.kinds}
        pool = [t for t in targets if any(provider.supports(k, t.exam) for k in usable)]
        if not usable or not pool:
            continue
        try:
            cands = provider.discover(subject, pool, usable)
        except Exception as exc:
            report.note(name, f"목록 조회 실패: {exc}", "error")
            cands = []
        for ident, why, sev in provider.notes:
            report.note(ident, why, sev)
        provider.notes.clear()
        for c in cands:
            found.setdefault((c.exam_id, c.kind), []).append(c)
        report.extra.setdefault("discovered", {})[name] = len(cands)

    # --- 2. 시행일 함정 알림: 회차 이름의 월과 실제 시행월이 다르면 반드시 남긴다 ---
    drifted: set[str] = set()
    for (exam_id, _kind), cands in sorted(found.items()):
        exam = exam_id.split("_")[-1]
        nominal = NOMINAL_MONTH.get(exam)
        for c in cands:
            if c.sitting_date and nominal and int(c.sitting_date[4:6]) != nominal:
                if exam_id in drifted:
                    break
                drifted.add(exam_id)
                d = c.sitting_date
                report.note(exam_id,
                            f"시행일 {d[:4]}-{d[4:6]}-{d[6:]} — 회차 이름({exam})의 월과 다르다. "
                            f"목록은 시행일 기준이라 월로 거르면 이 회차를 놓친다.", "info")
                break

    # --- 3. 후보를 골라 받는다 ---
    providers_cache: dict[str, object] = {}
    counts = {"expected": len(targets) * len(kinds), "done": 0, "failed": 0,
              "skipped": 0, "missing": 0, "planned": 0}
    touched: list[str] = []

    for target in targets:
        out_dir = space.source_dir(target.exam_id)
        manifest = load_manifest(space.manifest(target.exam_id))
        files = dict(manifest.get("files") or {})
        for kind in kinds:
            chain = chains[(target.exam_id, kind)]
            cands = found.get((target.exam_id, kind), [])
            if not cands:
                counts["missing"] += 1
                report.note(target.exam_id,
                            f"{kind}: 어느 프로바이더에서도 못 찾았다 "
                            f"(시도: {', '.join(chain)})", "warn")
                continue
            try:
                ordered = picker(cands, chain)
            except NotImplementedError as exc:
                counts["failed"] += 1
                report.note(target.exam_id, f"{kind}: {exc}", "error")
                continue

            have = existing_file(out_dir, kind)
            if have is not None and not args.force and not args.dry_run:
                counts["skipped"] += 1
                files.setdefault(kind, {}).update({"path": have.name, "skipped": True})
                continue
            if args.dry_run:
                counts["planned"] += 1
                best = ordered[0]
                files[kind] = {"planned": True, "verified": None, **best.evidence()}
                continue

            saved = _download_one(ordered, kind, out_dir, providers_cache, http, report,
                                  target.exam_id)
            if saved is None:
                counts["failed"] += 1
            else:
                counts["done"] += 1
                files[kind] = saved

        _write_manifest(space, target, subject, files, kinds, args.dry_run)
        touched.append(space.rel(out_dir))

    report.count(**counts)
    report.artifact(space.rel(space.sources))
    report.extra["exams"] = [t.exam_id for t in targets]
    if args.dry_run:
        report.extra["dry_run"] = True
        report.next = (f"python scripts/gw.py download --subject {subject.slug} "
                       f"--years {args.years} --exams {args.exams} --kinds {args.kinds}")
    elif counts["failed"] or counts["missing"]:
        report.next = (f"python scripts/gw.py download --subject {subject.slug} "
                       f"--years {args.years} --exams {args.exams} --kinds {args.kinds} "
                       f"--provider ebsi   # 못 받은 회차를 EBSi 로 다시 시도")
    else:
        report.next = f"python scripts/gw.py detect --subject {subject.slug}"
    ok = counts["failed"] == 0 and not report.has_error
    return _finish(report, args, ok=ok)


def _download_one(ordered: list[Candidate], kind: str, out_dir: Path,
                  cache: dict, http: Http, report: Report, exam_id: str) -> dict | None:
    """후보를 우선순위대로 시도한다. 검증에 실패하면 다음 프로바이더로 넘어간다."""
    for cand in ordered:
        provider = cache.get(cand.provider)
        if provider is None:
            provider = cache[cand.provider] = get_provider(cand.provider, http)
        try:
            data = provider.fetch(cand)
        except Exception as exc:
            report.note(exam_id, f"{kind}: {cand.provider} 내려받기 실패 — {exc}", "warn")
            continue
        finally:
            for ident, why, sev in getattr(provider, "notes", []):
                report.note(ident, why, sev)
            provider.notes.clear()

        info = verify_bytes(data, kind, cand.url)
        if not info["verified"]:
            report.note(exam_id,
                        f"{kind}: {cand.provider} 파일이 검증을 통과하지 못했다 "
                        f"(형식={info['actual_extension']}, {info['size_bytes']}바이트). 저장하지 않는다.",
                        "warn")
            continue
        ext = info["actual_extension"] or cand.ext_hint
        path = out_dir / f"{kind}{ext}"
        # 같은 종류의 다른 확장자가 남아 있으면 지운다. 원본이 두 벌 남는 것이 제일 위험하다.
        for stale in out_dir.glob(f"{kind}.*"):
            if stale != path and stale.suffix.lower() in {".pdf", ".png", ".jpg"}:
                stale.unlink()
        write_atomic(path, data)
        # 낱개 파일인데 과목 별칭에 걸리지 않았다면 '영역 통짜' 파일일 가능성이 높다.
        # (예: 2021학년도 수능 정답표는 과목별로 안 쪼개고 '4교시_과학탐구영역_정답표.pdf' 한 장이다)
        # 파일은 정상이지만 이후 단계에서 과목 부분만 잘라내야 하므로 반드시 알린다.
        if "alias_match" in cand.extra and not cand.extra["alias_match"] \
                and not cand.extra.get("zip_member"):
            report.note(exam_id,
                        f"{kind}: 파일명 '{cand.extra.get('attachment', path.name)}' 이 과목 별칭에 "
                        f"걸리지 않았다 — 영역 통짜 파일로 보인다. 이후 단계에서 해당 과목만 추려야 한다.",
                        "info")
        return {"path": path.name, "downloaded_at": datetime.now(timezone.utc).isoformat(),
                **cand.evidence(), **info}
    return None


def _write_manifest(space: Space, target: ExamTarget, subject, files: dict,
                    kinds: list[str], dry_run: bool) -> None:
    """회차별 manifest.json. 다음 단계(detect/crop)가 읽는 유일한 출처 정보다.

    파일명만으로는 나중에 아무것도 재현할 수 없어서 source_url 과 sha256 을 반드시 남긴다.
    (references/manifest-schema.md 의 v2 계약을 회차 단위로 쪼갠 형태)
    """
    missing = [k for k in kinds if not (files.get(k) or {}).get("verified")]
    if dry_run:
        status = "planned_complete" if not [k for k in kinds if k not in files] else "planned_incomplete"
        ok = None
    else:
        status = "verified" if not missing else "incomplete"
        ok = not missing
    sitting = next((v.get("sitting_date") for v in files.values() if v.get("sitting_date")), None)
    payload = {
        "schema_version": 2,
        "exam_id": target.exam_id,
        "slug": subject.slug,
        "subject_label": subject.label,
        "year": target.year,
        "year_semantics": "academic" if target.is_academic else "calendar",
        "exam": target.exam,
        "grade": target.grade,
        "sitting_date": sitting,
        "status": status,
        "ok": ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kinds_requested": kinds,
        "missing": missing,
        "files": files,
    }
    path = space.manifest(target.exam_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _finish(report: Report, args, ok: bool | None = None) -> int:
    """--quiet 이면 리포트 경로만 남긴다 (CONTRACT 5절: stdout 은 한 줄 요약까지)."""
    if getattr(args, "quiet", False):
        path = report.write(ok=ok)
        print(path)
        return 0 if report.to_dict(ok=ok)["ok"] else 1
    return report.finish(ok=ok)
