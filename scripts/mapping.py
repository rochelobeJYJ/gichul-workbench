# -*- coding: utf-8 -*-
"""`gw map` — 2015→2022(또는 그 다음 개정) 성취기준 매핑.

METHOD.md "교육과정 매핑" 절의 핵심 원칙 그대로:
    매핑은 런타임에 계산하지 않는다. 760문항에 Sonnet 태거 + Opus 감사 + 130문항 재검토가
    들어간 결과를 subjects/<slug>/mapping.json 에 데이터로 미리 배포해두고,
    이 모듈은 그것을 (1) 외부 원본에서 만들거나 (2) items/ 에 적용하거나 (3) 공백을 리포트할 뿐이다.
    쓰는 사람은 LLM 비용을 내지 않는다.

세 동작은 서로 배타적이지 않다 — CONTRACT.md 6절이 옵션을 전부 대괄호로 보여주는 이유다.
    --import 만 주면: 외부 파일 → mapping.json 정규화만 하고 끝낸다.
    --import 없이 그냥 실행: mapping.json → items/ 적용(기본 동작).
    --report-gaps 를 더하면: 위 동작 뒤에 공백 리포트를 이어붙인다.
"""
from __future__ import annotations

import collections
import contextlib
import io
import json
from pathlib import Path

from common import Report, Space, load_subject, all_subjects
from common.ids import split_qid, exam_sort_key

# fit(직접/부분/불가) → 정성 등급. 숫자 0~1로 억지로 눌러 담지 않는다 —
# 사람이 원문을 읽고 내린 정성적 판단(직접/부분)을 소수점으로 흉내 내면 오히려 정밀해 보이는 거짓말이 된다.
#
# ── 다만 이 등급을 items 의 classification.<rev>.confidence 에 그대로 실으면 안 된다 ──
# 통합 검증에서 실제로 걸린 충돌이다. 같은 필드에 `classify` 는 float(0.82)를,
# 여기서는 "high" 를 쓰고 있었다. CONTRACT.md 4절이 못박은 타입은 숫자 아니면 null 이고,
# 한 필드가 문서에서 float / 문자열로 갈리면 confidence 를 비교하는 소비자가 언젠가
# TypeError 로 죽는다(지금은 비교하는 곳이 없어서 조용히 통과하고 있었을 뿐이다).
# 그래서 confidence 에는 null 을 넣고, 정성 등급은 `grade` 키에 따로 남긴다.
# 정보 손실은 없다: 2022 쪽 등급의 근거인 fit(직접/부분/불가)은 이미 별도 필드로 실려 있고,
# 2015 쪽은 380/380 이 전부 "high" 인 상수라 애초에 아무 정보도 담고 있지 않았다.
_FIT_GRADE = {"직접": "high", "부분": "medium", "불가": None}


def register(parser) -> None:
    parser.add_argument("--subject", required=True, help="과목 슬러그 (subjects/<slug>/subject.json)")
    parser.add_argument("--revision", default="2022", help="대상 개정 (기본 2022)")
    parser.add_argument("--import", dest="import_path",
                         help="외부 매핑 원본 폴더 — 회차별 *.json 을 모아 mapping.json 으로 정규화한다")
    parser.add_argument("--taxonomy",
                         help="--import 와 함께 쓴다. {unit, standards:[{code,text}]} 목록이 담긴 JSON. "
                              "2022쪽 성취기준→단원명 조회와 --report-gaps 의 전체 코드 목록에 쓰인다")
    parser.add_argument("--generated-by", dest="generated_by",
                         help="mapping.json 의 generated_by 값을 직접 지정 (기본값은 --import 로부터 자동 생성)")
    parser.add_argument("--report-gaps", action="store_true",
                         help="대상 개정 성취기준 중 0문항인 것을 리포트에 남긴다")
    parser.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 무엇을 할지만 리포트한다")
    parser.add_argument("--force", action="store_true",
                         help="--import: 기존 mapping.json 을 덮어쓴다 / 기본 모드: 이미 by=manual 로 다르게 적혀 있는 "
                              "classification 도 mapping.json 값으로 덮어쓴다")
    parser.add_argument("--only", help="qid 콤마 목록 — 기본(적용) 모드에서 이 문항만 반영한다")
    parser.add_argument("--quiet", action="store_true",
                         help="stdout 에 리포트 경로만 남긴다")
    parser.add_argument("--workspace", help="작업 공간 경로 직접 지정 (기본 workspace/<slug>)")


