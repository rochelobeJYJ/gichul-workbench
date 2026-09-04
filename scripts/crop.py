# -*- coding: utf-8 -*-
"""`gw crop` — 문제지 PDF에서 문항·자료 크롭 PNG와 대지를 만든다.

    python scripts/gw.py crop --subject earth-science-ii [--only 2024_수능_07] [--dpi 300]

입력 : workspace/<slug>/sources/<exam_id>/problem.pdf (+ manifest.json)
출력 : crops/questions/<qid>.png
       crops/materials/<qid>_m1.png
       crops/questions/_contact_sheet_<exam_id>.png   ← LLM 이 한 장으로 검수하는 대지
       items/<qid>.json (source.page / source.rect / number_box 기록, 나머지 필드는 병합 보존)
       reports/crop.json

`number_box` 는 크롭 이미지 안에서 원래 문항 번호가 차지하던 자리를 이미지 크기 대비
0~1 비율 [left, top, width, height] 로 적은 것이다(croplib/numbox.py). 학습지에서 번호를
1, 2, 3… 으로 다시 매길 때 무엇을 덮어야 하는지가 여기에 있다. 이미 잘라 둔 크롭을
그대로 두고 이 값만 채우려면 `--number-box-only` 를 쓴다.

## 세 가지 경로
1. **direct**  텍스트 레이어가 있다 → 문항 번호 앵커로 자른다(croplib/tamgu.py).
2. **rects**   sources/<exam_id>/crop_rects.json 이 있다 → 그 사각형으로 자른다.
               텍스트로 앵커를 찾을 수 없는 회차를 사람/LLM 이 눈으로 지정한 결과다.
3. **vision**  텍스트 레이어가 없고 rects 도 없다 → 컬럼 단위 이미지를 렌더해 넘기고
               채워 넣을 rects 틀을 reports/ 에 만든다(croplib/vision.py).

## 판형
subject.layout 으로 전략을 고른다. 지금 실동작하는 것은 tamgu-1q1block 뿐이고,
나머지는 무엇을 구현해야 하는지 적힌 NotImplementedError 로 막혀 있다.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import time
from pathlib import Path

import manifest as mf
from common import Report, Space, load_subject
from common.ids import make_qid, split_qid
from common.progress import Progress, track
from croplib import imaging, materials as mat, numbox, qa, vision
from croplib.pdfdoc import Doc
from croplib.tamgu import (ExamPlan, QuestionPlan, Segment, attach_number_boxes,
                           plan_exam)

import fitz

MIN_CROP_H_PT = 48.0        # 이보다 낮은 크롭은 무언가 잘못된 것(원본 120px @ZOOM 2.5)
DEFAULT_DPI = 300
RECTS_FILENAME = "crop_rects.json"
# --number-box-only 가 '그때 그 크롭을 그대로 재구성했다' 고 인정하는 크기 오차(px).
# items 의 source.rect 반올림(소수 2자리) 때문에 1px 은 정상적으로 발생한다.
SIZE_DRIFT_PX = 1
# 대지(contact sheet) 한 장에 담는 칸 수. subject.question_count 와 우연히 같은 20 이지만
# **전혀 다른 값**이다 — 사람이 한 화면에서 훑을 수 있는 칸 수일 뿐이라 과목이 45문항이면
# 대지가 세 장으로 나뉜다. 이름을 붙여 둔 이유가 그것이다(예전엔 리터럴 20 이었다).
CONTACT_SHEET_CELLS = 20


# ══════════════════════════════════════════════════════════
# 판형 전략표 — 새 판형은 여기에 함수를 하나 더 다는 것으로 붙는다
# ══════════════════════════════════════════════════════════
def _plan_tamgu(doc: Doc, subject, exam_id: str) -> ExamPlan:
    # 예전에는 `subject.question_count or 20` 이었다. 20 은 탐구 영역 문항 수라
    # 사실상 과목 하드코딩이고(CONTRACT 0절), question_count 를 아직 안 채운 과목에서
    # **조용히 20문항만 자르고 성공으로 보고**하는 사고를 낸다 — 45문항짜리 국어라면
    # 25문항이 소리 없이 사라진다. 값이 없으면 기본값을 지어내지 말고 멈춘다.
    if not subject.question_count:
        raise ValueError(
            f"subjects/{subject.slug}/subject.json 에 question_count 가 없다 — "
            f"회차당 문항 수를 모르면 문항 앵커 기대치를 세울 수 없다"
        )
    return plan_exam(doc, subject.question_count)


def _plan_passage_group(doc: Doc, subject, exam_id: str) -> ExamPlan:
    raise NotImplementedError(
        "판형 'passage-group'(국어·영어)은 아직 크롭이 구현되지 않았다.\n"
        "  필요한 것: ① 지문 블록과 그에 딸린 문항 묶음(예: 15~17번)을 하나의 영역으로 잡는 앵커,\n"
        "            ② 지문 1회 + 문항 N회로 크롭을 나눠 저장하는 규칙(items 의 크기가 달라진다),\n"
        "            ③ 지문이 페이지를 넘어갈 때의 세그먼트 결합.\n"
        "  croplib/tamgu.py 의 세그먼트 계산은 그대로 재사용할 수 있다."
    )


def _plan_math_mixed(doc: Doc, subject, exam_id: str) -> ExamPlan:
    raise NotImplementedError(
        "판형 'math-mixed'(수학)은 아직 크롭이 구현되지 않았다.\n"
        "  필요한 것: ① 객관식/단답형 구간이 섞인 번호 흐름 처리,\n"
        "            ② 수식이 벡터 도형이라 자료 판별기(croplib/materials.py)의 '속 빈 테두리'\n"
        "               판정이 그대로는 통하지 않는다 — 수식 도형을 자료로 오인하지 않을 기준 필요.\n"
        "  문항 번호 앵커·컬럼 인식은 croplib/tamgu.py 를 그대로 쓸 수 있다."
    )


LAYOUT_STRATEGIES = {
    "tamgu-1q1block": _plan_tamgu,
    "passage-group": _plan_passage_group,
    "math-mixed": _plan_math_mixed,
}


def register(parser) -> None:
    parser.add_argument("--subject", required=True, help="과목 슬러그")
    parser.add_argument("--only", help="처리할 대상. qid 또는 exam_id 를 쉼표로 (예: 2024_수능_07,2025_수능)")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help=f"렌더 해상도 (기본 {DEFAULT_DPI})")
    parser.add_argument("--force", action="store_true", help="이미 있는 크롭도 다시 만든다")
    parser.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 계획만 세운다")
    parser.add_argument("--quiet", action="store_true", help="stdout 에 리포트 경로만 남긴다")
    parser.add_argument("--no-materials", action="store_true", help="자료(그림) 크롭을 건너뛴다")
    parser.add_argument("--workspace", help="작업 공간 경로 직접 지정 (기본 workspace/<slug>)")
    parser.add_argument("--number-box-only", action="store_true",
                        help="크롭 PNG 를 다시 만들지 않고 items 의 number_box 만 채운다")


# ══════════════════════════════════════════════════════════
# 보조
# ══════════════════════════════════════════════════════════
def _selected(only: str | None) -> tuple[set[str] | None, set[str] | None]:
    """--only 를 (exam_id 집합, qid 집합) 으로 나눈다. 둘 다 None 이면 전체."""
    if not only:
        return None, None
    exams: set[str] = set()
    qids: set[str] = set()
    for token in (t.strip() for t in only.split(",")):
        if not token:
            continue
        try:
            exam, _num = split_qid(token)
            qids.add(token)
            exams.add(exam)
        except ValueError:
            exams.add(token)
    return exams, (qids or None)


def _problem_pdf(space: Space, exam_id: str) -> Path | None:
    """계약상 sources/<exam_id>/problem.pdf. manifest 가 다른 이름을 가리키면 따른다.

    예전에는 여기서 manifest 를 직접 열어 `problem_pdf` 나 `files.problem` 을 추측해
    읽었는데, 값이 dict 인 형태(download 가 만드는 schema_version 2)에서는
    `d / dict` 가 TypeError 로 터지는 것을 except 가 삼키고 규약 폴백으로 흘렀다.
    extract 쪽 리더는 반대로 dict 는 알지만 `problem_pdf` 는 몰랐다 — 같은 회차를
    두 명령이 다르게 볼 수 있었다. 이제 판별은 scripts/manifest.py 한 곳에서만 한다.
    """
    return mf.resolve(space, exam_id, "problem")


def _load_rects(space: Space, exam_id: str) -> dict[int, list[Segment]] | None:
    """sources/<exam_id>/crop_rects.json → {번호: [Segment, ...]}.

    사람이나 LLM 이 컬럼 이미지를 보고 손으로 채우는 파일이라 형태를 너그럽게 받는다.
    page 는 사람이 세는 1-based, rect 는 PDF 좌표(pt) [x0, y0, x1, y1].
    문항이 컬럼·페이지를 넘어가는 경우가 있으므로 segments 목록도 받는다 —
    사각형 하나만 받게 만들면 그런 문항은 손으로도 표현할 수 없다.

        "07": {"page": 2, "rect": [88, 250, 419, 520]}
        "08": {"segments": [{"page": 2, "rect": [...]}, {"page": 2, "rect": [...]}]}
    """
    path = space.source_dir(exam_id) / RECTS_FILENAME
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    table = data.get("questions", data)
    out: dict[int, list[Segment]] = {}
    for key, val in table.items():
        if not isinstance(val, dict):
            continue
        try:
            number = int(str(key).rsplit("_", 1)[-1])
        except ValueError:
            continue
        raw = val.get("segments")
        if not raw:
            if not val.get("rect"):
                continue
            raw = [val]
        segs = []
        for part in raw:
            if not part.get("rect"):
                continue
            x0, y0, x1, y1 = (float(v) for v in part["rect"])
            segs.append(Segment(int(part.get("page", val.get("page", 1))) - 1, 0,
                                fitz.Rect(x0, y0, x1, y1)))
        if segs:
            out[number] = segs
    return out or None


def _write_rects_template(space: Space, exam_id: str, expected: int,
                          strips: list[vision.Strip]) -> Path:
    """vision 경로에서 채워 넣을 rects 틀. 완성되면 sources/<exam_id>/crop_rects.json 로 옮긴다."""
    payload = {
        "_설명": (f"컬럼 이미지(crops/questions/_vision_{exam_id}_p*_c*.png)를 보고 각 문항의 "
                 "PDF 좌표 사각형을 채운다. page 는 1부터, rect 는 [x0, y0, x1, y1] (pt). "
                 f"다 채우면 이 파일을 sources/{exam_id}/{RECTS_FILENAME} 로 저장하고 "
                 "crop 을 --force 로 다시 돌린다."),
        "_컬럼_범위": [{"image": f"_vision_{exam_id}_p{s.page + 1}_c{s.col}.png",
                     "page": s.page + 1,
                     "rect": [round(v, 1) for v in s.rect]} for s in strips],
        "questions": {f"{n:02d}": {"page": None, "rect": None} for n in range(1, expected + 1)},
    }
    path = space.reports / f"crop_rects_{exam_id}.template.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _merge_item(space: Space, qid: str, slug: str, exam_id: str, number: int,
                pdf: Path, segments: list[Segment], crop_rel: str,
                material_rels: list[str], mode: str,
                number_box: list[float] | None = None) -> None:
    """items/<qid>.json 병합. crop 이 소유한 필드만 덮어쓴다.

    extract 가 먼저 돌았을 수도 있으므로 text·answer·points·classification·status 는
    건드리지 않는다. extraction_mode 는 크롭이 텍스트 레이어를 실제로 재 본 결과라
    갱신하되, 사람이나 extract 가 'ocr' 로 바꿔 둔 것은 존중한다.
    """
    path = space.item(qid)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data.setdefault("qid", qid)
    data.setdefault("slug", slug)
    data.setdefault("exam_id", exam_id)
    data.setdefault("number", number)
    for key, default in (("points", None), ("answer", None), ("answer_symbol", None),
                         ("text", {}), ("classification", {}), ("status", "scaffold"),
                         ("notes", [])):
        data.setdefault(key, default)

    first = segments[0]
    data["source"] = {
        "pdf": space.rel(pdf),
        "page": first.page + 1,                     # 사람이 세는 1-based (계약 예시와 동일)
        "rect": [round(v, 2) for v in first.rect],
        # 문항이 컬럼·페이지를 넘어가면 사각형이 여러 개다. 계약의 source 에는 한 개
        # 자리밖에 없어 첫 조각을 page/rect 에 두고 전체는 여기에 남긴다.
        "segments": [{"page": s.page + 1, "rect": [round(v, 2) for v in s.rect]}
                     for s in segments],
    }
    data["crop"] = crop_rel
    data["materials"] = material_rels
    # 번호 자리를 못 믿을 때는 **키를 지운다**(계약 0절: 조용한 기본값 금지).
    # 예전 실행이 남긴 값을 그대로 두면, 앵커가 달라진 뒤에도 엉뚱한 자리를 덮는다.
    if number_box:
        data["number_box"] = number_box
    else:
        data.pop("number_box", None)
    if data.get("extraction_mode") in (None, "", "direct", "vision"):
        data["extraction_mode"] = mode

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ══════════════════════════════════════════════════════════
# 회차 하나 처리
# ══════════════════════════════════════════════════════════
def _render_question(doc: Doc, segments: list[Segment], zoom: float):
    """세그먼트를 렌더·정리해 (이미지, 여백, 제거된 세로선, 첫 조각 배치) 를 돌려준다.

    네 번째 값 `place` 는 첫 세그먼트의 PDF 좌표를 이 이미지의 픽셀로 옮기는 데 필요한
    보정값 묶음이다(croplib/numbox.py). 문항 번호는 앵커가 된 첫 조각에만 있으므로
    첫 조각 것만 만든다.
    """
    imgs, strips = [], []
    place = None
    for seg in segments:
        raw, origin = imaging.render_rect_at(doc.page(seg.page), seg.rect, zoom)
        im, removed = imaging.strip_edge_rules(raw, zoom)
        if place is None:
            place = numbox.placement(seg.page, origin, removed)
        imgs.append(im)
        strips.extend(removed)
    merged = imaging.stitch(imgs)
    if place is not None and len(imgs) > 1:
        # stitch 는 조각을 캔버스 가운데에 붙인다. 첫 조각이 가장 넓지 않으면 x 가
        # 그만큼 밀리므로 같은 식을 여기서 한 번 더 쓴다.
        place.paste_x = (merged.width - imgs[0].width) // 2
    # 여백 측정은 트리밍 '전'에 해야 한다 — 트리밍 후에는 모든 변이 패딩값으로 같아져
    # '어느 변에서 잘렸는지'를 영영 알 수 없다.
    margins = imaging.content_margins(merged)
    return merged, margins, strips, place


def _crop_exam(doc: Doc, space: Space, subject, exam_id: str, pdf: Path,
               plan_questions: list[QuestionPlan], mode: str, args, report: Report,
               warn, bar: Progress | None = None) -> list[dict]:
    """계획대로 잘라 저장하고 대지용 셀 목록을 돌려준다.

    QA 가 error 를 낸 문항도 **파일은 남긴다.** 무엇이 잘못됐는지는 결국 그림을 봐야
    알 수 있고, 파일이 없으면 대지에 빈 칸만 남아 원인 추적이 더 어려워지기 때문이다.
    대신 done 이 아니라 failed 로 세어 리포트에서 반드시 눈에 걸리게 한다.
    """
    zoom = imaging.zoom_for(args.dpi)
    _exams, only_qids = _selected(args.only)
    has_text = doc.has_text_layer()
    cells: list[dict] = []

    # 진행률은 문항 단위로 오른다 — 사용자가 세는 단위가 그것이다('문항 137/380').
    # 갈래가 여섯이라 갈래마다 advance() 를 심으면 하나만 놓쳐도 숫자가 어긋난다. 반복을 감싼다.
    for qp in (bar.wrap(plan_questions) if bar is not None else plan_questions):
        qid = make_qid(exam_id, qp.number)
        out_png = space.question_png(qid)
        if only_qids and qid not in only_qids:
            # 고르지 않은 문항도 대지에는 넣는다. 안 그러면 --only 로 한 문항만 다시
            # 자를 때 그 회차 대지가 한 칸짜리로 덮어써져 검수 기준이 사라진다.
            cells.append({"label": qid, "path": out_png if out_png.exists() else None,
                          "flags": {}})
            continue
        if not qp.segments:
            warn(qid, "크롭 영역을 계산하지 못했다 — 앵커 다음에 유효한 세그먼트가 없다", "error")
            report.bump("failed")
            cells.append({"label": qid, "path": None, "flags": {}})
            continue

        if out_png.exists() and not args.force:
            report.bump("skipped")
            cells.append({"label": qid, "path": out_png, "flags": {}})
            continue

        try:
            img, margins, stripped, place = _render_question(doc, qp.segments, zoom)
        except Exception as exc:                      # noqa: BLE001
            warn(qid, f"렌더 실패: {exc}", "error")
            report.bump("failed")
            cells.append({"label": qid, "path": None, "flags": {}})
            continue

        if img.height < MIN_CROP_H_PT * zoom:
            warn(qid, f"크롭이 너무 낮다({img.height}px) — 앵커가 어긋났을 수 있다", "error")
            report.bump("failed")
            cells.append({"label": qid, "path": None, "flags": {}})
            continue
        if imaging.is_blank(img):
            warn(qid, "크롭이 비어 있다(흰 이미지)", "error")
            report.bump("failed")
            cells.append({"label": qid, "path": None, "flags": {}})
            continue

        text = qa.crop_text(doc, qp.segments) if has_text else ""
        findings = qa.inspect(qp.number, text, margins, zoom, has_text)
        flags = qa.edge_flags(margins, zoom)
        failed = any(sev == "error" for sev, _ in findings)
        for sev, why in findings:
            warn(qid, why, sev)
        if stripped:
            warn(qid, f"가장자리 세로 실선을 제거했다: {stripped}", "info")

        # 글자가 전부 벡터 도형인 회차에서는 모든 글자가 '그림 후보'가 되어 자료 판별기가
        # 의미를 잃는다(페이지당 도형 1100개). 그럴 때는 자료 크롭을 건너뛴다 —
        # 안내는 문항마다가 아니라 회차마다 한 번만 남긴다(attention 30건 상한 방어).
        material_rels: list[str] = []
        if not args.no_materials and has_text:
            material_rels = _crop_materials(doc, space, qid, qp.segments, zoom, args, warn)

        # 문항 번호가 최종 크롭 안에서 차지하는 자리. 트리밍 사각형을 먼저 받아
        # 비율을 계산한 뒤 같은 사각형으로 자른다 — 저장되는 픽셀은 종전과 같다.
        tbox = imaging.trim_box(img, zoom, imaging.QUESTION_PAD_PT)
        number_box = numbox.ratio(qp.num_page, qp.num_rect, place, zoom, tbox)

        if not args.dry_run:
            out_png.parent.mkdir(parents=True, exist_ok=True)
            imaging.apply_box(img, tbox).save(out_png)
            _merge_item(space, qid, subject.slug, exam_id, qp.number, pdf,
                        qp.segments, space.rel(out_png), material_rels, mode,
                        number_box)
        if number_box is None:
            report.bump("number_box_missing")

        report.bump("failed" if failed else "done")
        report.bump("materials", len(material_rels))
        cells.append({"label": qid, "path": out_png if not args.dry_run else None,
                      "flags": flags})
    return cells


def _crop_materials(doc: Doc, space: Space, qid: str, segments: list[Segment],
                    zoom: float, args, warn) -> list[str]:
    rels: list[str] = []
    index = 0
    for seg in segments:
        for bbox, _source in mat.materials_for_segment(doc, seg.page, seg.rect, warn, qid):
            index += 1
            out = space.material_png(qid, index)
            if out.exists() and not args.force:
                rels.append(space.rel(out))
                continue
            clip = mat.safe_render_clip(doc, seg.page, bbox)
            try:
                img = imaging.trim(imaging.render_rect(doc.page(seg.page), clip, zoom),
                                   zoom, imaging.MATERIAL_PAD_PT)
            except Exception as exc:                  # noqa: BLE001
                warn(qid, f"자료 m{index} 렌더 실패: {exc}", "warn")
                continue
            if img.width < 12 or img.height < 12:
                warn(qid, f"자료 m{index} 렌더 결과가 너무 작다({img.width}x{img.height})", "warn")
                continue
            if not args.dry_run:
                out.parent.mkdir(parents=True, exist_ok=True)
                img.save(out)
            rels.append(space.rel(out))
    return rels


def _vision_exam(doc: Doc, space: Space, exam_id: str, expected: int, skipped: int,
                 args, report: Report, warn) -> list[dict]:
    """텍스트 레이어가 없는 회차 — 컬럼 단위 이미지를 렌더해 넘긴다."""
    zoom = imaging.zoom_for(args.dpi)
    strips = vision.plan_strips(doc)
    cells = []
    for s in strips:
        name = f"_vision_{exam_id}_p{s.page + 1}_c{s.col}.png"
        out = space.questions / name
        if not args.dry_run and (args.force or not out.exists()):
            out.parent.mkdir(parents=True, exist_ok=True)
            imaging.render_rect(doc.page(s.page), s.rect, zoom).save(out)
        cells.append({"label": name[1:], "path": out if not args.dry_run else None, "flags": {}})
    tpl = None
    if not args.dry_run:
        tpl = _write_rects_template(space, exam_id, expected, strips)
        report.artifact(space.rel(tpl))
    warn(exam_id,
         f"텍스트 레이어가 없다 — 문항 번호를 텍스트로 찾을 수 없어 컬럼 {len(strips)}장을 "
         f"렌더했다(vision 경로). {space.rel(tpl) if tpl else 'reports/crop_rects_*.template.json'} "
         f"를 채워 sources/{exam_id}/{RECTS_FILENAME} 로 저장한 뒤 --force 로 다시 돌린다.",
         "warn")
    report.bump("vision_exams")
    report.bump("skipped", skipped)
    return cells


# ══════════════════════════════════════════════════════════
# number_box 만 다시 채우는 가벼운 길 (--number-box-only)
# ══════════════════════════════════════════════════════════
def _refill_number_boxes(space: Space, subject, args, report: Report, warn) -> int:
    """크롭 PNG·자료·대지를 만들지 않고 items 의 `number_box` 만 채운다.

    380문항 재크롭이 비싸서 두는 길이다. 그래도 PDF 는 다시 읽어야 한다 —
    number_box 는 '최종 크롭 안에서의 비율'이고, 최종 크기는 트리밍과 가장자리
    실선 제거를 거친 **픽셀**에서만 나오기 때문이다. 대신 PNG 인코딩·자료 판별·
    대지 합성·items 의 다른 필드를 전부 건너뛴다(지구과학Ⅱ 20회차 실측 145s → 66s).

    사각형은 계획을 다시 세우지 않고 **items 에 적힌 source.segments** 를 쓴다.
    그 사이 앵커 알고리즘이 바뀌었어도 그때 실제로 렌더한 자리와 어긋나지 않는다.
    재구성이 맞았는지는 '계산한 크기 == 기존 PNG 크기' 로 문항마다 확인한다 —
    맞지 않으면(대개 --dpi 가 그때와 다르다) 값을 쓰지 않고 신고한다.
    """
    zoom = imaging.zoom_for(args.dpi)
    sel_exams, sel_qids = _selected(args.only)
    exams = [e for e in space.iter_exams() if not sel_exams or e in sel_exams]
    filled = 0
    # 회차당 2.5초라도 19회차면 1분이다 — 여기도 표시가 없으면 멈춘 줄 안다.
    for exam_id in track(exams, "회차", label="crop", args=args, detail=str):
        items = sorted(space.items.glob(f"{exam_id}_*.json"))
        if sel_qids:
            items = [p for p in items if p.stem in sel_qids]
        if not items:
            continue
        pdf = _problem_pdf(space, exam_id)
        if pdf is None:
            warn(exam_id, "problem.pdf 가 없다 — number_box 를 계산할 수 없다", "error")
            report.bump("skipped", len(items))
            continue
        try:
            doc = Doc(pdf)
        except Exception as exc:                      # noqa: BLE001
            warn(exam_id, f"PDF 를 열 수 없다: {exc}", "error")
            report.bump("skipped", len(items))
            continue
        try:
            plans = [QuestionPlan(int(p.stem.rsplit("_", 1)[-1])) for p in items]
            if attach_number_boxes(doc, plans, subject.question_count) == 0:
                warn(exam_id, "번호 토큰을 찾을 수 없다(텍스트 레이어 없음) — number_box 없이 둔다",
                     "info")
                report.bump("skipped", len(items))
                continue
            for path, qp in zip(items, plans):
                report.bump("checked")
                data = json.loads(path.read_text(encoding="utf-8"))
                raw_segs = numbox.segments_from_item(data)
                png = space.root / str(data.get("crop") or "")
                size = numbox.png_size(png) if data.get("crop") else None
                if not raw_segs or size is None:
                    warn(path.stem, "source.segments 나 크롭 PNG 가 없다 — 건너뛴다", "warn")
                    report.bump("skipped")
                    continue
                segs = [Segment(pg, 0, fitz.Rect(*rc)) for pg, rc in raw_segs]
                try:
                    img, _margins, _strips, place = _render_question(doc, segs, zoom)
                except Exception as exc:              # noqa: BLE001
                    warn(path.stem, f"렌더 실패: {exc}", "error")
                    report.bump("failed")
                    continue
                tbox = imaging.trim_box(img, zoom, imaging.QUESTION_PAD_PT)
                dw = abs((tbox[2] - tbox[0]) - size[0])
                dh = abs((tbox[3] - tbox[1]) - size[1])
                if max(dw, dh) > SIZE_DRIFT_PX:
                    warn(path.stem,
                         f"재구성 크기가 기존 크롭과 다르다({tbox[2] - tbox[0]}x{tbox[3] - tbox[1]} "
                         f"vs {size[0]}x{size[1]}) — --dpi 를 크롭 당시 값으로 맞춰라", "error")
                    report.bump("failed")
                    continue
                if dw or dh:
                    # items 의 source.rect 는 소수 2자리로 반올림돼 저장된다. 그 0.005pt 가
                    # 픽셀 격자 경계에 걸리면 재구성이 1px 어긋난다(실측 380문항 중 1건).
                    # 1px 은 상자 위치로 환산하면 0.07% 라 무시해도 되는 크기다.
                    report.bump("size_drift")
                box = numbox.ratio(qp.num_page, qp.num_rect, place, zoom, tbox)
                if box is None:
                    warn(path.stem, "번호 자리가 크롭 밖이거나 비율이 이상하다 — 키를 넣지 않는다",
                         "warn")
                    report.bump("number_box_missing")
                    if data.pop("number_box", None) is not None and not args.dry_run:
                        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
                    continue
                if data.get("number_box") != box:
                    data["number_box"] = box
                    if not args.dry_run:
                        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
                filled += 1
                report.bump("number_box")
        finally:
            doc.close()
    return filled


# ══════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════
def run(args) -> int:
    started = time.time()
    # 과목 정의를 못 읽으면 리포트를 남길 workspace 경로조차 정할 수 없다.
    # gw.py 는 예외를 그대로 흘려보내서 LLM 이 리포트 대신 traceback 을 읽게 된다
    # (통합 검증에서 7개 명령 중 5개가 그랬다). 형제 모듈(build/classify)과 같은
    # 한 줄 안내로 끝낸다 — load_subject 의 메시지가 등록된 과목 목록까지 담고 있다.
    try:
        subject = load_subject(args.subject)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] crop: {exc}")
        return 1
    space = Space(subject.slug, args.workspace)
    space.ensure()
    report = Report("crop", subject.slug, space)

    def warn(ident: str, why: str, severity: str = "warn") -> None:
        report.note(ident, why, severity)

    if args.number_box_only:
        # 크롭을 다시 만들지 않는 길. items 가 이미 있어야 하므로 판형 전략을 보지 않는다.
        # question_count 는 번호 토큰 상한이라 여기서도 필요하다(계약 0절: 기본값 금지).
        if not subject.question_count:
            msg = (f"subjects/{subject.slug}/subject.json 에 question_count 가 없다 — "
                   f"문항 번호 토큰의 상한을 세울 수 없다")
            warn(subject.slug, msg, "error")
            report.next = f"subjects/{subject.slug}/subject.json 에 question_count 를 채운다"
            print(msg)
            return report.finish()
        report.count(number_box=0, checked=0, failed=0, skipped=0,
                     number_box_missing=0, size_drift=0)
        n = _refill_number_boxes(space, subject, args, report, warn)
        report.artifact(space.rel(space.items))
        report.next = (f"python scripts/gw.py build --subject {subject.slug}" if n
                       else f"python scripts/gw.py crop --subject {subject.slug} --force")
        report.extra["dpi"] = args.dpi
        report.extra["dry_run"] = bool(args.dry_run)
        return report.finish()

    planner = LAYOUT_STRATEGIES.get(subject.layout)
    if planner is None:
        msg = (f"판형 {subject.layout!r} 에 대한 크롭 전략이 없다. "
               f"가능한 값: {', '.join(LAYOUT_STRATEGIES)}")
        warn(subject.slug, msg, "error")
        report.next = "subjects/<slug>/subject.json 의 layout 을 확인한다"
        print(msg)
        return report.finish()

    sel_exams, sel_qids = _selected(args.only)
    exams = [e for e in space.iter_exams() if not sel_exams or e in sel_exams]
    if not exams:
        warn(subject.slug,
             f"자를 회차가 없다: {space.rel(space.sources)} 아래에 <exam_id>/problem.pdf 가 필요하다",
             "error")
        report.next = f"python scripts/gw.py download --subject {subject.slug}"
        return report.finish()

    # 문항 수는 subject.json 의 검증 불변식이다(CONTRACT 3절). 여기에 기본값 20 을
    # 두면 그게 곧 '탐구 영역 전용' 하드코딩이라, question_count 를 안 채운 과목에서
    # 조용히 20문항만 자르고 done=20 으로 성공 보고를 한다. 채우라고 말하고 멈춘다.
    if not subject.question_count:
        msg = (f"subjects/{subject.slug}/subject.json 에 question_count 가 없다 — "
               f"회차당 문항 수를 모르면 크롭 기대치를 세울 수 없다")
        warn(subject.slug, msg, "error")
        report.next = f"subjects/{subject.slug}/subject.json 에 question_count 를 채운다"
        print(msg)
        return report.finish()

    expected_each = subject.question_count
    # --only 로 문항을 콕 집었으면 기대치도 그 개수다. 회차 전체 수로 두면 done=1 이 실패처럼 보인다.
    per_exam = len(sel_qids) if sel_qids else expected_each
    report.count(expected=per_exam if sel_qids else expected_each * len(exams),
                 done=0, failed=0, skipped=0, materials=0, exams=len(exams),
                 number_box_missing=0)
    pending_vision: list[str] = []

    # 380문항이면 몇 분이 걸린다. 표시가 없으면 멈춘 것과 구분되지 않는다.
    # 세는 단위는 사용자가 세는 단위(문항)다. 총량은 **이 실행이 실제로 훑을 문항 수**이고
    # 리포트의 expected 와 다를 수 있다 — `--only <qid>` 로 한 문항만 골라도 앵커 계획은
    # 그 회차 전체를 훑기 때문이다. 리포트 숫자는 손대지 않는다(진행률은 표시일 뿐이다).
    bar = Progress(expected_each * len(exams), "문항", label="crop", args=args).open()

    for exam_id in exams:
        bar.detail(exam_id)
        pdf = _problem_pdf(space, exam_id)
        if pdf is None:
            warn(exam_id, "problem.pdf 가 없다", "error")
            report.bump("skipped", per_exam)
            bar.advance(expected_each)
            continue

        try:
            doc = Doc(pdf)
        except Exception as exc:                      # noqa: BLE001
            warn(exam_id, f"PDF 를 열 수 없다: {exc}", "error")
            report.bump("skipped", per_exam)
            bar.advance(expected_each)
            continue

        try:
            rects = _load_rects(space, exam_id)
            has_text = doc.has_text_layer()

            if not has_text and not args.no_materials:
                warn(exam_id, "텍스트 레이어가 없어 자료(그림) 판별을 건너뛴다 — 자료는 문항 크롭 안에 있다",
                     "info")

            if rects:
                # 사람/LLM 이 지정한 사각형. 텍스트로 앵커를 못 찾는 회차의 정답 경로다.
                questions = [QuestionPlan(n, segs) for n, segs in sorted(rects.items())]
                # 사각형은 사람이 지정했어도 번호 토큰은 텍스트에서 찾을 수 있다.
                attach_number_boxes(doc, questions, expected_each)
                mode = "vision" if not has_text else "direct"
                missing = [n for n in range(1, expected_each + 1) if n not in rects]
                if missing:
                    warn(exam_id, f"{RECTS_FILENAME} 에 사각형이 없는 문항: {missing}", "error")
                cells = _crop_exam(doc, space, subject, exam_id, pdf, questions, mode,
                                   args, report, warn, bar)
            elif not has_text:
                pending_vision.append(exam_id)
                cells = _vision_exam(doc, space, exam_id, expected_each, per_exam,
                                     args, report, warn)
                # vision 회차는 문항이 아니라 컬럼 단위로 렌더한다. 문항 진행률을 문항마다
                # 올릴 방법이 없으니 회차가 끝난 자리에서 한 번에 올린다.
                bar.advance(expected_each)
            else:
                try:
                    plan = planner(doc, subject, exam_id)
                except NotImplementedError as exc:
                    # 리포트에는 한 줄만 남긴다(계약 5절: next 는 복구 명령 한 줄).
                    # 무엇을 구현해야 하는지는 stdout 과 함수 docstring 에 길게 적혀 있다.
                    warn(exam_id, str(exc).splitlines()[0], "error")
                    report.next = (f"subjects/{subject.slug}/subject.json 의 layout 을 확인한다 — "
                                   f"crop 은 아직 tamgu-1q1block 만 처리한다 "
                                   f"(구현 안내는 scripts/crop.py 의 _plan_* 함수)")
                    # 진행률 줄을 **먼저** 지운다. 순서가 바뀌면 안내문 위로 지우개가 지나간다.
                    bar.close()
                    print(exc)
                    return report.finish()
                if plan.missing:
                    warn(exam_id, f"문항 번호 앵커를 찾지 못했다: {plan.missing}", "error")
                if plan.duplicated:
                    warn(exam_id, f"같은 번호가 여러 번 나온다(첫 것을 썼다): {plan.duplicated}", "warn")
                if len(plan.columns) not in (1, 2, 3):
                    warn(exam_id, f"컬럼 {len(plan.columns)}개로 인식됐다 — 판형 확인 필요", "warn")
                cells = _crop_exam(doc, space, subject, exam_id, pdf, plan.questions,
                                   "direct", args, report, warn, bar)

            if cells and not args.dry_run:
                for i in range(0, len(cells), CONTACT_SHEET_CELLS):
                    suffix = "" if i == 0 else f"_{i // CONTACT_SHEET_CELLS + 1}"
                    sheet = space.questions / f"_contact_sheet_{exam_id}{suffix}.png"
                    imaging.contact_sheet(cells[i:i + CONTACT_SHEET_CELLS], sheet,
                                          f"{subject.label}  {exam_id}  ({args.dpi}dpi)")
                    report.artifact(space.rel(sheet))
        finally:
            doc.close()

    bar.close()   # 요약을 찍기 전에 진행률 줄을 지운다(stdout 과 겹치지 않게)
    report.artifact(space.rel(space.questions))
    report.artifact(space.rel(space.materials))
    failed = report.counts.get("failed", 0)
    if pending_vision:
        report.next = (f"crops/questions/_vision_* 를 보고 reports/crop_rects_*.template.json 을 채운 뒤 "
                       f"sources/<exam_id>/{RECTS_FILENAME} 로 저장하고 "
                       f"python scripts/gw.py crop --subject {subject.slug} "
                       f"--only {','.join(pending_vision)} --force")
    elif failed:
        report.next = (f"대지(_contact_sheet_*.png)를 보고 원인을 확인한 뒤 "
                       f"python scripts/gw.py crop --subject {subject.slug} --only <qid> --force")
    else:
        report.next = f"python scripts/gw.py extract --subject {subject.slug}"
    report.extra["dpi"] = args.dpi
    report.extra["dry_run"] = bool(args.dry_run)
    report.extra["elapsed_hint_sec"] = round(time.time() - started, 1)

    if args.quiet:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = report.finish()
        print(space.report("crop"))
        return code
    return report.finish()


if __name__ == "__main__":                            # 단독 실행 편의(gw.py 가 정식 경로)
    import argparse

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    p = argparse.ArgumentParser(prog="gw crop")
    register(p)
    raise SystemExit(run(p.parse_args()))
