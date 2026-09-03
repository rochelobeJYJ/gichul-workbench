# -*- coding: utf-8 -*-
"""판형 `tamgu-1q1block` — 문항 하나가 한 블록인 탐구 영역 표준 조판.

문항 번호 토큰의 x 좌표를 클러스터링해 컬럼을 찾고, 읽기 순서(페이지→컬럼→y)로
앵커를 늘어놓은 뒤 '이 앵커부터 다음 앵커 직전까지'를 문항 영역으로 삼는다.
19회차 380문항으로 검증된 알고리즘(CSAT_WIKI/wiki_earth2/build_question_snapshots.py)을
계약 형태로 재구성한 것이다. 임계값은 실측으로 얻은 값이라 그대로 살렸다.

## 왜 '다음 앵커까지' 인가 (다른 구현과의 선택)
D:/codex_work/programs/CSAT_Clipper/core/question_extractor.py 는 선지 마커(①~⑤)의
y 분산이 작으면(CHOICE_SPREAD_THRESHOLD=25pt) '선지 줄에서 딱 끊는다'. 크롭이
군더더기 없이 예뻐지지만 **선지가 두 줄로 접히거나 선지 아래에 그림이 더 있는
문항에서 마지막 선지를 잘라 먹는다** — 이 저장소가 가장 두려워하는 사고다.
그래서 경계는 '다음 문항 앵커 직전'(잘릴 수 없는 상한)으로 잡고, 선지 마커
분석은 경계 계산이 아니라 **사후 검증 신호**로만 쓴다(croplib/qa.py).
반대로 CSAT_Clipper 에서 가져온 것도 있다: 하단 푸터 존 개념과 4면 콘텐츠
기반 여백 정리는 렌더 후 이미지 도메인에서 수행한다(croplib/imaging.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz

from .pdfdoc import Doc

COL_TOL = 45             # 같은 컬럼으로 볼 x 좌표 오차(pt)
MIN_SEG_PT = 30          # 이어지는 세그먼트 최소 높이(머리글 슬리버 방지)
MIN_FIRST_SEG_PT = 8     # 첫 세그먼트는 짧아도 살린다(한 줄짜리 꼬리 문항)
GUTTER_PAD = 10          # 다음 컬럼 시작점 바로 앞까지 잘라내기
COL_LEFT_PAD = 16        # 번호 토큰 왼쪽으로 확보할 여유(pt)
BODY_EDGE_PAD = 2.0      # 판면 좌·우 끝에서 남기는 여유(pt)
BOTTOM_MARGIN_PT = 28    # 컬럼 마지막 문항의 기본 하단 한계

# ── 하단 꼬리 쓰레기 탐지 ────────────────────────────────
# 페이지 맨 아래 '* 확인 사항' 안내 박스·쪽번호 박스·저작권 문구. 문항에 붙어
# 있으면 자르지 않고, 독립 박스일 때만 그 위에서 끊는다.
TAIL_ZONE_FRAC = 0.72    # 페이지 하단 28%만 검사
TAIL_TEXT_RE = re.compile(r"확인\s*사항|저작권|한국교육과정평가원|문제지에\s*관한")
PAGENUM_RE = re.compile(r"^[-–—]?\s*(\d{1,3})\s*[-–—]?\.?$")
PAGENUM_BOTTOM_PT = 130  # 쪽번호는 페이지 하단 130pt 안에만 존재
PAGENUM_RULE_MAX_W = 170  # 쪽번호 박스 상단 가로선 최대 폭(pt)
TAIL_RULE_LOOKUP = 60    # 꼬리 문구 위쪽에서 감싸는 가로선을 찾는 거리(pt)
TAIL_PAD = 5.0           # 절단선 여유(pt)

# ── 컬럼 구분 세로 실선 ──────────────────────────────────
RULE_MAX_W_PT = 3.0      # PDF 도형 기준 '얇은' 세로선 폭
RULE_MIN_H_FRAC = 0.5    # 페이지 높이의 50% 이상 관통
RULE_CLEAR_PT = 1.5      # 실선 바깥쪽으로 확보할 여유(pt)

# ── 여백 과목 탭(회색 세로 박스) ─────────────────────────
# 평가원 문제지는 첫 장 바깥 여백에 과목 표시 세로 탭을 회색 채움 박스로 인쇄한다.
# 판면 바깥이므로 크롭에서 제외한다. 과목명 문자열이 아니라 '바깥 여백에 있는
# 좁고 긴 무채색 박스'라는 기하 조건으로만 판별한다(과목 하드코딩 금지).
TAB_MIN_H_PT = 40.0
TAB_MAX_W_PT = 60.0
TAB_GRAY_LO = 0.30       # 무채색 채움 밝기 하한(검정 박스 제외)
TAB_GRAY_HI = 0.97       # 상한(흰 박스 제외)
TAB_MARGIN_FRAC = 0.10   # 페이지 좌/우 바깥 10% 여백 안에 완전히 들어와야 함

# ── 다음 문항 시작선 보정 ────────────────────────────────
# 다음 문항 첫 줄에 분수(분자가 번호 토큰보다 위로 솟음)나 위첨자가 있으면
# 번호 토큰 y 만으로 자를 때 그 윗부분이 크롭에 침입한다.
NEXT_RAISE_PT = 20.0     # 번호 토큰 위쪽으로 살펴볼 거리(pt)
NEXT_OVERLAP_PT = 4.0    # 번호 토큰 아래까지 이만큼 내려와야 '같은 줄'로 인정
ANCHOR_LIFT_PT = 5.0     # 앵커 세그먼트 상단 여유

_NUM_STRICT = re.compile(r"^(\d{1,2})\.$")
_NUM_LOOSE = re.compile(r"^(\d{1,2})\.(?=\D|$)")


@dataclass
class Segment:
    """문항이 차지하는 (페이지, 컬럼, 사각형) 조각. 컬럼/페이지를 넘어가면 여러 개."""
    page: int          # 0-based
    col: int
    rect: fitz.Rect


@dataclass
class QuestionPlan:
    number: int
    segments: list[Segment] = field(default_factory=list)


@dataclass
class ExamPlan:
    columns: list[float]
    rules: list[tuple[float, float]]
    tabs: dict[int, list[tuple[float, float]]]
    questions: list[QuestionPlan]
    missing: list[int]
    duplicated: list[int]


# ══════════════════════════════════════════════════════════
# 문항 번호 앵커 / 컬럼
# ══════════════════════════════════════════════════════════
def number_tokens(doc: Doc, max_number: int, loose: bool = False) -> list[tuple[int, float, float, int]]:
    """[(page_idx, x, y, n)] — 'n.' 형태 워드.

    strict 는 워드 하나가 통째로 'n.' 인 것만 인정한다. 번호와 발문이 한 워드로
    붙어 나오는 판형(제목 줄이 따로 있는 조판)을 위해 loose 재시도를 둔다.
    loose 는 오탐(본문 속 '3.5' 같은 것)이 늘어나므로 strict 가 실패했을 때만 쓴다.
    """
    pat = _NUM_LOOSE if loose else _NUM_STRICT
    out = []
    for pidx in range(len(doc)):
        for w in doc.words(pidx):
            m = pat.match(w[4])
            if m and 1 <= int(m.group(1)) <= max_number:
                out.append((pidx, w[0], w[1], int(m.group(1))))
    return out


def detect_columns(tokens) -> list[float]:
    """번호 토큰 x 좌표를 클러스터링해 컬럼 시작 x 목록을 만든다.

    컬럼 수를 '페이지 절반' 같은 상수로 가정하지 않는 이유: 실제 문제지는 A3 2단이
    기본이지만 판형이 바뀌면 1단·3단도 나온다. 토큰 클러스터링은 그때도 그대로
    동작한다(CSAT_Clipper 의 detect_column_layout 은 mid_x=page_width/2 를 전제해
    이 조건에서 무너진다).
    """
    xs = sorted({round(t[1]) for t in tokens})
    if not xs:
        return []
    cols, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] <= COL_TOL:
            cur.append(x)
        else:
            cols.append(sum(cur) / len(cur))
            cur = [x]
    cols.append(sum(cur) / len(cur))
    return cols


def nearest_col(cols: list[float], x: float) -> int:
    return min(range(len(cols)), key=lambda i: abs(cols[i] - x))


def detect_column_rules(doc: Doc) -> list[tuple[float, float]]:
    """페이지 과반에 반복 등장하는 얇고 긴 세로 실선(=컬럼 구분선)의 x 구간."""
    per_page: list[set[int]] = []
    spans: dict[int, list[tuple[float, float]]] = {}
    for pidx in range(len(doc)):
        page = doc.page(pidx)
        min_h = page.rect.height * RULE_MIN_H_FRAC
        found = set()
        for d in page.get_drawings():
            r = d["rect"]
            if r.width <= RULE_MAX_W_PT and r.height >= min_h:
                k = int(round((r.x0 + r.x1) / 2.0))
                found.add(k)
                spans.setdefault(k, []).append((r.x0, r.x1))
        per_page.append(found)
    if not per_page:
        return []
    need = max(1, (len(per_page) + 1) // 2)
    rules = []
    for k, occ in spans.items():
        if sum(1 for f in per_page if k in f) >= need:
            rules.append((min(a for a, _ in occ), max(b for _, b in occ)))
    return sorted(rules)


def detect_margin_tabs(doc: Doc) -> dict[int, list[tuple[float, float]]]:
    """바깥 여백의 과목 표시 세로 탭(회색 박스) x 구간. {page_idx: [(x0,x1)]}.

    판면 밖 여백에만 존재하는 요소이므로 '페이지 폭의 바깥 10% 안에 완전히 들어온
    무채색 채움 박스'만 인정한다. 본문 안의 회색 음영 도형은 이 조건을 통과할 수
    없다(A3 판면 오른쪽 끝 754.5pt < 0.9×842pt). 탭은 특정 장에만 있어 장별로 적용한다.
    """
    out: dict[int, list[tuple[float, float]]] = {}
    for pidx in range(len(doc)):
        page = doc.page(pidx)
        w = page.rect.width
        lo, hi = w * TAB_MARGIN_FRAC, w * (1.0 - TAB_MARGIN_FRAC)
        for d in page.get_drawings():
            fill = d.get("fill")
            r = d["rect"]
            if not fill or r.height < TAB_MIN_H_PT or r.width > TAB_MAX_W_PT:
                continue
            if max(fill) - min(fill) > 0.03:
                continue
            if not (TAB_GRAY_LO <= fill[0] <= TAB_GRAY_HI):
                continue
            if not (r.x1 <= lo or r.x0 >= hi):
                continue
            out.setdefault(pidx, []).append((r.x0, r.x1))
    return {k: sorted(set(v)) for k, v in out.items()}


# ══════════════════════════════════════════════════════════
# 컬럼 x 범위 / 세그먼트 상·하단
# ══════════════════════════════════════════════════════════
def col_x_range(doc: Doc, pidx: int, cols: list[float], col_idx: int,
                rules: list[tuple[float, float]]) -> tuple[float, float]:
    page = doc.page(pidx)
    body_x0, body_x1 = doc.body_x(pidx)
    x0 = cols[col_idx] - COL_LEFT_PAD
    if col_idx + 1 < len(cols):
        x1 = cols[col_idx + 1] - GUTTER_PAD
    else:
        # 마지막 컬럼의 오른쪽은 판면 끝까지. 페이지 끝까지 열어 두면 바깥 여백의
        # 세로쓰기 과목명이 딸려 들어온다(pdfdoc.Doc.body_x 주석 참고).
        x1 = body_x1 + BODY_EDGE_PAD
    x0 = max(x0, body_x0 - BODY_EDGE_PAD, page.rect.x0)
    x1 = min(x1, body_x1 + BODY_EDGE_PAD, page.rect.x1)

    left_bound = cols[col_idx - 1] if col_idx > 0 else 0.0
    right_bound = cols[col_idx + 1] if col_idx + 1 < len(cols) else page.rect.width
    for rx0, rx1 in rules:
        cx = (rx0 + rx1) / 2.0
        if cols[col_idx] < cx <= right_bound:      # 오른쪽 거터의 실선 → 안쪽까지만
            x1 = min(x1, rx0 - RULE_CLEAR_PT)
        if left_bound <= cx < cols[col_idx]:       # 왼쪽 거터의 실선 → 바깥부터
            x0 = max(x0, rx1 + RULE_CLEAR_PT)
    return x0, x1


def raised_top(doc: Doc, pidx: int, x0: float, x1: float, anchor_y: float) -> float:
    """번호 토큰 첫 줄에서 위로 솟은 글자(분수 분자·위첨자)까지 포함한 y."""
    top = anchor_y
    for w in doc.words(pidx):
        if w[2] <= x0 or w[0] >= x1:
            continue
        if anchor_y - NEXT_RAISE_PT <= w[1] < top and w[3] >= anchor_y + NEXT_OVERLAP_PT:
            top = w[1]
    return top


def _rule_above(doc: Doc, pidx: int, line: dict, x0: float, x1: float,
                max_w: float | None = None) -> float | None:
    best = None
    for rx0, rx1, ry in doc.hrules(pidx):
        if ry >= line["y0"] or line["y0"] - ry > TAIL_RULE_LOOKUP:
            continue
        if rx1 <= x0 or rx0 >= x1:
            continue
        if max_w is not None and (rx1 - rx0) > max_w:
            continue
        if best is None or ry > best:
            best = ry
    return best


def tail_top(doc: Doc, pidx: int, x0: float, x1: float) -> float | None:
    """컬럼 [x0,x1] 하단 꼬리 쓰레기의 시작 y. 없으면 None.

    문구를 감싸는 박스의 상단 괘선까지 거슬러 올라가 그 위에서 자른다. 컬럼 범위로
    라인을 뽑는 것이 핵심 — 전폭으로 뽑으면 좌단 본문과 우단 '확인 사항'이 한 줄로
    합쳐져 본문 한가운데를 꼬리로 오인한다.
    """
    h = doc.page(pidx).rect.height
    zone = h * TAIL_ZONE_FRAC
    best = None
    for ln in doc.lines(pidx, x0, x1):
        if ln["y0"] < zone:
            continue
        txt = ln["text"].strip()
        y = None
        if TAIL_TEXT_RE.search(txt):
            y = ln["y0"]
            top = _rule_above(doc, pidx, ln, x0, x1)
            if top is not None:
                y = min(y, top)
        elif PAGENUM_RE.match(txt.replace(" ", "")) and ln["y0"] > h - PAGENUM_BOTTOM_PT:
            # 쪽번호는 좁은 박스 안에 홀로 놓인다(본문 표의 숫자 셀과 구분)
            top = _rule_above(doc, pidx, ln, x0, x1, max_w=PAGENUM_RULE_MAX_W)
            if top is not None:
                y = min(ln["y0"], top)
        if y is None:
            continue
        best = y if best is None else min(best, y)
    return None if best is None else best - TAIL_PAD


def seg_bottom(doc: Doc, pidx: int, next_in_same: float | None,
               x0: float, x1: float) -> float:
    page = doc.page(pidx)
    if next_in_same is not None:
        bottom = max(0.0, next_in_same - 3)
    else:
        bottom = page.rect.height - BOTTOM_MARGIN_PT
    tail = tail_top(doc, pidx, x0, x1)
    if tail is not None:
        bottom = min(bottom, tail)
    return bottom


def segments_for(doc: Doc, cols: list[float], rules: list[tuple[float, float]],
                 anchor: tuple[int, int, float], nxt: tuple[int, int, float] | None,
                 tabs: dict[int, list[tuple[float, float]]] | None = None) -> list[Segment]:
    """앵커(page,col,y)부터 다음 앵커 직전까지의 세그먼트 목록."""
    apage, acol, ay = anchor
    tabs = tabs or {}
    out: list[Segment] = []
    pidx, col = apage, acol
    while True:
        is_first = (pidx, col) == (apage, acol)
        page = doc.page(pidx)
        x0, x1 = col_x_range(doc, pidx, cols, col, rules + tabs.get(pidx, []))
        same_seg_next = None
        if nxt is not None and (nxt[0], nxt[1]) == (pidx, col):
            same_seg_next = raised_top(doc, pidx, x0, x1, nxt[2])
        top = (raised_top(doc, pidx, x0, x1, ay) - ANCHOR_LIFT_PT
               if is_first else doc.header_bottom(pidx))
        bottom = seg_bottom(doc, pidx, same_seg_next, x0, x1)
        if bottom - top > (MIN_FIRST_SEG_PT if is_first else MIN_SEG_PT):
            out.append(Segment(pidx, col,
                               fitz.Rect(x0, top, x1, min(bottom, page.rect.height))))
        if nxt is None or (nxt[0], nxt[1]) == (pidx, col):
            break
        if col + 1 < len(cols):
            col += 1
        else:
            pidx += 1
            col = 0
            if pidx >= len(doc):
                break
    return out


# ══════════════════════════════════════════════════════════
# 회차 전체 계획
# ══════════════════════════════════════════════════════════
def plan_exam(doc: Doc, expected: int) -> ExamPlan:
    """회차 하나의 문항 크롭 계획. expected 는 subject.question_count."""
    max_number = max(expected, 30)
    tokens = number_tokens(doc, max_number)
    cols = detect_columns(tokens)
    found = _anchors(tokens, cols)
    missing = [n for n in range(1, expected + 1) if n not in found]

    if missing:
        # 번호가 발문과 한 워드로 붙는 판형을 위한 느슨한 재시도. 이미 찾은 번호는
        # 유지하고 빠진 것만 채운다 — loose 는 본문 속 '3.'(소수점 등) 오탐이 있어
        # 컬럼 시작 x 근처(±COL_TOL)에 있는 토큰만 인정한다.
        loose = number_tokens(doc, max_number, loose=True)
        if cols:
            loose = [t for t in loose if abs(t[1] - cols[nearest_col(cols, t[1])]) <= COL_TOL]
        extra = _anchors(loose, cols or detect_columns(loose))
        for n in missing:
            if n in extra:
                found[n] = extra[n]
        if not cols:
            cols = detect_columns(loose)
        missing = [n for n in range(1, expected + 1) if n not in found]

    duplicated = _duplicated(tokens, expected)
    rules = detect_column_rules(doc)
    tabs = detect_margin_tabs(doc)

    ordered = sorted(found.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[1][2]))
    questions = []
    for i, (n, key) in enumerate(ordered):
        nxt = ordered[i + 1][1] if i + 1 < len(ordered) else None
        questions.append(QuestionPlan(n, segments_for(doc, cols, rules, key, nxt, tabs)))
    questions.sort(key=lambda q: q.number)
    return ExamPlan(cols, rules, tabs, questions, missing, duplicated)


def _anchors(tokens, cols) -> dict[int, tuple[int, int, float]]:
    """번호 → (page, col, y). 같은 번호가 여러 번 나오면 읽기 순서상 첫 것을 쓴다."""
    if not cols:
        return {}
    best: dict[int, tuple[int, int, float]] = {}
    for pidx, x, y, n in sorted(tokens, key=lambda t: (t[0], nearest_col(cols, t[1]), t[2])):
        if n not in best:
            best[n] = (pidx, nearest_col(cols, x), y)
    return best


def _duplicated(tokens, expected: int) -> list[int]:
    seen: dict[int, int] = {}
    for _p, _x, _y, n in tokens:
        seen[n] = seen.get(n, 0) + 1
    return sorted(n for n, c in seen.items() if c > 1 and n <= expected)
