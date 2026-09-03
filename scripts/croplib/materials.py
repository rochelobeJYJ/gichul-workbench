# -*- coding: utf-8 -*-
"""문항 안의 '자료'(그래프·표·모식도·지질도·사진)만 골라내는 판별기.

발문·<보 기> 박스·선지(①~⑤)는 제외한다. 문항 클립 사각형은 croplib/tamgu.py 가
계산한 것을 그대로 받아 쓰고, 이 모듈은 그 '안에서' 그림 영역만 추가로 고른다.
CSAT_WIKI/wiki_earth2/build_material_snapshots.py 에서 406장으로 검증된 알고리즘이며,
임계값은 전부 실측에서 나온 값이라 근거 주석과 함께 그대로 옮겼다.

── 판별 원리 ────────────────────────────────────────────────
클립 안에서 두 종류의 그림 원자료(primitive)를 모은다.
  - 래스터 이미지: page.get_image_info() 의 bbox. 평가원 문제지의 그래프·모식도·사진은
    거의 전부 축·눈금·라벨까지 포함해 미리 렌더된 이미지 한 장으로 삽입되어 있어,
    이미지 primitive 는 그 자체로 완결된 자료 조각이다.
  - 벡터 도형: page.get_drawings() 의 bbox. 이 판형에서 벡터로 그려지는 것은 사실상
    표의 괘선과 '<보 기> 박스 / 안내 상자'의 네 변뿐이다.

문제는 표 괘선과 '속 빈 테두리 박스'가 기하학적으로 구분되지 않는다는 점이다.
그래서 엄격 → 느슨 두 단계로 나눈다.
  1) strip_hollow_frames: 이미지를 빼고 도형끼리만, '실제로 맞닿은 모서리'만 인정하는
     엄격한 문턱(CORNER_GAP_PT)으로 묶는다.
  2) is_hollow_frame: 중복 제거한 선이 전부 클러스터 bbox 네 변에 붙어 있고 내부
     구분선이 없으면 표가 아니라 속 빈 테두리로 보고 버린다.
  3) materials_for_segment: 살아남은 도형 + 이미지를 느슨한 문턱(GAP_MERGE_PT)으로
     다시 묶어 최종 자료로 삼는다.
"""
from __future__ import annotations

import re

import fitz

from .pdfdoc import Doc

GAP_MERGE_PT = 22.0        # 최종 결합 문턱(pt). 나란한 (가)(나) 그림 사이 11.3pt,
                            # 표 칸 안 사진↔칸 구분선 15.9pt 를 덮는 값. 위험한
                            # '무관한 구조물과의 병합'은 strip_hollow_frames 가 먼저 막는다.
EDGE_EPS_PT = 1.5          # 클러스터 bbox 변에 '붙어 있다'고 볼 허용 오차(pt)
DIVIDER_SPAN_FRAC = 0.5    # 내부 구분선으로 인정할 최소 가로지름 비율.
                            # <보 기> 박스 안 분수 줄(폭의 28%)은 무시하고 표의 행/열
                            # 구분선(50% 이상)만 '표의 증거'로 인정한다.
MIN_MATERIAL_W_PT = 18.0   # 이보다 좁으면 버림(단독 세로선 등)
MIN_MATERIAL_H_PT = 10.0   # 이보다 낮으면 버림(단독 밑줄·구분선 등)
CORNER_GAP_PT = 3.0        # 도형끼리 묶을 엄격 문턱. 표든 장식 테두리든 자기 네 변은
                            # 항상 모서리에서 맞닿는다(실측 0~0.3pt). 무관한 두 구조물의
                            # 우연한 근접은 실측 최소 7.1pt('탐구 상자' 테두리↔안내문 밑줄)
                            # 였다. 3pt 면 선분 길이와 무관하게 항상 올바로 갈린다.
RENDER_PAD_PT = 2.5        # 렌더 전 bbox 를 살짝 넓혀 테두리 선이 잘리지 않게
SAFE_TEXT_GAP_PT = 0.6     # 그림 아닌 텍스트 줄에서 물러나는 여유. 자료와 다음 문장 사이가
                            # 1.2pt 밖에 안 되는 문항이 있어 고정 패딩만 쓰면 옆 문장의
                            # 위쪽 획이 찍혀 들어온다.

