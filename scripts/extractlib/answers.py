# -*- coding: utf-8 -*-
"""정답·배점 추출과 3중 대조.

이 모듈이 이 명령의 존재 이유다. 정답 하나가 틀리면 문항집 전체의 신뢰가 무너지는데,
PDF 판형은 회차마다 조용히 바뀐다. 그래서 **서로 실패 지점이 다른 세 축**으로 읽고
대조한다.

  answer_sheet  정답지(정답표) — fitz 텍스트 레이어. 배점까지 들어 있는 유일한 축.
                텍스트를 못 믿으면 원문자 ①~⑤ **픽셀 템플릿 대조**로 넘어간다.
                정답지가 스캔 이미지인 회차(사회탐구 전반, 2010년대 초반)에서
                이 축을 되살리는 것이 그 대조다 — 아래 '원문자 템플릿' 절 참조.
  solution      해설지 — fitz 텍스트 레이어. 첫머리 정답표 → 실패 시 블록별 '정답N'.
  pdfplumber    같은 파일을 pdfplumber 로. 괘선 표는 extract_tables,
                다단 텍스트는 layout 모드. **fitz 와 실패 지점이 다르다는 것이 핵심이다.**

세 축이 모두 일치하면 확정, 두 축만 일치하면 확정하되 warn, 다 갈리면 error 로 남기고
answer 를 null 로 둔다. 추측해서 채우지 않는다.

--- 겪은 사고들 (지우지 말 것) ---
* 인라인 정답표 정규식에 \\s* 를 쓰면 줄바꿈을 건너뛰어 '20.' 과 다음 줄 첫 숫자를
  묶어 버린다. 반드시 [ \\t]* 로 제한한다.
* 불완전한 인라인 결과가 완전한 칼럼 결과를 덮어쓰면 안 된다. 인라인 매칭이 20개를
  못 채웠다는 것은 애초에 그 판형이 아니라는 뜻이므로, 칼럼 결과가 완전하면 그쪽을 쓴다.
* 정답이 '없음'(전항 정답)인 출제 오류 문항이 실제로 있다. 파이프라인이 죽으면 안 된다.
"""
from __future__ import annotations

import base64
import re
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path

from .textnorm import (ANSWER_NONE, CHOICE_SYMBOLS, answer_to_int, compact,
                       squash)

# 번호와 정답이 같은 줄에 있는 판형: '01. 4  02. 5 ...', '01.5  02.1 ...'
# \s* 가 아니라 [ \t]* 인 이유는 위 주석 참조. 줄바꿈을 절대 넘지 않는다.
INLINE_ANSWER_RE = re.compile(r"(?<!\d)(0[1-9]|[1-9][0-9])[ \t]*\.[ \t]*([1-5])(?!\d)")
INLINE_INVALID_RE = re.compile(
    r"(?<!\d)(0[1-9]|[1-9][0-9])[ \t]*\.[ \t]*(없음|전항[ \t]*정답)")

# 해설 블록 안의 '정답3' / '정답 ③' 표기.
SOLUTION_MARKER_RE = re.compile(rf"정답\s*([{CHOICE_SYMBOLS}1-5])")

# 정답표 PDF 의 과목 구분 머리글: '( 지구과학II ) 과목'
SUBJECT_SECTION_RE = re.compile(r"\(\s*([^)\n]{1,30}?)\s*\)\s*과목")

INVALID_TOKENS = {"없음", "전항정답"}


@dataclass
class Reading:
    """한 축이 읽어낸 결과."""

    source: str                                  # 축 이름
    origin: str = ""                             # 어떤 파일·전략에서 나왔는지 (리포트용)
    answers: dict[int, int] = field(default_factory=dict)
    points: dict[int, int] = field(default_factory=dict)
    reason: str = ""                             # 비어 있을 때 왜 비었는지

    def __bool__(self) -> bool:
        return bool(self.answers)


# --------------------------------------------------------------------------
# 정답표 판형 2종
# --------------------------------------------------------------------------

def parse_inline_table(head: str, count: int) -> dict[int, int]:
    """'01. 4 02. 5 …' 처럼 번호와 답이 같은 줄에 있는 판형."""
    found: dict[int, int] = {}
    for match in INLINE_ANSWER_RE.finditer(head):
        number = int(match.group(1))
        if 1 <= number <= count and number not in found:
            found[number] = int(match.group(2))
    for match in INLINE_INVALID_RE.finditer(head):
        number = int(match.group(1))
        if 1 <= number <= count:
            found[number] = ANSWER_NONE
    return found


def parse_columnar_table(head: str, count: int) -> dict[int, int]:
    """번호 열과 정답 열이 따로 떨어져 추출되는 판형.

    '01.' '02.' … '10.' 이 먼저 나오고 그 뒤에 '3' '5' '3' … 이 이어진다.
    fitz 가 세로 텍스트 블록을 순서대로 토해낼 때 이렇게 된다.
    """
    found: dict[int, int] = {}
    numbers: list[int] = []
    digits: list[int] = []

    def flush() -> None:
        for number, digit in zip(numbers, digits):
            found.setdefault(number, digit)
        numbers.clear()
        digits.clear()

    for raw_line in head.splitlines():
        line = raw_line.strip()
        if re.fullmatch(r"(0?[1-9]|[1-9][0-9])\.", line):
            if digits:
                flush()
            numbers.append(int(line[:-1]))
        elif re.fullmatch(r"[1-5]", line) and numbers:
            digits.append(int(line))
        elif squash(line) in INVALID_TOKENS and numbers:
            digits.append(ANSWER_NONE)
    flush()
    return {n: a for n, a in found.items() if 1 <= n <= count}


def parse_paired_number_table(head: str, count: int) -> dict[int, int]:
    """번호와 정답이 마침표 없이 **한 줄씩 번갈아** 나오는 판형.

    위 두 파서는 지구과학Ⅰ·Ⅱ 회차에서만 검증된 것이었고, 사회탐구 실데이터에서
    구멍이 드러났다. 실측(korean-geography 2025_고3_3월학평, EBSi 해설지 1쪽 원문):

        '1\\n한국지리정답\\n※ 본 전국연합학력평가는 …\\n1\\n①\\n2\\n⑤\\n…\\n20\\n②\\n해 설\\n'

    번호에 마침표가 없다(`1` 이지 `01.` 이 아니다). parse_columnar_table 은 `\\d+\\.` 을
    요구하므로 이 표를 **한 칸도** 못 읽고, parse_inline_table 은 같은 줄을 요구하므로
    역시 못 읽는다. 그 결과 정답 세 축이 전부 죽어 20문항 전원 `answer=null` 이 된다
    (`gw extract --subject korean-geography` 로 실제 재현했다).

    ── 원문자는 여기까지 오지 않는다 ──
    textnorm.normalize_text 의 NFKC 가 ①→1 로 눕히므로, 이 단계에서 표는
    `['1','1','2','5','3','3', …]` 처럼 **번호도 정답도 맨 숫자**다. 즉 스트림만 보면
    어디가 번호이고 어디가 정답인지 알 방법이 없다.

    ── 그래서 번호열이 1,2,3,…,count 로 정확히 이어지는 구간만 인정한다 ──
    이것이 이 파서의 안전장치 전부다. 앞의 쪽번호('1')처럼 잡음이 한 칸 끼어 있으면
    시작 위치를 한 칸씩 밀며 다시 맞춰 본다. 부분 결과는 돌려주지 않는다 —
    `결과가 1..count 를 정확히 덮는지로 자기검증`하는 건 parse_number_answer_tokens 가
    이미 쓰는 이 모듈의 관용구다. 기존 두 파서가 실패했을 때만 불리므로,
    이미 통과하던 회차의 판정은 이 함수가 있든 없든 달라지지 않는다.
    """
    tokens = _tokenize_answer_sheet(head)
    for start in range(len(tokens)):
        found: dict[int, int] = {}
        index, expect = start, 1
        while expect <= count and index + 1 < len(tokens):
            number_token, answer_token = tokens[index], tokens[index + 1]
            if not re.fullmatch(r"\d{1,2}", number_token) or int(number_token) != expect:
                break
            if squash(answer_token) in INVALID_TOKENS:
                found[expect] = ANSWER_NONE
            else:
                answer = answer_to_int(answer_token)
                if answer is None:
                    break
                found[expect] = answer
            expect += 1
            index += 2
        if len(found) == count:
            return found
    return {}