def run(args) -> int:
    # 과목 정의를 못 읽으면 리포트를 남길 workspace 경로조차 정할 수 없다.
    # gw.py 는 예외를 그대로 흘려보내서 LLM 이 리포트 대신 traceback 을 읽게 된다
    # (통합 검증에서 7개 명령 중 5개가 그랬다). 형제 모듈(build/classify)과 같은
    # 한 줄 안내로 끝낸다 — load_subject 의 메시지가 등록된 과목 목록까지 담고 있다.
    try:
        subject = load_subject(args.subject)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] map: {exc}")
        return 1
    # --workspace 는 7개 명령 공통 옵션이다(crop 에만 있었다). 격리 실행이 안 되면
    # 여러 실행이 공유 workspace/<slug> 를 서로 밟는다.
    space = Space(subject.slug, getattr(args, "workspace", None))
    report = Report("map", subject.slug, space)
    revision = args.revision

    if args.import_path:
        _do_import(subject, args, report, revision)
    else:
        _do_apply(subject, args, report, revision, space)

    if args.report_gaps:
        _do_report_gaps(subject, args, report, revision)

    # --quiet 은 옵션으로 선언만 돼 있고 아무 일도 하지 않았다(통합 검증에서 발견).
    # 형제 모듈 crop.py 와 같은 규약으로 맞춘다: 요약을 삼키고 리포트 경로 한 줄만 남긴다.
    # 리포트 파일 자체는 어느 쪽이든 항상 쓴다 — LLM 이 읽는 유일한 출력이므로(CONTRACT 5절).
    if getattr(args, "quiet", False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = report.finish()
        print(space.report("map"))
        return code
    return report.finish()


# --- 외부 원본 → mapping.json 정규화 ---------------------------------------

def _do_import(subject, args, report, revision) -> None:
    import_dir = Path(args.import_path)
    if not import_dir.exists() or not import_dir.is_dir():
        report.note("import", f"폴더가 없다: {import_dir}", "error")
        return

    files = sorted(import_dir.glob("*.json"))
    if not files:
        report.note("import", f"*.json 파일이 없다: {import_dir}", "error")
        return

    code_to_unit = _load_taxonomy(Path(args.taxonomy)) if args.taxonomy else {}
    if args.taxonomy and not code_to_unit:
        report.note("taxonomy", f"{args.taxonomy} 에서 코드를 하나도 못 읽었다 — unit 이름이 전부 비게 된다", "warn")
    elif not args.taxonomy:
        report.note("taxonomy", "--taxonomy 미지정 — 2022 unit 이름과 target_catalog 를 못 채운다 "
                                 "(성취기준 코드 자체는 손실 없이 들어간다)", "warn")

    items: dict[str, dict] = {}
    subject_names = set()
    per_exam_seen: dict[str, set] = collections.defaultdict(set)  # 과목 안에서만 중복 검사 — PITFALLS 4-4
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.note(f.name, f"읽기 실패: {exc}", "error")
            continue
        subject_names.add(data.get("subject"))
        for raw in data.get("items", []):
            qid = raw.get("question_id")
            if not qid:
                report.note(f.name, "question_id 없는 항목", "error")
                continue
            try:
                exam_id, _num = split_qid(qid)
            except ValueError:
                report.note(qid, f"qid 형식이 아니다 ({f.name})", "error")
                continue
            if qid in per_exam_seen[exam_id]:
                report.note(qid, f"{f.name} 안에서 중복", "error")
                continue
            per_exam_seen[exam_id].add(qid)
            items[qid] = _normalize_item(raw, code_to_unit, revision)

    # 엉뚱한 폴더를 --import 했는지(예: earth1 을 earth-science-ii 에 실수로 넣었는지) 조기 발견.
    expected = (subject.curriculum or {}).get("2015")
    if expected and subject_names - {expected} - {None}:
        report.note("subject", f"가져온 파일의 subject 필드 {sorted(subject_names)} 가 "
                                f"subject.json curriculum.2015={expected!r} 와 다르다", "warn")

    target_catalog = _target_catalog(code_to_unit, (subject.standard_prefixes or {}).get(revision, []))

    ordered_items = {
        qid: items[qid]
        for qid in sorted(items, key=lambda q: (exam_sort_key(split_qid(q)[0]), split_qid(q)[1]))
    }

    generated_by = args.generated_by or (
        f"gw map --import {import_dir} · {len(files)}개 회차 원본 정규화 "
        f"(CSAT_WIKI Sonnet 태거 + Opus 감사 + 130문항 재검토 결과 반영)"
    )

    out = {
        "slug": subject.slug,
        "source_revision": "2015",
        "target_revision": revision,
        "generated_by": generated_by,
        "source_count": len(ordered_items),
        "target_catalog": target_catalog,
        "items": ordered_items,
    }

    if subject.mapping_path.exists() and not args.force:
        report.note("mapping.json", f"이미 있다: {subject.mapping_path} — 덮어쓰려면 --force", "error")
        report.count(files=len(files), parsed=len(ordered_items))
        report.next = f"python scripts/gw.py map --subject {subject.slug} --import \"{import_dir}\" --force"
        return

    if not args.dry_run:
        subject.mapping_path.parent.mkdir(parents=True, exist_ok=True)
        subject.mapping_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        report.artifact(subject.mapping_path)

    report.count(files=len(files), parsed=len(ordered_items),
                 target_catalog=len(target_catalog))
    report.next = f"python scripts/gw.py map --subject {subject.slug}  # mapping.json 을 items/ 에 적용"


def _load_taxonomy(path: Path) -> dict[str, str]:
    """{unit, standards:[{code,text}]} 모양의 목록을 아무 top-level 키에서나 찾아 code→unit 표를 만든다.

    타 과목이 같은 모양의 자기 taxonomy 파일을 새로 만들어도 이 함수가 그대로 재사용된다.
    top-level 키 이름(units_2015_earth1 같은)을 문자열로 검사하지 않는 이유가 이것이다 —
    검사했다면 새 과목마다 이 함수를 고쳐야 했을 것이다(원칙 0: 과목 하드코딩 금지).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    code_to_unit: dict[str, str] = {}
    for value in data.values() if isinstance(data, dict) else []:
        if not isinstance(value, list):
            continue
        for entry in value:
            if not isinstance(entry, dict):
                continue
            unit = entry.get("unit")
            standards = entry.get("standards")
            if not unit or not isinstance(standards, list):
                continue
            for s in standards:
                code = s.get("code") if isinstance(s, dict) else None
                if code:
                    code_to_unit[code] = unit
    return code_to_unit


def _ordered_unique(seq) -> list:
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def _normalize_item(raw: dict, code_to_unit: dict, revision: str) -> dict:
    """원본 한 문항을 mapping.json 의 항목 하나로 바꾼다. 손실 없이 옮기는 게 원칙이라
    'standard'(대표 1개, CONTRACT.md 4절 호환) 뿐 아니라 'standards'(전체 목록)도 같이 담는다 —
    지Ⅰ·Ⅱ 760문항 중 101문항이 2015쪽에서, 13문항이 2022쪽에서 성취기준을 2개 이상 받는다.
    """
    src_codes = raw.get("source_standards") or []
    # 원본 파일은 대상 개정 이름을 필드명에 그대로 박아 넣는다(mapping_2022). 이 저장소 밖(CSAT_WIKI)
    # 산출물이라 필드명을 못 바꾸므로, revision 이 다른 값이어도 찾을 수 있게 후보를 순서대로 본다.
    tgt = raw.get(f"mapping_{revision}") or raw.get("mapping_2022") or {}
    tgt_codes = tgt.get("standards") or []
    tgt_units = _ordered_unique(code_to_unit.get(c) for c in tgt_codes)
    fit = tgt.get("fit")

    return {
        "2015": {
            "standard": src_codes[0] if src_codes else None,
            "standards": src_codes,
            "unit": raw.get("source_unit"),
            "topic": raw.get("topic"),
            "points": raw.get("points"),
            # 출처가 사람이 원문을 대조해 만든 확정 매핑이라 '추정 확률'이 없다.
            # CONTRACT 4절의 타입(숫자|null)을 지켜 null 을 쓰고, 등급은 grade 로 남긴다.
            "confidence": None,
            "grade": "high",
            "by": "manual",
        },
        revision: {
            "target": tgt.get("target"),  # 지구시스템과학 | 행성우주과학 | 대응없음 (2022 두 과목군 자체를 가리킴)
            "standard": tgt_codes[0] if tgt_codes else None,
            "standards": tgt_codes,
            "unit": tgt_units[0] if tgt_units else None,
            "units": tgt_units,
            "fit": fit,
            "confidence": None,          # 숫자 점수가 아니다 — 위 _FIT_GRADE 주석 참조
            "grade": _FIT_GRADE.get(fit),
            "by": "manual",
            "note": tgt.get("note", ""),
        },
    }


# CONTRACT.md 4절이 classification.<rev> 에 정의한 키. 나머지는 전부 ext 아래로 간다.
CLASSIFICATION_KEYS = ("standard", "unit", "confidence", "by")


def _contract_entry(entry):
    """mapping.json 의 항목을 items/<qid>.json 에 실을 수 있는 형태로 맞춘다.

    두 가지를 한다.

    1) **타입 교정.** 이미 생성돼 있는 mapping.json(구버전 코드가 만든 것)은
       confidence 에 "high"/"medium" 같은 문자열을 담고 있다. CONTRACT 4절이 못박은
       타입은 숫자 아니면 null 이고 같은 필드를 `classify` 는 float 로 쓴다 —
       한 필드가 두 타입으로 갈리면 confidence 를 비교하는 소비자가 언젠가 죽는다.
       문자열이면 confidence 는 null 로 내리고 그 말을 grade(→ ext)로 옮긴다.
    2) **계약 외 키 격리.** standards·topic·points·target·units·fit·note·grade 는
       계약에 없는 확장 필드다. 지우면 정보가 사라지므로(101문항이 2015쪽에서
       성취기준을 2개 이상 받는다) 버리지 않고 `ext` 한 자리에 모은다.
       계약 키만 보는 소비자(build·validate·classify)는 아무 영향을 받지 않고,
       확장 필드를 쓰고 싶은 쪽은 어디를 봐야 하는지가 한 군데로 정해진다.

    **멱등이다.** 이미 ext 로 갈라 둔 항목을 다시 넣어도 같은 결과가 나온다.
    _is_manual_conflict 가 "기존 items 값 vs 새 값" 을 비교할 때 이 성질이 필요하다 —
    없으면 옛 평면형으로 저장된 by=manual 항목이 전부 '충돌'로 잡혀 재실행이 막힌다.
    """
    if not isinstance(entry, dict):
        return entry
    flat = dict(entry)
    nested = flat.pop("ext", None)
    ext: dict = dict(nested) if isinstance(nested, dict) else {}

    for key, value in flat.items():
        if key not in CLASSIFICATION_KEYS:
            ext[key] = value

    conf = flat.get("confidence")
    if conf is not None and not (isinstance(conf, (int, float)) and not isinstance(conf, bool)):
        flat["confidence"] = None
        # 옛 형태는 등급을 confidence 에 담았다. 별도 grade 가 이미 있으면 그쪽이 우선.
        if ext.get("grade") is None:
            ext["grade"] = conf if isinstance(conf, str) else str(conf)

    out = {k: flat[k] for k in CLASSIFICATION_KEYS if k in flat}
    if ext:
        out["ext"] = ext
    return out


def _is_manual_conflict(existing, new_value) -> bool:
    """이 자리를 덮으면 **사람이 적은 판정이 사라지는가.**

    조건 세 가지가 다 맞아야 충돌이다: 값이 이미 있고, 그 출처가 by=manual 이고,
    새 값과 실제로 다르다. by=keyword/llm 은 자동 산출물이라 mapping.json(사람이
    원문을 대조해 만든 확정 매핑)이 이기는 게 맞다.
    """
    if not isinstance(existing, dict):
        return False
    return existing.get("by") == "manual" and existing != new_value


def _is_qid(token: str) -> bool:
    try:
        split_qid(token)
        return True
    except ValueError:
        return False


def _target_catalog(code_to_unit: dict, prefixes: list[str]) -> list[dict]:
    if not prefixes or not code_to_unit:
        return []
    codes = sorted(c for c in code_to_unit if any(c.startswith(p) for p in prefixes))
    return [{"standard": c, "unit": code_to_unit[c]} for c in codes]


# --- mapping.json → items/*.json 적용 --------------------------------------

def _do_apply(subject, args, report, revision, space: Space) -> None:
    mapping = subject.mapping()
    if not mapping:
        report.note("mapping.json", f"없다: {subject.mapping_path}", "error")
        report.next = (f"python scripts/gw.py map --subject {subject.slug} "
                        f"--import <외부 매핑 폴더> --taxonomy <taxonomy.json>")
        return

    only = set(x.strip() for x in args.only.split(",")) if args.only else None
    mapping_items: dict = mapping.get("items", {})
    # 원본 개정을 "2015" 로 박아 두면 원본이 2022 인 과목(2022 → 다음 개정 매핑)에서 어긋난다.
    # mapping.json 이 스스로 적어 둔 값을 쓰고, 없을 때만 옛 기본값으로 떨어진다.
    source_revision = str(mapping.get("source_revision") or "2015")
    # 형제 모듈(crop/extract/classify/validate)의 --only 는 qid 와 exam_id 를 모두 받는다.
    # 여기만 qid 전용이라 `--only 2024_수능` 이 applied=0 으로 조용히 끝나면서
    # "mapping.json 에 없는 qid" 라는 엉뚱한 경고를 냈다(통합 검증에서 발견).
    known_exams = {split_qid(q)[0] for q in mapping_items if _is_qid(q)}
    if only:
        for token in sorted(only - mapping_items.keys() - known_exams):
            report.note(token, "mapping.json 에 없는 qid/exam_id — --only 목록을 확인해라", "warn")

    applied = 0
    skipped_conflict = 0
    forced_overwrite = 0
    missing_by_exam: dict[str, int] = collections.defaultdict(int)
    exam_total: dict[str, int] = collections.defaultdict(int)

    for qid, entry in mapping_items.items():
        try:
            exam_id, _num = split_qid(qid)
        except ValueError:
            report.note(qid, "mapping.json 의 qid 형식이 잘못됐다", "error")
            continue
        if only and qid not in only and exam_id not in only:
            continue
        exam_total[exam_id] += 1

        item_path = space.item(qid)
        if not item_path.exists():
            missing_by_exam[exam_id] += 1
            continue

        try:
            item_data = json.loads(item_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.note(qid, f"items/ 읽기 실패: {exc}", "error")
            continue

        classification = item_data.setdefault("classification", {})

        # ── 두 개정 모두에 같은 가드를 건다 ──
        # 예전에는 대상 개정(revision)에만 by=manual 충돌 가드가 있었고, 원본 개정
        # (source_revision)은 **무조건** 덮었다. items 에 사람이 직접 적어 둔 2015 판정이
        # `gw map` 한 번에 조용히 사라질 수 있었다는 뜻이다. 원본이든 대상이든 사람이
        # 손으로 적은 값의 무게는 같으므로 가드도 같아야 한다.
        #
        # 한 문항의 두 개정을 **한꺼번에** 판정한다 — 한쪽만 반영하면 items 의 두 개정이
        # 서로 다른 매핑 세대를 가리키게 되어 나중에 무엇이 옳은지 알 수 없게 된다.
        planned = {rev: _contract_entry(entry.get(rev))
                   for rev in _ordered_unique([source_revision, revision])}
        conflicts = [rev for rev, new_value in planned.items()
                     if _is_manual_conflict(classification.get(rev), new_value)]
        if conflicts and not args.force:
            report.note(qid, f"기존 classification.{'/'.join(conflicts)} 이(가) 이미 by=manual 로 "
                              f"다르게 적혀 있다 — 덮어쓰려면 --force", "warn")
            skipped_conflict += 1
            continue
        if conflicts:
            # --force 로 사람이 적은 판정을 실제로 지운 건수. 리포트에 남지 않으면
            # 무엇이 사라졌는지 나중에 알 방법이 없다.
            forced_overwrite += 1
            report.note(qid, f"--force: 사람이 적은 classification.{'/'.join(conflicts)} 을(를) "
                              f"mapping.json 값으로 덮었다", "warn")

        classification.update(planned)
        applied += 1

        if not args.dry_run:
            item_path.write_text(json.dumps(item_data, ensure_ascii=False, indent=2), encoding="utf-8")

    total_missing = sum(missing_by_exam.values())
    for exam_id in sorted(missing_by_exam, key=exam_sort_key):
        n = missing_by_exam[exam_id]
        report.note(exam_id, f"items/ 에 {n}/{exam_total[exam_id]}문항이 없다 — "
                              f"이 회차는 아직 download/crop/extract 를 안 돌렸을 수 있다", "info")

    report.count(mapping_items=len(mapping_items), applied=applied,
                 missing=total_missing, skipped_conflict=skipped_conflict,
                 forced_overwrite=forced_overwrite)

    if applied:
        report.next = f"python scripts/gw.py validate --subject {subject.slug}"
    elif total_missing:
        report.next = f"python scripts/gw.py extract --subject {subject.slug}  # items/ 가 비어 있다"


# --- 공백(0문항 성취기준) 리포트 --------------------------------------------

def _do_report_gaps(subject, args, report, revision) -> None:
    own_mapping = subject.mapping()
    if not own_mapping:
        report.note("mapping.json", f"없다: {subject.mapping_path}", "error")
        return

    catalog = own_mapping.get("target_catalog") or []
    if not catalog:
        report.note("target_catalog", "mapping.json 에 target_catalog 가 비어 있다 — "
                                       "--import 를 --taxonomy 와 함께 다시 돌려야 공백 리포트가 가능하다", "error")
        return

    prefixes = set((subject.standard_prefixes or {}).get(revision, []))

    # 같은 대상 개정으로, 성취기준 접두어가 겹치는 다른 과목의 매핑도 합산한다.
    # 지구과학Ⅰ·Ⅱ 처럼 여러 2015 과목이 같은 2022 과목군으로 흡수되는 구조라서,
    # 한 과목만 보면 실제로는 문항이 있는 성취기준을 0문항이라고 잘못 보고하게 된다.
    # (PITFALLS.md 5-3 — 12지시03-01·03-02, 12행우 6개는 "정말 없음"이었지만
    #  그걸 확인하려면 애초에 지Ⅰ·Ⅱ를 합쳐서 세어야 했다.)
    used = collections.Counter()
    contributors = []
    for other in all_subjects():
        other_mapping = other.mapping()
        if not other_mapping or other_mapping.get("target_revision") != revision:
            continue
        other_prefixes = set((other.standard_prefixes or {}).get(revision, []))
        if not (other_prefixes & prefixes):
            continue
        contributors.append(other.slug)
        for entry in other_mapping.get("items", {}).values():
            rev_entry = entry.get(revision) or {}
            codes = rev_entry.get("standards") or ([rev_entry["standard"]] if rev_entry.get("standard") else [])
            for code in codes:
                used[code] += 1

    zero = []
    for row in catalog:
        code = row.get("standard")
        if used.get(code, 0) == 0:
            zero.append({"standard": code, "unit": row.get("unit")})

    for row in zero:
        report.note(row["standard"], f"{row['unit']} — {', '.join(contributors) or subject.slug} 전체에서 0문항. "
                                      f"2022 신규 도입 개념이라 정말 없는 것인지, 판정을 잘못한 것인지 어휘로 재검색해서 확인해라.",
                    "warn")

    report.count(target_codes=len(catalog), zero_count=len(zero))
    report.extra["zero_standards"] = zero
    report.extra["gap_contributors"] = contributors
