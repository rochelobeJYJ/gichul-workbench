# -*- coding: utf-8 -*-
"""`gw extract` — 문제지·정답지·해설지에서 본문/정답/배점을 뽑아 items/ 에 채운다.

설계 원칙 세 가지.

1. **정답은 세 축으로 읽고 대조한다.** 정답지 / 해설지 / pdfplumber.
   세 축이 갈리면 채우지 않고 error 로 남긴다 (extractlib/answers.py 참조).
2. **items 는 병합한다.** crop 이 써 둔 source/crop/materials 를 절대 지우지 않는다.
   이 단계가 소유하는 키는 number/points/answer/answer_symbol/text/extraction_mode 뿐이다.
3. **회차 하나가 죽어도 나머지는 간다.** 판형은 회차마다 조용히 바뀐다.
   회차 단위·문항 단위로 예외를 격리하고, 실패는 리포트에 남긴다.

불변식(문항 수·총점)은 subject.json 에서 읽는다. 20 이나 50 을 코드에 박지 않는다.
"""
from __future__ import annotations

import contextlib
import io
import json
import time

from common import Report, Space, load_subject
from common.ids import make_qid, split_qid
from common.progress import Progress
from extractlib import answers as ax
from extractlib import sources as src
from extractlib.layouts import get_strategy
from extractlib.points import normalize_points, points_equal
from extractlib.textnorm import ANSWER_NONE, answer_to_symbol

# 이 단계가 소유하는 items 키. 나머지는 손대지 않는다 (crop/classify/map 의 몫).
# _merge_item 이 이 목록으로만 덮어쓰므로, 여기 없는 키는 실수로도 지워지지 않는다.
IDENTITY_KEYS = ("qid", "slug", "exam_id")
OWNED_KEYS = ("number", "points", "answer", "answer_symbol", "text",
              "extraction_mode")
# 계약(CONTRACT 4절)에 없는 확장 필드는 item["ext"] 아래 한 곳에 모은다.
# 예전에는 answer_check 가 items 최상위에, 파싱 원문이 text["raw"] 안에 흩어져 있어서
# "계약 키인지 이 단계의 부산물인지"를 items 만 보고는 구분할 수 없었다.
#   answer_check → ext.answer_check   (validate 가 실제로 소비한다. 지우면 안 된다)
#   text["raw"]  → ext.text_raw       (파싱 실패 시 전사 단계가 붙잡을 원문)
# ext 전체를 통째로 덮지 않는 이유: 다른 단계가 자기 확장 필드를 같은 자리에 둘 수 있다.
#
# choices_source / boxed_source 는 **문항 단위 부분 vision** 표시다. extraction_mode 는
# 회차 단위(문제지 한 권에 텍스트 레이어가 있는가)라 같은 회차 안에서 문항마다 갈리는
# 손실을 담지 못한다. 값은 "image" 하나뿐이고 정상이면 키 자체가 없다 — 멀쩡한 문항까지
# "text" 로 채워 봐야 읽는 쪽에 정보가 늘지 않는다. 판정 근거는
# extractlib/tamgu.py 의 image_choice_band() / boxed_source() 주석에 있다.
OWNED_EXT_KEYS = ("answer_check", "text_raw", "choices_source", "boxed_source")
NOTE_PREFIX = "extract: "

# 비직접(OCR) 텍스트를 본문으로 인정할 최소 성공률.
# 이보다 낮으면 vision 으로 떨어뜨린다 — 깨진 전사를 코퍼스에 넣느니 비우는 편이 낫다.
OCR_ACCEPT_RATIO = 0.8


def register(parser) -> None:
    parser.add_argument("--subject", required=True, help="과목 슬러그")
    parser.add_argument("--only", help="qid 또는 exam_id 를 쉼표로 (예: 2024_수능,2023_9월모평_07)")
    parser.add_argument("--force", action="store_true",
                        help="status=verified 인 문항도 덮어쓴다")
    parser.add_argument("--dry-run", action="store_true", help="items 를 쓰지 않는다")
    parser.add_argument("--quiet", action="store_true", help="한 줄 요약도 줄인다")
    parser.add_argument("--no-ocr", action="store_true",
                        help="OCR 폴백을 끈다 (빠른 재실행용, 텍스트 레이어 없는 회차는 비게 된다)")
    parser.add_argument("--workspace", help="작업 공간 경로 직접 지정 (기본 workspace/<slug>)")