def parse_answer_table(text: str, count: int, head_chars: int = 3500) -> dict[int, int]:
    """첫머리 정답표를 판형 3종으로 읽고 더 믿을 만한 쪽을 고른다.

    head 를 자르는 이유: 해설 본문에도 '13. 2' 같은 문자열이 우연히 생길 수 있다.
    정답표는 항상 문서 맨 앞에 있으므로 앞부분만 본다.
    """
    head = text[:head_chars]
    inline = parse_inline_table(head, count)
    if len(inline) >= count:
        return inline
    columnar = parse_columnar_table(head, count)
    if len(columnar) >= count:
        # 불완전한 인라인 결과는 줄바꿈을 넘어 잘못 매칭된 것일 수 있으므로 덮어쓰지 않는다.
        return columnar
    # 앞의 둘이 못 읽은 판형(마침표 없는 번호·정답 교대). 완전한 표일 때만 값을
    # 돌려주므로, 여기서 무언가 나왔다면 그건 조각이 아니라 표 전체다.
    paired = parse_paired_number_table(head, count)
    if paired:
        return paired
    merged = dict(columnar)
    merged.update(inline)
    return merged


# --------------------------------------------------------------------------
# 평가원 정답표(번호·정답·배점) 토큰 스트림
# --------------------------------------------------------------------------

def _tokenize_answer_sheet(section: str) -> list[str]:
    """정답표 섹션을 '번호 / 정답 / 배점' 후보 토큰만 남긴 리스트로."""
    tokens: list[str] = []
    for raw_line in section.splitlines():
        line = squash(compact(raw_line))
        if not line:
            continue
        if re.fullmatch(r"\d{1,2}", line) or line in CHOICE_SYMBOLS or line in INVALID_TOKENS:
            tokens.append(line)
            continue
        # 한 줄에 여러 칸이 붙어 나오는 판형(표 셀이 한 줄로 합쳐진 경우)도 받는다.
        parts = re.findall(rf"\d{{1,2}}|[{CHOICE_SYMBOLS}]", line)
        if parts and len(squash(line)) == sum(len(p) for p in parts):
            tokens.extend(parts)
    return tokens


def parse_number_answer_tokens(tokens: list[str], count: int
                               ) -> tuple[dict[int, int], dict[int, int]]:
    """토큰 스트림에서 (번호, 정답[, 배점]) 묶음을 훑는다.

    배점 칸이 있는 판형과 없는 판형이 둘 다 존재한다. 어느 쪽인지 미리 알 수 없으므로
    3칸 묶음으로 훑어 1..count 가 다 채워지면 그것을 쓰고, 아니면 2칸 묶음으로 다시 훑는다.
    **결과가 1..count 를 정확히 덮는지로 자기검증**하는 것이 요령이다.
    배점 후보를 {2,3} 으로 못 박지 않는 이유는 과목마다 배점 체계가 다르기 때문이다.
    """
    def scan(stride: int):
        answers: dict[int, int] = {}
        points: dict[int, int] = {}
        index = 0
        while index + stride - 1 < len(tokens):
            number_token = tokens[index]
            answer_token = tokens[index + 1]
            if not re.fullmatch(r"\d{1,2}", number_token):
                index += 1
                continue
            number = int(number_token)
            if not 1 <= number <= count or number in answers:
                index += 1
                continue
            if squash(answer_token) in INVALID_TOKENS:
                answer = ANSWER_NONE
            else:
                answer = answer_to_int(answer_token) or 0
                if answer == 0:
                    index += 1
                    continue
            if stride == 3:
                point_token = tokens[index + 2]
                if not re.fullmatch(r"[1-9]", point_token):
                    index += 1
                    continue
                points[number] = int(point_token)
            answers[number] = answer
            index += stride
        return answers, points

    triple = scan(3)
    if len(triple[0]) >= count:
        return triple
    pair = scan(2)
    if len(pair[0]) >= count:
        return pair[0], {}
    # 둘 다 불완전하면 더 많이 건진 쪽. 확정은 교차 검증이 판단한다.
    return (triple if len(triple[0]) >= len(pair[0]) else (pair[0], {}))


def select_subject_section(text: str, aliases: list[str]) -> tuple[str, str]:
    """여러 과목이 한 정답표 PDF 에 들어 있을 때 우리 과목 구간만 잘라낸다.

    과목 머리글이 아예 없으면 단일 과목 정답표로 보고 전체를 돌려준다.
    (EBSi 가 과목별로 쪼개서 주는 회차가 그렇다.)
    """
    wanted = {squash(a).casefold() for a in aliases if a}
    matches = list(SUBJECT_SECTION_RE.finditer(text))
    if not matches:
        return text, "single-section"
    for index, match in enumerate(matches):
        name = squash(match.group(1)).casefold()
        if name in wanted:
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            return text[start:end], f"section:{match.group(1)}"
    return "", "과목 구간을 찾지 못했다"


