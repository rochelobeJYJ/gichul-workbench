# -*- coding: utf-8 -*-
"""작업 폴더 경로 규약. docs/CONTRACT.md 1절의 코드판."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

CURRICULUM_PDF = REPO / "curriculum" / "pdf"
CURRICULUM_STANDARDS = REPO / "curriculum" / "standards"
SUBJECTS = REPO / "subjects"
BUILDER = REPO / "builder"
WORKSPACE = REPO / "workspace"


class Space:
    """한 과목의 작업 공간. 경로 문자열을 코드에 흩뿌리지 않기 위한 껍데기."""

    def __init__(self, slug: str, root: Path | str | None = None):
        self.slug = slug
        self.root = Path(root) if root else (WORKSPACE / slug)

    # --- 디렉터리 ---
    @property
    def sources(self) -> Path:
        return self.root / "sources"

    @property
    def pages(self) -> Path:
        return self.root / "pages"

    @property
    def questions(self) -> Path:
        return self.root / "crops" / "questions"

    @property
    def materials(self) -> Path:
        return self.root / "crops" / "materials"

    @property
    def items(self) -> Path:
        return self.root / "items"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def output(self) -> Path:
        return self.root / "output"

    # --- 파일 ---
    def source_dir(self, exam_id: str) -> Path:
        return self.sources / exam_id

    def manifest(self, exam_id: str) -> Path:
        return self.sources / exam_id / "manifest.json"

    def item(self, qid: str) -> Path:
        return self.items / f"{qid}.json"

    def question_png(self, qid: str) -> Path:
        return self.questions / f"{qid}.png"

    def material_png(self, qid: str, index: int) -> Path:
        return self.materials / f"{qid}_m{index}.png"

    def report(self, step: str) -> Path:
        return self.reports / f"{step}.json"

    def ensure(self) -> "Space":
        for d in (self.sources, self.questions, self.materials,
                  self.items, self.reports, self.output):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def rel(self, path: Path | str) -> str:
        """리포트/아이템에 적을 상대 경로. 작업 공간을 옮겨도 깨지지 않는다."""
        p = Path(path)
        try:
            return p.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return p.as_posix()

    def iter_exams(self):
        if not self.sources.exists():
            return
        from .ids import exam_sort_key
        for d in sorted((p for p in self.sources.iterdir() if p.is_dir()),
                        key=lambda p: exam_sort_key(p.name)):
            yield d.name

    def iter_items(self):
        if not self.items.exists():
            return
        from .ids import split_qid
        for p in sorted(self.items.glob("*.json"),
                        key=lambda p: (split_qid(p.stem)[0], split_qid(p.stem)[1])):
            yield p
