# -*- coding: utf-8 -*-
"""gichul-workbench 공용 계약 모듈."""
from . import console as _console

_console.setup()  # 한국어 윈도우 콘솔 인코딩 방어. console.py 참조.

from .ids import make_exam_id, make_qid, split_qid, normalize_exam, exam_sort_key
from .paths import (REPO, Space, SUBJECTS, CURRICULUM_PDF, CURRICULUM_STANDARDS,
                    BUILDER, WORKSPACE)
from .report import Report
from .subjects import Subject, load_subject, all_subjects

__all__ = [
    "console",
    "make_exam_id", "make_qid", "split_qid", "normalize_exam", "exam_sort_key",
    "REPO", "Space", "SUBJECTS", "CURRICULUM_PDF", "CURRICULUM_STANDARDS", "BUILDER",
    "WORKSPACE",
    "Report", "Subject", "load_subject", "all_subjects",
]
