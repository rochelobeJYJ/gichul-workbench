# -*- coding: utf-8 -*-
"""파일 수준: 회차 폴더에서 무엇을 읽을지, 그 파일에서 텍스트를 어떻게 꺼낼지.

핵심 설계: **한 파일에서 나온 텍스트를 한 종류만 들고 다니지 않는다.**
2022학년도 수능 해설 PDF 에서 배운 것 — 본문 글리프는 깨졌는데(CID 인코딩 손상)
첫머리 정답표의 아스키 숫자는 멀쩡했다. 그때 "한글 비율이 낮으니 OCR 로 갈아타자"
하고 직접 텍스트 레이어를 버리면 가장 신뢰도 높은 정답 소스를 스스로 버리는 꼴이 된다.
그래서 TextLayers 는 direct / plumber / ocr 을 **함께** 들고 있고,
쓰는 쪽이 목적에 맞는 레이어를 고른다.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import manifest as mf

from .textnorm import hangul_ratio, normalize_text, unmapped_pua

# 회차 폴더 안의 역할별 파일. docs/CONTRACT.md 1절.
ROLES = ("problem", "answer", "solution")
DOC_SUFFIXES = (".pdf",)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

OCR_SCRIPT = Path(__file__).resolve().parent / "win_ocr.ps1"

# 텍스트 레이어가 "있다"고 볼 최소 길이. 문자를 벡터로 아웃라인화한 문제지는
# 페이지당 몇 글자만 나온다(2025학년도 수능 지구과학Ⅱ 는 4쪽 통틀어 3글자였다).
MIN_TEXT_LAYER_CHARS = 500
# 글리프가 깨졌다고 볼 한글 비율 경계. 정상 문제지는 0.65~0.85 사이에 있다.
MIN_HANGUL_RATIO = 0.40


class OcrUnavailable(RuntimeError):
    """Windows OCR 을 쓸 수 없다. 호출한 쪽이 vision 모드로 넘어가야 한다."""


@dataclass
class ExamSources:
    exam_id: str
    directory: Path
    files: dict[str, Path] = field(default_factory=dict)

    def get(self, role: str) -> Path | None:
        return self.files.get(role)


def find_sources(space, exam_id: str) -> ExamSources:
    """manifest.json 을 먼저 믿고, 없으면 파일명 규약으로 찾는다.

    detect 단계가 manifest 를 남기지만 extract 는 그것 없이도 돌아야 한다.
    파이프라인 앞단을 다시 돌리지 않고 extract 만 재실행하는 일이 잦기 때문이다.

    manifest 형태 판별(문자열/딕셔너리/옛 problem_pdf)은 예전에 이 함수가 직접 했다.
    같은 판별을 crop 도 따로 하고 있었고 둘이 커버하는 형태가 달라서, 파일명이 규약과
    다른 회차에서 두 명령의 판단이 갈릴 수 있었다. 이제 scripts/manifest.py 한 곳만 안다.
    """
    directory = space.source_dir(exam_id)
    found: dict[str, Path] = {}
    for role in ROLES:
        # mf.resolve 가 manifest → 파일명 규약(.pdf → 이미지) 순서까지 담당한다.
        candidate = mf.resolve(space, exam_id, role)
        if candidate is not None:
            found[role] = candidate
    return ExamSources(exam_id=exam_id, directory=directory, files=found)


# --------------------------------------------------------------------------
# 텍스트 레이어
# --------------------------------------------------------------------------

@dataclass
class TextLayers:
    """한 파일에서 나올 수 있는 모든 텍스트. 필요한 것만 채워진다."""

    path: Path
    direct: str = ""      # fitz(PyMuPDF) 텍스트 레이어
    plumber: str = ""     # pdfplumber layout 모드 — 다단/표를 좌표대로 편다
    ocr: str = ""         # Windows OCR (단 정렬 적용)
    ocr_error: str = ""
    # 매핑표에 없어 normalize_text 가 지워 버린 수식 폰트 글자 {글자: 개수}.
    # 지워진 뒤에는 흔적이 남지 않아(문장이 멀쩡해 보인다) 여기서만 알 수 있다.
    unmapped_pua: dict = field(default_factory=dict)

    @property
    def direct_usable(self) -> bool:
        """직접 텍스트 레이어를 본문 파싱에 쓸 수 있는가."""
        return (len(self.direct) >= MIN_TEXT_LAYER_CHARS
                and hangul_ratio(self.direct) >= MIN_HANGUL_RATIO)

    def body(self) -> tuple[str, str]:
        """(본문용 텍스트, 모드). 모드는 items 의 extraction_mode 값이 된다."""
        if self.direct_usable:
            return self.direct, "direct"
        if self.ocr:
            return self.ocr, "ocr"
        return "", "vision"


def read_pdf_fitz(path: Path) -> tuple[str, dict[str, int]]:
    """(정규화한 텍스트, 매핑 못 한 수식 폰트 글자 수).

    두 번째 값을 함께 돌려주는 이유: normalize_text 는 매핑표에 없는 사설 영역
    글자를 **말없이 버린다**. 버려진 뒤의 문장은 멀쩡해 보이므로('Al(s)' → 'Al()')
    원문을 손에 쥐고 있는 이 자리에서 세지 않으면 영영 알 수 없다.
    """
    import fitz

    document = fitz.open(path)
    try:
        raw_pages = [page.get_text("text") for page in document]
    finally:
        document.close()
    leftovers: dict[str, int] = {}
    for raw in raw_pages:
        for char, count in unmapped_pua(raw).items():
            leftovers[char] = leftovers.get(char, 0) + count
    pages = [normalize_text(raw) for raw in raw_pages]
    return "\n\n".join(page for page in pages if page), leftovers


def read_pdf_plumber(path: Path) -> str:
    """pdfplumber 의 layout 모드.

    fitz 와 실패 지점이 다르다는 것이 존재 이유다. 특히 정답표가
    '01.' '02.' … 번호 열과 '3' '5' … 정답 열로 쪼개져 나오는 판형에서,
    fitz 는 열 단위로 토해내지만 pdfplumber layout 은 좌표대로 한 줄에 붙여 준다.
    즉 칼럼분리형 판형이 저절로 인라인형이 되어 같은 파서로 읽힌다.
    """
    import pdfplumber

    chunks: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            try:
                text = page.extract_text(layout=True) or ""
            except Exception:
                text = page.extract_text() or ""
            if text:
                chunks.append(normalize_text(text))
    return "\n\n".join(chunks)


def read_pdf_page_tables(path: Path) -> list[tuple[str, list]]:
    """페이지별 (텍스트, 표 목록).

    문서 전체의 표를 한 자루에 담아 오면 안 된다 — 평가원 정답표 PDF 한 개에
    한 교시의 8개 과목이 페이지별로 들어 있다. 전부 합치면 첫 과목(물리학Ⅰ)의 표를
    우리 과목 정답으로 읽어 버린다. 실제로 겪은 사고이고, 3중 대조의 다수결이
    막아 주긴 했지만 축 하나가 통째로 오염되는 것이라 반드시 페이지 단위로 걸러야 한다.
    """
    import pdfplumber

    pages: list[tuple[str, list]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            try:
                text = normalize_text(page.extract_text() or "")
            except Exception:
                text = ""
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []
            pages.append((text, tables))
    return pages


# --------------------------------------------------------------------------
# OCR — Windows 내장 엔진
# --------------------------------------------------------------------------

def _render_to_pngs(path: Path, out_dir: Path, zoom: float = 3.0) -> list[Path]:
    """PDF 는 페이지마다, 이미지는 그대로(확대해서) PNG 로 뽑는다.

    렌더링을 파이썬이 맡는 이유: 정답지가 PNG 로만 제공되는 회차가 많아
    'PDF 전용 OCR' 은 반쪽짜리가 된다. 확대율도 여기서 조절해야 한다 —
    원본 정답표 PNG 는 폭 600px 이라 등배로 넣으면 OCR 이 거의 못 읽는다.
    """
    if path.suffix.lower() in IMAGE_SUFFIXES:
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGB")
            size = (max(1, int(image.width * zoom)), max(1, int(image.height * zoom)))
            target = out_dir / "img000.png"
            image.resize(size, Image.LANCZOS).save(target)
        return [target]

    import fitz

    outputs: list[Path] = []
    document = fitz.open(path)
    try:
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            target = out_dir / f"page{index:03d}.png"
            pixmap.save(target)
            outputs.append(target)
    finally:
        document.close()
    return outputs


def ocr_pages(path: Path, zoom: float = 3.0, timeout: int = 900) -> list[dict]:
    """Windows 내장 OCR 로 페이지별 줄 텍스트 + 좌표를 얻는다."""
    if not OCR_SCRIPT.exists():
        raise OcrUnavailable(f"OCR 스크립트가 없다: {OCR_SCRIPT}")
    with tempfile.TemporaryDirectory(prefix="gw_ocr_") as tmp:
        tmp_dir = Path(tmp)
        try:
            images = _render_to_pngs(path, tmp_dir, zoom=zoom)
        except Exception as exc:
            raise OcrUnavailable(f"렌더링 실패: {exc}") from exc
        if not images:
            return []
        list_file = tmp_dir / "images.txt"
        list_file.write_text("\n".join(str(p) for p in images) + "\n", encoding="utf-8")
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(OCR_SCRIPT), "-ListFile", str(list_file)],
                capture_output=True, text=True, encoding="utf-8",
                timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OcrUnavailable(f"OCR 실행 실패: {exc}") from exc
        if completed.returncode != 0 or not (completed.stdout or "").strip():
            detail = [line for line in (completed.stderr or "").splitlines() if line.strip()]
            raise OcrUnavailable("OCR 실패: " + (detail[0].strip() if detail else "출력 없음"))
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise OcrUnavailable(f"OCR 출력이 JSON 이 아니다: {exc}") from exc


def ocr_text(path: Path, columns: int = 2, zoom: float = 3.0) -> str:
    """OCR 결과를 단(column) 순서대로 이어 붙인 텍스트.

    이것이 OCR 에서 좌표까지 받아오는 이유다. 탐구 문제지는 2단 편집이라
    OCR 이 돌려주는 자연 읽기 순서는 두 단을 가로로 오가며 뒤섞인다.
    그대로 쓰면 1번 문항과 11번 문항의 문장이 섞여 문항 분리가 아예 불가능해진다.
    (원본 구현이 2025학년도 수능에서 'OCR 도 문항 분리 불가' 판정을 내린 원인이 이것이다.)
    x 중심으로 단을 가르고 단 안에서 y 로 정렬하면 원래 읽기 순서가 복원된다.
    columns 는 판형 전략이 알려준다 — 1단 판형이면 1을 넘기면 된다.
    """
    parts: list[str] = []
    for page in ocr_pages(path, zoom=zoom):
        width = float(page.get("width") or 0) or 1.0
        lines = page.get("lines") or []

        def key(line):
            box = line.get("bbox") or [0, 0, 0, 0]
            center = (float(box[0]) + float(box[2])) / 2
            column = min(int(center / width * columns), columns - 1) if columns > 1 else 0
            return (column, float(box[1]), float(box[0]))

        parts.append("\n".join(line.get("text", "") for line in sorted(lines, key=key)))
    return normalize_text("\n".join(parts))


def load_layers(path: Path | None, *, want_plumber: bool = True,
                want_ocr: bool = False, columns: int = 2) -> TextLayers | None:
    """한 파일의 텍스트 레이어를 필요한 만큼 채워서 돌려준다.

    want_ocr 은 '무조건 OCR 하라'가 아니라 '직접 레이어가 못 쓸 때 시도해도 좋다'는 뜻.
    OCR 은 회차당 수십 초가 들기 때문에 직접 레이어가 멀쩡하면 부르지 않는다.
    """
    if path is None or not path.exists():
        return None
    layers = TextLayers(path=path)
    if path.suffix.lower() in DOC_SUFFIXES:
        try:
            layers.direct, layers.unmapped_pua = read_pdf_fitz(path)
        except Exception as exc:
            layers.ocr_error = f"fitz 실패: {exc}"
        if want_plumber:
            try:
                layers.plumber = read_pdf_plumber(path)
            except Exception:
                layers.plumber = ""
    if want_ocr and not layers.direct_usable:
        try:
            layers.ocr = ocr_text(path, columns=columns)
        except OcrUnavailable as exc:
            layers.ocr_error = str(exc)
    return layers
