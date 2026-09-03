# -*- coding: utf-8 -*-
"""글자 수준 정규화.

여기서 하는 일은 전부 "실제로 당해봤기 때문에" 있는 것들이다.

1. NFKC — 로마숫자 Ⅱ(U+2161)가 그대로 남으면 과목 별칭 대조가 실패한다.
   NFKC 는 원문자 ①→1, 괄호한글 ㉠→ㄱ 도 함께 펴 준다. 이건 부작용이 아니라
   **의도한 것**이다. 정답 기호와 보기 라벨이 회차마다 다른 코드포인트로 들어오는데,
   전부 평문으로 눕혀 놓고 한 가지 형태만 파싱하는 편이 훨씬 안전하다.
   대신 선택지 심벌은 이 단계 이후로는 '①' 이 아니라 '1' 로 만나게 된다 —
   파서가 둘 다 받도록 되어 있는 이유다.
2. 사설 PUA 글리프 — 일부 회차의 PDF 는 숫자·연산기호를 U+E0xx 로 넣는다.
   매핑하지 않으면 배점/정답 숫자가 통째로 사라진다.
3. 첫가끝 자모(ᄀ U+1100)를 완성형 호환 자모(ㄱ)로 —
   해설·문제지의 <보기> 라벨이 이 형태로 들어오는 회차가 있다.
"""
from __future__ import annotations

import re
import unicodedata

CHOICE_TO_INT = {
    "①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5,
    "⓵": 1, "⓶": 2, "⓷": 3, "⓸": 4, "⓹": 5,
}
INT_TO_CHOICE = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤"}
CHOICE_SYMBOLS = "①②③④⑤"

# '정답 없음'(출제 오류로 공고된 문항)의 내부 표현. 정수 0 / 빈 기호.
# 검증된 기존 코퍼스(wiki_earth2)가 쓰는 표기라 그대로 맞춘다.
ANSWER_NONE = 0
ANSWER_NONE_SYMBOL = ""

# 사설 영역(Private Use Area) 글리프 매핑. 오름차순으로 0~9 가 배치된 폰트를 쓴 회차가 있다.
PUA_REPLACEMENTS = {
    "\ue034": "1", "\ue035": "2", "\ue036": "3", "\ue037": "4", "\ue038": "5",
    "\ue039": "6", "\ue03a": "7", "\ue03b": "8", "\ue03c": "9", "\ue03d": "0",
    "\ue046": "-", "\ue053": "+", "\ue047": "=", "\ue04f": ":", "\ue06d": "/",
}

# ── 수식 폰트(HyhwpEQ · HancomEQN)의 사설 영역 배치 ────────────────────────
# 문제지·해설지의 수식은 본문 폰트가 아니라 한글 워드프로세서의 수식 전용 폰트로
# 조판된다. 그 글자들은 표준 코드포인트가 없어 ToUnicode 가 U+E0xx 로 돌려주고,
# 위 숫자표에 없는 것은 normalize_text 가 **조용히 지운다**. 지구과학에서는 수식이
# 적어(2024 수능 문제지 미매핑 18자) 눈에 띄지 않았지만 화학·물리는 다르다 —
# 화학Ⅰ 2024 수능 문제지는 PUA 582자 중 199자가 미매핑이었고, 그 결과
#   'Al(s)' → 'Al()',  'x/y' → '/',  'Y>Z' → 'YZ',  't₂' → '2'
# 처럼 **문장이 멀쩡해 보이면서 알맹이만 빠진** 전사가 items 에 들어갔다.
#
# 아래 네 블록은 알파벳 순으로 연속 배열돼 있다. 실제 문제지에서 글리프를 렌더해
# 눈으로 읽어 확인한 자리는
#   대문자 A(E000) B(E001) E(E004) G(E006) K(E00A) M(E00C) N(E00D) P(E00F)
#          S(E012) V(E015)                                     … 10자
#   소문자 a(E0E5)~z(E0FE) 중 f·j·o·u 를 뺀                     … 22자
#   그리스 Δ(E088) Ω(E09C) / δ(E0A0) θ(E0A4) π(E0AC) ρ(E0AD) φ(E0B1) … 7자
# 이고, 전부 '블록 시작 + 알파벳 순서' 와 정확히 맞았다(어긋난 것 0). 두 폰트
# (HyhwpEQ·HancomEQN)가 같은 코드포인트에서 같은 글자를 냈고 2024·2025 두 학년도,
# 화학·생명과학·지구과학 문서가 모두 일치했다.
# **확인하지 못한 칸(f·j·o·u 와 관찰되지 않은 대문자·그리스 문자)은 연속성으로
# 채운 추정이다.** 어긋난 것이 나오면 그 칸만 예외로 빼면 된다.
_GREEK_UPPER = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
_GREEK_LOWER = "αβγδεζηθικλμνξοπρστυφχψω"
_ASCII_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"