BOGI_HEADING_RE = re.compile(r"<\s*보\s*기\s*>")
BOGI_ITEM_RE = re.compile(r"^[ㄱㄴㄷㄹㅁ]\s*\.")
CHOICE_LINE_RE = re.compile(r"^[①②③④⑤]")

ATTACH_GAP_PT = 14.0       # 그림 가장자리에서 이 거리 이내의 짧은 라벨만 흡수
ATTACH_MAX_HEIGHT_PT = 20.0
ATTACH_MAX_LEN = 12        # 발문·보기처럼 긴 문장은 제외
ATTACH_OVERLAP_FRAC = 0.10
ATTACH_LABEL_RE = re.compile(
    r"^[\(\[]?[가-힣A-Za-z0-9ㄱ-ㅎ㉠-㉭①-⑤]{1,4}[\)\]]?"
    r"(?:[,\s]+[\(\[]?[가-힣A-Za-z0-9ㄱ-ㅎ㉠-㉭①-⑤]{1,4}[\)\]]?){0,3}$"
)

IMAGE_KEEP_FRAC = 0.3      # 클립과 이 비율 미만으로 겹치는 이미지는 옆 문항 것으로 본다
IMAGE_FULL_FRAC = 0.98     # 이 이상 겹치면 bbox 전체를 쓴다(가장자리 반올림 오차 흡수)


def _overlap(a: fitz.Rect, b: fitz.Rect) -> bool:
    """fitz.Rect.intersects() 는 폭 또는 높이가 0인(순수 선분) rect 를 empty 로 보고
    항상 False 를 낸다 — 표 괘선·화살표 등 대부분의 벡터 도형이 여기 해당하므로
    직접 구현한다."""
    return not (a.x1 < b.x0 or a.x0 > b.x1 or a.y1 < b.y0 or a.y0 > b.y1)


def _gap(a: fitz.Rect, b: fitz.Rect) -> float:
    dx = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return (dx * dx + dy * dy) ** 0.5


class Prim:
    __slots__ = ("rect", "kind")   # kind: "image" | "draw"

    def __init__(self, rect: fitz.Rect, kind: str):
        self.rect = rect
        self.kind = kind


def collect_primitives(page, clip: fitz.Rect) -> list[Prim]:
    prims: list[Prim] = []

    seen_img = set()
    for info in page.get_image_info():
        bbox = fitz.Rect(info["bbox"])
        if bbox.is_empty or not _overlap(bbox, clip):
            continue
        inter = bbox & clip
        area = max(0.0, inter.width) * max(0.0, inter.height)
        full = max(1e-6, bbox.width * bbox.height)
        if area / full < IMAGE_KEEP_FRAC:
            continue
        use = bbox if area / full > IMAGE_FULL_FRAC else inter
        key = (round(use.x0, 1), round(use.y0, 1), round(use.x1, 1), round(use.y1, 1))
        if key in seen_img:
            continue
        seen_img.add(key)
        prims.append(Prim(use, "image"))

    seen_draw = set()
    for d in page.get_drawings():
        r = fitz.Rect(d["rect"])
        if not _overlap(r, clip):
            continue
        r = fitz.Rect(max(r.x0, clip.x0), max(r.y0, clip.y0),
                      min(r.x1, clip.x1), min(r.y1, clip.y1))
        if r.x1 < r.x0 or r.y1 < r.y0:
            continue
        key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
        if key in seen_draw:
            continue
        seen_draw.add(key)
        prims.append(Prim(r, "draw"))

    return prims


def cluster_primitives(prims: list[Prim], gap_pt: float) -> list[list[Prim]]:
    """단일 문턱으로 근접 primitive 를 묶는다(single-linkage, primitive 대 primitive).

    반드시 primitive 간 간격으로 판정해야 한다. 이미 합쳐진 클러스터의 bbox 로
    판정하면 큰 테두리 박스 하나의 bbox 안에 표·이미지·본문이 전부 들어와 '내부의
    모든 것을 포함'으로 오판한다(투영 함정).
    """
    n = len(prims)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _gap(prims[i].rect, prims[j].rect) <= gap_pt:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[Prim]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(prims[i])
    return list(groups.values())