# --------------------------------------------------------------------------
# items 병합
# --------------------------------------------------------------------------

def _finish(report, args) -> int:
    """--quiet 이면 한 줄만 남긴다. 리포트 파일은 어느 쪽이든 항상 쓴다.

    리포트가 LLM 이 읽는 유일한 출력이므로(CONTRACT 5절) --quiet 이 리포트를
    없애서는 안 된다. 줄어드는 것은 사람용 stdout 뿐이다.
    """
    if not getattr(args, "quiet", False):
        return report.finish()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = report.finish()
    lines = buffer.getvalue().splitlines()
    if lines:
        print(lines[0])
    return code


def _load_item(path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _merge_item(base: dict, updates: dict, notes: list[str]) -> dict:
    """crop 이 써 둔 값을 보존하면서 이 단계의 결과만 덮어쓴다.

    updates 를 통째로 update() 하지 않고 OWNED_KEYS 만 골라 넣는 이유는,
    나중에 이 함수에 다른 키가 섞여 들어와도 crop/classify 의 결과를 덮지 못하게
    구조로 막기 위해서다. 주석으로 약속하는 것보다 코드로 막는 편이 오래 간다.
    """
    item = dict(base)
    for key in IDENTITY_KEYS + OWNED_KEYS:
        if key in updates:
            item[key] = updates[key]

    # ext 는 칸 단위로 병합한다. 통째로 갈아끼우면 다른 단계가 같은 자리에 둔
    # 확장 필드까지 사라진다.
    ext = dict(base.get("ext") or {})
    new_ext = updates.get("ext") or {}
    for key in OWNED_EXT_KEYS:
        if key in new_ext:
            ext[key] = new_ext[key]
        else:
            ext.pop(key, None)
    if ext:
        item["ext"] = ext
    else:
        item.pop("ext", None)
    # 옛 자리에 남은 값은 지운다. 두 자리에 남겨 두면 어느 쪽이 최신인지 알 수 없다.
    # 읽는 쪽(validate)은 하위호환을 위해 두 자리를 다 본다 — 이미 디스크에 있는
    # 옛 items 를 extract 재실행 없이도 검증할 수 있어야 하기 때문이다.
    item.pop("answer_check", None)
    if isinstance(item.get("text"), dict):
        item["text"].pop("raw", None)

    item.setdefault("status", "scaffold")
    # 이 단계가 만든 note 만 갈아끼운다. 다른 단계의 note 는 그대로 둔다.
    kept = [n for n in (base.get("notes") or []) if not str(n).startswith(NOTE_PREFIX)]
    item["notes"] = kept + [NOTE_PREFIX + n for n in notes]
    return item


# --------------------------------------------------------------------------
# 회차 처리
# --------------------------------------------------------------------------

class ExamResult:
    def __init__(self, exam_id: str):
        self.exam_id = exam_id
        self.items: dict[int, dict] = {}
        self.item_notes: dict[int, list[str]] = {}
        self.notes: list[tuple[str, str, str]] = []   # (id, why, severity)
        self.mode = "vision"
        self.axes: dict[str, str] = {}
        # 부분 vision 문항 번호. 문항마다 note 를 남기면 회차 하나가 attention 상한
        # 30건(CONTRACT 5절)을 혼자 먹으므로 회차 끝에서 한 줄로 모아 알린다.
        self.image_parts: dict[str, list[int]] = {}


def _read_exam(space, subject, strategy, exam_id: str, *, use_ocr: bool) -> ExamResult:
    result = ExamResult(exam_id)
    # ★ 문항 수는 **회차 단위**다. 한 슬러그 안에 판형이 둘 있는 과목이 실재한다
    #   (통합과목: 2025년 3월까지 20문항, 6월부터 25문항). 과목 스칼라 하나로 읽으면
    #   20문항 회차에서 정답지 원문자 픽셀 대조의 자기검증('찾은 원문자 수 == 문항 수')이
    #   20 != 25 로 깨져 **정답 축이 통째로 죽는다**(실측: 그 회차 answer 가 전부 null).
    count = subject.question_count_for(exam_id)
    if not count:
        raise ValueError("subject.question_count 가 없다 — 문항 수 불변식을 세울 수 없다")

    found = src.find_sources(space, exam_id)
    if found.get("problem") is None:
        result.notes.append((exam_id, "문제지 파일이 없다", "error"))

    # --- 텍스트 레이어 ---------------------------------------------------
    problem = src.load_layers(found.get("problem"), want_plumber=False,
                              want_ocr=use_ocr, columns=strategy.columns)
    # 해설지는 OCR 하지 않는다. 글리프가 깨진 회차라도 첫머리 정답표의 숫자는
    # 직접 텍스트 레이어에 멀쩡히 남아 있고, 이 단계는 해설 본문을 쓰지 않는다.
    solution = src.load_layers(found.get("solution"), want_plumber=True, want_ocr=False)
    # 정답지는 PNG 로만 제공되는 회차가 많아 OCR 을 시도해 볼 값어치가 있다(이미지 1장).
    answer = src.load_layers(found.get("answer"), want_plumber=True, want_ocr=use_ocr,
                             columns=1)

    # 수식 폰트의 사설 영역 글자가 매핑표에 없으면 normalize_text 가 조용히 지운다.
    # 지워진 문장은 멀쩡해 보이기 때문에('Al(s)' → 'Al()') 리포트로 끌어올리지 않으면
    # 아무도 모른다. 실제로 화학Ⅰ 2024 수능 문제지에서 199자가 그렇게 사라져 있었다.
    for role, layers in (("문제지", problem), ("해설지", solution), ("정답지", answer)):
        leftovers = getattr(layers, "unmapped_pua", None) if layers else None
        if not leftovers:
            continue
        top = ", ".join(f"U+{ord(c):04X}×{n}" for c, n in
                        sorted(leftovers.items(), key=lambda kv: -kv[1])[:5])
        result.notes.append(
            (exam_id, f"{role}에서 매핑되지 않은 수식 폰트 글자 {sum(leftovers.values())}자가 "
                      f"지워졌다({len(leftovers)}종: {top}) — extractlib/textnorm.py 의 "
                      f"EQFONT_REPLACEMENTS 에 추가해야 전사가 온전해진다", "warn"))

    body_text, mode = (problem.body() if problem else ("", "vision"))
    if problem is not None and mode == "vision" and problem.ocr_error:
        result.notes.append((exam_id, f"OCR 사용 불가 — {problem.ocr_error}", "warn"))

    # --- 문항 분리 -------------------------------------------------------
    blocks: dict[int, str] = {n: "" for n in range(1, count + 1)}
    parsed: dict[int, object] = {}
    if body_text:
        try:
            blocks = strategy.split(strategy.clean(body_text, subject), count)
        except ValueError as exc:
            # 2단 판형이 뒤섞여 분리가 불가능한 회차. 정답·배점만 채우고
            # 발문/선택지는 크롭 이미지와 전사 단계에 맡긴다.
            result.notes.append((exam_id, f"문항 분리 실패 — vision 모드로 진행 ({exc})", "warn"))
            blocks = {n: "" for n in range(1, count + 1)}
            mode = "vision"

    if mode != "vision":
        for number, block in blocks.items():
            parsed[number] = strategy.parse(block)
        # ★ 세는 것은 '에러가 아닌 문항'이 아니라 **선택지까지 텍스트로 복원된 문항**이다.
        # choices_source=="image" 는 '텍스트로는 못 읽었지만 크롭이 본체라 실패는 아니다'라는
        # 표시라서, 이것을 성공으로 세면 OCR 품질 관문이 조용히 무력화된다.
        # 실측: 텍스트 레이어가 없는 지구과학Ⅱ 2025 수능은 OCR 본문에서 선택지 라벨이
        # 통째로 사라져 20문항이 전부 image 로 판정됐고, 그 결과 20/20 '성공'이 되어
        # vision 으로 떨어지지 않고 **믿을 수 없는 OCR 전사가 본문으로 채택될 뻔했다.**
        ok = sum(1 for p in parsed.values() if not p.error and not p.choices_source)
        if mode != "direct" and ok < count * OCR_ACCEPT_RATIO:
            # OCR 전사가 이 정도로 깨지면 본문으로 쓸 수 없다. 배점 표기는 그대로 쓴다.
            result.notes.append(
                (exam_id, f"OCR 본문 신뢰 불가({ok}/{count} 파싱) — vision 모드로 진행", "warn"))
            mode = "vision"
            parsed = {}
    result.mode = mode

    # --- 정답 세 축 -------------------------------------------------------
    kice = (subject.providers or {}).get("kice") or {}
    aliases = list(kice.get("aliases") or [])
    if subject.label:
        aliases.append(subject.label)

    readings = [
        ax.read_answer_sheet(answer, aliases, count, subject.points_total),
        ax.read_solution(solution, count),
        ax.read_pdfplumber(found.get("answer"), answer, solution, aliases, count),
    ]
    live = [r for r in readings if r]
    for reading in readings:
        result.axes[reading.source] = reading.origin if reading else f"없음({reading.reason})"
    if len(live) < 3:
        missing = ", ".join(f"{r.source}: {r.reason}" for r in readings if not r)
        severity = "error" if not live else "warn"
        result.notes.append(
            (exam_id, f"정답 교차검증 축 {len(live)}/3 — {missing}", severity))

    # --- 배점 세 축 -------------------------------------------------------
    marks, mark_reason = strategy.points_from_marks(blocks, count, subject.points_total)
    candidates = [ax.Reading(source="question_marks", origin="[N점] 표기", points=marks)]
    candidates += [r for r in readings if r.points]
    # 배점 축은 **완전할 때만** 표를 준다. 일부 문항만 읽힌 배점표는 정보가 아니라
    # 잡음이고, 한 표만으로 교차검증을 무승부로 만들어 멀쩡한 배점을 지운다.
    # 합계 비교는 points_equal 로 한다. 배점이 실수가 될 수 있어서다(통합과목 1.5/2.5점).
    # `==` 로 두면 부동소수 오차 한 번에 **멀쩡한 배점 축이 통째로 버려지고**
    # 문항 배점이 전부 빈다 — 값이 틀리는 게 아니라 사라지는 쪽이라 더 조용하다.
    point_readings = [r for r in candidates
                      if len(r.points) >= count
                      and (not subject.points_total
                           or points_equal(sum(r.points.values()), subject.points_total))]
    if not marks and mark_reason and any(b for b in blocks.values()):
        result.notes.append((exam_id, f"배점 표기 해석 실패 — {mark_reason}", "warn"))
    if not point_readings:
        result.notes.append((exam_id, "배점을 확정한 축이 없다 — 문항별 배점이 빈다", "warn"))

    # --- 문항별 확정 ------------------------------------------------------
    total_points = 0
    for number in range(1, count + 1):
        qid = make_qid(exam_id, number)
        notes: list[str] = []
        verdict = ax.cross_check(live, number)
        answer_value = verdict.value
        if verdict.severity != "ok":
            result.notes.append((qid, verdict.why, verdict.severity))
        if answer_value == ANSWER_NONE:
            notes.append("정답 없음(출제 오류로 공고된 문항)")
            result.notes.append((qid, "정답이 '없음'인 출제 오류 문항", "info"))

        point_verdict = ax.cross_check(point_readings, number, field_name="points")
        if point_verdict.severity == "error":
            notes.append("배점을 확정하지 못했다")
            # 축이 아예 없는 경우는 회차 단위로 이미 한 번 남겼다. 여기서 또 남기면
            # 문항 수만큼 곱해져 attention 상한을 혼자 먹는다. 축이 있는데 갈린 경우만 남긴다.
            if point_readings:
                result.notes.append((qid, "배점 " + point_verdict.why, "warn"))
        elif point_verdict.severity == "warn" and point_verdict.disagree:
            result.notes.append((qid, "배점 " + point_verdict.why, "warn"))
        if point_verdict.value:
            total_points += point_verdict.value

        item_parsed = parsed.get(number)
        text = {"stem": "", "boxed": "", "choices": []}
        # 계약 밖 확장 필드는 전부 여기(ext)로 모은다. text 는 계약이 정한
        # {stem, boxed, choices} 만 담게 두고, 파싱 원문은 ext.text_raw 로 뺀다.
        ext: dict = {
            "answer_check": {
                "agree": sorted(verdict.agree),
                "disagree": {k: v for k, v in sorted(verdict.disagree.items())},
                "severity": verdict.severity,
            },
        }
        if item_parsed is not None:
            if item_parsed.error:
                notes.append(item_parsed.error)
                result.notes.append((qid, item_parsed.error, "warn"))
            else:
                text = {"stem": item_parsed.stem, "boxed": item_parsed.boxed,
                        "choices": item_parsed.choices}
                if item_parsed.warning:
                    notes.append(item_parsed.warning)
                    result.notes.append((qid, item_parsed.warning, "warn"))
                # 부분 vision 표시. 값이 있을 때만 키를 만든다 — 멀쩡한 문항에
                # "text" 를 채워 넣으면 items 만 커지고 읽는 쪽은 얻는 게 없다.
                for key, value in (("choices_source", item_parsed.choices_source),
                                   ("boxed_source", item_parsed.boxed_source)):
                    if not value:
                        continue
                    ext[key] = value
                    notes.append(f"{key}={value} — 크롭 이미지가 본체다(전사 대기)")
                    result.image_parts.setdefault(key, []).append(number)
            # 파싱에 실패해도 원문은 남긴다. 전사 단계가 붙잡을 유일한 실마리다.
            if item_parsed.raw:
                ext["text_raw"] = item_parsed.raw

        result.items[number] = {
            "qid": qid,
            "slug": subject.slug,
            "exam_id": exam_id,
            "number": number,
            "points": point_verdict.value,
            "answer": answer_value,
            "answer_symbol": (answer_to_symbol(answer_value)
                              if answer_value is not None else None),
            "text": text,
            "extraction_mode": result.mode,
            "ext": ext,
        }
        result.item_notes[number] = notes

    # 부분 vision 문항은 회차 단위로 한 줄만 남긴다(위 image_parts 주석 참조).
    # severity 는 info 다 — 코드로 고칠 것이 없고 전사 단계가 이미지에서 읽어야 한다.
    for key, numbers in sorted(result.image_parts.items()):
        result.notes.append(
            (exam_id, f"{key}=image 문항 {len(numbers)}건 — 텍스트 레이어로 복원 불가, "
                      f"크롭 이미지가 본체다: {', '.join(str(n) for n in numbers)}", "info"))

    # --- 불변식 ----------------------------------------------------------
    if (subject.points_total and total_points
            and not points_equal(total_points, subject.points_total)):
        result.notes.append(
            (exam_id, f"배점 합이 맞지 않는다: {normalize_points(total_points)} "
                      f"!= {subject.points_total}", "error"))
    return result


# --------------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------------

def _wanted(only: str | None):
    """--only 를 (exam_id 집합, qid 집합) 으로. 둘 다 비면 전부."""
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


def run(args) -> int:
    started = time.time()
    # 과목 정의를 못 읽으면 리포트를 남길 workspace 경로조차 정할 수 없다.
    # gw.py 는 예외를 그대로 흘려보내서 LLM 이 리포트 대신 traceback 을 읽게 된다
    # (통합 검증에서 7개 명령 중 5개가 그랬다). 형제 모듈(build/classify)과 같은
    # 한 줄 안내로 끝낸다 — load_subject 의 메시지가 등록된 과목 목록까지 담고 있다.
    try:
        subject = load_subject(args.subject)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] extract: {exc}")
        return 1
    # --workspace 는 7개 명령 공통 옵션이다(crop 에만 있었다). 공유 workspace/<slug> 를
    # 여러 실행이 서로 밟아 산출물을 지우는 사고가 실제로 여러 번 났다.
    space = Space(subject.slug, getattr(args, "workspace", None))
    report = Report("extract", subject.slug, space)

    try:
        strategy = get_strategy(subject.layout)
    except NotImplementedError as exc:
        report.note(subject.slug, str(exc).replace("\n", " "), "error")
        report.next = f"subjects/{subject.slug}/subject.json 의 layout 을 확인한다"
        return _finish(report, args)

    count = subject.question_count or 0
    if not count:
        report.note(subject.slug,
                    "subject.question_count 가 없다 — 문항 수·배점 불변식을 세울 수 없다",
                    "error")
        report.next = f"subjects/{subject.slug}/subject.json 에 question_count 를 채운다"
        return _finish(report, args)

    wanted_exams, wanted_qids = _wanted(args.only)
    try:
        all_exams = list(space.iter_exams())
    except Exception as exc:  # noqa: BLE001
        # sources/ 아래에 exam_id 규약을 벗어난 폴더가 섞이면 정렬 키가 터진다.
        # 그것 하나로 전체가 멈추지 않도록 이름순으로 떨어뜨린다.
        report.note(subject.slug,
                    f"회차 정렬 실패({exc}) — 이름순으로 진행한다. "
                    f"sources/ 에 exam_id 형식이 아닌 폴더가 있는지 확인한다", "warn")
        all_exams = sorted(p.name for p in space.sources.iterdir() if p.is_dir()) \
            if space.sources.exists() else []
    exam_ids = [e for e in all_exams if wanted_exams is None or e in wanted_exams]
    if not exam_ids:
        report.note(subject.slug,
                    f"처리할 회차가 없다: {space.sources} 아래에 sources/<exam_id>/ 가 있어야 한다",
                    "error")
        report.next = f"python scripts/gw.py download --subject {subject.slug}"
        return _finish(report, args)

    # 회차마다 문항 수가 다를 수 있으므로 `문항수 × 회차수` 가 아니라 합계로 센다.
    count_of = {e: (subject.question_count_for(e) or count) for e in exam_ids}
    report.count(expected=sum(count_of.values()), done=0, failed=0, skipped=0,
                 exams=len(exam_ids))
    space.ensure()
    modes: dict[str, int] = {}
    axes_used: dict[str, int] = {}
    uncropped: set[str] = set()

    # 회차 하나를 읽는 데 PDF 세 권(문제·정답·해설)을 파싱한다. 19회차면 수십 초라
    # 표시가 없으면 멈춘 것처럼 보인다. 세는 단위는 회차 — 문항 단위로 세면 숫자가
    # 회차 파싱이 끝날 때마다 20씩 튀어서 오히려 진행 상황을 가린다.
    bar = Progress(len(exam_ids), "회차", label="extract", args=args).open()
    for exam_id in bar.wrap(exam_ids):
        bar.detail(exam_id)
        try:
            result = _read_exam(space, subject, strategy, exam_id, use_ocr=not args.no_ocr)
        except Exception as exc:  # noqa: BLE001 — 회차 하나가 전체를 막지 않는다
            report.note(exam_id, f"{type(exc).__name__}: {exc}", "error")
            report.bump("failed", count_of[exam_id])
            continue

        for ident, why, severity in result.notes:
            report.note(ident, why, severity)
        modes[result.mode] = modes.get(result.mode, 0) + 1
        live = sum(1 for origin in result.axes.values() if not origin.startswith("없음"))
        axes_used[f"{live}축"] = axes_used.get(f"{live}축", 0) + 1

        for number, payload in sorted(result.items.items()):
            qid = payload["qid"]
            if wanted_qids and qid not in wanted_qids:
                continue
            path = space.item(qid)
            existing = _load_item(path)
            if existing.get("status") == "verified" and not args.force:
                report.bump("skipped")
                continue
            merged = _merge_item(existing, payload, result.item_notes.get(number, []))
            # crop 이 아직 안 돈 회차인지 여기서만 알 수 있다. extract 는 sources/ 만
            # 보면 되므로 혼자서도 끝까지 돌지만, 그 결과 items/<qid>.json 은
            # crop/source/materials 가 통째로 빠진 반쪽이 된다(CONTRACT 4절 위반).
            # 통합 검증에서 실제로 그런 items 20개를 발견했는데, extract 가
            # [OK] + next=classify 로 끝내버려서 크롭이 빠진 걸 아무도 못 봤다.
            # 회차 단위로 모아 두었다가 아래에서 한 번만 알린다(문항마다 남기면 20건씩 쌓인다).
            if "crop" not in merged:
                uncropped.add(exam_id)
            if payload["answer"] is None:
                report.bump("failed")
            else:
                report.bump("done")
            if not args.dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                                encoding="utf-8")

    bar.close()   # 요약을 찍기 전에 진행률 줄을 지운다
    for exam_id in sorted(uncropped):
        report.note(exam_id, "crop 이 안 돌아 items 에 crop/source/materials 가 없다 "
                             "— 본문·정답은 채워졌지만 크롭 이미지가 빠진 반쪽 상태다", "warn")

    report.artifact(space.rel(space.items))
    # 회차 단위 건강 상태 요약. attention 을 다 읽지 않아도 이 두 줄로 판이 보인다.
    report.extra["modes"] = modes
    report.extra["cross_check_axes"] = axes_used
    report.extra["elapsed_read_sec"] = round(time.time() - started, 1)
    if args.dry_run:
        report.extra["dry_run"] = True
    if report.has_error:
        report.next = (f"reports/extract.json 의 attention 을 확인한 뒤 "
                       f"python scripts/gw.py extract --subject {subject.slug} --only <회차> --force")
    elif uncropped:
        # 크롭이 빠진 회차가 있으면 classify 로 보내면 안 된다 — 되돌아갈 곳을 찍어 준다.
        report.next = (f"python scripts/gw.py crop --subject {subject.slug} "
                       f"--only {','.join(sorted(uncropped))}")
    else:
        report.next = f"python scripts/gw.py classify --subject {subject.slug}"
    return _finish(report, args)
