# -*- coding: utf-8 -*-
"""tamgu-1q1block 판형 구현 — 탐구 영역 표준 2단 편집, 문항 하나가 한 블록.

지구과학Ⅱ 19회차 380문항을 실제로 통과시킨 알고리즘을 옮긴 것이다.
**검증된 것은 다시 발명하지 않는다.** 대신 과목 이름이 코드에 박혀 있던 자리를
전부 subject.json 에서 오는 값으로 바꿨다.

원본에서 과목 하드코딩이었던 것들:
  '과학탐구영역'  → subject.area + '영역'
  '지구과학I/II'  → subject.label + providers.kice.aliases
  20문항 / 50점   → subject.question_count / subject.points_total
  배점 {2,3}      → [N점] 표기와 총점에서 역산 (아래 points_from_marks 참조)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textnorm import CHOICE_TO_INT, INT_TO_CHOICE, compact, squash

# 문항 시작: 줄머리의 'N.' — 두 자리까지.
QUESTION_START_RE = re.compile(r"(?m)^(\d{1,2})\.(?:\s|$)")
# <보기> 안의 항목 라벨. NFKC 를 거치면 ㉠㉡㉢ 도 여기로 눕는다.
SECTION_LABEL_RE = re.compile(r"(?<![가-힣])([ㄱ-ㅎ])\.")
# 배점 표기. 값을 숫자로 뽑는다 — '3점'을 코드에 박지 않기 위해서다.
POINT_MARK_RE = re.compile(r"\[\s*(\d)\s*점\s*\]")


@dataclass
class ParsedQuestion:
    stem: str = ""
    boxed: str = ""
    choices: list[str] = field(default_factory=list)
    raw: str = ""
    error: str = ""
    warning: str = ""


# --------------------------------------------------------------------------
# 잡음 제거 — 여기 있는 규칙은 전부 실제 문제지에서 본 머리글/꼬리글이다
# --------------------------------------------------------------------------

def noise_tokens(subject) -> set[str]:
    """머리글/세로 제목이 글자 단위로 쪼개져 나올 때 버릴 토큰들.

    2단 편집 문제지의 세로 제목은 텍스트 레이어에서 '지' '구' '과' '학' 처럼
    한 글자씩 별도 줄로 나온다. 과목 이름을 코드에 적는 대신
    subject.json 의 label/aliases 에서 글자를 만들어 낸다.
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
    if subject.area:
        tokens.add(squash(subject.area))
        tokens.add(squash(subject.area) + "영역")
    return {t for t in tokens if t}


def alias_patterns(subject) -> list[re.Pattern]:
    """'2(지구과학II)' 같은 쪽 번호 꼬리글을 잡는 정규식."""
    names = [subject.label or ""]
    kice = (subject.providers or {}).get("kice") or {}
    names.extend(kice.get("aliases") or [])
    patterns = []
    for name in names:
        flat = squash(name)
        if flat:
            patterns.append(re.compile(rf"^\d+\({re.escape(flat)}\)$"))
    return patterns


def should_skip_line(line: str, tokens: set[str], patterns: list[re.Pattern]) -> bool:
    """문제지 머리글/꼬리글이면 True."""
    if not line:
        return True
    flat = squash(line)
    if flat in tokens:
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
    if any(p.match(flat) for p in patterns):
        return True
    # '1 32' 처럼 쪽 번호와 문제지 코드만 있는 줄. 숫자 토큰이 2개 이상인 줄은 본문일 수 없다
    # (선택지가 원문자 없이 숫자만으로 한 줄에 나오는 판형은 토큰 5개라 이 규칙에 걸리지만,
    #  그 판형은 아래 선택지 파서가 줄 단위로 따로 처리하므로 여기 오기 전에 걸러진다).
    if re.fullmatch(r"(?:\d+[ \t]*){2,3}", line.strip()):
        return True
    return False


def clean_text(text: str, subject) -> str:
    """1번 문항이 시작하는 줄부터 남기고 머리글/꼬리글을 걷어낸다."""
    tokens = noise_tokens(subject)
    patterns = alias_patterns(subject)
    lines: list[str] = []
    started = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not started:
            if re.match(r"^1\.(\s|$)", line):
                started = True
            else:
                continue
        if should_skip_line(line, tokens, patterns):
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

def split_questions(text: str, count: int) -> dict[int, str]:
    """1..count 가 순서대로 나오는 것만 문항 시작으로 인정한다.

    본문 속 '3. ' 같은 문자열이 문항 시작으로 오인되는 것을 막는 유일한 방법이
    '기대 번호와 같을 때만 받는다'였다. 정규식만으로는 절대 안 걸러진다.
    """
    matches = []
    expected = 1
    for match in QUESTION_START_RE.finditer(text):
        if int(match.group(1)) == expected:
            matches.append(match)
            expected += 1
            if expected > count:
                break
    if len(matches) != count:
        raise ValueError(f"문항 분리가 {count}개가 아니다: {len(matches)}개")

    blocks: dict[int, str] = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks[int(match.group(1))] = trim_block(text[start:end])
    return blocks


