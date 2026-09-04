# -*- coding: utf-8 -*-
"""`gw rates` — EBSi 의 문항별 오답률을 끌어와 items/<qid>.json 의 ext 에 담는다.

## 왜 이 명령이 있나

"어려운 문항만 골라 문항집을 만든다"는 요구는 **난이도의 객관적 근거**가 있어야 성립한다.
배점(2·3점)은 출제자의 의도이지 실제 난이도가 아니다. EBSi 는 회차·과목마다 실제 응시
데이터에서 나온 **문항별 오답률**을 공개하므로, 그것이 우리가 구할 수 있는 유일한 실측치다.

## 이 명령이 소유하는 것

`items/<qid>.json` 의 `ext.error_rate` (0~100 실수) · `ext.error_rate_source` ·
`ext.error_rate_choices` 세 칸뿐이다. 다른 칸은 읽기만 한다.
값이 없는 문항에는 **키 자체를 만들지 않는다**(CONTRACT 0-5 '조용한 기본값 금지').

## 가장 위험한 자리 — 문항 번호 대응

오답률 표의 '문항번호' 와 우리 qid 의 번호가 1:1 이 아니면 **에러 없이 전부 틀린다.**
16번의 오답률이 15번에 붙어도 파일은 멀쩡해 보인다. 그래서 값을 쓰기 전에
표에 함께 실려 오는 **정답·배점을 우리 items 와 대조**한다. 하나라도 어긋나면
그 회차는 통째로 쓰지 않는다. 대조 표본이 MIN_CROSS_CHECK 미만이어도 쓰지 않는다 —
한두 건이 우연히 맞는 것은 번호 대응의 근거가 못 된다(PITFALLS 5-5 '작은 표본의 100%').

## 표는 전 문항을 덮지 않는다

EBSi 가 공개하는 것은 **오답률 상위 15문항**이다(실측: 수능·모평·학평 전부, 2009년
시행 회차까지 동일). 20문항 과목이면 다섯 문항은 값이 없는 것이 정상이고, 빠지는 것은
언제나 오답률이 가장 낮은 쪽이다. 즉 "오답률 N% 이상만 고른다"는 용도에는 손실이 없다.

15위가 동점이면 그 창에 누가 들어갈지가 **호출마다 흔들린다**(providers/ebsi.py 의
fetch_rates 주석에 실측). 그래서 회차에 따라 16행이 담기고, `--force` 로 다시 돌리면
행이 하나 늘 수 있다. 이 명령은 값을 지우지 않으므로 줄어들지는 않는다.
"""
from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timezone

from common import (Progress, Report, Space, exam_sort_key, load_subject,
                    make_exam_id, normalize_exam)
from common.ids import GRADE_BEARING, split_qid
from providers import ExamTarget, Http, PROVIDER_NAMES, RateSheet, get_provider

# 이 단계가 소유하는 ext 칸. _merge_item 이 이 목록으로만 덮어쓰므로 다른 단계의
# 확장 필드(extract 의 answer_check·text_raw 등)는 실수로도 지워지지 않는다.
OWNED_EXT_KEYS = ("error_rate", "error_rate_source", "error_rate_choices")
NOTE_PREFIX = "rates: "

# 번호 대응을 인정하는 최소 대조 표본. 5건이 전부 맞을 확률은 우연이라면 (1/5)^5 ≈ 0.03%
# 이고, 번호가 한 칸 밀렸다면 거의 확실히 어긋난다. 1~2건으로는 아무것도 못 가린다.
MIN_CROSS_CHECK = 5

# --min 목록의 상한. 리포트는 LLM 이 읽는 유일한 출력이라 길이를 제한한다
# (attention 30건 상한과 같은 이유). 넘치면 above_min_truncated 로 몇 건이 잘렸는지 남긴다.
ABOVE_MIN_LIMIT = 200

# 오답률을 제공하는 프로바이더. 지금은 EBSi 뿐이다 — 평가원은 응시 데이터를 배포하지 않는다.
RATE_PROVIDERS = ("ebsi",)


