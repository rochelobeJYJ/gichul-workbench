# -*- coding: utf-8 -*-
"""렌더 · 이미지 도메인 정리 · 대지(contact sheet).

픽셀 상수는 전부 pt 로 두고 zoom 을 곱해 쓴다. 원본 구현은 ZOOM=2.5 고정이라
픽셀 상수를 그대로 박아 뒀는데, --dpi 를 열어 주는 순간 그 값들이 의미를 잃는다
(예: '세로선 폭 ≤ 4px' 는 180dpi 기준이라 300dpi 에서는 선을 놓친다).
"""
from __future__ import annotations

import io
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageStat

# ── 렌더 후 가장자리 세로 실선 제거 ──────────────────────
# 컬럼 구분선을 PDF 도형 단계에서 걸러도, 판형에 따라 렌더 결과 가장자리에 선이
# 1~2px 남는다. 이미지에서 한 번 더 지운다.
EDGE_FRAC = 0.03          # 좌/우 가장자리 3% 폭만 검사
EDGE_RULE_MAX_PT = 1.6    # 라인 폭 상한(pt). 원본 4px @ZOOM 2.5 = 1.6pt
EDGE_RULE_COVER = 0.5     # 이미지 높이의 50% 이상 관통
EDGE_RULE_DARK = 195      # 이 값보다 어두우면 라인 픽셀로 간주
EDGE_CUT_MAX_FRAC = 0.06  # 한쪽에서 잘라낼 수 있는 최대 폭(과잉 트리밍 방지)
EDGE_ARM_FRAC = 0.25      # 이 이상 안쪽으로 뻗은 가로선이 붙어 있으면 '박스 테두리'

# ── 여백 트리밍 ─────────────────────────────────────────
TRIM_THRESHOLD = 245      # 이보다 밝으면 흰 배경
QUESTION_PAD_PT = 6.0     # 문항 크롭에 남기는 균일 패딩(pt)
MATERIAL_PAD_PT = 5.6     # 자료 크롭 패딩(pt). 원본 14px @ZOOM 2.5

# ── 대지 ────────────────────────────────────────────────
SHEET_COLS = 5            # 20문항이 한 장에 5×4 로 들어간다
SHEET_CELL_W = 400
SHEET_CELL_H = 560
SHEET_LABEL_H = 26
SHEET_GUTTER = 12
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\gulim.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
)


def zoom_for(dpi: int) -> float:
    return dpi / 72.0


# ══════════════════════════════════════════════════════════
# 가장자리 세로 실선 제거
# ══════════════════════════════════════════════════════════
def _longest_run(colmask: np.ndarray) -> int:
    if not colmask.any():
        return 0
    flat = np.concatenate(([0], colmask.astype(np.int8), [0]))
    idx = np.flatnonzero(np.diff(flat))
    return int((idx[1::2] - idx[0::2]).max())


def _run_extent(colmask: np.ndarray) -> tuple[int, int]:
    flat = np.concatenate(([0], colmask.astype(np.int8), [0]))
    idx = np.flatnonzero(np.diff(flat))
    starts, ends = idx[0::2], idx[1::2]
    i = int(np.argmax(ends - starts))
    return int(starts[i]), int(ends[i]) - 1


def _bands(cols: list[int]) -> list[list[int]]:
    out, cur = [], []
    for c in cols:
        if cur and c != cur[-1] + 1:
            out.append(cur)
            cur = []
        cur.append(c)
    if cur:
        out.append(cur)
    return out


def _arm_len(row: np.ndarray, c: int, step: int) -> int:
    n = len(row)
    i, length = c, 0
    while 0 <= i < n and row[i]:
        length += 1
        i += step
    return length