def union_rect(prims: list[Prim]) -> fitz.Rect:
    """prims bbox 의 합집합. fitz 의 __or__/include_rect 는 쓰지 않는다 — 폭이나 높이가
    0인 선분 rect 를 empty 로 보고 상대를 통째로 무시해 버린다."""
    return fitz.Rect(min(p.rect.x0 for p in prims), min(p.rect.y0 for p in prims),
                     max(p.rect.x1 for p in prims), max(p.rect.y1 for p in prims))


def is_hollow_frame(prims: list[Prim], bbox: fitz.Rect) -> bool:
    if any(p.kind == "image" for p in prims):
        return False

    dedup: dict[tuple, fitz.Rect] = {}
    for p in prims:
        r = p.rect
        dedup[(round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))] = r

    w, h = bbox.width, bbox.height
    for r in dedup.values():
        thin_h = r.height <= EDGE_EPS_PT
        thin_v = r.width <= EDGE_EPS_PT
        if thin_h and not thin_v:
            if not (abs(r.y0 - bbox.y0) <= EDGE_EPS_PT or abs(r.y1 - bbox.y1) <= EDGE_EPS_PT):
                if r.width >= DIVIDER_SPAN_FRAC * w:
                    return False           # 내부 가로 구분선 → 표
                continue                   # 짧은 장식선 → 무시
        elif thin_v and not thin_h:
            if not (abs(r.x0 - bbox.x0) <= EDGE_EPS_PT or abs(r.x1 - bbox.x1) <= EDGE_EPS_PT):
                if r.height >= DIVIDER_SPAN_FRAC * h:
                    return False           # 내부 세로 구분선 → 표
                continue
        else:
            return False                   # 면적이 있는 도형 → 실제 그림 요소
    return True


def text_lines_in(doc: Doc, pidx: int, rect: fitz.Rect) -> list[dict]:
    return [ln for ln in doc.lines(pidx, rect.x0 - 2, rect.x1 + 2)
            if not (ln["y1"] < rect.y0 - 1 or ln["y0"] > rect.y1 + 1)]


def looks_like_bogi_or_choice(lines: list[dict]) -> bool:
    for ln in lines:
        txt = ln["text"].strip()
        if txt and (BOGI_HEADING_RE.search(txt) or BOGI_ITEM_RE.match(txt)
                    or CHOICE_LINE_RE.match(txt)):
            return True
    return False


def attach_nearby_labels(doc: Doc, pidx: int, bbox: fitz.Rect, seg: fitz.Rect) -> fitz.Rect:
    out = fitz.Rect(bbox)
    for ln in doc.lines(pidx, seg.x0, seg.x1):
        txt = ln["text"].strip()
        if not txt or len(txt) > ATTACH_MAX_LEN:
            continue
        if BOGI_HEADING_RE.search(txt) or BOGI_ITEM_RE.match(txt) or CHOICE_LINE_RE.match(txt):
            continue
        if not ATTACH_LABEL_RE.match(txt.replace(" ", "")):
            continue
        lr = fitz.Rect(ln["x0"], ln["y0"], ln["x1"], ln["y1"])
        if lr.height > ATTACH_MAX_HEIGHT_PT or _overlap(lr, out) or _gap(lr, out) > ATTACH_GAP_PT:
            continue
        h_ov = max(0.0, min(lr.x1, out.x1) - max(lr.x0, out.x0))
        v_ov = max(0.0, min(lr.y1, out.y1) - max(lr.y0, out.y0))
        if (lr.y0 > out.y1 - 1 or lr.y1 < out.y0 + 1) and h_ov < ATTACH_OVERLAP_FRAC * out.width:
            continue
        if (lr.x0 > out.x1 - 1 or lr.x1 < out.x0 + 1) and v_ov < ATTACH_OVERLAP_FRAC * out.height:
            continue
        out = fitz.Rect(min(out.x0, lr.x0), min(out.y0, lr.y0),
                        max(out.x1, lr.x1), max(out.y1, lr.y1))
    return out