def register(parser) -> None:
    parser.add_argument("--subject", required=True, help="과목 슬러그")
    parser.add_argument("--only", help="qid 또는 exam_id 를 쉼표로 (예 2024_수능,2025_수능_07)")
    parser.add_argument("--provider", default="ebsi", choices=list(PROVIDER_NAMES),
                        help="오답률 출처 (현재 ebsi 만 제공한다)")
    parser.add_argument("--min", type=float, metavar="오답률",
                        help="이 값 이상인 문항을 리포트에 따로 모아 준다 (수집 범위는 줄이지 않는다)")
    parser.add_argument("--dry-run", action="store_true",
                        help="어느 회차의 표가 있는지까지만 확인하고 items 를 쓰지 않는다")
    parser.add_argument("--force", action="store_true",
                        help="이미 ext.error_rate 가 있는 문항도 다시 쓴다")
    parser.add_argument("--quiet", action="store_true", help="한 줄 요약도 줄인다")
    parser.add_argument("--workspace", help="작업 공간 경로 직접 지정 (기본 workspace/<slug>)")
    parser.add_argument("--fast", action="store_true",
                        help="요청 사이 딜레이를 줄인다. 소량 재시도용 — 대량 수집에는 쓰지 마라")


# --------------------------------------------------------------------------
# 보조
# --------------------------------------------------------------------------

def _finish(report: Report, args, ok: bool | None = None) -> int:
    """--quiet 이면 한 줄만 남긴다. 리포트 파일은 어느 쪽이든 항상 쓴다(CONTRACT 5절)."""
    if not getattr(args, "quiet", False):
        return report.finish(ok=ok)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = report.finish(ok=ok)
    lines = buffer.getvalue().splitlines()
    if lines:
        print(lines[0])
    return code


def _wanted(only: str | None) -> tuple[set[str] | None, set[str] | None]:
    """--only 를 (exam_id 집합, qid 집합) 으로. 둘 다 비면 전부.

    extract 와 같은 문법이어야 한다 — 모듈마다 --only 문법이 다르면 안 된다(CONTRACT 6절).
    """
    if not only:
        return None, None
    exams, qids = set(), set()
    for token in (t.strip() for t in only.split(",")):
        if not token:
            continue
        try:
            split_qid(token)
        except ValueError:
            exams.add(token)
        else:
            qids.add(token)
            exams.add(split_qid(token)[0])
    return exams, qids


def exam_target(exam_id: str) -> ExamTarget:
    """`2025_고3_3월학평` → ExamTarget. exam_id 를 되짚는 유일한 자리다.

    되짚은 값으로 make_exam_id 를 다시 돌려 원본과 같은지 확인한다. 이 왕복 검사가
    없으면 형식이 조금 다른 폴더 이름이 조용히 다른 회차로 조회된다.
    """
    parts = exam_id.split("_")
    if len(parts) < 2:
        raise ValueError(f"exam_id 형식이 아니다: {exam_id!r}")
    year = int(parts[0])
    exam = normalize_exam(parts[-1])
    grade = None
    if exam in GRADE_BEARING:
        if len(parts) == 3 and parts[1].startswith("고"):
            grade = int(parts[1][1:])
        else:
            raise ValueError(f"학평인데 학년이 없다: {exam_id!r}")
    if make_exam_id(year, exam, grade) != exam_id:
        raise ValueError(f"exam_id 를 되짚지 못했다: {exam_id!r}")
    return ExamTarget(exam_id=exam_id, year=year, exam=exam, grade=grade)


