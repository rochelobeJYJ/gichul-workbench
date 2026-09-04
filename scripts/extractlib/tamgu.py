# -*- coding: utf-8 -*-
"""tamgu-1q1block 판형 구현 — 탐구 영역 표준 2단 편집, 문항 하나가 한 블록.

지구과학Ⅱ 19회차 380문항을 실제로 통과시킨 알고리즘을 옮긴 것이다.
**검증된 것은 다시 발명하지 않는다.** 대신 과목 이름이 코드에 박혀 있던 자리를
전부 subject.json 에서 오는 값으로 바꿨다.

원본에서 과목 하드코딩이었던 것들:
  '과학탐구영역'  → subject.area + '영역'
  '지구과학I/II'  → subject.label + providers.kice.aliases
  20문항 / 50점   → subject.question_count / subject.points_total
  배점 {2,3}      → [N점] 표기를 읽고, 표기 없는 문항이 있을 때만 총점에서 역산
                    (아래 points_from_marks 참조. 배점 계단이 셋인 판형이 실재해서
                     '표기 없음 = 기본 배점' 가정을 조건부로 바꿨다)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from .points import (POINT_MARK_RE, normalize_points, on_point_grid,  # noqa: F401
                     points_equal, read_point_mark)
from .textnorm import CHOICE_TO_INT, INT_TO_CHOICE, compact, fold_name, squash

# 문항 시작: 줄머리의 'N.' — 두 자리까지.
QUESTION_START_RE = re.compile(r"(?m)^(\d{1,2})\.(?:\s|$)")
# <보기> 안의 항목 라벨. NFKC 를 거치면 ㉠㉡㉢ 도 여기로 눕는다.
SECTION_LABEL_RE = re.compile(r"(?<![가-힣])([ㄱ-ㅎ])\.")
# 배점 표기 정규식(POINT_MARK_RE)의 정의는 extractlib/points.py 한 곳뿐이다.
# 여기서 이름만 다시 내주는 이유: `from .tamgu import POINT_MARK_RE` 로 쓰던 자리를 깨지 않으면서
# **복사본을 없애기** 위해서다. 복사본은 한쪽만 고쳐지고, 실제로 그렇게 조용히 갈라졌다.
# 가운뎃점 변형(textnorm.NAME_SEPARATORS 와 같은 목록). 과목명이 이 글자를 품는 과목에서만 쓴다.
MIDDOT_VARIANTS = "·∙•・･‧⋅"


@dataclass
class ParsedQuestion:
    stem: str = ""
    boxed: str = ""
    choices: list[str] = field(default_factory=list)
    raw: str = ""
    error: str = ""
    warning: str = ""
    # 이 칸의 본체가 텍스트가 아니라 그림이라는 표시. 값은 "image" 하나뿐이고,
    # 정상(텍스트에서 복원됨)은 빈 문자열 = 표시 없음이다. items 에서는
    # ext.choices_source / ext.boxed_source 로 실린다 (extract.py 참조).
    choices_source: str = ""
    boxed_source: str = ""
    # 선택지를 **평범하지 않은 경로로 복원했다**는 표시. 값은 "flattened" 하나뿐이고
    # 정상(줄 단위로 그대로 읽힘)은 빈 문자열이다. items 에서는 ext.choices_layout 으로
    # 실린다. choices_source 와 달리 **내용은 온전하다** — 검수할 때 어디를 먼저 볼지
    # 알려주는 자리다(flatten_choice_band 참조).
    choices_layout: str = ""


# --------------------------------------------------------------------------
# 잡음 제거 — 여기 있는 규칙은 전부 실제 문제지에서 본 머리글/꼬리글이다
# --------------------------------------------------------------------------

def noise_tokens(subject) -> set[str]:
    """머리글/세로 제목이 글자 단위로 쪼개져 나올 때 버릴 토큰들.

    2단 편집 문제지의 세로 제목은 텍스트 레이어에서 '지' '구' '과' '학' 처럼
    한 글자씩 별도 줄로 나온다. 과목 이름을 코드에 적는 대신
    subject.json 의 label/aliases 에서 글자를 만들어 낸다.

    이름에 가운뎃점이 있으면 **그 변형들을 전부** 넣는다. 세로 제목도 한 글자씩
    쪼개지므로 가운뎃점 한 개가 독립된 줄로 나오는데(실측: 2024 수능 사회·문화
    1쪽 여백 제목이 '사/회/・/문/화'), 별칭의 '·'(U+00B7)와 문제지의 '・'(U+FF65→
    NFKC U+30FB)가 달라 그 한 줄만 살아남아 마지막 선택지 끝에 붙었다.
    """
    names = [subject.label or ""]
    kice = (subject.providers or {}).get("kice") or {}
    names.extend(kice.get("aliases") or [])
    tokens: set[str] = set()
    for name in names:
        flat = squash(name)
        if not flat:
            continue
        tokens.add(flat)
        tokens.update(flat)          # 글자 단위
        if any(ch in MIDDOT_VARIANTS for ch in flat):
            tokens.update(MIDDOT_VARIANTS)
    if subject.area:
        tokens.add(squash(subject.area))
        tokens.add(squash(subject.area) + "영역")
    return {t for t in tokens if t}


def folded_noise_tokens(subject) -> set[str]:
    """noise_tokens 의 '구분자 무시' 판. **두 글자 이상만** 담는다.

    같은 과목명이라도 가운뎃점 코드포인트가 자리마다 다르다(textnorm 의
    NAME_SEPARATORS 주석 참조). 실측: 2024 수능 사회·문화 문제지 꼬리글은
    한 쪽에서는 '사회탐구영역(사회･문화)'(U+FF65), 다른 쪽에서는
    '2 (사회․문화)'(U+2024→마침표) 로 찍힌다. squash 로만 대조하면 둘 다 안 걸려
    꼬리글이 **마지막 선택지 뒤에 그대로 붙는다**(⑤ … 지지한다. 사회탐구영역(사회・문화) 1).

    한 글자짜리(세로 제목 조각)를 여기 넣지 않는 이유: fold_name 은 마침표도 지우므로
    본문의 '2.' 같은 줄이 별칭 글자 '2' 와 같아져 **본문 한 줄이 조용히 사라진다.**
    한 글자 대조는 기존 squash 경로가 그대로 맡는다.
    """
    names = [subject.label or ""]
    kice = (subject.providers or {}).get("kice") or {}
    names.extend(kice.get("aliases") or [])
    if subject.area:
        names.extend([subject.area, f"{subject.area}영역"])
        names.append(f"{subject.area}영역({subject.label or ''})")
    folded = {fold_name(n) for n in names}
    return {t for t in folded if len(t) >= 2}


def alias_patterns(subject) -> list[re.Pattern]:
    """'2(지구과학II)' 같은 쪽 번호 꼬리글을 잡는 정규식.

    대조 대상은 fold_name 을 거친 줄이라 괄호가 이미 지워져 있다 — 그래서 패턴에도
    괄호를 넣지 않는다(위 folded_noise_tokens 주석의 가운뎃점 사고와 같은 자리다).
    """
    names = [subject.label or ""]
    kice = (subject.providers or {}).get("kice") or {}
    names.extend(kice.get("aliases") or [])
    patterns = []
    for name in names:
        flat = fold_name(name)
        if flat:
            patterns.append(re.compile(rf"^\d+{re.escape(flat)}$"))
    return patterns


def should_skip_line(line: str, tokens: set[str], patterns: list[re.Pattern],
                     folded_tokens: set[str] | None = None) -> bool:
    """문제지 머리글/꼬리글이면 True."""
    if not line:
        return True
    flat = squash(line)
    if flat in tokens:
        return True
    folded = fold_name(line)
    if folded_tokens and folded in folded_tokens:
        return True
    if "저작권" in line:
        return True
    if "대학수학능력시험" in line and "문제지" in line:
        return True
    if line.startswith("제") and "교시" in line:
        return True
    if "성명" in line or "수험번호" in line:
        return True
    if "선택" in line and "제[" in flat:
        return True
    if any(p.match(folded) for p in patterns):
        return True
    # '1 32' 처럼 쪽 번호와 문제지 코드만 있는 줄. 숫자 토큰이 2개 이상인 줄은 본문일 수 없다
    # — **단 숫자 선택지 다섯 줄은 예외다.** '1 9 / 2 18 / …' 같은 판형이 실재하고
    #   한 줄만 보면 쪽번호와 구분되지 않는다. 그 예외는 줄 배열을 보는 clean_text 의
    #   numeric_choice_lines 가 미리 골라 이 함수에 오기 전에 보호한다.
    if re.fullmatch(r"(?:\d+[ \t]*){2,3}", line.strip()):
        return True
    return False


# '1 9' 처럼 라벨과 값이 숫자 둘뿐인 줄. 쪽번호 줄('1 32')과 생김새가 완전히 같다.
_NUM_PAIR_LINE_RE = re.compile(r"^(\d{1,2})[ \t]+\d{1,4}$")


def numeric_choice_lines(lines: list[str]) -> set[int]:
    """숫자만으로 된 선택지 다섯 줄의 줄 번호.

    (실측 사고) 화학Ⅰ 2025 수능 19번의 선택지는 '1 9 / 2 18 / 3 21 / 4 24 / 5 27'
    다섯 줄이다. 한 줄만 보면 쪽번호+문제지코드 줄('1 32')과 구분할 수 없어서
    should_skip_line 의 잡음 규칙이 **다섯 줄을 통째로 지웠고**, 그 문항의 choices 가
    0개가 됐다(계산 답이 숫자인 문항이라 화학·물리에서 흔하다).

    한 줄로는 못 가르지만 **1~5 가 붙어 있는 다섯 줄**이라는 배열은 쪽번호가 흉내낼 수
    없다. 그래서 줄 단위 판정 대신 배열로 보호한다. 줄 간격이 정확히 1일 때만 인정해
    표 한가운데의 숫자 행이 우연히 걸리는 것을 막는다.
    """
    pairs = [(index, int(m.group(1)))
             for index, line in enumerate(lines)
             if (m := _NUM_PAIR_LINE_RE.match(line))]
    protected: set[int] = set()
    for start in range(len(pairs) - 4):
        window = pairs[start:start + 5]
        if [label for _, label in window] != [1, 2, 3, 4, 5]:
            continue
        if window[-1][0] - window[0][0] != 4:      # 다섯 줄이 연속이어야 한다
            continue
        protected.update(index for index, _ in window)
    return protected


def clean_text(text: str, subject) -> str:
    """1번 문항이 시작하는 줄부터 남기고 머리글/꼬리글을 걷어낸다."""
    tokens = noise_tokens(subject)
    folded = folded_noise_tokens(subject)
    patterns = alias_patterns(subject)
    raw_lines = [raw_line.strip() for raw_line in text.splitlines()]
    protected = numeric_choice_lines(raw_lines)
    lines: list[str] = []
    started = False
    for index, line in enumerate(raw_lines):
        if not started:
            if re.match(r"^1\.(\s|$)", line):
                started = True
            else:
                continue
        if index not in protected and should_skip_line(line, tokens, patterns, folded):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def is_trailer_line(line: str) -> bool:
    """문항 블록 끝에 달라붙는 답안지 안내 문구."""
    flat = squash(line)
    if "선택" in line:
        return True
    if "확인 사항" in line or "답안지" in line:
        return True
    if flat in {"제[", "]", "]선택", "[", "제[]선택"}:
        return True
    return False


def trim_block(block: str) -> str:
    lines = [line.rstrip() for line in block.splitlines()]
    while lines and is_trailer_line(lines[-1].strip()):
        lines.pop()
    text = "\n".join(lines).strip()
    text = re.sub(r"\s+1\s+제\[\s*\]\s*선택\s+\d+\s+\d+\s*$", "", text)
    return text.strip()


# --------------------------------------------------------------------------
# 문항 분리
# --------------------------------------------------------------------------

def _block_score(text: str, start: int, end: int) -> int:
    """이 구간이 '문항 하나' 답게 생겼는가. 0~2점.

    두 근거 모두 **과목이 아니라 5지선다 문항의 성질**이다(absent_choice_band 와 같은 논리).
      +1 발문이 있다 — 물음표. 실측 2040문항 중 이 조건을 어긴 블록은 아래 사고로
         잘못 잘린 세 개뿐이었다.
      +1 선택지가 있다 — 라벨 1~5 가 오름차순으로 나온다.
    한쪽만으로는 못 가른다. 자료 상자 안의 번호 목록으로 잘린 조각도 뒤 문항의
    물음표나 선택지를 통째로 삼켜 한 조건은 우연히 통과한다(실측, 아래 두 사고).
    """
    block = text[start:end]
    score = 1 if "?" in block else 0
    need = 1
    for line in block.splitlines():
        for value in _inline_labels(line):
            if value == need:
                need += 1
        if need > 5:
            break
    return score + (1 if need > 5 else 0)


# 후보 조합 탐색을 포기하는 상한. 넘으면 옛 그리디로 떨어진다 —
# 느려지느니 예전과 같은 답을 내는 편이 낫다.
_SPLIT_SEARCH_BUDGET = 20000


def _pick_starts(text: str, count: int) -> list[int] | None:
    """번호 1..count 의 시작 위치를 고른다. 못 고르면 None.

    ★ 이 함수가 있는 이유(실측 사고 두 건):
      자료 상자 안의 번호 목록이 문항 시작으로 오인된다.
        2018 고1 9월 통합사회 — 근로 계약서의 '3.'~'6.' 때문에 블록 3~6 이 통째로 어긋났다.
        2019 고1 11월 통합사회 — 사막화 정리표의 '1. 원인:' 때문에 블록 1·2 가 갈렸다.
      옛 규칙('기대 번호와 같은 첫 후보를 받는다')은 이 둘을 원리적으로 못 막는다.
      가짜 번호도 기대 번호와 같기 때문이다. 크롭 쪽 컬럼 오검출과 뿌리가 같다.

    그래서 '첫 후보'가 아니라 **블록이 문항답게 생기는 조합**을 고른다.
    점수가 같으면 예전과 같은 답(가장 이른 위치)을 낸다 — 후보가 하나뿐인
    회차에서는 계산 결과가 옛 규칙과 글자 그대로 같다.
    """
    candidates: dict[int, list[int]] = {n: [] for n in range(1, count + 1)}
    total = 0
    for match in QUESTION_START_RE.finditer(text):
        number = int(match.group(1))
        if 1 <= number <= count:
            candidates[number].append(match.start())
            total += 1
    if any(not positions for positions in candidates.values()):
        return None
    if total * total > _SPLIT_SEARCH_BUDGET:
        return None

    @lru_cache(maxsize=None)
    def best(number: int, index: int) -> tuple[int, tuple[int, ...]]:
        """(number 부터 끝까지의 점수 합, 고른 위치들). 점수가 같으면 이른 쪽."""
        start = candidates[number][index]
        if number == count:
            return _block_score(text, start, len(text)), (start,)
        top: tuple[int, tuple[int, ...]] | None = None
        for next_index, next_start in enumerate(candidates[number + 1]):
            if next_start <= start:
                continue
            tail = best(number + 1, next_index)
            if not tail[1]:
                continue
            score = _block_score(text, start, next_start) + tail[0]
            # 엄격 부등호라 점수가 같으면 먼저 본 것(=더 이른 위치)이 남는다.
            if top is None or score > top[0]:
                top = (score, (start,) + tail[1])
        return top if top is not None else (-1, ())

    picked: tuple[int, tuple[int, ...]] | None = None
    for index in range(len(candidates[1])):
        found = best(1, index)
        if found[1] and (picked is None or found[0] > picked[0]):
            picked = found
    best.cache_clear()
    return list(picked[1]) if picked and picked[1] else None


def split_questions(text: str, count: int) -> dict[int, str]:
    """1..count 가 순서대로 나오는 것만 문항 시작으로 인정한다.

    본문 속 '3. ' 같은 문자열이 문항 시작으로 오인되는 것을 막는 첫 걸음이
    '기대 번호와 같을 때만 받는다'였다. 정규식만으로는 절대 안 걸러진다.
    그것만으로는 부족해서 후보가 여럿일 때 **블록이 문항답게 생기는 조합**을
    고른다 — `_pick_starts` 주석의 사고 두 건 참조.
    """
    starts = _pick_starts(text, count)
    if starts is None:
        # 조합 탐색이 답을 못 냈다. 옛 그리디로 떨어져 같은 실패 메시지를 낸다 —
        # 새 경로가 못 푸는 회차를 예전보다 나쁘게 만들지 않는다.
        matches = []
        expected = 1
        for match in QUESTION_START_RE.finditer(text):
            if int(match.group(1)) == expected:
                matches.append(match.start())
                expected += 1
                if expected > count:
                    break
        if len(matches) != count:
            raise ValueError(f"문항 분리가 {count}개가 아니다: {len(matches)}개")
        starts = matches

    blocks: dict[int, str] = {}
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        blocks[index + 1] = trim_block(text[start:end])
    return blocks


# --------------------------------------------------------------------------
# 선택지
# --------------------------------------------------------------------------

def _label_rows(lines: list[str], digit_after: bool = False) -> list[tuple[int, str, str]]:
    """(줄 번호, 정규화한 라벨, 라벨 뒤에 붙어 있던 글자) 목록.

    NFKC 정규화를 거치면 원문자 ①~⑤ 가 평문 1~5 가 되므로 둘 다 받는다.
    라벨 뒤에 공백 없이 글자가 바로 붙는 판형('1저기압 하강')이 실제로 있어서
    `(?=[^\\d\\s])` 선행 검사를 함께 쓴다. 숫자가 이어지면(10hPa) 라벨이 아니다.

    `digit_after=True` 면 그 선행 검사를 풀어 **선택지 본문이 숫자로 시작하는** 줄도
    받는다. 오탐이 크게 늘기 때문에 기본값은 False 이고, 아래 3차 시도에서만 켠다.
    """
    rows: list[tuple[int, str, str]] = []
    tight = re.compile(r"^([①②③④⑤1-5])(?:\s+|(?=[^\d\s]))(.*)$")
    loose = re.compile(r"^([①②③④⑤1-5])\s*(\S.*)$")
    for index, raw_line in enumerate(lines):
        line = compact(raw_line)
        match = re.match(r"^([①②③④⑤1-5])$", line)
        if match is None:
            match = tight.match(line)
        if match is None and digit_after:
            match = loose.match(line)
        if match is None:
            continue
        groups = match.groups()
        label = groups[0]
        remainder = (groups[1] if len(groups) > 1 else "") or ""
        normalized = str(CHOICE_TO_INT[label]) if label in CHOICE_TO_INT else label
        rows.append((index, normalized, remainder.strip()))
    return rows


def _build_choices(lines: list[str], window: list[tuple[int, str, str]]) -> list[str]:
    choices: list[str] = []
    for offset, (line_index, label, remainder) in enumerate(window):
        next_index = window[offset + 1][0] if offset + 1 < len(window) else len(lines)
        parts: list[str] = []
        if remainder:
            parts.append(remainder)
        parts.extend(lines[line_index + 1:next_index])
        cut = next((i for i, part in enumerate(parts) if is_trailer_line(part.strip())), None)
        if cut is not None:
            parts = parts[:cut]
        # 숫자만 있는 꼬리는 쪽 번호다. 단, 마지막 선택지에서만 떼어낸다 —
        # 표 판형에서는 행의 마지막 칸이 숫자('0+1', '1')인 경우가 있어
        # 모든 선택지에서 떼면 정답 후보 값을 지워 버린다.
        #
        # 두 가지 안전장치가 더 있다. 둘 다 **선택지 값 자체가 숫자인 문항**(화학·물리의
        # 계산 문항이 그렇다) 때문에 생겼다. 실측: 화학Ⅰ 2025 수능 19번 '⑤ 27' 의 27 이
        # 쪽번호로 오인돼 지워지고 ⑤ 가 빈 선택지가 됐다.
        #   (1) 라벨 줄에 붙어 있던 조각(remainder)은 떼지 않는다. 쪽번호는 언제나
        #       다음 줄에 따로 오지, '⑤' 와 같은 줄에 붙지 않는다.
        #   (2) 숫자 꼬리는 한 번만 뗀다. 쪽번호 줄은 하나뿐이다.
        last = offset + 1 == len(window)
        floor = 1 if remainder else 0
        digits_trimmed = 0
        while len(parts) > floor:
            tail = parts[-1].strip()
            if is_trailer_line(tail):
                parts.pop()
                continue
            if last and not digits_trimmed and re.fullmatch(r"\d+", squash(tail)):
                parts.pop()
                digits_trimmed += 1
                continue
            break
        choices.append(f"{INT_TO_CHOICE[int(label)]} {compact(' '.join(parts))}".strip())
    return choices


def extract_choices_from_lines(lines: list[str]) -> tuple[list[str], list[str], bool]:
    """줄 단위로 선택지 5개를 찾는다. (선택지, 앞쪽 줄들, 완화규칙사용) 을 돌려준다.

    기본 규칙: 라벨 1~5 가 **연속한 다섯 줄**에 나온다. 뒤에서부터 훑는 이유는
    본문에도 '1' 로 시작하는 줄이 얼마든지 있고 선택지는 항상 문항 맨 끝이기 때문이다.

    시도 순서는 '엄격 → 느슨' 이다. 뒤로 갈수록 오탐 위험이 커지므로 앞 규칙이
    답을 주면 절대 뒤 규칙을 보지 않는다.
      1) 엄격 라벨 · 연속 다섯 줄            (완화 표시 없음)
      2) 엄격 라벨 · 순서만 유지             (표 판형. 완화 표시)
      3) 숫자 뒤따름 허용 · 연속 다섯 줄     (사회탐구. 완화 표시)
    """
    label_rows = _label_rows(lines)

    window = _consecutive_window(label_rows)
    if window is not None:
        return _build_choices(lines, window), lines[:window[0][0]], False

    # --- 완화 규칙 -------------------------------------------------------
    # 선택지가 표로 짜인 문항(각 행이 여러 칸으로 쪼개져 나온다)에서는 라벨 사이에
    # 그 행의 나머지 칸들이 끼어들어 '연속한 다섯 줄' 규칙이 깨진다.
    #   예: '1저기압 하강' / '1' / '2저기압' / '상승' / '0+1' / ...
    # 그래서 라벨 1~5 가 순서만 지키면 받아 주는 경로를 따로 둔다.
    # 다만 이 경로로 뽑은 선택지는 본문 숫자를 라벨로 오인했을 수 있으므로
    # **반드시 표시해서** 검수 대상으로 올린다.
    chain: list[tuple[int, str, str]] = []
    cursor = -1
    for wanted in ("1", "2", "3", "4", "5"):
        # 1번은 가장 이른 후보를 고른다 — 표 행의 나머지 칸에 있는 '1' 이 아니라
        # 행 머리의 '1저기압 하강' 이 선택지 본체이기 때문이다.
        candidate = next(((i, lab, rem) for i, lab, rem in label_rows
                          if lab == wanted and i > cursor), None)
        if candidate is None:
            chain = []
            break
        chain.append(candidate)
        cursor = candidate[0]
    if chain:
        return _build_choices(lines, chain), lines[:chain[0][0]], True

    # --- 3차: 선택지 본문이 숫자로 시작하는 판형 --------------------------
    # NFKC 가 ⑤ 를 '5' 로 눕혀 놓기 때문에(textnorm 주석 참조) '⑤2단계에서…' 는
    # '52단계에서…' 가 된다. 라벨 뒤에 숫자가 오면 라벨로 안 보는 기본 규칙이
    # 여기서 정면으로 걸린다 — 사회탐구는 선택지가 숫자로 시작하는 일이 흔하다.
    # 실측(2024 수능 사회·문화): 5번 '⑤2단계에서 도출한…', 14번 '①1모둠과 2모둠이…',
    # 2025 수능 10번 '①1970년 계층 구조는…' 세 문항에서 선택지가 0개로 사라졌다.
    #
    # 그래서 '숫자가 이어져도 라벨로 본다'를 켜되, **연속 다섯 줄 규칙만** 남긴다.
    # 순서만 맞으면 받아 주는 2차 완화 규칙까지 함께 풀면 본문 표의 숫자 다섯 개가
    # 선택지로 둔갑한다. 이 경로로 뽑은 것도 검수 대상으로 표시한다.
    window = _consecutive_window(_label_rows(lines, digit_after=True))
    if window is not None:
        return _build_choices(lines, window), lines[:window[0][0]], True
    return [], lines, False


def _consecutive_window(label_rows: list[tuple[int, str, str]]):
    """라벨 1~5 가 연속한 다섯 줄로 나오는 마지막 구간. 없으면 None."""
    for start in range(len(label_rows) - 5, -1, -1):
        window = label_rows[start:start + 5]
        if [label for _, label, _ in window] == ["1", "2", "3", "4", "5"]:
            return window
    return None


# --------------------------------------------------------------------------
# 선택지가 그림인 문항
# --------------------------------------------------------------------------

# 선택지 자리가 분수·도형이면 텍스트 레이어에는 조각만 남는다. 조각의 최대 길이 —
# 실측(화학Ⅰ 2024 수능 3번 '1 /', '5 /2', 물리학Ⅰ 2024 수능 6번 '3 /2', 18번 '4 2B0')
# 은 전부 6자 이하다. 12 는 'A/2' 류가 한 줄에 다 붙어 나오는 변형까지 덮되
# 문장 한 조각('~로 옳은 것은?')은 못 들어오는 값이다.
IMAGE_FRAGMENT_MAX_LEN = 12
IMAGE_BAND_MIN_LINES = 3      # 조각이 이보다 적으면 쪽번호·꼬리글과 구분되지 않는다
# 조각 줄머리에서 살아남은 선택지 번호(1~5)의 가짓수. 2 인 이유: 실측에서 라벨이
# 통째로 사라지는 문항이 있다 — 화학Ⅰ 2024 수능 20번의 선택지 자리에는 '1 /2' '3'
# '3 /2' '9' 네 줄뿐이라 살아남은 번호가 1·3 둘이다. 3 으로 두면 이 문항이 계속
# error 로 남는다. 1 로 내리지 않는 이유는 그 자리가 선택지 자리인지 확인할 근거가
# 번호밖에 없어서다.
IMAGE_BAND_MIN_LABELS = 2
# 조각에 허용하는 글자. 한글·따옴표·물음표가 하나라도 있으면 문장이지 조각이 아니다.
_FRAGMENT_CHARS_RE = re.compile(r"^[0-9A-Za-z+\-−~×÷·/().,:;'\[\]{}<>=^_|²³½¼¾°∘Δ\s]+$")
_FRAGMENT_LABEL_RE = re.compile(r"^([1-5])(?!\d)")


def _is_image_fragment(line: str) -> bool:
    """수식·도형이 그림으로 그려질 때 텍스트 레이어에 남는 조각인가.

    한글이 한 글자라도 있으면 조각이 아니다 — 판정을 '글자 모양'이 아니라
    '문장인가'에 걸어야 오탐이 안 난다(PITFALLS 4-3). 선택지가 통째로
    그림이면 그 자리에는 문장이 남을 수 없다.
    """
    text = compact(line)
    if not text or len(text) > IMAGE_FRAGMENT_MAX_LEN:
        return False
    if re.search(r"[가-힣ㄱ-ㅎㅏ-ㅣ]", text):
        return False
    return bool(_FRAGMENT_CHARS_RE.fullmatch(text))


def image_choice_band(lines: list[str]) -> int | None:
    """선택지 자리가 그림인 문항에서 그 자리가 시작하는 줄 번호. 아니면 None.

    (실측) 화학·물리는 선택지가 분수·그래프인 문항이 회차당 2~4건 있다. 분자·분모·
    빗금이 서로 다른 줄로 흩어지고, NFKC 가 ①을 '1' 로 눕혀 놓기 때문에 분모의 '2'와
    라벨 '②'를 원리적으로 가를 수 없다 — **텍스트로는 복원이 불가능하다.** 실측 잔해:
      화학Ⅰ 2024 수능 3번  '1 /' '2' '3 /' '2' '5 /2' '9'
      물리학Ⅰ 2024 수능 17번 '2' '3' '4' '5'   (선택지가 그래프 다섯 장)
    이런 문항은 크롭 이미지가 본체라는 점에서 vision 회차와 같다(CONTRACT 4절).
    복원 불가를 error 로 올리면 검증기가 매 회차 오탐을 쏟아 신뢰를 잃는다.

    판정은 **선택지 추출이 이미 실패한 뒤에만** 부른다. 그 위에서 요구하는 근거 셋:
      1. 블록 끝이 조각 줄로만 이어질 것 — 문장이 남았다면 그건 다른 실패다.
         (답안지 안내 꼬리글은 건너뛴다. trim_block 이 떼지 못한 채 남는 회차가 있다 —
          실측 물리학Ⅰ 2024 수능 6번의 '제[' '] 선택' 이 선택지 자리 한복판을 끊었다.)
      2. 조각이 IMAGE_BAND_MIN_LINES 줄 이상일 것.
      3. 조각 줄머리에서 선택지 번호가 IMAGE_BAND_MIN_LABELS 가지 이상 보일 것 —
         번호가 하나도 안 보이면 그 자리가 선택지 자리인지 알 방법이 없다.
      4. 그 앞에 발문이 남아 있을 것 — 블록 전체가 조각이면 잃은 것이 선택지만이
         아니다. 그건 다른 실패이고 error 로 남아야 한다.
    """
    start = len(lines)
    fragments = 0
    while start > 0:
        line = lines[start - 1]
        if _is_image_fragment(line):
            fragments += 1
        elif not is_trailer_line(line):
            break
        start -= 1
    if start == 0 or fragments < IMAGE_BAND_MIN_LINES:
        return None
    labels = {m.group(1) for line in lines[start:]
              if _is_image_fragment(line) and (m := _FRAGMENT_LABEL_RE.match(compact(line)))}
    if len(labels) < IMAGE_BAND_MIN_LABELS:
        return None
    return start


# 줄 안 아무 자리에서나 선택지 라벨(1~5)을 찾는다. 줄머리만 보는 _label_rows 와 다른 이유는
# **표 판형에서는 선택지 둘이 한 줄에 들어오기 때문**이다('3 에베레스트산의 높이 4 지구의 반지름').
# 라벨 뒤에 숫자가 이어지면 라벨이 아니다 — 자료 상자의 '1930년대'가 여기서 걸러진다.
# 앞은 줄머리이거나 공백이어야 한다. '[1.5점]'의 1, '10hPa'의 0 같은 것이 라벨로 둔갑하지 않는다.
# 뒤가 줄 끝이어도 라벨로 본다 — 표 판형에서는 라벨만 있고 칸 내용은 다음 줄에 오는 줄이 섞인다
# (실측 통합과학 2025 고1 9월 10번: '3' 한 글자짜리 줄. 줄 끝을 안 받으면 이 줄에서 밴드가 끊긴다).
_INLINE_LABEL_RE = re.compile(r"(?:^|(?<=\s))([1-5])(?=\D|$)")


def _inline_labels(line: str) -> list[int]:
    """한 줄에서 왼쪽부터 읽은 선택지 라벨 번호들."""
    return [int(m.group(1)) for m in _INLINE_LABEL_RE.finditer(compact(line))]


# ---- 한 줄에 라벨이 여럿인 선지 밴드 ---------------------------------------
#
# 이 밴드에는 **두 판형이 겹쳐 있다.** 겉모습이 같아서 예전에는 둘을 한꺼번에
# '그림'으로 덮었다.
#   (A) 단일 열인데 지면이 좁아 한 줄에 선지가 둘씩 들어간 것 — 한국 탐구 시험지에서
#       가장 흔한 판형이다(ㄱㄴㄷ 조합형). 라벨과 내용이 텍스트에 전부 남아 있으므로
#       **복원해야 맞다.** 이걸 '그림'이라고 적으면 고칠 수 있는 파싱 실패가 조용해진다.
#   (B) 2~3열 표 — 열마다 뜻이 다르다(A열/B열, ㄱ/ㄴ). 한 줄로 펴면 열 경계가 사라져
#       **틀린 선지 다섯 개**가 만들어진다. 이쪽만 복원 불가다.
#
# 아래 세 함수가 그 경계다. `_cut_choice_band` 가 밴드를 조각내고,
# `flatten_choice_band` 가 (A)임을 **증명될 때만** 편다. `table_choice_band` 는
# (B)라는 **적극적 증거**가 있을 때만 표시한다. 어느 쪽도 증명이 안 되면
# 복원도 표시도 하지 않는다 — error 로 남는 편이 낫다.

# 조합·나열형 선지의 항목 이름. 'ㄱ' 'A' '갑' '(가)' 처럼 **한 글자짜리 지시 기호**만 받는다.
# 이 좁음이 곧 증명이다 — 표의 한 칸은 낱말이지 지시 기호가 아니고,
# 표의 한 행은 칸을 **공백**으로 잇지 쉼표나 화살표로 잇지 않는다.
_ITEM_TOKEN = r"(?:\([가-힣]\)|[ㄱ-ㅎ]|[A-Za-z]|[가-힣])"
# 'ㄱ' 'ㄱ, ㄴ' '갑, 을' 'A' — 쉼표(가운뎃점)로 이은 조합형.
_COMBINATION_RE = re.compile(rf"^{_ITEM_TOKEN}(?:\s*[,·]\s*{_ITEM_TOKEN})*$")
# '(가) → (나) → (다)' — 화살표로 이은 순서 나열형.
_SEQUENCE_RE = re.compile(rf"^{_ITEM_TOKEN}(?:\s*[→⇒]\s*{_ITEM_TOKEN})+$")
# 표의 열 이름 줄('A B C A B C', 'A B', 낱줄로 흩어진 'ㄱ' 'ㄴ' 'ㄱ' 'ㄴ')의 토큰.
_HEADER_TOKEN_RE = re.compile(rf"^{_ITEM_TOKEN}$")


def _cut_choice_band(lines: list[str]) -> tuple[int, list[str]] | None:
    """블록 끝의 선지 밴드를 (시작 줄, 조각 다섯) 으로 자른다. 못 자르면 None.

    자르기가 옳다는 것을 **스스로 증명하는 조건**만 통과시킨다. 하나라도 어긋나면
    None 이다 — 잘못 편 선지 다섯 개는 표시 오탐보다 훨씬 나쁘기 때문이다.
      1. 밴드는 블록 **끝**에서 라벨을 품은 줄로만 이어진다(답안지 꼬리글은 건너뛴다).
      2. 밴드를 한 줄로 이었을 때 라벨 1~5 가 **순서대로 정확히 한 번씩** 나온다.
         한 번이라도 더 나오거나('④ 2배' 의 2 같은 것) 빠지면 어디서 끊을지 알 수 없다.
      3. 밴드는 라벨 1 로 시작한다. 앞에 글자가 남아 있으면 그건 선지가 아닌 무언가다.
      4. 다섯 조각이 모두 비어 있지 않다.
      5. 조각을 도로 이으면 원문과 **글자 그대로** 같다 — 없던 글자를 만들지 않는다.
    """
    start = len(lines)
    while start > 0:
        line = lines[start - 1]
        if not _inline_labels(line) and not is_trailer_line(line):
            break
        start -= 1
    if start == 0 or start == len(lines):
        return None
    blob = " ".join(lines[start:]).strip()
    marks = list(_INLINE_LABEL_RE.finditer(blob))
    if [int(m.group(1)) for m in marks] != [1, 2, 3, 4, 5]:
        return None
    if marks[0].start() != 0:
        return None
    parts, rebuilt = [], ""
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(blob)
        rebuilt += blob[mark.start():end]
        parts.append(blob[mark.end():end].strip())
    if rebuilt != blob or not all(parts):
        return None
    # 조각은 원문을 **자른 것**이지 만든 것이 아니다. 자르기만 하는 한 이 검사는
    # 언제나 통과한다 — 그래서 두는 것이다. 나중에 누가 조각에 손질(띄어쓰기 보정 따위)을
    # 넣으면 여기서 곧바로 걸린다. 틀린 선지 다섯 개는 조용히 만들어지기 때문이다.
    if any(part not in blob for part in parts):
        return None
    return start, parts


def _column_header_above(lines: list[str], band_start: int) -> bool:
    """밴드 바로 위가 표의 **열 이름 줄**인가 — 열 구조의 적극적 증거.

    (실측) 2~3열 선지 표는 열 이름을 밴드 바로 위에 찍고, 좌우로 나란히 놓인
    묶음 수만큼 그것을 되풀이한다.
      통합과학 2022 고1 9월 4번  : `A B C A B C`      (3열 × 2묶음)
      통합과학 2025 고1 9월 10번 : `A` `B` `A B`      (2열, 줄이 쪼개져 나온다)
      통합과학 2025 고1 9월 17번 : `ㄱ` `ㄴ` `ㄱ` `ㄴ` (2열 × 2묶음)
    그래서 '한 글자 지시 기호가 주기적으로 되풀이된다'를 근거로 삼는다. 되풀이가
    핵심이다 — 그림 범례의 `A B C D E` 한 줄은 주기가 없어 여기서 걸러진다.
    """
    tokens: list[str] = []
    index = band_start
    while index > 0:
        parts = lines[index - 1].split()
        if not parts or not all(_HEADER_TOKEN_RE.match(p) for p in parts):
            break
        tokens = parts + tokens
        index -= 1
    if len(tokens) < 2:
        return False
    for size in range(2, len(tokens) + 1):
        if len(tokens) % size:
            continue
        base = tokens[:size]
        if len(set(base)) == size and tokens == base * (len(tokens) // size):
            return True
    return False


def _shares_vocabulary(parts: list[str]) -> bool:
    """다섯 조각이 **모두** 다른 조각과 낱말을 공유하는가 — 순열표의 지문.

    2~3열 표는 작은 낱말 묶음의 순열이다(수권/지권/생물권, 감소/증가/일정).
    그래서 어느 행을 집어도 다른 행과 낱말이 겹친다. 단일 열 선지 다섯 개가
    다섯 개 모두 서로 낱말을 겹치는 일은 조합형(ㄱ/ㄴ/ㄷ)뿐인데, 그쪽은
    `_COMBINATION_RE` 가 먼저 '한 칸짜리'임을 증명해 준다.
    """
    # 낱말에 붙은 구두점은 떼고 센다. 안 떼면 'ㄱ,' 과 'ㄱ' 이 남남이 되어
    # **겹침을 놓치고**, 놓친 쪽이 곧 '복원해도 된다'는 판정이라 위험한 방향으로 틀린다.
    vocab = [{w.strip(",.·;:()[]") for w in part.split()} - {""} for part in parts]
    return all(any(this & other for index2, other in enumerate(vocab) if index2 != index)
               for index, this in enumerate(vocab))


def flatten_choice_band(lines: list[str]) -> tuple[int, list[str]] | None:
    """한 줄에 라벨이 여럿인 **단일 열** 선지를 원문 그대로 편다. 못 펴면 None.

    `_cut_choice_band` 의 다섯 조건에 더해, 그 밴드가 표가 **아니라는** 증명을 요구한다.
    둘 중 하나면 된다.
      (a) 다섯 조각이 전부 조합형·나열형이다(`ㄱ, ㄴ` `갑, 을` `A` `(가) → (나) → (다)`).
          표의 한 행은 칸을 공백으로 잇는다 — 쉼표·화살표로 이어진 조각은 통째로 한 칸이다.
      (b) 열 이름 줄이 없고(`_column_header_above`) 조각들이 순열표의 지문
          (`_shares_vocabulary`)을 보이지 않는다. 열 구조의 증거가 양쪽 다 없다.
    둘 다 못 세우면 편집하지 않는다. 틀린 선지 다섯 개를 조용히 만드는 것보다
    error 로 남기는 편이 낫다.
    """
    cut = _cut_choice_band(lines)
    if cut is None:
        return None
    start, parts = cut
    if all(_COMBINATION_RE.match(p) or _SEQUENCE_RE.match(p) for p in parts):
        return start, parts
    if not _column_header_above(lines, start) and not _shares_vocabulary(parts):
        return start, parts
    return None


def table_choice_band(lines: list[str]) -> int | None:
    """선택지가 **2~3열 표**라 텍스트로 복원할 수 없는 문항의 밴드 시작 줄. 아니면 None.

    (실측) 통합과학에는 선택지가 2~3열 표인 문항이 있다. 텍스트 레이어에서는 한 줄에
    선택지가 둘씩 들어온다(`1 염화 나트륨 설탕 2 염화 나트륨 염화 칼륨`, `1감소 감소 2감소 증가`).
    표는 열마다 뜻이 다르므로 한 줄 문자열로 펴면 열이 사라져 **틀린 선택지 다섯 개**가
    만들어진다. 그래서 복원하지 않고 크롭 이미지를 본체로 삼는다.

    ★ 이 규칙은 한 번 넓게 잡혔다가 좁혀진 자리다(실측). 예전 판은 '블록 끝이 라벨을
    품은 줄로 이어지고 라벨이 오름차순'이면 표시했는데, 그 모양은 **단일 열 선지가
    한 줄에 둘씩 들어간 흔한 판형**과 구별되지 않는다. 통합과목 1,220문항에서 표시 36건
    중 28건이 그 오탐이었고, 잃을 열이 없는 문항들이 error 에서 warn 으로 내려앉아
    **고칠 수 있는 파싱 실패가 조용해졌다.** 표시는 넓히면 결국 아무도 안 본다.

    그래서 이제 **열 구조의 적극적 증거 둘을 모두** 요구한다. 어느 하나만으로는 못 가른다 —
    열 이름처럼 보이는 짧은 줄은 그림 범례일 수 있고, 낱말이 겹치는 것은 ㄱㄴㄷ 조합형도
    마찬가지다.
      1. 밴드가 라벨 1~5 로 정확히 잘린다(`_cut_choice_band`).
      2. 밴드 바로 위에 **열 이름 줄**이 있다(`_column_header_above`).
      3. 다섯 조각이 **작은 낱말 묶음의 순열**이다(`_shares_vocabulary`).

    ⚠ 일부러 안 덮는 것 둘.
      · 라벨과 칸 내용이 **줄마다 따로** 떨어지는 표(지구과학Ⅱ 2027 6월모평 2번 —
        `1` / `조력 발전` / `파력 발전` / … 3열 표). 라벨이 다섯 번 이상 나와 1번에서 걸린다.
        그 문항은 **라벨 간격이 일정해 표 파서로 복원할 여지가 남아 있다** — 복원할 수 있는
        것을 '그림'이라고 적으면 고칠 수 있는 버그를 덮는다(boxed_source 주석과 같은 원칙).
      · 라벨 한 칸이 텍스트 레이어에서 통째로 사라진 문항(통합사회 2024 고1 6월 18번은
        ③ 줄이 없다). 나머지 넷은 멀쩡한 텍스트라 '이 칸의 본체가 그림'이라는 말이 거짓이 된다.
        error 로 남겨 사람이 보게 한다.
    """
    cut = _cut_choice_band(lines)
    if cut is None:
        return None
    start, parts = cut
    if not _column_header_above(lines, start):
        return None
    if not _shares_vocabulary(parts):
        return None
    return start


def absent_choice_band(lines: list[str]) -> int | None:
    """선택지 자리가 텍스트 레이어에 **통째로 없는** 문항. 밴드 시작(=끝) 줄, 아니면 None.

    (실측) 통합사회 2024 고1 10월 4번은 선택지 다섯 개가 벡터로 그려져 텍스트가 0자다.
    블록에는 발문과 자료 문장만 남고 라벨이 하나도 없다. 조각조차 남지 않으므로
    image_choice_band 의 '조각 줄' 근거로는 원리적으로 못 잡는다.

    판정 근거는 **모순 하나**다 — 답을 고르라고 묻는 발문이 있는데 ⑤ 가 블록 어디에도 없다.
    ⑤ 를 축으로 잡은 이유: 5지선다의 마지막 라벨이라 선택지가 조금이라도 살아 있으면
    반드시 남는다. 반대로 '1 이 없다'는 근거로는 못 쓴다 — 자료 상자의 숫자가 늘 1을 흉내낸다.
    실제로 이 문항의 자료에는 '3만 원', '2만 원', '4만 원'이 있어서 느슨한 라벨 대조로는
    라벨이 있는 것처럼 보인다. 5 만 없다.

    발문 존재를 물음표로 확인한다. 과목이 아니라 **선다형 문항의 성질**이라 하드코딩이 아니다
    — 이 판정이 없으면 문항 분리가 어긋나 반쪽만 담긴 블록까지 '그림'으로 덮어버린다.
    """
    if not any(5 in _inline_labels(line) for line in lines):
        if any("?" in line for line in lines):
            return len(lines)
    return None


def choices_image_band(lines: list[str]) -> int | None:
    """선택지를 텍스트로 복원할 수 없는 문항인가. 맞으면 발문이 끝나는 줄 번호.

    세 판정을 **좁은 것부터** 차례로 본다. 앞의 것이 답을 주면 뒤를 보지 않는다.
      1. image_choice_band  — 분수·도형 조각만 남은 판형 (화학·물리)
      2. table_choice_band  — 선택지가 2~3열 표인 판형 (통합과학)
      3. absent_choice_band — 선택지 자리가 통째로 텍스트에 없는 판형

    ★ 부르는 쪽(parse_question)은 이 함수보다 **먼저** flatten_choice_band 를 시도한다.
      복원할 수 있는 것을 '그림'이라고 적으면 고칠 수 있는 파싱 실패가 조용해진다.
    """
    for finder in (image_choice_band, table_choice_band, absent_choice_band):
        band = finder(lines)
        if band is not None:
            return band
    return None


def split_boxed(statement_part: str) -> str:
    """<보기> 뒤쪽을 항목 단위로 줄바꿈해 둔다. 라벨을 못 찾으면 통짜로 둔다."""
    flat = compact(statement_part)
    matches = list(SECTION_LABEL_RE.finditer(flat))
    if len(matches) < 2:
        return flat
    segments = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(flat)
        segments.append(flat[match.start():end].strip())
    return "\n".join(segments)


def _boxed_label_pos(stem_blob: str) -> int | None:
    """'<보기>' 중 **상자 머리표**인 것의 위치. 없으면 None.

    발문 문장에도 '<보기>에서 (있는 대로) 고른 것은?' 이 들어가므로 예전에는 그냥
    마지막 것에서 잘랐다. 그러면 상자 머리표가 텍스트 레이어에 없는 문항에서
    **발문 한복판이 잘린다** — 2025학년도 수능 한국지리 15번이 그랬다. 자료 상자와
    <보 기> 상자가 통째로 벡터로 그려져 텍스트가 없어서, 발문 안의 '<보기>에서' 가
    마지막 후보가 됐고 그 뒤의 '(단, …) [3점]' 이 boxed 로 밀려났다. 그 결과
    validate 의 '[N점] 표기 ↔ points' 대조가 **정상 문항을 error 로 잡았다.**

    가르는 규칙은 조사(助詞) 하나면 충분하다. 상자 머리표는 홀로 서고, 발문 안의
    것은 반드시 '에'(에서/에게)가 곧바로 붙는다. 좁게 잡는다 — 손상 탐지 패턴은
    넓게 잡았다가 좁히면 이미 신뢰를 잃은 뒤다(PITFALLS 4-3).
    """
    positions = [m.start() for m in re.finditer(re.escape("<보기>"), stem_blob)]
    for pos in reversed(positions):
        if not stem_blob[pos + len("<보기>"):].startswith("에"):
            return pos
    return None


def boxed_source(stem: str, boxed: str) -> str:
    """<보기> 상자가 그림이라 텍스트에 없으면 "image", 아니면 "".

    (실측) 2025학년도 수능 한국지리는 자료 표와 <보 기> 상자가 통째로 벡터로 그려져
    텍스트 레이어에 없다. 15번이 그런 문항인데 `extraction_mode` 는 direct 이고
    text.boxed 만 빈다 — 회차 단위 모드로는 표현할 수 없는 **문항 단위 부분 손실**이다.

    판정 근거는 **발문이 상자를 가리키는데 상자가 없다**는 모순 하나뿐이다.
    '자료 이미지가 있는데 boxed 가 비었다'는 근거로 삼지 않았다. 실측으로 반증된다 —
    2024학년도 수능 한국지리 20문항 중 17문항이 '자료 이미지 있음 + boxed 빔'인데
    그중 16문항은 애초에 <보기> 상자가 없는 정상 문항이다(지도·그래프 자료가 본체).
    그 근거를 쓰면 정상 문항 열여섯 개에 거짓 표시를 달게 된다.

    보기 항목(ㄱ.ㄴ.ㄷ.)이 발문 쪽에 살아 있으면 표시하지 않는다. 그건 상자가
    그림인 것이 아니라 상자 머리표만 못 찾아 내용이 발문에 섞인 것이라, 고칠 데가
    파싱 쪽에 있다 — 그림이라고 적으면 고칠 수 있는 버그를 덮는다.
    """
    if boxed.strip():
        return ""
    if not re.search(re.escape("<보기>"), stem or ""):
        return ""
    if len(SECTION_LABEL_RE.findall(stem)) >= 2:
        return ""
    return "image"


def parse_question(block: str, choice_count: int = 5) -> ParsedQuestion:
    """한 문항 블록 → 발문 / <보기> / 선택지."""
    block = trim_block(block)
    body = re.sub(r"^\d+\.\s*", "", block, count=1).strip()
    lines = [compact(line) for line in body.splitlines() if compact(line)]
    choices, stem_lines, relaxed = extract_choices_from_lines(lines)
    choices_source = ""
    choices_layout = ""
    if len(choices) != choice_count:
        # ★ 복원을 먼저 시도한다. 그림 판정보다 앞이어야 한다 —
        #   순서가 뒤집히면 '텍스트에 다 남아 있는 선지'가 그림으로 덮여
        #   고칠 수 있는 파싱 실패가 조용해진다(table_choice_band 주석의 그 사고).
        flat = flatten_choice_band(lines)
        if flat is not None:
            band, parts = flat
            choices = [f"{INT_TO_CHOICE[index]} {part}" for index, part in enumerate(parts, 1)]
            stem_lines, relaxed, choices_layout = lines[:band], False, "flattened"
    if len(choices) != choice_count:
        # 선택지가 그림·표인 문항이면 실패가 아니다 — 선택지 자리만 비우고 발문은 살린다.
        # 발문까지 버리면 '[N점] 표기가 없음' 같은 파생 오탐이 문항마다 하나씩 더 붙는다.
        band = choices_image_band(lines)
        if band is None:
            return ParsedQuestion(raw=body,
                                  error=f"선택지 {choice_count}개를 추출하지 못했다({len(choices)}개)")
        choices, stem_lines, relaxed = [], lines[:band], False
        choices_source = "image"
        choices_layout = ""

    stem_blob = compact(" ".join(stem_lines))
    stem, boxed = stem_blob, ""
    cut = _boxed_label_pos(stem_blob)
    if cut is not None:
        stem = stem_blob[:cut].strip()
        boxed = split_boxed(stem_blob[cut + len("<보기>"):])
    warning = "선택지를 완화 규칙(표 판형)으로 뽑았다 — 검수 필요" if relaxed else ""
    return ParsedQuestion(stem=stem, boxed=boxed, choices=choices, raw=body,
                          warning=warning, choices_source=choices_source,
                          choices_layout=choices_layout,
                          boxed_source=boxed_source(stem, boxed))


# --------------------------------------------------------------------------
# 배점
# --------------------------------------------------------------------------

def points_from_marks(blocks: dict[int, str], count: int,
                      points_total: int | None) -> tuple[dict[int, float], str]:
    """[N점] 표기와 총점으로 문항별 배점을 만든다.

    배점 값을 코드에 박지 않으려고 역산한다. **다만 역산이 성립하는 판형과 아닌 판형이
    갈린다.** 실측한 두 판형이 정확히 그 경계다 —

      20문항 판형(탐구 전 과목, 통합과목 2023~2025.3): `[3점]` 10개 + 표기 없는 10개.
        표기 없는 것이 기본 배점(2점)이라는 가정이 성립한다. 계단은 두 개다.
      25문항 판형(통합과목 2025.6~): `[1.5점]`×8 + `[2점]`×9 + `[2.5점]`×8 = 50.
        **표기 없는 문항이 하나도 없다.** 계단이 셋이면 '표기 없음 = 기본 배점' 이라고
        부를 기본이 애초에 없어서, 출제자가 25문항 전부에 표기를 붙였다.
        (2025·2026 6개 회차 문제지 원본 실측. 세 계단 회차에서 표기 수는 언제나 25/25였다.)

    그래서 역산은 **표기가 없는 문항이 있을 때만** 한다. 그리고 그때는 표기값이 한 가지여야
    한다 — 표기가 여러 계단인데 표기 없는 문항이 남아 있으면, 그 문항이 어느 계단인지
    말해 주는 것이 아무것도 없다. 그 자리에서 평균을 채우면 정확히 이번 사고가 된다.

    ★ 이번에 막는 조용한 실패(실측): 옛 정규식이 `[1.5점]`·`[2.5점]` 16개를 못 읽어
    `[2점]` 9개만 표기로 남았고, 나머지 16문항을 (50-18)/16 = **2점**으로 역산해 채웠다.
    합 50 · 문항 25 · 표기↔배점 대조까지 전부 통과해서 아무 신호도 안 났다.
    아래 '역산한 기본 배점이 표기값과 같으면 포기' 규칙이 그 사고를 값싸게 잡는다 —
    기본 배점과 표기값이 같으면 그 표기는 아무 정보도 없는 표기이고, 그런 문제지는 없다.

    틀린 배점을 채우느니 비워 두는 편이 낫다. 못 세우면 이유를 돌려주고 포기한다.
    """
    marks: dict[int, float] = {}
    for number, block in blocks.items():
        value = read_point_mark(block or "")
        if value is not None:
            marks[number] = value
    if not blocks:
        return {}, "문항 블록이 없다"
    if points_total is None:
        return {}, "subject.points_total 이 없어 기본 배점을 역산할 수 없다"

    marked_total = sum(marks.values())
    # --- (A) 전 문항에 표기가 있는 판형: 역산할 것이 없다 -------------------
    if len(marks) >= count:
        if not points_equal(marked_total, points_total):
            return {}, (f"표기가 {len(marks)}/{count}문항에 다 있는데 합이 총점과 다르다 "
                        f"({normalize_points(marked_total)} != {points_total}) — 표기를 잘못 읽었다")
        return {n: normalize_points(marks[n]) for n in sorted(marks)}, ""

    # --- (B) 일부만 표기된 판형: 표기 없는 것을 기본 배점으로 본다 ----------
    tiers = sorted({normalize_points(v) for v in marks.values()})
    if len(tiers) > 1:
        # 표기 계단이 둘 이상인데 표기 없는 문항이 남았다. 그 문항이 어느 계단인지
        # 알 길이 없다 — 여기서 평균을 채우면 '조용히 틀린 값'이 된다.
        return {}, (f"표기 계단이 {tiers} 인데 표기 없는 문항이 {count - len(marks)}개 남았다 "
                    f"— 기본 배점을 정할 근거가 없다")
    remaining_questions = count - len(marks)
    remaining_points = points_total - marked_total
    if remaining_questions <= 0 or remaining_points <= 0:
        return {}, f"배점 역산 실패(표기 {len(marks)}개, 합 {normalize_points(marked_total)})"
    base = remaining_points / remaining_questions
    if base <= 0 or not on_point_grid(base):
        return {}, (f"기본 배점이 배점 격자(0.5점)에 떨어지지 않는다 "
                    f"({normalize_points(remaining_points)}/{remaining_questions} = {base})")
    if tiers and points_equal(base, tiers[0]):
        return {}, (f"역산한 기본 배점이 표기값과 같다({normalize_points(base)}) "
                    f"— 표기를 못 읽고 있다는 뜻이다(소수 배점 표기를 확인하라)")
    base = normalize_points(base)
    return {n: normalize_points(marks.get(n, base)) for n in range(1, count + 1)}, ""