def safe_render_clip(doc: Doc, pidx: int, bbox: fitz.Rect) -> fitz.Rect:
    """bbox 를 RENDER_PAD_PT 만큼 넓히되, 이 그림에 속하지 않는 본문 텍스트 줄 쪽으로는
    넘어가지 않게 각 변을 도로 줄인다."""
    clip = fitz.Rect(bbox.x0 - RENDER_PAD_PT, bbox.y0 - RENDER_PAD_PT,
                     bbox.x1 + RENDER_PAD_PT, bbox.y1 + RENDER_PAD_PT)
    for ln in doc.lines(pidx, clip.x0, clip.x1):
        lr = fitz.Rect(ln["x0"], ln["y0"], ln["x1"], ln["y1"])
        if _overlap(lr, bbox):
            continue
        if lr.y1 <= bbox.y0:
            clip.y0 = max(clip.y0, lr.y1 + SAFE_TEXT_GAP_PT)
        elif lr.y0 >= bbox.y1:
            clip.y1 = min(clip.y1, lr.y0 - SAFE_TEXT_GAP_PT)
        if lr.x1 <= bbox.x0:
            clip.x0 = max(clip.x0, lr.x1 + SAFE_TEXT_GAP_PT)
        elif lr.x0 >= bbox.x1:
            clip.x1 = min(clip.x1, lr.x0 - SAFE_TEXT_GAP_PT)
    return clip & doc.page(pidx).rect


def _warn_if_texty(doc: Doc, pidx: int, bbox: fitz.Rect, warn, qid: str) -> None:
    lines = text_lines_in(doc, pidx, bbox)
    if looks_like_bogi_or_choice(lines):
        return
    if sum(len(ln["text"].strip()) for ln in lines) > 20 and bbox.width * bbox.height > 4000:
        # info 로 남긴다 — 실측 19회차에서 22건 나왔고 대부분 '탐구 과정' 안내 상자처럼
        # 그림이 아닌 텍스트 박스라 정상 동작이다. warn 으로 두면 리포트 attention 30건
        # 상한을 이것만으로 채워 정작 중요한 것이 밀려난다.
        warn(qid, f"자료 후보에서 뺀 테두리 영역에 텍스트가 있다 "
                  f"(page={pidx + 1}, rect={[round(v, 1) for v in bbox]}) — 자료 누락 여부만 확인",
             "info")


def strip_hollow_frames(doc: Doc, pidx: int, prims: list[Prim], warn, qid: str) -> list[Prim]:
    """도형만 엄격 문턱으로 묶어 '속 빈 테두리'를 먼저 제거한다.

    이미지가 섞이기 전에 프레임을 없애야, 그 프레임이 근처 이미지를 잘못 끌어들일
    기회 자체가 사라진다(지질도 이미지↔<보 기> 박스 15pt 사례).
    """
    draws = [p for p in prims if p.kind == "draw"]
    if not draws:
        return prims
    removed: set[int] = set()
    for cl in cluster_primitives(draws, CORNER_GAP_PT):
        bbox = union_rect(cl)
        if not is_hollow_frame(cl, bbox):
            continue
        _warn_if_texty(doc, pidx, bbox, warn, qid)
        removed.update(id(p) for p in cl)
    return [p for p in prims if id(p) not in removed]


def materials_for_segment(doc: Doc, pidx: int, seg: fitz.Rect, warn, qid: str):
    """(bbox, source_kind) 목록을 y0 오름차순으로."""
    page = doc.page(pidx)
    prims = collect_primitives(page, seg)
    if not prims:
        return []
    prims = strip_hollow_frames(doc, pidx, prims, warn, qid)
    if not prims:
        return []

    results = []
    for cl in cluster_primitives(prims, GAP_MERGE_PT):
        bbox = union_rect(cl)
        if is_hollow_frame(cl, bbox):
            _warn_if_texty(doc, pidx, bbox, warn, qid)
            continue
        if bbox.width < MIN_MATERIAL_W_PT or bbox.height < MIN_MATERIAL_H_PT:
            continue
        if looks_like_bogi_or_choice(text_lines_in(doc, pidx, bbox)):
            warn(qid, f"<보기>/선지 텍스트와 겹쳐 제외한 그림 후보가 있다 "
                      f"(page={pidx + 1}, rect={[round(v, 1) for v in bbox]})")
            continue
        bbox = attach_nearby_labels(doc, pidx, bbox, seg)
        if looks_like_bogi_or_choice(text_lines_in(doc, pidx, bbox)):
            bbox = union_rect(cl)      # 라벨 흡수가 과했다 → 되돌린다
        kinds = {p.kind for p in cl}
        source = "images" if kinds == {"image"} else ("drawings" if kinds == {"draw"} else "mixed")
        results.append((bbox, source))

    results.sort(key=lambda t: (round(t[0].y0, 1), round(t[0].x0, 1)))
    return results