def _looks_like_box_border(dark: np.ndarray, band: list[int], inward: int) -> bool:
    """세로선 끝에 안쪽으로 긴 가로선이 붙어 있으면 박스 테두리(=본문)로 본다.

    <보 기> 박스가 컬럼 폭을 꽉 채우는 문항에서, 박스 왼쪽 변을 컬럼 구분선으로
    오인해 잘라 버리는 사고를 막는 장치다.
    """
    h, w = dark.shape
    c = band[len(band) // 2]
    y0, y1 = _run_extent(dark[:, c])
    need = w * EDGE_ARM_FRAC
    edge = band[-1] if inward > 0 else band[0]
    for y in (y0, y0 + 1, y0 + 2, y1 - 2, y1 - 1, y1):
        if 0 <= y < h and _arm_len(dark[y], edge, inward) >= need:
            return True
    return False


def strip_edge_rules(img: Image.Image, zoom: float) -> tuple[Image.Image, list]:
    """좌/우 가장자리를 관통하는 얇은 세로 실선을 찾아 그 안쪽까지 잘라낸다."""
    g = np.asarray(img.convert("L"))
    h, w = g.shape
    if w < 80 or h < 80:
        return img, []
    dark = g < EDGE_RULE_DARK
    zone = max(2, int(round(w * EDGE_FRAC)))
    need = h * EDGE_RULE_COVER
    max_cut = int(w * EDGE_CUT_MAX_FRAC)
    max_band = max(1, int(round(EDGE_RULE_MAX_PT * zoom)))
    removed = []

    left_cut = 0
    for band in _bands([c for c in range(0, zone) if _longest_run(dark[:, c]) >= need]):
        if len(band) > max_band or _looks_like_box_border(dark, band, +1):
            continue
        cut = band[-1] + 2
        if cut <= max_cut:
            left_cut = max(left_cut, cut)

    right_cut = w
    for band in _bands([c for c in range(max(zone, w - zone), w) if _longest_run(dark[:, c]) >= need]):
        if len(band) > max_band or _looks_like_box_border(dark, band, -1):
            continue
        cut = band[0] - 1
        if (w - cut) <= max_cut:
            right_cut = min(right_cut, cut)

    if left_cut > 0:
        removed.append(["left", left_cut])
    if right_cut < w:
        removed.append(["right", w - right_cut])
    if not removed:
        return img, []
    return img.crop((left_cut, 0, right_cut, h)), removed


# ══════════════════════════════════════════════════════════
# 렌더 · 합성 · 트리밍
# ══════════════════════════════════════════════════════════
def render_rect(page, rect: fitz.Rect, zoom: float) -> Image.Image:
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def stitch(imgs: list[Image.Image]) -> Image.Image:
    """세그먼트를 세로로 이어 붙인다(컬럼·페이지를 넘어가는 문항)."""
    if len(imgs) == 1:
        return imgs[0]
    width = max(im.width for im in imgs)
    canvas = Image.new("RGB", (width, sum(im.height for im in imgs)), "white")
    y = 0
    for im in imgs:
        canvas.paste(im, ((width - im.width) // 2, y))
        y += im.height
    return canvas


def content_margins(img: Image.Image, threshold: int = TRIM_THRESHOLD) -> dict[str, int]:
    """4면의 흰 여백 두께(px). 0 에 가까우면 그쪽에서 내용이 잘렸을 수 있다.

    mock-exam-question-crop 스킬의 audit_crop_edges.py 에서 가져온 검사다.
    **트리밍 전에** 재야 의미가 있다 — 트리밍하면 모든 여백이 패딩값으로 같아진다.
    """
    mask = np.asarray(img.convert("L")) < threshold
    rows, cols = np.where(mask)
    if rows.size == 0:
        return {"left": img.width, "right": img.width, "top": img.height, "bottom": img.height}
    return {
        "left": int(cols.min()),
        "right": int(img.width - 1 - cols.max()),
        "top": int(rows.min()),
        "bottom": int(img.height - 1 - rows.max()),
    }


def trim(img: Image.Image, zoom: float, pad_pt: float) -> Image.Image:
    """흰 여백을 잘라내고 균일 패딩만 남긴다(4면 동일).

    CSAT_Clipper 의 '4면 균일 패딩(5pt)' 아이디어를 PDF 좌표가 아니라 렌더 이미지에서
    수행한다. 좌표 단계에서 콘텐츠 bbox 로 조이면 표 테두리·연한 괘선처럼 텍스트
    span 으로 잡히지 않는 요소가 잘려 나가는데, 이미지에서는 실제로 찍힌 픽셀만
    보므로 그런 사고가 없다.
    """
    pad = int(round(pad_pt * zoom))
    mask = np.asarray(img.convert("L")) < TRIM_THRESHOLD
    if not mask.any():
        return img
    ys, xs = np.where(mask)
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(img.width, int(xs.max()) + 1 + pad)
    y1 = min(img.height, int(ys.max()) + 1 + pad)
    return img.crop((x0, y0, x1, y1))


def is_blank(img: Image.Image) -> bool:
    return ImageStat.Stat(img.convert("L")).stddev[0] < 4


# ══════════════════════════════════════════════════════════
# 대지(contact sheet)
# ══════════════════════════════════════════════════════════
def _font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:      # Pillow 10 미만
        return ImageFont.load_default()


def contact_sheet(cells: list[dict], out_path: Path, title: str,
                  cols: int = SHEET_COLS) -> Path:
    """LLM 이 한 장만 보고 이상한 크롭을 짚어내기 위한 대지.

    cells: [{"label": str, "path": Path, "flags": {"left":bool,...}}]
    각 칸에 얇은 테두리를 그린다 — 크롭의 실제 범위(흰 여백 포함)가 보이지 않으면
    '위가 잘렸는지'를 눈으로 판단할 수 없다. 여백이 0 에 가까운 변에는 빨간 줄을
    그어 의심 지점을 먼저 보게 한다.
    """
    rows = max(1, (len(cells) + cols - 1) // cols)
    head = 34
    sheet_w = cols * SHEET_CELL_W + (cols + 1) * SHEET_GUTTER
    sheet_h = head + rows * (SHEET_CELL_H + SHEET_LABEL_H) + (rows + 1) * SHEET_GUTTER
    sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
    draw = ImageDraw.Draw(sheet)
    f_label = _font(18)
    draw.text((SHEET_GUTTER, 8), title, fill=(20, 20, 20), font=_font(22))

    for i, cell in enumerate(cells):
        col, row = i % cols, i // cols
        cx = SHEET_GUTTER + col * (SHEET_CELL_W + SHEET_GUTTER)
        cy = head + SHEET_GUTTER + row * (SHEET_CELL_H + SHEET_LABEL_H + SHEET_GUTTER)
        flags = cell.get("flags") or {}
        bad = any(flags.values())
        draw.text((cx, cy + 4), cell["label"],
                  fill=(190, 0, 0) if bad else (30, 30, 30), font=f_label)
        box_y = cy + SHEET_LABEL_H
        draw.rectangle((cx, box_y, cx + SHEET_CELL_W, box_y + SHEET_CELL_H),
                       outline=(205, 205, 205))
        path = cell.get("path")
        if not path or not Path(path).exists():
            draw.text((cx + 10, box_y + 10), "(없음)", fill=(190, 0, 0), font=f_label)
            continue
        with Image.open(path) as opened:
            im = opened.convert("RGB")
        im.thumbnail((SHEET_CELL_W - 8, SHEET_CELL_H - 8), Image.Resampling.LANCZOS)
        ix = cx + (SHEET_CELL_W - im.width) // 2
        iy = box_y + (SHEET_CELL_H - im.height) // 2
        sheet.paste(im, (ix, iy))
        draw.rectangle((ix - 1, iy - 1, ix + im.width, iy + im.height), outline=(150, 150, 150))
        if flags.get("left"):
            draw.line((ix, iy, ix, iy + im.height), fill=(220, 0, 0), width=3)
        if flags.get("right"):
            draw.line((ix + im.width, iy, ix + im.width, iy + im.height), fill=(220, 0, 0), width=3)
        if flags.get("top"):
            draw.line((ix, iy, ix + im.width, iy), fill=(220, 0, 0), width=3)
        if flags.get("bottom"):
            draw.line((ix, iy + im.height, ix + im.width, iy + im.height), fill=(220, 0, 0), width=3)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path