# --------------------------------------------------------------------------
# 원문자 ①~⑤ 픽셀 템플릿 대조 — OCR 없이 정답표 이미지를 읽는다
# --------------------------------------------------------------------------
#
# ## 왜 필요한가 (실측)
#
# 사회탐구·초기 회차에서 정답 3중 대조가 사실상 1축으로 무너져 있었다.
#   - answer.png : Windows OCR 이 원문자 ①~⑤ 를 못 읽는다. 한국지리 2025_고3_3월학평
#     에서 ㉦·ㅅ·ㅎ 따위로 읽혔고, 2010학년도 수능 스캔 정답표에서는 완전한 잡음으로
#     17문항의 '정답'이 만들어졌다. 그래서 read_answer_sheet 가 OCR 결과를 통째로
#     버린다(그 판단 자체는 옳다 — 부분적으로 읽힌 정답표는 정보가 아니라 잡음이다).
#   - pdfplumber 축 : 2단 해설지를 좌표대로 펴면서 정답표와 본문을 같은 줄로 합쳐
#     못 읽는다.
# 남는 것은 해설지 축 하나뿐이고, 3중 대조는 이름만 남는다.
#
# ## 왜 OCR 이 아니라 템플릿인가
#
# ①②③④⑤ 는 **글자 모양이 고정된 다섯 종**이다. 문서 폰트가 바뀌어도 '동그라미 안에
# 숫자 하나'라는 구조는 변하지 않는다. 일반 OCR 은 이 다섯 글자를 잘 모르는 반면,
# 픽셀 대조는 다섯 후보만 놓고 고르면 되므로 훨씬 쉬운 문제다. 필요한 것은 Pillow 와
# numpy 뿐이다(둘 다 이미 의존성이다). 폰트도 필요 없다 — 아래 _TEMPLATES 가
# 렌더 결과를 1비트로 구워 넣은 것이다.
#
# ## 어떻게 읽는가
#
#   1) 회색조로 올려 읽고(폭 1800px 목표) 임계값 여러 개로 이진화해 본다.
#      임계값 하나로 고정하면 안 된다 — 실측: 2025학년도 수능 정답표는 임계값 160 에서
#      원 테두리가 끊겨 '닫힌 고리'가 하나도 안 남고, 190 에서 20개가 전부 잡힌다.
#   2) 8-연결 컴포넌트 중 **닫힌 고리**(정사각형에 가깝고, 속이 비었고, 안쪽에 잉크가
#      있고, 모서리가 빈 것)만 고른다. 표 괘선·한글·쪽번호는 이 조건에서 떨어진다.
#   3) 고리 안쪽 글리프를 24×24 로 정규화해 다섯 템플릿과 정규화 상관을 잰다.
#   4) 고리 개수가 문항 수와 정확히 같을 때만 결과를 돌려준다. 이 모듈의 관용구다
#      (parse_number_answer_tokens 와 같은 자기검증) — 부분 표는 잡음이다.
#      실제로 2023학년도 6월모평 정답표에는 '없음'(전항 정답) 칸이 있어 고리가 19개만
#      나온다. 그걸 1~19번으로 밀어 넣으면 14번 이후가 통째로 어긋난다.
#   5) **읽는 순서를 가정하지 않는다.** 번호 칸의 자릿수를 세어 행우선/열우선을 판정하고,
#      가릴 수 없으면 기권한다. 자세한 사정은 _reading_order 참조 — 이 단계가 없으면
#      원문자를 100% 정확히 읽고도 번호만 어긋난 정답표가 나온다.
#
# ## 실측 결과 (정답표 20장)
# 지구과학Ⅱ EBSi 정답 이미지 17장 + 한국지리 answer.png + 2010학년도 수능 스캔
# 정답표(JPG) + 2021학년도 수능 평가원 정답표(PDF).
#   - 19장에서 20/20 문항을 읽어 해설지 정답표·육안 확인과 **전부 일치**.
#   - 1장(2023 6월모평)은 '없음' 칸 때문에 고리가 19개라 기권. 틀린 답은 0장.
#   - 판형은 둘 다 나왔다: EBSi 계열 17장은 행우선, 평가원 계열 2장(2010·2021)은 열우선.

CIRCLED_CELL = 24                      # 정규화 격자 한 변
CIRCLED_TARGET_WIDTH = 1800            # 이 폭이 되도록 확대해 읽는다
# 이진화 임계값 후보. 낮은 쪽부터 시도해 문항 수와 딱 맞는 순간 멈춘다.
CIRCLED_THRESHOLDS = (150, 170, 190, 205, 220, 235)
CIRCLED_MIN_SCORE = 0.55               # 최고 상관이 이보다 낮으면 글자가 아니다
CIRCLED_MIN_MARGIN = 0.05              # 1위와 2위 차이가 이보다 작으면 애매하다

# ①~⑤ 안쪽 글리프를 24×24 1비트로 구운 것. 값 하나에 폰트별 템플릿이 여러 개 이어 붙어
# 있고(72바이트씩), 대조는 그중 가장 잘 맞는 것 하나로 한다.
#
# 만든 방법: 바탕·굴림·맑은고딕·Times·MS Gothic·HY중고딕·휴먼편지·SimSun 여덟 폰트로
# ①~⑤ 를 96pt 로 렌더한 뒤 **아래 _label → _ring_candidates → _normalize_inner 를 그대로**
# 통과시켜 얻은 벡터를 1비트로 눌렀다. 질의와 템플릿이 같은 코드를 지나므로 정규화 방식이
# 어긋날 수 없다. 폰트는 굽는 시점에만 필요하고 실행 시점에는 필요 없다 — 여기 데이터로
# 들어와 있으므로 폰트가 없는 환경에서도 그대로 동작한다.
_TEMPLATES_B64 = {
    1: "AAAAAAAAADwAAfwAAdwAABwAABwAABwAABwAABwAABwAABwAABwAABwAABwAABwAABwAABwAABwAABwAABwAAf+AAAAAAAAAAAAAAAAAAA4AAA4AAP4AAP4AAP4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAAAAAAAAAAAAAAAAADwAA/wAA/wAA/wAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAA//AA//AAAAAAAAAAAAAAAAAAAYAAB4AAP4AAP4AAP4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAAAAAAAAAAAAAAAAAA4AAA4AAB4AAH4AAP4AAP4AAM4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAA4AAAAAAAAAAAAAAAAAADwAAPwAAfwAAPwAABwAADwAABwAABwAABwAABwAABwAABwAABwAABwAABwAABwAABwAABgAAf+AAf+AAAAAAAAAAAAAAAAAABwAAfwAAfwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAADwAAf+AAAAAAAAA",  # 폰트 7종
    2: "AAAAAAAAAP8AAceAAwHAAwHAA4HAA4HAAwHAAAGAAAOAAAcAAA4AABwAADgAAHAAAOBAAMBAAYDAAwHAA//AA/+AAAAAAAAAAAAAAAAAAP4AAf+AA/+AB4PABwHABwHABwHAAAPAAAeAAA8AAB4AAHwAAPAAAeAAA8AAA4AABwAAB/+AB//AB//AAAAAAAAAAAAAAAAAAH8AAP+AAefAAYHAAAHAAAHAAAHAAAHAAAPAAAOAAAcAAA8AAB4AADwAAHgAAPAAAeAAA8AAA//AA//AAAAAAAAAAAAAAAAAAP4AAf8AA/+AA4OAB4PAB4PAAAPAAAOAAAeAAA8AAB4AADwAAHgAAfAAAeAAA8AAB4AAB//AB//AB//AAAAAAAAAAAAAAAAAAH4AAP8AAf+AA4OAA4OAA4OAAAOAAAeAAAcAAB4AAD4AAHgAAPAAAeAAAcAAA4AAA4AAA/+AA/+AA/+AAAAAAAAAAAAAAAAAAP4AAf+AA8eAA4HAB4HABwHAAwHAAAHAAAOAAAeAAA8AAB4AADwAAHgAAPAAAeAAA8HABwHAB//AB//AAAAAAAAAAAAAAAAAAf4AA/+ABwPABgHADgHADwHgDwHgBgHAAAPAAAeAAAcAAB4AADwAAHAAAOAQAcAQBwAwDgAwD//wD//wAAAAAAAA",  # 폰트 7종
    3: "AAAAAAAAAP8AAcOAAYGAA4HAAYHAAAHAAAGAAAOAAH4AAH4AAAeAAAHAAAHAAADAAwDAA4DAA4HAA4HAA4eAAP4AAAAAAAAAAAAAAAAAAP4AAf+AA+eAA4HAA4HAA4HAAAHAAAOAAD+AAD8AAB+AAAPAAAHAAwHAAwHAA4HAA4HAA+fAAf+AAP8AAAAAAAAAAAAAAAAAAfwAA/4AA48AAAeAAAOAAAOAAAcAAA8AAP4AAf4AAP8AAA+AAAOAAAOAAAOAAAOAAgOAA4+AA/8AAfwAAAAAAAAAAAAAAAAAAP4AAf8AA/+AB4OABwPAAAPAAAOAAAeAAD8AAH4AAD+AAAeAAAPAAAHABgHABwHAB4PAA/+AA/8AAP4AAAAAAAAAAAAAAAAAAH4AAP8AAf+AA8OAA4PAAYOAAAOAAAeAAB8AAB4AAB+AAAPAAAHAAAHAA4HAA4HAA4PAAf+AAf8AAH4AAAAAAAAAAAAAAAAAAP4AAf8AA8eAA4PAB4HAAAHAAAOAAAOAAB8AAB4AAB8AAAeAAAHAAAHAAAHABwHABwHAB8fAA/+AAP4AAAAAAAAAAAAAAAAAAf4AA/+ABgPABgHADwHgDwHABgHAAAPAAA+AAP4AAB+AAAPAAAHgDgDgDgDgDgDgDAHgBgPAA/+AAf8AAAAAAAAA",  # 폰트 7종
    4: "AAAAAAAAAAMAAAcAAA8AAA8AAB8AADcAAHcAAGcAAMcAAccAAYcAAwcABgcABgcAB//gAAcAAAcAAAcAAAcAAD/AAAAAAAAAAAAAAAAAAAcAAA8AAB8AAB8AAD8AAD8AAHcAAPcAAOcAAccAAccAA4cABwcAB//gB//gA//gAAcAAAcAAAcAAAcAAAAAAAAAAAAAAAAAAAcAAA8AAA8AAB8AAD8AAH8AAHcAAOcAAccAA8cAA4eABwcADweAD//gD//gAAeAAAcAAAcAAAcAAAcAAAAAAAAAAAAAAAAAAA+AAA+AAB+AAD+AAD+AAH+AAPeAAOeAAeeAA8eAA4eAB4eADweAD//gD//gD//gAAeAAAeAAAeAAAeAAAAAAAAAAAAAAAAAAA8AAA8AAB8AAB8AAD8AAD8AAHcAAHcAAPcAAOcAAecAAccAA8cAA//AA//AA//AAAcAAAcAAAcAAAcAAAAAAAAAAAAAAAAAAA8AAA8AAB8AAD8AAD8AAHcAAPcAAOcAAccAAccAA4cABwcABwcAB//AB//AAAcAAAcAAAcAAD/AAD/AAAAAAAAAAAAAAAAAAAcAAA8AAB8AAB8AADcAAHcAAGcAAMcAAccAAYcAAwcABwcABgcAD/9gD//wAAcAAAcAAAcAAA8AAD/gAAAAAAAA",  # 폰트 7종
    5: "AAAAAAAAA/8AA/8AA/wAAwAAAwAAAwAAAwAAA3wAA/4AA4cAAwOAAAGAAAGAAAGAAAGAA4GAA4OAAwMAAYYAAPwAAAAAAAAAAAAAAAAAAf+AAf+AAf8AA8AAA4AAA4AAA/wAA/8AA/+AA4eAAwPAAAHAAAHAAAHAAgHABwHAB4PAB/+AA/8AAf4AAAAAAAAAAAAAAAAAA/+AA/+AA4AAA4AAA4AAA4AAA4AAA/AAA/4AA/8AAA+AAAOAAAOAAAOAAAOAAAOAAAeAA48AA/4AAfwAAAAAAAAAAAAAAAAAA//AB//AB//ABwAABwAABwAABzwAB/8AB/+AB8eABwPAAAHAAAHAAAHAAAHAB4HAB4PAA/+AA/8AAP4AAAAAAAAAAAAAAAAAAf+AAf+AAf8AA4AAA4AAA4AAA7wAA/4AA/8AA8eAAAOAAAOAAAOAAAOAAwOAA4OAA4OAA88AAf8AAPwAAAAAAAAAAAAAAAAAA//AA//AA4AAA4AAA4AAA4AABzgAB/4AB/8AB4OAAwHAAAHAAAHAAAHABwHABwHAB4OAA8eAAf8AAP4AAAAAAAAAAAAAAAAABgPAB//AB/8ABgAABgAABgAABjgABv8AB8fABgHgBADgAADgAADgBADgDgDgDgDgDgHABgPAA/+AAf4AAAAAAAAA",  # 폰트 7종
}
_TEMPLATE_CACHE: dict | None = None


