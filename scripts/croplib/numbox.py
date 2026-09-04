# -*- coding: utf-8 -*-
"""`items/<qid>.json` 의 `number_box` — 크롭 이미지 안에서 원래 문항 번호가 있던 자리.

학습지에서 문항 번호를 1, 2, 3… 으로 다시 매기려면 잘라낸 이미지 안의 어디를 덮어야
하는지 알아야 한다. 크롭은 문항 번호 토큰에 앵커링해서 잘리므로 그 자리를 이미
알고 있었는데, 예전에는 `(page, col, y)` 만 남기고 x·폭·높이를 버렸다. 그것을 살려
**크롭 사각형 대비 0~1 비율** 로 환산해 두는 것이 이 모듈이다.

## 왜 pt 가 아니라 비율인가
크롭 PNG 는 화면·인쇄 어디서든 임의의 폭으로 표시된다. 비율이면 표시 폭이 얼마든
CSS 로 그대로 겹칠 수 있다(`left: calc(<left> * 100%)`). pt 나 px 로 적으면 소비하는
쪽이 dpi 를 알아야 하고, dpi 는 items 에 남지 않는다.

## 사상 경로 — 픽셀이 네 번 움직인다
크롭 PNG 는 PDF 사각형을 그대로 찍은 그림이 아니다. 아래 네 단계를 거치므로
번호 토큰의 PDF 좌표를 그대로 나누면 조금씩 어긋난다.

  1. 렌더    클립이 픽셀 격자로 반올림된다 → `imaging.render_rect_at` 의 원점
  2. 실선제거 좌측 세로 실선을 잘라내면 x 가 왼쪽으로 밀린다 → `left_cut`
  3. 합성    세그먼트가 여럿이면 첫 조각이 캔버스 가운데로 붙는다 → `paste_x`
  4. 트리밍  흰 여백을 깎는다 → `imaging.trim_box`

`Placement` 가 1~3 을, `ratio()` 의 `tbox` 인자가 4 를 담는다.

## 못 믿을 값은 키를 만들지 않는다
비율이 음수거나 1을 넘거나 폭·높이가 0이면 **키 자체를 넣지 않는다**(계약 0절).
번호가 크롭 밖에 있다는 뜻이고, 그런 사각형을 그려 봐야 엉뚱한 자리를 덮는다.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

# 비율 소수 자릿수. 3자리면 0.026(높이 2.6%)짜리 상자에서 상대오차가 2%까지 벌어져
# 한 자리 늘렸다. 4자리는 4000px 폭 이미지에서 0.4px 해상도라 충분하다.
RATIO_ND = 4


@dataclass
class Placement:
    """첫 세그먼트의 PDF 좌표를 합성 이미지 픽셀로 옮기는 데 필요한 값들.

    번호는 앵커가 된 첫 조각에만 있으므로 첫 조각 것만 들고 다닌다(공유 필드 규약:
    '여러 조각으로 나뉜 문항은 첫 조각 기준').
    """
    page: int                      # 첫 세그먼트의 페이지(0-based)
    origin: tuple[int, int]        # 렌더 픽셀 원점 (px = pdf*zoom - origin)
    left_cut: int = 0              # strip_edge_rules 가 왼쪽에서 잘라낸 px
    paste_x: int = 0               # stitch 가 캔버스에 붙인 x


def placement(page: int, origin: tuple[int, int], removed, paste_x: int = 0) -> Placement:
    """`imaging.strip_edge_rules` 의 removed 목록에서 왼쪽 절단폭을 읽어 조립한다."""
    left = 0
    for side, amount in (removed or []):
        if side == "left":
            left = int(amount)
    return Placement(page=page, origin=origin, left_cut=left, paste_x=int(paste_x))


def ratio(num_page: int | None, num_rect, place: Placement | None,
          zoom: float, tbox: tuple[int, int, int, int]) -> list[float] | None:
    """번호 토큰의 PDF 사각형 → 최종 크롭 대비 [left, top, width, height] 비율.

    못 믿을 값이면 None. 부르는 쪽은 None 이면 `number_box` 키를 넣지 않는다.
    """
    if num_rect is None or place is None or num_page is None:
        return None
    if num_page != place.page:
        # 앵커 세그먼트가 너무 짧아 버려지면 segments[0] 이 다음 컬럼 조각이 된다.
        # 그때 번호는 크롭 안에 없다.
        return None
    ox, oy = place.origin
    shift_x = place.paste_x - place.left_cut - tbox[0]
    shift_y = -tbox[1]
    x0 = num_rect[0] * zoom - ox + shift_x
    x1 = num_rect[2] * zoom - ox + shift_x
    y0 = num_rect[1] * zoom - oy + shift_y
    y1 = num_rect[3] * zoom - oy + shift_y

    w_img = tbox[2] - tbox[0]
    h_img = tbox[3] - tbox[1]
    if w_img <= 0 or h_img <= 0:
        return None
    box = [x0 / w_img, y0 / h_img, (x1 - x0) / w_img, (y1 - y0) / h_img]
    box = [round(v, RATIO_ND) for v in box]
    return box if _sane(box) else None


def _sane(box: list[float]) -> bool:
    left, top, w, h = box
    if w <= 0 or h <= 0:
        return False
    if left < 0 or top < 0:
        return False
    return left + w <= 1.0 and top + h <= 1.0


# ══════════════════════════════════════════════════════════
# 기존 크롭을 다시 만들지 않고 number_box 만 채우는 가벼운 길
# ══════════════════════════════════════════════════════════
def segments_from_item(data: dict) -> list[tuple[int, tuple[float, float, float, float]]]:
    """items 의 source.segments(없으면 source.page/rect) → [(page_idx, (x0,y0,x1,y1))].

    계획을 다시 세우지 않고 **그때 실제로 렌더한 사각형**을 쓴다. crop_rects.json 으로
    자른 회차나 그 사이 앵커 알고리즘이 바뀐 회차에서도 어긋나지 않는다.
    page 는 items 에 사람이 세는 1-based 로 적혀 있어 0-based 로 되돌린다.
    하나라도 형태가 어긋나면 빈 목록을 돌려준다 — 반쪽짜리로 계산하지 않는다.
    """
    src = data.get("source") or {}
    raw = src.get("segments")
    if not raw:
        if not src.get("rect") or src.get("page") is None:
            return []
        raw = [{"page": src["page"], "rect": src["rect"]}]
    out = []
    for part in raw:
        rect, page = part.get("rect"), part.get("page")
        if not rect or page is None or len(rect) != 4:
            return []
        out.append((int(page) - 1, tuple(float(v) for v in rect)))
    return out


def png_size(path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:                                 # noqa: BLE001
        return None