def _load_item(path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _merge_item(base: dict, updates: dict, note: str | None) -> dict:
    """이 단계가 소유하는 ext 칸만 갈아끼운다. 나머지는 손대지 않는다."""
    item = dict(base)
    ext = dict(base.get("ext") or {})
    for key in OWNED_EXT_KEYS:
        if key in updates:
            ext[key] = updates[key]
    # 빈 ext 를 남기지 않는다 — extract 의 _merge_item 과 같은 규칙이라야 items 모양이
    # 어느 단계를 거쳤는지에 따라 갈리지 않는다.
    if ext:
        item["ext"] = ext
    else:
        item.pop("ext", None)
    kept = [n for n in (base.get("notes") or []) if not str(n).startswith(NOTE_PREFIX)]
    item["notes"] = kept + ([NOTE_PREFIX + note] if note else [])
    return item


# --------------------------------------------------------------------------
# 번호 대응 검증
# --------------------------------------------------------------------------

class Crosscheck:
    """오답률 표의 번호가 우리 items 의 번호와 같은 문항을 가리키는가.

    근거는 표에 함께 실려 오는 정답·배점이다. 둘 다 우리가 이미 3중 대조로 확정한
    값이라, 표와 맞으면 번호 대응이 맞다는 강한 증거가 된다.
    """

    def __init__(self):
        self.checked = 0                     # 대조에 쓸 수 있었던 문항 수
        self.mismatch: list[str] = []        # 어긋난 문항의 설명

    def feed(self, number: int, row: dict, item: dict) -> None:
        used = False
        ours_answer = item.get("answer")
        theirs_answer = row.get("answer")
        # 정답이 '없음'(전항 정답, ANSWER_NONE=0) 인 문항은 EBSi 표기 규약을 모르므로 뺀다.
        if ours_answer and theirs_answer:
            used = True
            if int(ours_answer) != int(theirs_answer):
                self.mismatch.append(
                    f"{number}번 정답 우리={ours_answer} EBSi={theirs_answer}")
        ours_points = item.get("points")
        theirs_points = row.get("points")
        if ours_points and theirs_points:
            used = True
            if int(ours_points) != int(theirs_points):
                self.mismatch.append(
                    f"{number}번 배점 우리={ours_points} EBSi={theirs_points}")
        if used:
            self.checked += 1

    @property
    def ok(self) -> bool:
        return self.checked >= MIN_CROSS_CHECK and not self.mismatch

    def why_not(self) -> str:
        if self.mismatch:
            head = "; ".join(self.mismatch[:6])
            more = f" (외 {len(self.mismatch) - 6}건)" if len(self.mismatch) > 6 else ""
            return (f"오답률 표와 우리 정답·배점이 어긋난다 — {head}{more}. "
                    f"문항 번호 대응이 깨졌거나 한쪽이 틀렸다. 확인 전에는 쓰지 않는다")
        return (f"정답·배점을 대조할 수 있는 문항이 {self.checked}건뿐이라 "
                f"(최소 {MIN_CROSS_CHECK}건) 번호 대응을 확인할 수 없다 — "
                f"extract 를 먼저 돌려 정답을 채워라")


# --------------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------------

def _exam_ids(space: Space, report: Report) -> list[str]:
    """items 에 실제로 존재하는 회차 목록. 오답률은 items 에 붙는 값이다."""
    found: set[str] = set()
    if space.items.exists():
        for path in space.items.glob("*.json"):
            try:
                found.add(split_qid(path.stem)[0])
            except ValueError:
                report.note(path.name, "qid 형식이 아닌 items 파일이라 건너뛴다", "warn")
    return sorted(found, key=_order)


def collect_above(space: Space, exam_ids: list[str], threshold: float) -> list[dict]:
    """items 에 실제로 담긴 오답률 중 임계값 이상인 문항을 오답률 내림차순으로.

    리포트에만 쓰는 요약이다. 수집 범위를 줄이지는 않는다 — 임계값 때문에 값을
    안 담으면 나중에 임계값을 낮출 때마다 사이트를 다시 두드려야 한다.
    """
    wanted = set(exam_ids)
    out: list[dict] = []
    for path in space.items.glob("*.json"):
        try:
            if split_qid(path.stem)[0] not in wanted:
                continue
        except ValueError:
            continue
        rate = (_load_item(path).get("ext") or {}).get("error_rate")
        if isinstance(rate, (int, float)) and rate >= threshold:
            out.append({"qid": path.stem, "error_rate": rate})
    return sorted(out, key=lambda r: (-r["error_rate"], r["qid"]))


def _order(exam_id: str):
    """시간순 정렬. 규약을 벗어난 이름 하나가 정렬을 터뜨리지 않게 감싼다."""
    try:
        return (0,) + exam_sort_key(exam_id)
    except (ValueError, IndexError):
        return (1, exam_id)


def run(args) -> int:
    try:
        subject = load_subject(args.subject)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] rates: {exc}")
        return 1
    space = Space(subject.slug, getattr(args, "workspace", None))
    report = Report("rates", subject.slug, space)

    count = subject.question_count or 0
    if not count:
        report.note(subject.slug,
                    "subject.question_count 가 없다 — 오답률 표의 번호가 우리 문항 범위 안인지 "
                    "확인할 수 없다", "error")
        report.next = f"subjects/{subject.slug}/subject.json 에 question_count 를 채운다"
        return _finish(report, args)

    wanted_exams, wanted_qids = _wanted(args.only)
    exam_ids = [e for e in _exam_ids(space, report)
                if wanted_exams is None or e in wanted_exams]
    if not exam_ids:
        report.note(subject.slug,
                    f"오답률을 붙일 items 가 없다: {space.items} 아래에 <qid>.json 이 있어야 한다",
                    "error")
        report.next = f"python scripts/gw.py extract --subject {subject.slug}"
        return _finish(report, args)

    targets: list[ExamTarget] = []
    for exam_id in exam_ids:
        try:
            targets.append(exam_target(exam_id))
        except ValueError as exc:
            report.note(exam_id, f"회차 이름을 되짚지 못해 건너뛴다 — {exc}", "warn")
    if not targets:
        report.note(subject.slug, "조회할 수 있는 회차가 하나도 없다", "error")
        report.next = f"python scripts/gw.py extract --subject {subject.slug}"
        return _finish(report, args)

    if args.provider not in RATE_PROVIDERS:
        report.note(args.provider,
                    f"'{args.provider}' 는 오답률을 제공하지 않는다 "
                    f"(가능: {', '.join(RATE_PROVIDERS)}). 평가원은 응시 데이터를 배포하지 않는다.",
                    "error")
        report.next = f"python scripts/gw.py rates --subject {subject.slug} --provider ebsi"
        return _finish(report, args)

    http = Http(delay=(0.05, 0.15) if args.fast else (0.5, 1.5),
                list_pause=0.05 if args.fast else 0.2)
    try:
        provider = get_provider(args.provider, http)
    except Exception as exc:  # noqa: BLE001
        report.note(args.provider, f"프로바이더를 불러오지 못했다: {exc}", "error")
        return _finish(report, args)

    # --- 1. 목록에서 회차별 표의 좌표를 찾는다 (표 본문은 아직 받지 않는다) ---
    try:
        sheets = provider.discover_rates(subject, targets)
    except Exception as exc:  # noqa: BLE001
        report.note(args.provider, f"오답률 목록 조회 실패: {exc}", "error")
        sheets = []
    for ident, why, severity in provider.notes:
        report.note(ident, why, severity)
    provider.notes.clear()

    by_exam = {s.exam_id: s for s in sheets}
    missing = [t.exam_id for t in targets if t.exam_id not in by_exam]
    for exam_id in missing:
        report.note(exam_id, f"{args.provider} 목록에서 이 회차의 오답률 표를 찾지 못했다", "warn")

    counts = {"exams": len(targets), "sheets": len(sheets), "rows": 0,
              "written": 0, "skipped": 0, "failed": 0, "missing": len(missing)}
    per_exam: dict[str, dict] = {}
    space.ensure()

    # --- 2. 회차마다 표를 받아 대조하고 items 에 담는다 ---
    bar = Progress(len(by_exam), "회차", label="rates", args=args).open()
    for exam_id in bar.wrap(sorted(by_exam, key=_order)):
        bar.detail(exam_id)
        sheet: RateSheet = by_exam[exam_id]
        sheet.extra["_question_count"] = count
        if args.dry_run:
            per_exam[exam_id] = {"planned": True, "paper_id": sheet.extra.get("paper_id"),
                                 "title": sheet.title}
            continue
        try:
            sheet = provider.fetch_rates(sheet)
        except Exception as exc:  # noqa: BLE001 — 회차 하나가 전체를 막지 않는다
            report.note(exam_id, f"오답률 표 내려받기 실패: {type(exc).__name__}: {exc}", "warn")
            counts["failed"] += 1
            continue
        finally:
            for ident, why, severity in provider.notes:
                report.note(ident, why, severity)
            provider.notes.clear()

        if not sheet.rows:
            report.note(exam_id, f"오답률 표가 비어 있다 — {sheet.reason or '이유 미상'}", "warn")
            counts["failed"] += 1
            continue
        counts["rows"] += len(sheet.rows)

        # 번호 대응 검증. 여기서 막히면 그 회차는 한 글자도 쓰지 않는다.
        check = Crosscheck()
        items: dict[int, dict] = {}
        for row in sheet.rows:
            number = row["number"]
            path = space.item(f"{exam_id}_{number:02d}")
            item = _load_item(path)
            if not item:
                continue
            items[number] = item
            check.feed(number, row, item)
        if not check.ok:
            report.note(exam_id, check.why_not(), "error")
            counts["failed"] += 1
            per_exam[exam_id] = {"rows": len(sheet.rows), "checked": check.checked,
                                 "mismatch": len(check.mismatch), "written": 0}
            continue

        evidence = sheet.evidence()
        collected_at = datetime.now(timezone.utc).isoformat()
        written = 0
        for row in sheet.rows:
            number = row["number"]
            qid = f"{exam_id}_{number:02d}"
            if wanted_qids and qid not in wanted_qids:
                continue
            base = items.get(number)
            if base is None:
                report.note(qid, "오답률 표에는 있는데 items 파일이 없다 — crop/extract 미실행", "warn")
                counts["skipped"] += 1
                continue
            if (base.get("ext") or {}).get("error_rate") is not None and not args.force:
                counts["skipped"] += 1
                continue
            updates = {
                "error_rate": row["error_rate"],
                "error_rate_source": {**evidence, "rank": row["rank"],
                                      "collected_at": collected_at},
            }
            if row.get("choices"):
                updates["error_rate_choices"] = row["choices"]
            merged = _merge_item(base, updates,
                                 f"오답률 {row['error_rate']}% ({evidence.get('coverage')} "
                                 f"{row['rank']}위, {evidence.get('provider')})")
            space.item(qid).parent.mkdir(parents=True, exist_ok=True)
            space.item(qid).write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
            written += 1
        counts["written"] += written
        rates = [r["error_rate"] for r in sheet.rows]
        per_exam[exam_id] = {"rows": len(sheet.rows), "checked": check.checked,
                             "mismatch": 0, "written": written,
                             "max": max(rates), "min": min(rates),
                             "endpoints": sheet.extra.get("endpoints") or []}
        # 두 조회 경로 중 하나만 답한 경우. 옛 회차에서는 정상이지만(팝업 문이 2013년
        # 이전을 안 준다) 사실을 지우지는 않는다 — 표가 얇아진 이유가 여기에 있다.
        if sheet.extra.get("_partial"):
            per_exam[exam_id]["partial"] = sheet.extra["_partial"]
    bar.close()

    report.count(**counts)
    report.artifact(space.rel(space.items))
    report.extra["per_exam"] = per_exam
    report.extra["coverage"] = {
        "kind": "top15",
        "why": ("EBSi 는 회차·과목마다 오답률 상위 15문항만 공개한다. 나머지 문항에는 "
                "ext.error_rate 키 자체가 생기지 않는다 — 빠지는 것은 언제나 오답률이 "
                "가장 낮은 쪽이라 '오답률 N 이상' 용도에는 손실이 없다. "
                "15위가 동점이면 창에 누가 들어갈지가 호출마다 흔들려 16행이 담기기도 한다."),
    }
    if args.min is not None:
        # **이번에 받은 표가 아니라 items 에 실제로 들어 있는 값**을 센다.
        # "이미 모아 둔 작업 공간에 --min 70 만 다시 물어본다"가 이 옵션의 주 용도인데,
        # 이번에 쓴 것만 세면 두 번째 실행부터 목록이 비어 값이 없는 것처럼 보인다.
        above_min = collect_above(space, exam_ids, args.min)
        report.extra["min_error_rate"] = args.min
        report.extra["above_min"] = above_min[:ABOVE_MIN_LIMIT]
        report.count(above_min=len(above_min))
        if len(above_min) > ABOVE_MIN_LIMIT:
            report.extra["above_min_truncated"] = len(above_min) - ABOVE_MIN_LIMIT
    if args.dry_run:
        report.extra["dry_run"] = True
        report.next = f"python scripts/gw.py rates --subject {subject.slug}"
    elif report.has_error:
        report.next = (f"reports/rates.json 의 attention 을 확인한 뒤 "
                       f"python scripts/gw.py rates --subject {subject.slug} --only <회차>")
    else:
        report.next = f"python scripts/gw.py validate --subject {subject.slug}"
    return _finish(report, args)