def _eq_block(start: int, letters: str) -> dict[str, str]:
    """사설 영역 연속 블록 하나를 매핑표로 편다."""
    return {chr(start + index): letter for index, letter in enumerate(letters)}


EQFONT_REPLACEMENTS = {
    **_eq_block(0xE000, _ASCII_UPPER),
    **_eq_block(0xE085, _GREEK_UPPER),
    **_eq_block(0xE09D, _GREEK_LOWER),
    **_eq_block(0xE0E5, _ASCII_LOWER),
    # 괄호·연산기호. 전부 문맥에서 확인했다 —
    #   '(s)는' 'H(aq)'(E044/E045), 'X⁺'(E048), '|pH-pOH|은'(E04D/E101),
    #   '(g/mL)'(E054), '노도<1'(E055), 'Y>Z'(E056), '√10'(E05C).
    # E054 와 이미 있던 E06D 가 둘 다 '/' 인 것은 크기 변형이 따로 배정돼 있어서다.
    "\ue044": "(", "\ue045": ")", "\ue048": "+", "\ue04d": "|",
    "\ue054": "/", "\ue055": "<", "\ue056": ">", "\ue05c": "√",
    "\ue101": "|",
    # 블록 밖에 따로 있는 넷. 전부 지구과학Ⅱ 19회차 해설·문제지에서 남아 있던
    # 미매핑 글자를 문맥으로 확인한 것이다 —
    #   'E,P,S' 't1,t2,t3'(E052),  '직선 l을 그린다'(E0BB),
    #   '2vΩsinφ의 관계가 성립'(E0C2 — φ 의 다른 자형이다. 표준형 φ 는 E0B1),
    #   'sin60°' 'cos60°' '(θ′-θ)≥0°'(E0C8).
    # E0C8 이 지워지던 것이 GLYPH_SMELLS 의 '각도 기호 누락(90N 꼴)' 이 잡으려던
    # 손상의 원인 가운데 하나다 — 이제 도(°)가 지워지지 않는다.
    "\ue052": ",", "\ue0bb": "l", "\ue0c2": "φ", "\ue0c8": "°",
}

# 첫가끝 자모 → 호환 자모. NFKC 가 이 방향으로는 정규화해 주지 않는다.
JAMO_REPLACEMENTS = {
    "ᄀ": "ㄱ", "ᄂ": "ㄴ", "ᄃ": "ㄷ", "ᄅ": "ㄹ", "ᄆ": "ㅁ",
    "ᄇ": "ㅂ", "ᄉ": "ㅅ", "ᄋ": "ㅇ",
}

CHAR_REPLACEMENTS = {**PUA_REPLACEMENTS, **EQFONT_REPLACEMENTS, **JAMO_REPLACEMENTS}

PUA_RANGE = (0xE000, 0xF8FF)


def unmapped_pua(text: str) -> dict[str, int]:
    """매핑표에 없어서 **지워질** 사설 영역 글자와 그 개수.

    normalize_text 가 이것들을 조용히 버리기 때문에, 새 과목·새 판형에서 처음 보는
    수식 폰트를 만났을 때 리포트에 드러낼 수 있도록 원문(정규화 전)에서 세어 둔다.
    """
    counts: dict[str, int] = {}
    low, high = PUA_RANGE
    for char in text or "":
        if low <= ord(char) <= high and char not in CHAR_REPLACEMENTS:
            counts[char] = counts.get(char, 0) + 1
    return counts


