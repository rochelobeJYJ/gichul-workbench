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

# 첫가끝 자모 → 호환 자모. NFKC 가 이 방향으로는 정규화해 주지 않는다.
JAMO_REPLACEMENTS = {
    "ᄀ": "ㄱ", "ᄂ": "ㄴ", "ᄃ": "ㄷ", "ᄅ": "ㄹ", "ᄆ": "ㅁ",
    "ᄇ": "ㅂ", "ᄉ": "ㅅ", "ᄋ": "ㅇ",
}

CHAR_REPLACEMENTS = {**PUA_REPLACEMENTS, **JAMO_REPLACEMENTS}


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
