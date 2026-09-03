# -*- coding: utf-8 -*-
"""`gw validate` — 산출물 구조·정합성 검사. docs/CONTRACT.md 8절의 유일한 구현.

세 원본을 하나로 합쳤다(CSAT_WIKI, 지구과학Ⅱ 파이프라인에서 19회차 380문항으로 실전 검증됨):
  - wiki_earth2/validate_cards.py     구조 검사, 글리프 손상 잔존 탐지
  - wiki_earth2/validate_scaffold.py  정답·배점 3중 대조, 50점 불변식
  - wiki_earth2/validate_metadata.py  분류(성취기준) 메타데이터 검사
  - wiki_2022_지구시스템과학/validate_mapping.py  2015↔2022 매핑 검사

이 도구의 신뢰를 담보하는 모듈이라 실패를 숨기지 않는다 — 애매하면 error 로 기운다.

## 판형(layout) 경계
CONTRACT 3절: layout 은 크롭·추출 전략 선택자다. 이 파일의 검사 대부분(선택지 5개,
[N점] 표기, 정답 3중 대조, 크롭 이미지, 자료 3중 대조)은 "문항 하나 = 한 블록, 5지선다"
라는 tamgu-1q1block 의 물리적 전제 위에 서 있다. passage-group(국어·영어, 지문 하나에
문항 여럿)·math-mixed(단답형 섞임, "선택지 5개" 자체가 성립 안 함)는 전제가 다르므로
LAYOUT_CHECKERS 테이블에 자리만 만들고 NotImplementedError 로 명확히 막는다.
points_total/question_count 불변식만은 subject.json 데이터를 그대로 세는 것이라
판형과 무관해 테이블 밖(공통)에 둔다.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # PyMuPDF — 텍스트 스트림 순서로 읽는 축
import pdfplumber  # 단어 좌표로 다시 읽는 축 — 아래 pdfplumber_answer_rows 참조
from PIL import Image

from common import CURRICULUM_STANDARDS, Report, Space, load_subject, split_qid

# ---------------------------------------------------------------------------
# 국가 시험 형식 상수. ids.py 의 EXAM_ALIASES 와 같은 층위 — "지구과학" 같은 과목
# 분기가 아니라 수능/모평 5지선다 자체의 형식이라 여기 고정해도 0절 위반이 아니다.
CHOICE_SYMBOLS = "①②③④⑤"
CHOICE_TO_INT = {sym: i + 1 for i, sym in enumerate(CHOICE_SYMBOLS)}

# 텍스트 레이어 손상이 복원되지 않고 남은 흔적 (validate_cards.py GLYPH_SMELLS 원본).
# 과목마다 손상 양상이 다를 수 있다 — 지금은 지구과학Ⅱ 실전에서 나온 5개뿐이라
# contract_gaps 로 "subject.json 에 확장 자리 필요"를 남긴다(직접 스키마를 늘리지 않음).
GLYPH_SMELLS = [
    (re.compile(r"\d\s?km\s?s(?![a-z])"), "km/s 빗금 유실 의심"),
    (re.compile(r"[ᄀ-ᄒ]"), "옛한글 자모(ㄱㄴㄷ 미복원)"),
    (re.compile(r"(?<![\w/])/\s*\d{2,}-\d{2,}"), "근호+지수 붕괴 흔적(/ 202-82 꼴)"),
    # 위도/경도 표기(90N 꼴)만 잡으려 좁힌 정규식. 실전에서 압력 단위 "100N/m²"가
    # 느슨한 패턴( \d+[NSEW] )에 걸려 오탐이 났다 — 뒤에 '/'나 글자가 오면 단위이지
    # 각도가 아니므로 부정 전방탐색으로 뺐다.
    (re.compile(r"(?<!\d)\d{1,2}[NSEW](?![/\w])"), "각도 기호 누락 의심(90N 꼴)"),
    (re.compile(r"\bSiO\b(?!₄|<sub)"), "아래첨자 누락 의심(SiO)"),
]

# 크롭 이미지 정상성 판정 기준. 과목과 무관한 일반 휴리스틱이라 상수로 둔다.
MIN_IMAGE_DIM_PX = 20
MIN_IMAGE_BYTES = 200
BLANK_EXTREMA_THRESHOLD = 3  # 그레이스케일 최대-최소 명암차가 이 이하면 사실상 단색(빈 크롭)

# 해설/정답 PDF 파싱용 정규식.
# _HEADER_RE: 머리말 요약표 "01. ①  02. ③ ..." 매칭.
# 숫자·마침표·기호 사이는 줄내 공백류(반각/탭/전각/nbsp)만 허용하고 줄바꿈 \n 은
# 뺐다. docs/PITFALLS.md 3-1: 인라인 정답표 정규식에 \s* 를 쓰면 줄바꿈을 건너뛰어
# 엉뚱한 줄의 숫자를 정답으로 읽는 사고가 실전에서 있었다고 기록돼 있다 — 한 칸(문항
# 번호+기호)은 원래 한 줄 안에서 끝나야 정상이니 줄바꿈까지 건너뛸 이유가 없다.
# 처음엔 [ \t]* 로 좁혔더니 실측 19회차 중 한 회차(2025 9월모평)가 20/20에서
# 19/20으로 퇴행했다 — 그 회차의 표 칸 사이 공백이 전각 공백(U+3000)이었다.
# 그래서 흔한 공백류를 다 넣되 \n 만은 계속 뺐다.
_HEADER_RE = re.compile(r"(\d{1,2})[ \t　\xa0]*[.．][ \t　\xa0]*([①②③④⑤])")
# _BLOCK_START_RE: 해설 본문의 문항 경계 "7. 화성암의 조직" 매칭.
# 함정: 머리말 표의 "01. ①"도 "숫자+마침표" 형태라 그냥 두면 표 전체가 문항 1개로
# 오인식된다(실측: 19회차 중 18회차에서 블록[1]만 잡히고 2~20은 통째로 사라졌다).
# "숫자. " 바로 뒤에 동그라미 기호가 오면 그건 표의 한 칸이지 문항 제목이 아니므로
# 부정 전방탐색으로 제외한다. \s* 를 앞으로 소비해버리면 전방탐색이 공백을 못 보고
# 통과해버리므로(백트래킹으로 \s*를 0글자로 줄여버림), 전방탐색 안에서 공백까지 같이
# 검사해야 한다 — 이 순서를 바꾸면 다시 오탐이 난다.
_BLOCK_START_RE = re.compile(r"(?<!\d)(\d{1,2})\.(?!\s*[①②③④⑤])\s*")
# _BLOCK_ANSWER_RE: 문항 블록 안의 "정답①"(공백 유무 불문) 표기.
_BLOCK_ANSWER_RE = re.compile(r"정답\s*([①②③④⑤1-5])")


def normalize_choice(token: str) -> str | None:
    if token in CHOICE_TO_INT:
        return token
    if token.isdigit() and 1 <= int(token) <= 5:
        return CHOICE_SYMBOLS[int(token) - 1]
    return None


def extract_pdf_text(path: Path) -> str:
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def parse_answer_header(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for num_s, sym in _HEADER_RE.findall(text):
        out.setdefault(int(num_s), sym)
    return out


def split_solution_blocks(text: str, n: int) -> dict[int, str]:
    """해설 본문을 문항 번호 경계로 나눈다. 1..n 이 순서대로 나타나는 지점만
    진짜 경계로 인정해 본문 속 우연한 '숫자.' 오탐(소수점 등)을 걸러낸다."""
    positions = [(int(m.group(1)), m.start()) for m in _BLOCK_START_RE.finditer(text)]
    starts: list[tuple[int, int]] = []
    expected = 1
    for num, pos in positions:
        if num == expected and expected <= n:
            starts.append((num, pos))
            expected += 1
    blocks: dict[int, str] = {}
    for i, (num, pos) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(text)
        blocks[num] = text[pos:end]
    return blocks


def pdfplumber_answer_rows(pdf_path: Path) -> str:
    """같은 페이지를 fitz 와는 다른 경로(단어 좌표)로 다시 읽는다.

    fitz.get_text() 는 PDF 내부 텍스트 스트림 순서를 그대로 따르는데, 실측 결과
    이 순서가 흐트러지는 문서가 있었다(2022 수능·2027 6월모평: fitz 축은 20문항 중
    2문항만 잡았다). pdfplumber 는 단어 하나하나의 top/x0 좌표를 주므로 y좌표로
    줄을 다시 묶으면 스트림 순서와 무관하게 표를 재구성할 수 있다 — 실측 결과 두
    문서 모두 이 축에서는 20문항이 온전히 나왔다. "같은 표를 다른 파서로 두 번
    읽는다"는 CONTRACT의 pdfplumber 축 의도가 바로 이 실패 사례에서 나왔다.
    """
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return ""
        words = pdf.pages[0].extract_words(use_text_flow=False)
    words.sort(key=lambda w: (round(w["top"] / 3), w["x0"]))  # 3pt 단위로 같은 줄 묶기
    lines: list[str] = []
    buf: list[str] = []
    cur_top = None
    for w in words:
        top = round(w["top"] / 3)
        if cur_top is not None and top != cur_top:
            lines.append(" ".join(buf))
            buf = []
        buf.append(w["text"])
        cur_top = top
    if buf:
        lines.append(" ".join(buf))
    return "\n".join(lines)


def load_known_standard_codes(standards_dir: Path) -> dict[str, set[str]]:
    """curriculum/standards/<revision>.json 을 개정연도별로 나눠 "code" 필드를 모은다.

    처음엔 파일 하나를 통으로 훑어 전 revision 을 한 집합에 합쳤었다. 그런데
    `standards` 명령이 실제로 만든 curriculum/standards/2022.json 을 붙여 시험해보니
    2015개정 "지구과학Ⅱ"와 2022개정 "지구과학"(전혀 다른 과목)이 우연히 같은
    접두사 "12지구"를 쓴다 — 그래서 2015 분류 코드가 2022 파일 속 무관한 코드와
    문자열만 맞아떨어져 조용히 통과해버리는 오탐을 실제로 재현했다. 그래서
    반드시 파일의 "revision" 필드로 연도를 나눠 담고, item.classification 의 같은
    연도 버킷하고만 대조한다. revision 필드가 없는 예외적인 파일은 파일명(스템)을
    연도로 대신 쓴다 — 스키마가 아직 CONTRACT 에 못박히지 않았으니 관대하게 둔다.
    """
    by_year: dict[str, set[str]] = {}
    if not standards_dir.exists():
        return by_year
    for path in standards_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        year = str(data.get("revision") or path.stem)
        _collect_codes(data, by_year.setdefault(year, set()))
    return by_year


def _collect_codes(node, codes: set[str]) -> None:
    if isinstance(node, dict):
        val = node.get("code")
        if isinstance(val, str):
            codes.add(val)
        for v in node.values():
            _collect_codes(v, codes)
    elif isinstance(node, list):
        for v in node:
            _collect_codes(v, codes)


def _check_image_sane(path: Path, ident: str, note) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        note(ident, f"크롭 이미지 접근 실패: {exc}", "error")
        return
    if size < MIN_IMAGE_BYTES:
        note(ident, f"크롭 이미지가 너무 작다({size} bytes): {path.name}", "error")
        return
    try:
        with Image.open(path) as img:
            w, h = img.size
            if w < MIN_IMAGE_DIM_PX or h < MIN_IMAGE_DIM_PX:
                note(ident, f"크롭 이미지 크기 이상({w}x{h}): {path.name}", "error")
                return
            lo, hi = img.convert("L").getextrema()
            if hi - lo <= BLANK_EXTREMA_THRESHOLD:
                note(ident, f"크롭 이미지가 빈 화면으로 보임(명암차 {hi - lo}): {path.name}", "warn")
    except Exception as exc:  # noqa: BLE001 — 손상 파일은 형식 무관하게 잡아야 한다
        note(ident, f"크롭 이미지를 열 수 없음({exc}): {path.name}", "error")


# ---------------------------------------------------------------------------
# tamgu-1q1block 판형 검사. 현재 유일하게 실동작하는 판형.

# 발문 안의 배점 표기. 값을 숫자로 뽑는다 — extractlib/tamgu.py 의 POINT_MARK_RE 와
# 같은 규칙이다(그쪽은 추출, 이쪽은 검증이라 의존은 만들지 않고 규칙만 맞춘다).
POINT_MARK_RE = re.compile(r"\[\s*(\d)\s*점\s*\]")


def _point_tiers(subject) -> tuple[int, int] | None:
    """이 판형의 배점 계단 (기본 배점, 표기 배점) 을 subject.json 에서 역산한다.

    예전엔 이 자리에 `points not in (2, 3)` 과 `"[3점]" in stem` 이 리터럴로 박혀
    있었다. 2점/3점은 **탐구 영역의 값**이지 모든 과목의 값이 아니다 — 배점 구성이
    다른 과목이 들어오면 멀쩡한 문항 전부가 error 로 쏟아진다. extract 쪽
    (extractlib/tamgu.points_from_marks)은 애초에 이 값을 역산하도록 만들어 두었는데
    validate 만 박아 두면 두 단계가 서로 다른 전제 위에 서게 된다.

    이 판형은 배점 계단이 두 개뿐이고([N점] 표기가 없는 기본 배점과, 표기가 붙는
    한 단계 위) 표기 없는 문항이 다수라 `points_total // question_count` 가 곧
    기본 배점이다. 탐구 20문항 50점 → (2, 3). 45문항 100점이어도 → (2, 3).
    배점 계단이 셋 이상인 판형(수학 2·3·4점)은 이 함수가 아니라 LAYOUT_CHECKERS 에
    자기 몫의 검사기를 달아야 한다 — 그래서 이 함수는 tamgu 검사기 전용이다.
    """
    n, total = subject.question_count, subject.points_total
    if not n or not total:
        return None
    base = total // n
    return base, base + 1


def _check_item_tamgu_1q1block(space: Space, subject, qid: str, item: dict,
                                known_codes: dict[str, set[str]], note) -> None:
    text = item.get("text") or {}
    mode = item.get("extraction_mode")
    stem = text.get("stem") or ""
    choices = text.get("choices") or []
    points = item.get("points")
    tiers = _point_tiers(subject)

    # extraction_mode == vision 은 텍스트 레이어가 없는 회차다(실측: 2025 수능
    # 문제지가 그렇다). text 가 비어 있는 게 정상이라 CONTRACT 4절이 명시한
    # 예외 — 여기서 걸면 그 회차 문항 전부가 오탐이 된다.
    if mode == "vision":
        if stem or choices:
            note(qid, "extraction_mode=vision인데 text가 채워져 있음 — vision 예외를 재확인해야 한다", "info")
    else:
        # 5지선다는 과목이 아니라 국가 시험 형식이라 CHOICE_SYMBOLS 한 곳에서만 센다.
        if len(choices) != len(CHOICE_SYMBOLS):
            note(qid, f"선택지 {len(choices)}개({len(CHOICE_SYMBOLS)}개 아님)", "error")

        # [N점] 표기 ↔ points. CONTRACT가 "가장 강한 자동 검증"이라 부르는 축이라
        # 양방향 다 error 로 둔다(원본 validate_scaffold.py 도 예외 없이 problem 처리).
        # 표기 안의 숫자를 읽는다 — '3' 을 박으면 배점 계단이 다른 과목에서 무너진다.
        mark = POINT_MARK_RE.search(stem)
        if mark:
            marked = int(mark.group(1))
            if points != marked:
                note(qid, f"발문에 [{marked}점]인데 points={points!r}", "error")
        elif tiers and points == tiers[1]:
            note(qid, f"points={points}인데 발문에 [{tiers[1]}점] 표기가 없음", "error")

        # 글리프 손상 잔존 — 발문 + 자료 서술 + 선택지만 본다. 원문 보존 필드가
        # 있다면 그건 무수정 보존이 원칙이라(CONTRACT) 검사 대상에서 뺀다.
        target = stem + " " + (text.get("boxed") or "") + " " + " ".join(choices)
        for pattern, label in GLYPH_SMELLS:
            hit = pattern.search(target)
            if hit:
                note(qid, f"{label} — {hit.group(0)!r}", "warn")

    if tiers is None:
        note(qid, "subject.json 에 question_count/points_total 이 없어 배점 검사를 못 한다", "warn")
    elif points not in tiers:
        note(qid, f"배점 이상({points!r}) — 이 과목은 {tiers[0]}점 또는 {tiers[1]}점만 가능"
                  f" (question_count={subject.question_count}, points_total={subject.points_total})",
             "error")

    # 정답 필드 자체 정합성(기호 ↔ 정수). "없음"/전원정답 처리된 문항은 표준
    # 기호가 아예 없을 수 있다(실측: 2023 6월모평 14번 — 평가원이 전원정답
    # 처리한 문항의 해설 PDF 정답표에 기호 대신 "없음"이라 찍혀 있었다).
    # 이런 경우 error 로 깨는 대신 warn 으로 사람에게 넘긴다.
    answer = item.get("answer")
    symbol = item.get("answer_symbol")
    if symbol not in CHOICE_TO_INT:
        note(qid, f"answer_symbol이 표준 기호가 아님({symbol!r}) — 복수정답/전원정답 처리 문항일 수 있다", "warn")
    elif answer != CHOICE_TO_INT[symbol]:
        note(qid, f"answer={answer!r} 와 answer_symbol={symbol!r} 이 서로 다른 값을 가리킴", "error")

    # materials: 선언 ↔ 실제 파일. "본문 링크" 축은 CONTRACT 4절 items 스키마에
    # text 가 마크다운이 아닌 순수 문자열이라 이미지 링크 자체가 없다 — build 가
    # HTML을 만들 때(CONTRACT 6절)에야 링크가 생기므로 그쪽에서 재검증해야 하는
    # 부분이다. 지금은 대체로 "자료가 있으면 자료 서술(text.boxed)도 있어야
    # 한다"를 본다(validate_cards.py 의 원래 7번 검사와 동일한 취지).
    declared = item.get("materials") or []
    declared_norm = sorted(Path(d).as_posix() for d in declared)
    actual = sorted(space.rel(p) for p in space.materials.glob(f"{qid}_m*.png"))
    if declared_norm != actual:
        note(qid, f"materials 선언 {declared_norm} != 실제 파일 {actual}", "error")
    for d in declared:
        if not (space.root / d).exists():
            note(qid, f"materials에 선언된 파일이 실제로 없음: {d}", "error")
    if actual and mode != "vision" and not (text.get("boxed") or "").strip():
        note(qid, f"자료 이미지 {len(actual)}장인데 자료 서술(text.boxed)이 비어 있음", "warn")

    # 크롭 이미지 존재 + 최소 크기(빈 이미지·너무 작은 이미지 탐지)
    qpng = space.question_png(qid)
    if not qpng.exists():
        note(qid, f"문항 크롭 이미지 없음: {space.rel(qpng)}", "error")
    else:
        _check_image_sane(qpng, qid, note)
    for i, d in enumerate(declared, start=1):
        p = space.root / d
        if p.exists():
            _check_image_sane(p, f"{qid}_m{i}", note)

    # classification: subject.json 접두사 대조 + (있으면) curriculum/standards/ 실재 대조
    classification = item.get("classification") or {}
    for year, prefixes in (subject.standard_prefixes or {}).items():
        applies = (subject.curriculum or {}).get(year)
        if not applies:
            continue  # 이 과목엔 해당 교육과정이 없음(예: earth-science-ii의 2022: null) — 정상
        entry = classification.get(year) or {}
        code = entry.get("standard")
        if not code:
            note(qid, f"{year} 성취기준 분류가 없음(classify 큐 대기 중일 수 있음)", "warn")
            continue
        if prefixes and not any(code.startswith(p) for p in prefixes):
            note(qid, f"{year} 성취기준 {code!r} 가 subject.json 접두사 {prefixes} 밖", "error")
        # 이 연도 revision 파일이 아예 없으면 "모른다"이지 "틀렸다"가 아니다 — 조용히 넘어간다.
        # (다른 연도 revision 파일에 우연히 같은 코드가 있어도 그건 대조하지 않는다 — 위 함수 설명 참고)
        year_codes = known_codes.get(year)
        if year_codes and code not in year_codes:
            note(qid, f"{year} 성취기준 {code!r} 가 curriculum/standards/{year}.json 에 존재하지 않음", "error")


def recorded_answer_check(item: dict) -> dict | None:
    """extract 가 남긴 3중 대조 기록을 찾는다. **두 자리를 다 본다.**

    계약 밖 확장 필드는 item["ext"] 아래로 모으기로 정리했지만(ext.answer_check),
    이미 디스크에 있는 items 는 최상위 answer_check 를 갖고 있다. extract 를 다시
    돌리지 않고도 검증이 되어야 하므로 옛 자리도 계속 읽는다. 새 자리가 우선이다 —
    한 문항에 둘 다 있으면 최신은 언제나 ext 쪽이다.
    """
    for candidate in ((item.get("ext") or {}).get("answer_check"), item.get("answer_check")):
        if isinstance(candidate, dict) and "severity" in candidate:
            return candidate
    return None


def apply_recorded_answer_check(qid: str, item: dict, note) -> bool:
    """extract 가 이미 남긴 3중 대조 기록을 CONTRACT 8-3 규칙대로 옮겨 적는다.

    CONTRACT 6절 표에 3중 대조는 애초에 `extract` 의 책임("본문·정답·배점 추출
    (3중 대조)")으로 적혀 있다. 실제로 scripts/extractlib/answers.py 의 cross_check()
    가 정답지·해설지·pdfplumber 3축을 이미 읽어 두고 {agree, disagree, severity}
    로 item.json 에 남긴다(실측: workspace/earth-science-ii 2021_수능 20문항 확인).
    extract 쪽 리더가 CID 손상·OCR 폴백까지 갖춘 더 정교한 버전이라 validate 가
    PDF를 다시 여는 것보다 이 기록을 그대로 믿는 편이 낫다 — 그래서 이 필드가 있는
    문항은 이 함수로 끝내고, 없는 문항만 아래 cross_check_answers() 로 넘긴다
    (필드가 생기기 전 옛 데이터, 또는 이 필드를 안 쓰는 다른 extract 구현 대비).
    반환값은 "이 문항을 처리했는가"(False 면 폴백으로 넘겨야 한다).
    """
    ac = recorded_answer_check(item)
    if ac is None:
        return False
    severity = ac.get("severity")
    disagree = ac.get("disagree") or {}
    agree = ac.get("agree") or []
    if severity == "error":
        detail = ", ".join(f"{k}={v}" for k, v in sorted(disagree.items())) or "표결 소스가 없음"
        note(qid, f"정답 3중 대조 전원 불일치(extract 기록): {detail}", "error")
    elif severity == "warn":
        detail = ", ".join(f"{k}={v}" for k, v in sorted(disagree.items()))
        note(qid, f"정답 3중 대조 불일치(extract 기록, 일치축={agree}): {detail} — 두 축 이상 일치해 통과 처리", "warn")
    # severity == "ok" 는 조용히 통과.
    return True


def cross_check_answers(space: Space, exam_id: str, subject, rows: list[tuple[int, str, dict]],
                         note) -> None:
    """정답 3중 대조를 validate 스스로 재현한다(item에 answer_check 기록이 없을 때만 호출됨).

    CONTRACT 8절 3번 — 두 축만 일치해도 통과(warn), 전원 불일치면 error.

    실측(19회차): '정답' 원본이 표 형태 텍스트 PDF가 아니라 스크린샷(.png)인 회차가
    17/19 이었다. 그래서 "정답지 표 파싱"의 실질 소스는 해설 PDF 머리말에 함께
    인쇄된 요약표다(정답 PDF가 진짜 텍스트 PDF면 그쪽을 우선한다). 여기에
    "pdfplumber 표"(같은 머리말을 좌표 기반으로 재구성, 위 pdfplumber_answer_rows
    참조)와 "해설지"(본문 블록별 '정답①' 재확인)를 더해 3축을 만든다.
    """
    src_dir = space.source_dir(exam_id)
    answer_pdf = src_dir / "answer.pdf"
    solution_pdf = src_dir / "solution.pdf"
    header_src = answer_pdf if answer_pdf.exists() else (solution_pdf if solution_pdf.exists() else None)

    axis_header: dict[int, str] = {}
    axis_pw: dict[int, str] = {}
    axis_block: dict[int, str] = {}

    if header_src is not None:
        try:
            axis_header = parse_answer_header(extract_pdf_text(header_src)[:3000])
        except Exception as exc:  # noqa: BLE001
            note(exam_id, f"정답지 표 파싱 실패({header_src.name}): {exc}", "info")
        try:
            axis_pw = parse_answer_header(pdfplumber_answer_rows(header_src)[:3000])
        except Exception as exc:  # noqa: BLE001
            note(exam_id, f"pdfplumber 표 파싱 실패({header_src.name}): {exc}", "info")

    if solution_pdf.exists():
        try:
            blocks = split_solution_blocks(extract_pdf_text(solution_pdf), subject.question_count)
            for number, block in blocks.items():
                m = _BLOCK_ANSWER_RE.search(block)
                if m:
                    sym = normalize_choice(m.group(1))
                    if sym:
                        axis_block[number] = sym
        except Exception as exc:  # noqa: BLE001
            note(exam_id, f"해설지 파싱 실패: {exc}", "info")
    elif header_src is None:
        note(exam_id, "정답/해설 원본을 찾을 수 없음 — 3중 대조 생략", "info")
        return

    for number, qid, item in rows:
        item_symbol = item.get("answer_symbol")
        votes: dict[str, str] = {}
        if item_symbol in CHOICE_TO_INT:
            votes["item"] = item_symbol
        if number in axis_header:
            votes["정답지표"] = axis_header[number]
        if number in axis_pw:
            votes["pdfplumber표"] = axis_pw[number]
        if number in axis_block:
            votes["해설지"] = axis_block[number]

        if len(votes) < 2:
            continue  # 대조할 소스가 부족 — 판정 보류(실데이터에서 흔함, 조용히 넘어간다)

        top_value, top_count = Counter(votes.values()).most_common(1)[0]
        if top_count == len(votes):
            continue  # 전원 일치
        detail = ", ".join(f"{k}={v}" for k, v in sorted(votes.items()))
        if top_count >= 2:
            note(qid, f"정답 3중 대조 불일치(다수={top_value}): {detail} — 두 축 이상 일치해 통과 처리", "warn")
        else:
            note(qid, f"정답 3중 대조 전원 불일치: {detail}", "error")


def _check_item_passage_group(space, subject, qid, item, known_codes, note) -> None:
    raise NotImplementedError(
        "passage-group 판형(국어·영어, 지문 하나에 문항 여러 개)은 아직 validate가 모른다. "
        "지문 하나를 공유하는 문항들을 무엇으로 묶어 검증할지(지문 id? 문항 범위?)부터 "
        "정의해야 한다. subjects/_template/README.md, docs/LAYOUTS.md 참조."
    )


def _check_item_math_mixed(space, subject, qid, item, known_codes, note) -> None:
    raise NotImplementedError(
        "math-mixed 판형(객관식+단답형 혼합)은 아직 validate가 모른다. "
        "단답형 문항은 '선택지 5개' 불변식이 애초에 성립하지 않으므로 문항 타입별 "
        "분기(item.text.choices 유무 등)를 먼저 스키마에 넣어야 한다."
    )


LAYOUT_CHECKERS = {
    "tamgu-1q1block": _check_item_tamgu_1q1block,
    "passage-group": _check_item_passage_group,
    "math-mixed": _check_item_math_mixed,
}
# tamgu-1q1block 만 3중 대조 로직이 있다. 다른 판형은 정답 표기 형식 자체가
# 다를 수 있어(단답형은 숫자, 지문형은 문항마다 다른 위치) 여기서 같이 막는다.
CROSS_CHECKERS = {
    "tamgu-1q1block": cross_check_answers,
}


def register(parser) -> None:
    parser.add_argument("--subject", required=True, help="검사할 과목 slug")
    parser.add_argument("--strict", action="store_true", help="warn도 실패(exit 1)로 친다")
    parser.add_argument("--only", help="qid 또는 exam_id 를 쉼표로(예: 2024_수능,2023_9월모평_07) — "
                                        "회차 단위 불변식(1·2번)은 생략됨")
    parser.add_argument("--quiet", action="store_true", help="stdout 요약 없이 리포트 파일만 남긴다")
    parser.add_argument("--workspace", help="작업 공간 경로 직접 지정 (기본 workspace/<slug>)")


def run(args) -> int:
    # 과목 정의를 못 읽으면 리포트를 남길 workspace 경로조차 정할 수 없다.
    # gw.py 는 예외를 그대로 흘려보내서 LLM 이 리포트 대신 traceback 을 읽게 된다
    # (통합 검증에서 7개 명령 중 5개가 그랬다). 형제 모듈(build/classify)과 같은
    # 한 줄 안내로 끝낸다 — load_subject 의 메시지가 등록된 과목 목록까지 담고 있다.
    try:
        subject = load_subject(args.subject)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] validate: {exc}")
        return 1
    # --workspace 는 7개 명령 공통 옵션이다(crop 에만 있었다). 격리 실행이 안 되면
    # 여러 실행이 공유 workspace/<slug> 를 서로 밟는다.
    space = Space(args.subject, getattr(args, "workspace", None))
    report = Report("validate", args.subject, space)
    warn_count = 0

    def note(ident: str, why: str, severity: str = "warn") -> None:
        nonlocal warn_count
        report.note(ident, why, severity)
        if severity == "warn":
            warn_count += 1

    def finish(ok: bool | None = None) -> int:
        if args.quiet:
            report.write(ok=ok)
            data_ok = (not report.has_error) if ok is None else ok
            return 0 if data_ok else 1
        return report.finish(ok=ok)

    # --only 는 형제 명령(crop/extract)과 같은 문법이다 — qid 든 exam_id 든
    # 섞어서 콤마로 받는다("2024_수능,2023_9월모평_07"). qid 인지 exam_id 인지는
    # split_qid 로 구분한다(qid 만 번호가 붙는다).
    only_qids: set[str] | None = None
    only_exams: set[str] | None = None
    if args.only:
        only_qids, only_exams = set(), set()
        for token in (t.strip() for t in args.only.split(",") if t.strip()):
            try:
                split_qid(token)
                only_qids.add(token)
            except ValueError:
                only_exams.add(token)

    item_paths = list(space.iter_items())
    if only_qids is not None:
        item_paths = [p for p in item_paths
                      if p.stem in only_qids or split_qid(p.stem)[0] in only_exams]

    if not item_paths:
        report.count(items=0)
        note(args.subject, "items/ 에 문항이 없다 — extract 를 먼저 돌려야 한다", "error")
        report.next = f"python scripts/gw.py extract --subject {args.subject}"
        return finish(ok=False)

    items: dict[str, dict] = {}
    for p in item_paths:
        try:
            items[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            note(p.stem, f"items json 파싱 실패: {exc}", "error")
    report.count(items=len(items))

    known_codes = load_known_standard_codes(CURRICULUM_STANDARDS)
    needed_years = [y for y, applies in (subject.curriculum or {}).items()
                     if applies and (subject.standard_prefixes or {}).get(y)]
    missing_years = [y for y in needed_years if not known_codes.get(y)]
    if missing_years:
        note(args.subject,
             f"curriculum/standards/ 에 {', '.join(missing_years)} revision 데이터가 없음 "
             "— 그 연도 성취기준 실재 대조는 생략하고 접두사 검사만 수행",
             "info")

    exams: dict[str, list[tuple[int, str, dict]]] = defaultdict(list)
    for qid, item in items.items():
        try:
            exam_id, number = split_qid(qid)
        except ValueError as exc:
            note(qid, f"qid 형식 이상: {exc}", "error")
            continue
        exams[exam_id].append((number, qid, item))

    checker = LAYOUT_CHECKERS.get(subject.layout)
    cross_checker = CROSS_CHECKERS.get(subject.layout)
    verified = scaffold = vision = 0

    try:
        for exam_id in sorted(exams):
            rows = sorted(exams[exam_id])

            # 회차 단위 불변식(1·2번). --only 로 부분집합만 볼 때는 카운트가
            # 원래부터 안 맞으므로 의미가 없어 생략한다.
            if not only_qids:
                points_sum = sum((it.get("points") or 0) for _, _, it in rows)
                if points_sum != subject.points_total:
                    note(exam_id, f"배점 합 {points_sum} != subject.points_total {subject.points_total}", "error")
                if len(rows) != subject.question_count:
                    note(exam_id, f"문항 수 {len(rows)} != subject.question_count {subject.question_count}", "error")

            # 정답 3중 대조. extract 가 이미 answer_check 를 남긴 문항은 그 기록을
            # 쓰고, 없는 문항만 validate 가 직접 PDF 를 재파싱한다(둘 다 위 함수
            # 설명 참고). --only 로 부분집합만 보더라도 대조 자체는 의미가 있어
            # 아래는 회차 단위 불변식과 달리 생략하지 않는다.
            needs_fallback = [(n, q, it) for n, q, it in rows
                              if not apply_recorded_answer_check(q, it, note)]
            if needs_fallback and cross_checker is not None:
                cross_checker(space, exam_id, subject, needs_fallback, note)

            for number, qid, item in rows:
                if item.get("status") == "verified":
                    verified += 1
                else:
                    scaffold += 1
                if item.get("extraction_mode") == "vision":
                    vision += 1
                if checker is not None:
                    checker(space, subject, qid, item, known_codes, note)
    except NotImplementedError as exc:
        note(subject.slug, str(exc), "error")
        report.next = "docs/LAYOUTS.md 를 보고 이 판형의 검증 로직부터 설계한다"
        return finish(ok=False)

    report.count(verified=verified, scaffold=scaffold, vision=vision)
    report.artifact(space.rel(space.report("validate")))

    ok = (not report.has_error) and not (args.strict and warn_count > 0)
    report.next = (f"python scripts/gw.py build --subject {args.subject}" if ok
                   else f"attention 을 고친 뒤 python scripts/gw.py validate --subject {args.subject} 로 재검증")
    return finish(ok=ok)