class CircledUnavailable(RuntimeError):
    """Pillow/numpy 가 없어 픽셀 대조를 할 수 없다. 부르는 쪽이 다른 축으로 넘어가야 한다."""


def _imaging():
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:                        # pragma: no cover - 환경 문제
        raise CircledUnavailable(f"Pillow/numpy 가 필요하다: {exc}") from exc
    return np, Image


def _gray_array(path: Path, page: int = 0):
    """이미지(또는 PDF 한 쪽)를 회색조 배열로. 폭이 작으면 확대한다.

    원본 정답표 PNG 는 폭 600px 이라 원 테두리가 1px 이다. 등배로 이진화하면 고리가
    쉽게 끊긴다(위 임계값 이야기와 같은 뿌리). 확대해 두면 테두리가 두꺼워져 안정된다.
    """
    np, Image = _imaging()
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        try:
            import fitz
        except ImportError as exc:                    # pragma: no cover
            raise CircledUnavailable(f"PDF 를 그림으로 펴려면 PyMuPDF 가 필요하다: {exc}") from exc
        document = fitz.open(str(path))
        try:
            if page >= len(document):
                raise CircledUnavailable(f"{page + 1}쪽이 없다")
            target = document[page]
            zoom = max(1.0, CIRCLED_TARGET_WIDTH / max(1.0, target.rect.width))
            pixmap = target.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace="gray")
            return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n)[:, :, 0].copy()
        finally:
            document.close()

    with Image.open(path) as opened:
        image = opened.convert("L")
        zoom = max(1, min(4, round(CIRCLED_TARGET_WIDTH / max(1, image.width))))
        if zoom > 1:
            image = image.resize((image.width * zoom, image.height * zoom), Image.LANCZOS)
        return np.asarray(image, dtype=np.uint8).copy()


def _pdf_page_count(path: Path) -> int:
    try:
        import fitz
    except ImportError:                               # pragma: no cover
        return 0
    try:
        document = fitz.open(str(path))
    except Exception:                                 # noqa: BLE001
        return 0
    try:
        return len(document)
    finally:
        document.close()


def _label(mask):
    """8-연결 컴포넌트. (라벨 배열, {라벨: (픽셀수, y0, x0, y1, x1)}).

    픽셀 단위 BFS 가 아니라 **런(가로 연속 구간) 단위 union-find** 로 짰다.
    1800×1200 배열을 임계값 여섯 개로 훑어야 해서 속도가 실제로 문제가 된다
    (픽셀 BFS 는 이미지 한 장에 수 초, 이 방식은 수십 ms).
    """
    np, _Image = _imaging()
    height, width = mask.shape
    parent = [0]

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    flat = mask.astype(np.int8)
    all_runs: list[list[tuple[int, int, int]]] = []
    previous: list[tuple[int, int, int]] = []
    for y in range(height):
        edges = np.flatnonzero(np.diff(np.concatenate(([0], flat[y], [0]))))
        runs: list[tuple[int, int, int]] = []
        for start, end in zip(edges[0::2].tolist(), edges[1::2].tolist()):
            parent.append(len(parent))
            here = len(parent) - 1
            # 반열림 구간 [start, end) 끼리 대각선까지 닿으면 같은 성분이다.
            for pstart, pend, plabel in previous:
                if start <= pend and pstart <= end:
                    union(here, plabel)
            runs.append((start, end, here))
        all_runs.append(runs)
        previous = runs

    labels = np.zeros((height, width), dtype=np.int32)
    boxes: dict[int, list[int]] = {}
    for y, runs in enumerate(all_runs):
        for start, end, raw in runs:
            root = find(raw)
            labels[y, start:end] = root
            box = boxes.get(root)
            if box is None:
                boxes[root] = [end - start, y, start, y, end - 1]
            else:
                box[0] += end - start
                box[3] = y
                box[2] = min(box[2], start)
                box[4] = max(box[4], end - 1)
    return labels, {k: tuple(v) for k, v in boxes.items()}


