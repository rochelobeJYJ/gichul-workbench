# -*- coding: utf-8 -*-
"""텍스트 레이어가 없는 회차를 위한 vision 경로.

2025학년도 수능 지구과학Ⅱ 문제지가 그렇다 — 글자가 전부 벡터로 아웃라인화되어
페이지당 도형이 1100개를 넘고 워드는 0개다. 문항 번호를 텍스트로 찾을 수 없으니
앵커 기반 크롭이 성립하지 않는다.

그래서 **컬럼 단위로 통째로 렌더해서 넘긴다.** 사람이나 LLM 이 그 이미지를 보고
문항 경계를 읽어 `sources/<exam_id>/crop_rects.json` 에 적어 주면, 다음 실행에서
그 사각형으로 정상적인 문항 크롭이 만들어진다(crop.py 의 rects 경로).
문항의 extraction_mode 는 두 경우 모두 'vision' 이다 — 본문을 텍스트 레이어가
아니라 이미지에서 읽어야 한다는 뜻이기 때문이다.

컬럼 경계는 텍스트 없이도 구할 수 있다. 실측(A3 문제지 842×1191pt):
  - 판면 상단 전폭 괘선 x = 88.0 ~ 754.5  → 판면 좌우 끝
  - 컬럼 구분 세로 실선 x ≈ 420.5 ~ 421.4 → 컬럼 경계
  - 머리글 괘선 y = 147.4 (첫 장은 246.6) → 본문 시작
텍스트 레이어가 없어도 이 셋은 전부 도형이라 그대로 읽힌다.
"""
from __future__ import annotations

from dataclasses import dataclass

import fitz

from .pdfdoc import Doc
from .tamgu import RULE_CLEAR_PT, detect_column_rules

BOTTOM_MARGIN_PT = 20.0   # 페이지 맨 아래로 남기는 최소 여백
INK_TOL_PT = 2.0          # 이 컬럼에 속한다고 볼 x 허용 오차
INK_PAD_PT = 6.0          # 마지막 잉크 아래로 남기는 여유
MIN_COL_W_PT = 60.0       # 이보다 좁은 조각은 컬럼으로 보지 않는다


@dataclass
class Strip:
    page: int             # 0-based
    col: int              # 1-based(사람이 읽는 이름표용)
    rect: fitz.Rect


def column_bottom(doc: Doc, pidx: int, x0: float, x1: float) -> float:
    """컬럼 [x0,x1] 의 잉크가 끝나는 y.

    **하단 괘선으로 꼬리 박스를 찾으려 하면 안 된다.** 처음에 '페이지 하단 25% 안의
    넓은 가로 괘선 = 꼬리 박스 상단'으로 잡았다가, 컬럼 폭을 꽉 채운 <보 기> 박스의
    아래 변(y=1043.8)을 꼬리로 오인해 그 아래 있던 3번 문항의 선지 줄(y=1055~1066)을
    통째로 잘라 먹었다. 텍스트가 없으니 '확인 사항' 같은 문구로 꼬리를 식별할 방법도
    없다. 그래서 이 경로에서는 꼬리 판별을 포기하고 **컬럼 잉크 끝까지 그냥 넣는다.**
    넘겨주는 용도의 이미지라 쪽번호가 조금 딸려 들어오는 것은 무해하지만, 선지가
    잘리는 것은 치명적이다 — 위험이 대칭이 아니다.

    쪽번호 박스는 두 컬럼 사이 거터에 걸쳐 있어(실측 x 395.7~446.0, 컬럼 경계 421)
    '컬럼 안에 완전히 들어온 요소'만 세는 것으로 자연히 빠진다.
    """
    page = doc.page(pidx)
    limit = page.rect.height - BOTTOM_MARGIN_PT
    lo, hi = x0 - INK_TOL_PT, x1 + INK_TOL_PT
    bottom = None
    for d in page.get_drawings():
        r = d["rect"]
        if r.x0 >= lo and r.x1 <= hi and r.y1 < limit:
            bottom = r.y1 if bottom is None else max(bottom, r.y1)
    for info in page.get_image_info():
        r = fitz.Rect(info["bbox"])
        if r.x0 >= lo and r.x1 <= hi and r.y1 < limit:
            bottom = r.y1 if bottom is None else max(bottom, r.y1)
    if bottom is None:
        return limit
    return min(limit, bottom + INK_PAD_PT)


def column_ranges(doc: Doc, pidx: int, rules: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """판면을 컬럼 구분 실선으로 쪼갠 x 구간들. 실선이 없으면 판면 전체 한 컬럼."""
    x0, x1 = doc.body_x(pidx)
    cuts = [(a, b) for a, b in rules if x0 < (a + b) / 2 < x1]
    ranges = []
    cur = x0
    for a, b in sorted(cuts):
        if a - RULE_CLEAR_PT - cur >= MIN_COL_W_PT:
            ranges.append((cur, a - RULE_CLEAR_PT))
        cur = b + RULE_CLEAR_PT
    if x1 - cur >= MIN_COL_W_PT:
        ranges.append((cur, x1))
    return ranges or [(x0, x1)]


def plan_strips(doc: Doc) -> list[Strip]:
    rules = detect_column_rules(doc)
    out: list[Strip] = []
    for pidx in range(len(doc)):
        top = doc.header_bottom(pidx)
        for i, (x0, x1) in enumerate(column_ranges(doc, pidx, rules), start=1):
            bottom = column_bottom(doc, pidx, x0, x1)
            if bottom - top < MIN_COL_W_PT:
                continue
            out.append(Strip(pidx, i, fitz.Rect(x0, top, x1, bottom)))
    return out