# --------------------------------------------------------------------------
# 선택지
# --------------------------------------------------------------------------

def _label_rows(lines: list[str]) -> list[tuple[int, str, str]]:
    """(줄 번호, 정규화한 라벨, 라벨 뒤에 붙어 있던 글자) 목록.

    NFKC 정규화를 거치면 원문자 ①~⑤ 가 평문 1~5 가 되므로 둘 다 받는다.
    라벨 뒤에 공백 없이 글자가 바로 붙는 판형('1저기압 하강')이 실제로 있어서
    `(?=[^\\d\\s])` 선행 검사를 함께 쓴다. 숫자가 이어지면(10hPa) 라벨이 아니다.
    """
    rows: list[tuple[int, str, str]] = []
    for index, raw_line in enumerate(lines):
        line = compact(raw_line)
        match = re.match(r"^([①②③④⑤1-5])$", line)
        if match is None:
            match = re.match(r"^([①②③④⑤1-5])(?:\s+|(?=[^\d\s]))(.*)$", line)
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
        last = offset + 1 == len(window)
        while parts and (is_trailer_line(parts[-1].strip())
                         or (last and re.fullmatch(r"\d+", squash(parts[-1])))):
            parts.pop()
        choices.append(f"{INT_TO_CHOICE[int(label)]} {compact(' '.join(parts))}".strip())
    return choices


def extract_choices_from_lines(lines: list[str]) -> tuple[list[str], list[str], bool]:
    """줄 단위로 선택지 5개를 찾는다. (선택지, 앞쪽 줄들, 완화규칙사용) 을 돌려준다.

    기본 규칙: 라벨 1~5 가 **연속한 다섯 줄**에 나온다. 뒤에서부터 훑는 이유는
    본문에도 '1' 로 시작하는 줄이 얼마든지 있고 선택지는 항상 문항 맨 끝이기 때문이다.
    """
    label_rows = _label_rows(lines)

    for start in range(len(label_rows) - 5, -1, -1):
        window = label_rows[start:start + 5]
        if [label for _, label, _ in window] == ["1", "2", "3", "4", "5"]:
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
            return [], lines, False
        chain.append(candidate)
        cursor = candidate[0]
    return _build_choices(lines, chain), lines[:chain[0][0]], True


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


def parse_question(block: str, choice_count: int = 5) -> ParsedQuestion:
    """한 문항 블록 → 발문 / <보기> / 선택지."""
    block = trim_block(block)
    body = re.sub(r"^\d+\.\s*", "", block, count=1).strip()
    lines = [compact(line) for line in body.splitlines() if compact(line)]
    choices, stem_lines, relaxed = extract_choices_from_lines(lines)
    if len(choices) != choice_count:
        return ParsedQuestion(raw=body,
                              error=f"선택지 {choice_count}개를 추출하지 못했다({len(choices)}개)")

    stem_blob = compact(" ".join(stem_lines))
    stem, boxed = stem_blob, ""
    if "<보기>" in stem_blob:
        # 발문 문장에도 '<보기>에서 있는 대로 고른 것은?' 이 들어가므로 마지막 것으로 자른다.
        head, tail = stem_blob.rsplit("<보기>", 1)
        stem, boxed = head.strip(), split_boxed(tail)
    warning = "선택지를 완화 규칙(표 판형)으로 뽑았다 — 검수 필요" if relaxed else ""
    return ParsedQuestion(stem=stem, boxed=boxed, choices=choices, raw=body,
                          warning=warning)


# --------------------------------------------------------------------------
# 배점
# --------------------------------------------------------------------------

def points_from_marks(blocks: dict[int, str], count: int,
                      points_total: int | None) -> tuple[dict[int, int], str]:
    """[N점] 표기와 총점으로 문항별 배점을 만든다.

    배점 값을 코드에 박지 않으려고 역산한다. 표기가 붙은 문항은 표기값 N,
    나머지는 (총점 - 표기 합) / (문항 수 - 표기 수) 로 나온 기본 배점.
    이 나눗셈이 양의 정수로 떨어지지 않으면 표기를 잘못 읽은 것이므로 포기한다 —
    **틀린 배점을 채우느니 비워 두는 편이 낫다.**
    """
    marks: dict[int, int] = {}
    for number, block in blocks.items():
        match = POINT_MARK_RE.search(block or "")
        if match:
            marks[number] = int(match.group(1))
    if not blocks:
        return {}, "문항 블록이 없다"
    if points_total is None:
        return {}, "subject.points_total 이 없어 기본 배점을 역산할 수 없다"
    remaining_questions = count - len(marks)
    remaining_points = points_total - sum(marks.values())
    if remaining_questions <= 0 or remaining_points <= 0:
        return {}, f"배점 역산 실패(표기 {len(marks)}개, 합 {sum(marks.values())})"
    base, rest = divmod(remaining_points, remaining_questions)
    if rest or base <= 0:
        return {}, (f"기본 배점이 정수로 떨어지지 않는다 "
                    f"({remaining_points}/{remaining_questions})")
    return {n: marks.get(n, base) for n in range(1, count + 1)}, ""
