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


def crop_text(doc, segments) -> str:
    return "\n".join(doc.page(s.page).get_text("text", clip=s.rect) for s in segments)


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