def _hole_mask(labels, cid: int, y0: int, x0: int, y1: int, x1: int):
    """컴포넌트 bbox 안에서 '테두리에 둘러싸여 바깥과 통하지 않는' 영역."""
    np, _Image = _imaging()
    sub = labels[y0:y1 + 1, x0:x1 + 1] == cid
    height, width = sub.shape
    padded = np.zeros((height + 2, width + 2), dtype=bool)
    padded[1:-1, 1:-1] = sub
    seen = np.zeros_like(padded)
    seen[0, 0] = True
    queue = deque([(0, 0)])
    while queue:
        y, x = queue.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < padded.shape[0] and 0 <= nx < padded.shape[1] \
                    and not padded[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                queue.append((ny, nx))
    return ((~padded) & (~seen))[1:-1, 1:-1]


def _ring_candidates(labels, boxes):
    """닫힌 고리처럼 생긴 컴포넌트만 (y0, x0, y1, x1, 구멍마스크) 로 돌려준다.

    조건 셋 다 실측으로 좁힌 것이다.
      - 가로세로 12~200px, 종횡비 0.75~1.35 : 표 괘선(가늘고 길다)·문장이 떨어진다.
      - 채움률 0.04~0.45 : 속이 빈 테두리만 남는다(숫자·한글은 0.5 이상).
        아래쪽 경계가 0.10 이 아니라 0.04 인 이유 — 고리의 채움률은 π·(획 두께)/(지름)
        이라 **해상도에 반비례**한다. 0.10 으로 두면 지름 86px 짜리 얇은 원(바탕체를
        96pt 로 렌더한 것)이 0.09 로 떨어져 나간다. 실제 판별은 아래 구멍 검사가 한다.
      - 구멍 넓이가 bbox 의 20% 이상 : 'ㅁ' 같은 작은 닫힌 획이 떨어진다.
      - 원형도 0.60~0.90 : 테두리와 구멍을 합친 넓이가 bbox 를 얼마나 채우는가.
        원이면 π/4 ≈ 0.785, 사각형이면 1.0 에 붙는다. **예방적 필터다** — 정답표 20장
        (아래 실측 목록)에서 이 조건을 꺼도 결과가 달라지지 않았다. 그래도 두는 이유는,
        '속 빈 닫힌 도형 + 안쪽에 숫자' 는 표의 네모 칸도 만족하는 조건이라 판형이 조금만
        달라지면 칸이 원문자로 둔갑할 수 있기 때문이다. 원과 네모를 가르는 것은 결국
        모서리가 비었는가이고, 그게 이 비율이다. 경계(0.60~0.90)는 실측된 원들이
        여유 있게 들어가는 범위로 잡았다.
    """
    out = []
    for cid, (count, y0, x0, y1, x1) in boxes.items():
        width, height = x1 - x0 + 1, y1 - y0 + 1
        if not (12 <= width <= 200 and 12 <= height <= 200):
            continue
        if not (0.75 <= width / height <= 1.35):
            continue
        if not (0.04 <= count / (width * height) <= 0.45):
            continue
        hole = _hole_mask(labels, cid, y0, x0, y1, x1)
        filled = int(hole.sum()) + count
        if hole.sum() < 0.20 * width * height:
            continue
        if not (0.60 <= filled / (width * height) <= 0.90):
            continue
        out.append((y0, x0, y1, x1, hole))
    return out


def _normalize_inner(gray, mask, box, hole):
    """고리 안쪽 잉크를 24×24 로 정규화한 단위벡터. 잉크가 없으면 None.

    평균을 빼고 길이를 1로 맞춘다(정규화 상관). 이렇게 하면 원본의 밝기·대비가
    달라도 점수가 흔들리지 않는다 — 스캔본과 벡터 PDF 를 같은 척도로 볼 수 있다.
    """
    np, Image = _imaging()
    y0, x0, y1, x1 = box
    ink = mask[y0:y1 + 1, x0:x1 + 1] & hole
    if ink.sum() < 4:
        return None
    ys, xs = np.nonzero(ink)
    gy0, gy1, gx0, gx1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    patch = 255.0 - gray[y0 + gy0:y0 + gy1 + 1, x0 + gx0:x0 + gx1 + 1].astype(np.float32)
    # 고리 획이 걸쳐 들어온 자리는 0 으로 눌러 둔다 — 안쪽 글리프만 남겨야 한다.
    patch = patch * hole[gy0:gy1 + 1, gx0:gx1 + 1]

    image = Image.fromarray(np.clip(patch, 0, 255).astype(np.uint8))
    scale = (CIRCLED_CELL - 4) / max(image.height, image.width)
    size = (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale))))
    image = image.resize(size, Image.LANCZOS)
    canvas = Image.new("L", (CIRCLED_CELL, CIRCLED_CELL), 0)
    canvas.paste(image, ((CIRCLED_CELL - size[0]) // 2, (CIRCLED_CELL - size[1]) // 2))

    vector = np.asarray(canvas, dtype=np.float32).ravel()
    vector = vector - vector.mean()
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-6 else None


def _templates():
    """base64 로 구운 1비트 템플릿을 단위벡터로 편다. 한 번만 만든다."""
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is not None:
        return _TEMPLATE_CACHE
    np, _Image = _imaging()
    cell = CIRCLED_CELL * CIRCLED_CELL
    stride = cell // 8
    table: dict[int, list] = {}
    for value, blob in _TEMPLATES_B64.items():
        raw = base64.b64decode(blob) if blob else b""
        vectors = []
        for offset in range(0, len(raw) - stride + 1, stride):
            bits = np.unpackbits(np.frombuffer(raw[offset:offset + stride], dtype=np.uint8))
            vector = bits[:cell].astype(np.float32)
            vector = vector - vector.mean()
            norm = float(np.linalg.norm(vector))
            if norm > 1e-6:
                vectors.append(vector / norm)
        table[value] = vectors
    _TEMPLATE_CACHE = table
    return table


def _classify_glyph(vector) -> tuple[int | None, float, float]:
    """(정답값, 최고점수, 1·2위 차이). 애매하면 정답값이 None."""
    np, _Image = _imaging()
    scores = {value: max((float(np.dot(vector, t)) for t in templates), default=-1.0)
              for value, templates in _templates().items() if templates}
    if not scores:
        return None, 0.0, 0.0
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, top = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else -1.0
    if top < CIRCLED_MIN_SCORE or (top - runner) < CIRCLED_MIN_MARGIN:
        return None, top, top - runner
    return best, top, top - runner


def _rows_of(found) -> list[list]:
    """(y, x, 지름, 벡터) 목록을 행으로 묶고 각 행을 왼쪽부터 정렬한다."""
    rows: list[list] = []
    for glyph in sorted(found, key=lambda g: (g[0], g[1])):
        if rows and abs(glyph[0] - rows[-1][0][0]) < glyph[2] * 0.8:
            rows[-1].append(glyph)
        else:
            rows.append([glyph])
    return [sorted(row, key=lambda g: g[1]) for row in rows]


def _grid_reason(rows: list[list]) -> str:
    """격자로 보이지 않으면 그 이유를, 격자면 빈 문자열을 돌려준다.

    두 가지 일을 한다.
    1) **_reading_order 의 전제 조건.** 아래 읽기순서 판정은 '행 개수 × 열 개수' 격자를
       가정하고 rows[r][c] 로 접근한다. 행마다 칸 수가 다르면 그 계산 자체가 성립하지 않는다.
    2) 흩어진 글자 조각 걸러내기. 정답표의 원문자는 열이 맞고 크기가 같지만,
       본문에 우연히 섞인 'ㅇ' 따위는 그렇지 않다.
    """
    if len(rows) < 2:
        return "행이 하나뿐이라 정답표 격자로 보기 어렵다"
    widths = {len(row) for row in rows}
    if len(widths) != 1:
        return f"행마다 칸 수가 다르다({sorted(len(r) for r in rows)}) — 격자가 아니다"

    diameters = [g[2] for row in rows for g in row]
    if max(diameters) > 1.25 * min(diameters):
        return f"동그라미 크기가 고르지 않다({min(diameters)}~{max(diameters)}px)"

    # 같은 열이면 행이 달라도 x 중심이 지름 안쪽에서 맞아야 한다.
    span = sum(diameters) / len(diameters)
    head = rows[0]
    for row in rows[1:]:
        for above, below in zip(head, row):
            if abs(above[1] - below[1]) > span:
                return "열이 맞지 않는다 — 흩어진 글자 조각으로 보인다"
    return ""


def _digit_counts(boxes, rows) -> list[list[int]]:
    """원문자마다 **왼쪽 번호 칸의 자릿수**를 센다. 읽기 순서를 정하는 데 쓴다.

    정답표는 [번호][정답][번호][정답]… 이 가로로 반복되므로, i번째 원문자의 번호 칸은
    i-1번째 원문자와 i번째 원문자 사이에 있다. 그 구간에 온전히 들어가는 잉크 덩어리
    개수가 곧 자릿수다('12' 는 두 덩이). 숫자를 읽지 않고 **세기만** 한다 —
    아래 _reading_order 가 필요로 하는 정보는 그것뿐이고, 세는 쪽이 훨씬 안전하다.
    """
    counts: list[list[int]] = []
    for row in rows:
        span = sum(g[2] for g in row) / len(row)
        row_counts = []
        previous_right = None
        for glyph in row:
            centre_y, centre_x, diameter = glyph[0], glyph[1], glyph[2]
            left = centre_x - diameter / 2
            window_left = max(left - 4 * span, previous_right if previous_right else 0.0)
            top, bottom = centre_y - 0.7 * span, centre_y + 0.7 * span
            n = 0
            for _count, y0, x0, y1, x1 in boxes.values():
                # 구간 안에 **온전히** 들어가는 것만 센다. 표 괘선은 칸 밖으로 뻗어 있어
                # 자연히 빠진다(전체를 가로지르는 한 덩어리인 경우가 많다).
                if y0 >= top and y1 <= bottom and x0 >= window_left and x1 <= left:
                    box_h, box_w = y1 - y0 + 1, x1 - x0 + 1
                    if 0.25 * span <= box_h <= 1.2 * span:
                        # 숫자는 어느 서체에서나 세로로 길다. 가로가 세로에 육박하면
                        # 두 자리가 붙어 한 덩어리로 잡힌 것이다(실측: '20' 의 2와 0).
                        n += 2 if box_w > 0.9 * box_h else 1
            row_counts.append(n)
            previous_right = centre_x + diameter / 2
        counts.append(row_counts)
    return counts


def _expected_digit_counts(shape: tuple[int, int], column_major: bool) -> list[list[int]]:
    """행×열 격자를 행우선/열우선으로 번호 매겼을 때의 자릿수 표."""
    height, width = shape
    out = []
    for r in range(height):
        row = []
        for c in range(width):
            number = (c * height + r + 1) if column_major else (r * width + c + 1)
            row.append(len(str(number)))
        out.append(row)
    return out


def _reading_order(rows, boxes) -> tuple[list, str]:
    """원문자를 문항 번호 순서로 편다. 정할 수 없으면 (빈 목록, 이유).

    ── 왜 '왼쪽부터, 위에서 아래로' 를 그냥 믿으면 안 되는가 (실측 사고) ──
    EBSi 정답표는 첫 행이 1,2,3,4,5 인 **행우선**이다. 그런데 평가원 정답표는
    첫 행이 1,6,11,16 인 **열우선**이다(문항번호/정답/배점 묶음을 옆으로 4벌 이어 붙인
    판형). 2021학년도 수능 정답표에 행우선을 가정했더니 원문자 20개를 전부 정확히
    읽고도 **번호만 어긋난 정답표**가 나왔다 — 픽셀 대조가 맞을수록 더 위험한 종류의
    오류다.

    그래서 번호 칸의 자릿수 표를 실제로 세어 두 가설 중 맞는 쪽만 채택하고,
    둘 다 맞거나 둘 다 틀리면 기권한다. 자릿수만 세면 되므로 숫자를 읽을 필요가 없다.
    """
    shape = (len(rows), len(rows[0]))
    observed = _digit_counts(boxes, rows)
    row_major = observed == _expected_digit_counts(shape, column_major=False)
    column_major = observed == _expected_digit_counts(shape, column_major=True)
    if row_major and column_major:
        # 1~9 만 있는 표(문항 9개 이하)는 두 가설의 자릿수가 같다. 이때는 격자 모양이
        # 한 줄이 아닌 이상 구분할 근거가 없으므로 기권한다.
        return [], "번호 칸 자릿수만으로는 행우선/열우선을 구분할 수 없다"
    if not row_major and not column_major:
        return [], (f"번호 칸 자릿수({observed})가 행우선·열우선 어느 쪽과도 맞지 않는다 "
                    f"— 판형을 모르는 채로 번호를 붙일 수 없다")

    if row_major:
        return [glyph for row in rows for glyph in row], "행우선"
    height, width = shape
    return [rows[r][c] for c in range(width) for r in range(height)], "열우선"


def _read_circled_page(gray, count: int) -> tuple[dict[int, int], str]:
    """회색조 한 장에서 원문자 정답표를 읽는다. (answers, 이유)."""
    np, _Image = _imaging()
    best: tuple[int, list, dict] | None = None
    for threshold in CIRCLED_THRESHOLDS:
        mask = gray < threshold
        labels, boxes = _label(mask)
        rings = _ring_candidates(labels, boxes)
        if not rings:
            continue
        # 정답표의 원은 크기가 같다. 가장 흔한 지름에서 벗어난 것(제목의 괄호 등)은 뺀다.
        diameters = sorted(r[2] - r[0] + 1 for r in rings)
        median = diameters[len(diameters) // 2]
        kept = [r for r in rings if 0.75 * median <= (r[2] - r[0] + 1) <= 1.3 * median]
        glyphs = []
        for y0, x0, y1, x1, hole in kept:
            vector = _normalize_inner(gray, mask, (y0, x0, y1, x1), hole)
            if vector is not None:
                glyphs.append(((y0 + y1) / 2, (x0 + x1) / 2, y1 - y0 + 1, vector))
        if len(glyphs) == count:
            best = (threshold, glyphs, boxes)
            break
        if best is None or len(glyphs) > len(best[1]):
            best = (threshold, glyphs, boxes)
    if best is None or not best[1]:
        return {}, "원문자로 보이는 동그라미를 찾지 못했다"

    glyphs = best[1]
    if len(glyphs) != count:
        # 여기서 부분 결과를 돌려주면 '없음' 칸이 있는 회차에서 그 뒤 번호가 전부 밀린다
        # (실측: 2023학년도 6월모평 14번이 전항 정답이라 고리가 19개다).
        return {}, f"원문자를 {len(glyphs)}개 찾았다(문항 {count}개) — 번호가 밀릴 수 있어 버린다"

    rows = _rows_of(glyphs)
    why_not_grid = _grid_reason(rows)
    if why_not_grid:
        return {}, why_not_grid

    ordered, order_why = _reading_order(rows, best[2])
    if not ordered:
        return {}, order_why

    answers: dict[int, int] = {}
    weak: list[int] = []
    for index, glyph in enumerate(ordered, start=1):
        value, _top, _margin = _classify_glyph(glyph[3])
        if value is None:
            weak.append(index)
            continue
        answers[index] = value
    if weak:
        return {}, f"원문자 판정이 애매한 칸이 있다: {weak}"
    return answers, f"원문자 템플릿 대조({order_why}, 임계값 {best[0]})"


def _pdf_page_texts(path: Path) -> list[str]:
    try:
        import fitz
    except ImportError:                               # pragma: no cover
        return []
    try:
        document = fitz.open(str(path))
    except Exception:                                 # noqa: BLE001
        return []
    try:
        return [page.get_text("text") for page in document]
    finally:
        document.close()


def parse_circled_answer_image(path, count: int,
                               aliases: list[str] | None = None) -> tuple[dict[int, int], str]:
    """정답지 파일 하나에서 원문자 정답표를 읽는다. (answers, 설명).

    PDF 면 쪽마다 시도해 처음으로 완전한 표가 나온 쪽을 쓴다. 다만 **과목 구간을 먼저
    가려야 한다** — 평가원 정답표 PDF 한 개에 한 교시 8과목이 쪽마다 들어 있어서,
    아무 쪽이나 집으면 첫 과목(물리학Ⅰ)의 표를 우리 과목 정답으로 읽는다. 이건
    read_pdfplumber 가 이미 겪은 사고이고, 여기서는 3중 대조의 다수결도 못 막는다
    (같은 파일을 두 축이 함께 오독하기 때문). 그래서 문서에 과목 머리글이 하나라도
    있으면 **머리글이 우리 과목인 쪽만** 본다. 별칭을 못 받았으면 아예 기권한다.
    """
    path = Path(path)
    try:
        if path.suffix.lower() != ".pdf":
            return _read_circled_page(_gray_array(path), count)

        pages = _pdf_page_texts(path)
        if not pages:
            if _pdf_page_count(path) <= 0:
                return {}, "PDF 를 열 수 없다"
            pages = [""] * _pdf_page_count(path)
        labelled = any(SUBJECT_SECTION_RE.search(text) for text in pages)
        if labelled and not aliases:
            return {}, "여러 과목이 든 정답표인데 과목 별칭이 없어 어느 쪽인지 못 가린다"

        last = ""
        for index, text in enumerate(pages):
            if labelled:
                section, how = select_subject_section(text, aliases or [])
                if not (section.strip() and how.startswith("section:")):
                    continue
            answers, why = _read_circled_page(_gray_array(path, index), count)
            if answers:
                return answers, f"{why}, {index + 1}쪽"
            last = why
        return {}, last or "원문자 정답표를 찾지 못했다"
    except CircledUnavailable as exc:
        return {}, str(exc)
    except Exception as exc:                          # noqa: BLE001 — 축 하나가 죽어도 나머지는 간다
        return {}, f"원문자 대조 실패: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# 축 1 — 정답지
# --------------------------------------------------------------------------

def read_answer_sheet(layers, aliases: list[str], count: int,
                      points_total: int | None = None) -> Reading:
    """정답지(정답표)를 읽는다. 배점까지 나오는 유일한 축.

    두 가지 방법을 쓴다. **텍스트가 먼저다.** 텍스트로 완전한 정답표를 읽어내면
    그것으로 끝내고, 그러지 못했을 때만 원문자 ①~⑤ 픽셀 대조로 내려간다.

    순서가 이 방향인 이유는 실측 사고 때문이다. 픽셀 대조를 먼저 돌게 했더니
    2021학년도 수능 정답표 PDF(원문자가 없는 텍스트 판형)에서 한글 자모 조각이
    '닫힌 고리'로 잡혀 **자신 있게 틀린 정답 20개**를 만들었다. 텍스트가 이미
    완전한 답을 주는 자리에서는 그림을 다시 볼 이유가 없다. 픽셀 대조는 지금까지
    아무것도 못 얻던 자리(스캔 정답표)에서만 쓴다 — 그래야 이 축이 나빠질 수 없다.

    이 폴백이 없으면 정답지가 스캔 이미지인 회차에서 이 축이 통째로 죽는다.
    사회탐구와 2010년대 초반 회차가 그랬고, '3중 대조'가 해설지 1축만 남은
    이름뿐인 검증이 되어 있었다.
    """
    reading = Reading(source="answer_sheet")
    if layers is None:
        reading.reason = "정답지 파일 없음"
        return reading
    reading.origin = layers.path.name

    from_ocr = not layers.direct.strip()

    def circled_fallback(why_before: str, keep: dict[int, int] | None = None,
                         keep_points: dict[int, int] | None = None) -> Reading:
        """텍스트 경로가 완전한 표를 못 만들었을 때만 부른다.

        keep 은 텍스트로 건진 부분 결과다. 픽셀 대조가 완전한 표를 주면 그것으로
        갈아끼우고, 못 주면 부분 결과를 그대로 되돌려 놓는다 — 폴백을 붙인 것 때문에
        원래 있던 표가 사라지면 안 된다.
        """
        found, why = parse_circled_answer_image(layers.path, count, aliases)
        if len(found) >= count:
            reading.answers = found
            reading.points = {}          # 원문자 표에는 배점이 없다
            reading.origin = f"{layers.path.name}({why})"
            reading.reason = ""
            return reading
        reading.answers = dict(keep or {})
        reading.points = dict(keep_points or {})
        reading.reason = f"{why_before} / 원문자 대조도 실패: {why}"
        return reading

    text = layers.direct or layers.ocr
    if not text.strip():
        return circled_fallback("정답지에서 텍스트를 얻지 못했다(이미지 정답표는 OCR 실패)")

    section, how = select_subject_section(text, aliases)
    if not section.strip():
        return circled_fallback(how)
    answers, points = parse_number_answer_tokens(_tokenize_answer_sheet(section), count)
    if not answers:
        # 표가 아니라 '01. 3 02. 5' 형태로 적힌 정답지도 있다.
        answers = parse_answer_table(section, count, head_chars=len(section))

    if from_ocr:
        # OCR 로 읽은 정답표는 자기검증을 통과할 때만 믿는다.
        #
        # 실제로 겪은 사고 둘.
        # (1) 정답표 PNG 의 원문자 ①~⑤ 를 Windows OCR 이 ㉦ 따위로 읽으면서 격자 속 숫자
        #     몇 개만 건져, 4번·7번에만 엉뚱한 값이 들어간 축이 만들어졌다. 정답은 다수결이
        #     막아 줬지만 배점 교차검증은 그 한 표 때문에 무승부가 되어 배점이 통째로 비었다.
        # (2) 2010학년도 수능 스캔 정답표에서는 원문자가 'ㅅ' 'ㅎ' 으로 읽히고 번호·배점 칸의
        #     숫자만 남아, 완전한 잡음으로 17개 문항 '정답'이 만들어졌다.
        # 부분적으로 읽힌 OCR 정답표는 정보가 아니라 잡음이다. 배점 합이라는 독립 검산이
        # 가능하면 그것까지 통과해야 받는다.
        if len(answers) < count:
            return circled_fallback(
                f"OCR 정답표가 부분적으로만 읽혔다({len(answers)}/{count}) — 잡음으로 보고 버린다")
        if points and points_total and sum(points.values()) != points_total:
            return circled_fallback(
                f"OCR 정답표의 배점 합이 맞지 않는다"
                f"({sum(points.values())} != {points_total}) — 잡음으로 보고 버린다")

    if len(answers) < count:
        # 직접 텍스트로도 표를 다 못 채웠다. 완전한 표를 줄 수 있는 픽셀 대조에 한 번 더
        # 기회를 주되, 실패하면 지금 건진 조각을 그대로 유지한다(예전과 같은 결과).
        return circled_fallback(f"정답표를 {len(answers)}/{count} 만 읽었다",
                                keep=answers, keep_points=points)

    reading.answers, reading.points = answers, points
    reading.origin = f"{layers.path.name}({how}{'/OCR' if from_ocr else ''})"
    return reading


# --------------------------------------------------------------------------
# 축 2 — 해설지
# --------------------------------------------------------------------------

def read_solution(layers, count: int) -> Reading:
    """해설지 첫머리 정답표를 우선하고, 실패하면 블록별 '정답N' 표기로 내려간다.

    **글리프가 깨진 해설 PDF 라도 직접 텍스트 레이어를 먼저 본다.**
    2022학년도 수능 해설이 그렇다 — 한글은 전부 깨졌지만 정답표의 아스키 숫자는 멀쩡했다.
    한글 비율이 낮다고 이 레이어를 버리면 가장 좋은 정답 소스를 스스로 버리는 셈이다.
    """
    reading = Reading(source="solution")
    if layers is None:
        reading.reason = "해설지 파일 없음"
        return reading
    reading.origin = layers.path.name

    for label, text in (("직접 텍스트", layers.direct), ("OCR", layers.ocr)):
        if not text.strip():
            continue
        found = parse_answer_table(text, count)
        if len(found) >= count:
            reading.answers = found
            reading.origin = f"{layers.path.name}(첫머리 정답표/{label})"
            return reading

    # 첫머리 정답표가 없거나 불완전한 회차. 블록별 '정답N' 을 센다.
    body = layers.direct if layers.direct_usable else layers.ocr
    markers = parse_solution_markers(body, count)
    if markers:
        reading.answers = markers
        reading.origin = f"{layers.path.name}(블록별 정답 표기)"
        return reading
    reading.reason = "해설지에서 정답표도 정답 표기도 찾지 못했다"
    return reading


def parse_solution_markers(text: str, count: int) -> dict[int, int]:
    """해설 본문을 문항 블록으로 갈라 각 블록의 '정답N' 을 읽는다.

    블록 분리에 의존하므로 첫머리 정답표보다 약한 축이다. 순번이 1부터 연속으로
    올라가는 후보만 블록 시작으로 인정해 잡음(본문 속 '3. ' 등)을 걸러낸다.
    """
    if not text.strip():
        return {}
    starts: list[tuple[int, int]] = []   # (문항번호, 위치)
    expected = 1
    for match in re.finditer(r"(?m)^(\d{1,2})\.\s+(?![①②③④⑤1-5]\b)", text):
        number = int(match.group(1))
        if number == expected:
            starts.append((number, match.start()))
            expected += 1
            if expected > count:
                break
    if len(starts) < count:
        return {}

    found: dict[int, int] = {}
    for index, (number, position) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(text)
        marker = SOLUTION_MARKER_RE.search(text[position:end])
        if marker:
            value = answer_to_int(marker.group(1))
            if value:
                found[number] = value
    return found


# --------------------------------------------------------------------------
# 축 3 — pdfplumber
# --------------------------------------------------------------------------

def read_pdfplumber(answer_path, answer_layers, solution_layers,
                    aliases: list[str], count: int) -> Reading:
    """같은 원본을 pdfplumber 로 다시 읽는 축.

    fitz 가 실패하는 자리에서 pdfplumber 가 성공하고, 그 반대도 있다.
      - 괘선이 있는 평가원 정답표: extract_tables 가 셀 단위로 정확히 준다.
      - 칼럼분리형 해설 정답표: layout 모드가 좌표대로 펴 주어 인라인형이 된다.
        (fitz 는 이 판형을 세로로 토해내서 별도 파서가 필요했다.)
    """
    reading = Reading(source="pdfplumber")

    if answer_path is not None and answer_path.suffix.lower() == ".pdf":
        try:
            from .sources import read_pdf_page_tables

            pages = read_pdf_page_tables(answer_path)
            labelled = any(SUBJECT_SECTION_RE.search(text) for text, _ in pages)
            tokens: list[str] = []
            for text, tables in pages:
                # 여러 과목이 한 정답표에 들어 있으면 우리 과목 머리글이 있는 페이지만 쓴다.
                # 머리글이 아예 없는 문서(과목별로 쪼개 주는 회차)는 전부 쓴다.
                if labelled:
                    section, how = select_subject_section(text, aliases)
                    if not (section.strip() and how.startswith("section:")):
                        continue
                for table in tables:
                    for row in table:
                        for cell in row:
                            if cell is None:
                                continue
                            for piece in str(cell).splitlines():
                                piece = squash(piece)
                                if piece:
                                    tokens.append(piece)
            answers, points = parse_number_answer_tokens(tokens, count)
            if answers:
                reading.answers, reading.points = answers, points
                reading.origin = f"{answer_path.name}(extract_tables)"
                return reading
            if labelled and not tokens:
                reading.reason = "정답표 PDF 에서 우리 과목 페이지를 찾지 못했다"
        except Exception as exc:
            reading.reason = f"표 추출 실패: {exc}"

    for layers in (answer_layers, solution_layers):
        if layers is None or not layers.plumber.strip():
            continue
        section, _how = select_subject_section(layers.plumber, aliases)
        target = section if section.strip() else layers.plumber
        found = parse_answer_table(target, count)
        if found:
            reading.answers = found
            reading.origin = f"{layers.path.name}(layout)"
            reading.reason = ""
            return reading

    if not reading.reason:
        reading.reason = "pdfplumber 로 정답표를 찾지 못했다"
    return reading


# --------------------------------------------------------------------------
# 교차 검증
# --------------------------------------------------------------------------

@dataclass
class Verdict:
    value: int | None
    agree: list[str] = field(default_factory=list)     # 같은 값을 낸 축
    disagree: dict[str, int] = field(default_factory=dict)
    severity: str = "ok"                               # ok | warn | error
    why: str = ""


def cross_check(readings: list[Reading], number: int, field_name: str = "answers") -> Verdict:
    """한 문항에 대해 축들의 표를 모아 확정한다.

    세 축 일치 → 확정(조용히). 두 축 일치 → 확정하되 warn. 다 갈리면 → error, 값은 None.
    """
    votes: dict[str, int] = {}
    for reading in readings:
        value = getattr(reading, field_name).get(number)
        if value is not None:
            votes[reading.source] = value
    if not votes:
        return Verdict(None, severity="error", why="어떤 축에서도 읽지 못했다")

    tally = Counter(votes.values())
    top_value, top_count = tally.most_common(1)[0]
    agree = [src for src, val in votes.items() if val == top_value]
    disagree = {src: val for src, val in votes.items() if val != top_value}

    if len(tally) == 1:
        # 축이 두 개뿐이어도 값이 같으면 문항 단위로는 조용히 넘어간다.
        # "축이 몇 개 살아 있었나"는 회차 단위 사실이라 회차 note 로 한 번만 남긴다 —
        # 문항마다 남기면 380건이 되어 attention 30건 상한을 혼자 다 먹는다.
        return Verdict(top_value, agree=agree)

    # 값이 갈렸다. 과반이 있으면 채택하되 반드시 남긴다.
    tied = [value for value, cnt in tally.items() if cnt == top_count]
    if top_count >= 2 and len(tied) == 1:
        detail = ", ".join(f"{src}={val}" for src, val in sorted(disagree.items()))
        return Verdict(top_value, agree=agree, disagree=disagree, severity="warn",
                       why=f"축 불일치 — 다수결 {top_value} 채택 (이견: {detail})")
    detail = ", ".join(f"{src}={val}" for src, val in sorted(votes.items()))
    return Verdict(None, disagree=votes, severity="error",
                   why=f"모든 축이 갈렸다 — 사람이 확인해야 한다 ({detail})")