def normalize_text(text: str) -> str:
    """PDF 원문 → 파서가 믿을 수 있는 텍스트."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\xa0", " ")
    for source, target in CHAR_REPLACEMENTS.items():
        text = text.replace(source, target)

    # 제어문자와 매핑되지 않은 PUA 는 버린다. 남겨두면 정규식이 단어 경계를
    # 엉뚱하게 잡아 '정답3' 같은 토큰이 통째로 안 잡힌다.
    cleaned: list[str] = []
    for char in text:
        code = ord(char)
        if char in ("\n", "\t"):
            cleaned.append(char)
            continue
        if code < 32 or 0x80 <= code <= 0x9F:
            continue
        if 0xE000 <= code <= 0xF8FF:
            continue
        cleaned.append(char)
    text = "".join(cleaned)

    text = re.sub(r"<\s*보\s*기\s*>", "<보기>", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact(text: str) -> str:
    """줄바꿈까지 공백 하나로 눕힌다."""
    return re.sub(r"\s+", " ", text or "").strip()


def squash(text: str) -> str:
    """공백을 전부 제거. 과목명 대조처럼 띄어쓰기가 회차마다 다른 곳에 쓴다."""
    return re.sub(r"\s+", "", text or "")


# 가운뎃점은 **코드포인트가 한 종류가 아니다.** 같은 '사회·문화' 를 회차마다 다른
# 글자로 찍는다(실측):
#   ·  U+00B7   subject.json 의 별칭이 쓰는 표준형
#   ･  U+FF65   2024·2025 수능 사회·문화 문제지 머리글 (NFKC 로 ・ U+30FB 가 된다)
#   ․  U+2024   2024학년도 수능 사회탐구 정답표 '( 사회․문화 ) 과목'
#               — NFKC 가 이것을 마침표 '.' 로 눕힌다
# 마지막 것 때문에 정답표에서 과목 구간을 못 찾아 정답 3중 대조가 2축으로 주저앉았다
# (2024 수능 사회·문화). 과목명 대조에서는 구분자를 전부 지우고 비교한다.
NAME_SEPARATORS = re.compile(r"[\s.,·∙•・･‧⋅‐‑–—\-_()\[\]（）]")


def fold_name(text: str) -> str:
    """과목명 대조 전용 정규화 — NFKC 로 눕히고 구분자를 지우고 대소문자를 눕힌다.

    **NFKC 를 먼저 하는 것이 핵심이다.** 대조의 한쪽(문제지 텍스트)은 normalize_text 가
    이미 NFKC 를 거쳤는데 다른 한쪽(subject.json 의 label·aliases)은 원문 그대로다.
    casefold 를 먼저 하면 로마숫자 'Ⅰ'(U+2160) 이 'ⅰ'(U+2170) 로 눕고 문제지 쪽은
    NFKC 로 이미 'I'→'i' 라서 **둘이 영영 안 만난다.**

    실측(물리학Ⅰ 2024 수능): 쪽 꼬리글 '과학탐구영역(물리학I)' 이 이 불일치 때문에
    걸러지지 않고 6번 문항 블록 끝에 남았다. 이름에 로마숫자가 있는 과목(물리학Ⅰ·
    화학Ⅰ·지구과학Ⅱ …)이 전부 해당한다. PITFALLS 3-5 가 "과목명 비교 전에 반드시
    정규화한다"고 적어 둔 바로 그 사고다.
    """
    folded = unicodedata.normalize("NFKC", text or "")
    return NAME_SEPARATORS.sub("", folded).casefold()


def hangul_ratio(text: str) -> float:
    """한글 비율. 글리프가 깨진(CID 손상) PDF 를 골라내는 유일하게 쓸만한 지표다.

    2022학년도 수능 해설 PDF 가 그렇다 — 텍스트 레이어는 있는데 한글이 전부
    깨진 사설 코드로 나온다. 길이만 보면 정상으로 보이므로 길이로는 못 잡는다.
    """
    non_space = len(re.findall(r"\S", text or ""))
    if non_space == 0:
        return 0.0
    return len(re.findall(r"[가-힣]", text)) / non_space


def answer_to_int(value) -> int | None:
    """'④' / '4' / 4 → 4. 아니면 None."""
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 5 else None
    token = str(value).strip()
    if token in CHOICE_TO_INT:
        return CHOICE_TO_INT[token]
    token = unicodedata.normalize("NFKC", token)
    if re.fullmatch(r"[1-5]", token):
        return int(token)
    return None


def answer_to_symbol(value) -> str | None:
    """정답을 items 스키마의 answer_symbol 표기(원문자)로."""
    if value == ANSWER_NONE:
        return ANSWER_NONE_SYMBOL
    number = answer_to_int(value)
    return INT_TO_CHOICE[number] if number else None
