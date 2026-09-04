# -*- coding: utf-8 -*-
"""크롭 결과 자동 점검.

크롭에서 가장 흔한 사고는 두 가지다 — **마지막 선지가 잘리는 것**과 **다른 문항이
섞여 들어오는 것**. 둘 다 이미지를 봐야만 알 수 있다고 여기기 쉽지만, 다음 세
신호로 기계가 먼저 후보를 짚어낼 수 있다.

  1. 크롭 안의 텍스트에 선지 마커 ①~⑤ 가 다 있는가 (텍스트 레이어가 있을 때)
  2. 크롭 안에 '다음 문항 번호'가 줄머리로 등장하지 않는가
  3. 렌더 이미지의 네 변 여백이 0 에 가깝지 않은가 (audit_crop_edges.py 방식)

여기서 나온 것은 '후보'지 증거가 아니다. 최종 판정은 대지를 눈으로 보는 것이다.
"""
from __future__ import annotations

import re

CHOICE_MARKS = "①②③④⑤"
EDGE_FLAG_PT = 2.0     # 이보다 얇은 여백이면 그 변에서 잘렸을 수 있다고 본다

# ── 회차 안에서 유독 좁은 크롭 ───────────────────────────
NARROW_CROP_RATIO = 0.75   # 기준 폭의 이 비율 미만이면 신고
NARROW_CROP_MIN_N = 3      # 이보다 적으면 '회차 안 비교'가 의미 없다


def crop_text(doc, segments) -> str:
    return "\n".join(doc.page(s.page).get_text("text", clip=s.rect) for s in segments)


def narrow_crops(widths: dict[str, int]) -> tuple[list[str], int]:
    """회차 안에서 유독 좁은 크롭의 qid 목록과 비교 기준이 된 폭(px).

    ## 왜 이 검사가 필요한가
    컬럼 인식이 어긋나 크롭이 세로 띠로 잘려도 **기존 신호가 하나도 울리지 않는다.**
    띠 안에 그 문항의 글자가 남아 있어 '머리에 번호가 없다'·'선지 ⑤ 가 없다'가 조용하고,
    4면 여백도 정상이며, 텍스트 추출이 멀쩡해서 validate 도 통과한다(실측 통합사회 2025
    고1 9월: 25장 중 13장이 폭 322~423px 로 잘렸는데 리포트에 아무 표시가 없었다).
    남는 신호는 **폭 하나뿐**이라 여기서 명시적으로 잰다.

    ## 왜 중앙값이 아니라 **최대폭**을 기준으로 삼나 (실측)
    이 사고는 회차의 **과반**을 한꺼번에 망가뜨릴 수 있다. 위 회차의 크롭 폭 중앙값은
    423px 로 이미 망가진 값이었고, '중앙값의 75% 미만' 규칙은 **0건**을 신고한다.
    반대로 크롭 폭은 컬럼 폭을 넘을 수 없으므로 **회차에서 가장 넓은 크롭은 언제나
    성한 크롭**이다(한 장이라도 성하면). 그래서 최대폭을 기준으로 잡는다.

    임계값 0.75 의 근거(실측). 성한 크롭 **900장**(지구과학Ⅱ 19회차 380 · 통합과학 19회차
    390 · 통합사회 5회차 110 · 한국지리 20)에서 이 비율이 0.85 아래로 내려간 장이 **0**,
    0.90 아래는 1장(0.884)뿐이었다. 파손된 크롭은 0.12·0.32~0.43·0.60 이었다.
    두 무리 사이가 0.60~0.884 로 비어 있어 그 가운데를 잡았다. 좁혀도(0.85) 넓혀도(0.65)
    한쪽이 곧 오탐·누락으로 넘어가는 자리라 여유를 양쪽에 나눠 뒀다.
    """
    if len(widths) < NARROW_CROP_MIN_N:
        return [], 0
    ref = max(widths.values())
    limit = ref * NARROW_CROP_RATIO
    return sorted(q for q, w in widths.items() if w < limit), ref


def edge_flags(margins: dict[str, int], zoom: float) -> dict[str, bool]:
    limit = EDGE_FLAG_PT * zoom
    return {k: (v < limit) for k, v in margins.items()}


def inspect(number: int, text: str, margins: dict[str, int], zoom: float,
            has_text_layer: bool) -> list[tuple[str, str]]:
    """[(severity, why)] 목록. severity 는 리포트 계약의 info|warn|error."""
    out: list[tuple[str, str]] = []
    flags = edge_flags(margins, zoom)

    if has_text_layer:
        head = text.strip()
        if not re.search(rf"(?m)^\s*{number}\.", head):
            out.append(("error", "크롭 머리에 문항 번호가 없다 — 앵커가 어긋났다"))
        if re.search(rf"(?m)^\s*{number + 1}\.\s", head):
            out.append(("error", f"크롭 안에 다음 문항({number + 1}번) 번호가 있다 — 두 문항이 섞였다"))
        marks = [m for m in CHOICE_MARKS if m in text]
        if not marks:
            out.append(("warn", "선지 마커(①~⑤)가 하나도 없다 — 선지가 통째로 빠졌을 수 있다"))
        elif CHOICE_MARKS[-1] not in marks:
            out.append(("warn", f"마지막 선지 ⑤ 가 없다(찾은 것: {''.join(marks)}) — 아래가 잘렸을 수 있다"))
        elif len(marks) < 5:
            out.append(("warn", f"선지 마커가 {len(marks)}개뿐이다({''.join(marks)})"))

    if flags.get("bottom"):
        out.append(("warn", f"아래 여백이 {margins['bottom']}px — 마지막 선지가 잘렸을 수 있다"))
    for side, label in (("top", "위"), ("left", "왼쪽"), ("right", "오른쪽")):
        if flags.get(side):
            out.append(("info", f"{label} 여백이 {margins[side]}px — 내용이 변에 닿아 있다"))
    return out
