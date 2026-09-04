# -*- coding: utf-8 -*-
"""작업 폴더 경로 규약. docs/CONTRACT.md 1절의 코드판.

두 가지를 정한다.

1. **읽는 자료가 어디 있나** — `REPO` 아래 subjects/·curriculum/standards/·builder/.
   보통은 클론한 저장소 폴더이고, 스킬 폴더(`~/.claude/skills/gichul-workbench/`)로
   통째로 복사해 넣은 경우에도 모양이 같아 그대로 맞는다.
   휠로 설치한 경우에만 자리가 다르다 — `_locate_repo()` 주석 참조.

2. **만든 것이 어디 쌓이나** — `workspace_root()`.
   예전에는 `<저장소>/workspace` 고정이었다. 수 GB 짜리 산출물이 프로그램 폴더에
   쌓여 `git pull` 을 괴롭히고, 스킬 폴더로 설치한 경우엔 스킬 폴더가 부풀었다.
   지금은 사용자가 정한 폴더를 기억한다. 결정 규칙과 그 이유는 `common/config.py` 에 있다.
   여기서는 **묻지 않는다** — `import common` 은 거의 모든 진입점에서 일어나므로
   임포트 도중에 질문이 뜨면 비대화형 실행이 통째로 멈춘다.
"""
from __future__ import annotations

from pathlib import Path

from . import config

_HERE = Path(__file__).resolve()


def _locate_repo() -> Path:
    """자료(subjects/·curriculum/·builder/)가 있는 뿌리.

    두 배치를 지원한다.
      - 클론·스킬 폴더: `<뿌리>/scripts/common/paths.py`  →  parents[2]
      - 휠 설치:        `<site-packages>/common/paths.py` 이고 자료는
                        `<site-packages>/gichul_workbench_data/` 에 있다
                        (pyproject.toml 의 force-include).

    **정본은 클론이다.** 클론이 보이면 언제나 클론이 이긴다. 이유가 둘 있다 —
    갱신이 `git pull` 로 오고, `classify --learn --install` 이 만든 사전이
    `subjects/<과목>/keywords.json` 으로 **되쓰기** 되기 때문이다.
    클론 안에서 돌면 그 둘이 같은 폴더에서 일어난다. 휠 설치본에서는 되쓰기가
    가상환경 안으로 들어가 다음 재설치 때 말없이 사라진다.

    둘 다 아니면 클론 모양을 그대로 돌려준다. 없는 자리를 지어내지 않는다 —
    `all_subjects()` 가 빈 목록을 내고 `gw subjects` 가 무엇이 없는지 말한다.
    """
    clone = _HERE.parents[2]
    if (clone / "subjects").is_dir():
        return clone
    installed = _HERE.parents[1] / "gichul_workbench_data"
    if (installed / "subjects").is_dir():
        return installed
    return clone


REPO = _locate_repo()

CURRICULUM_PDF = REPO / "curriculum" / "pdf"
CURRICULUM_STANDARDS = REPO / "curriculum" / "standards"
SUBJECTS = REPO / "subjects"
BUILDER = REPO / "builder"

_root_cache: Path | None = None


def workspace_root() -> Path:
    """산출물이 쌓이는 뿌리. `--workspace` 를 주지 않았을 때의 답이다.

    한 실행 안에서는 답을 붙들어 둔다. 실행 도중에 값이 갈리면 crop 은 A 에,
    extract 는 B 에 쓰는 일이 생긴다.
    """
    global _root_cache
    if _root_cache is None:
        _root_cache, _ = config.resolve(REPO)
    return _root_cache


def workspace_source() -> str:
    """작업 폴더가 어디서 정해졌는지(env/config/repo-legacy/default). 안내용."""
    return config.resolve(REPO)[1]


def invalidate() -> None:
    """붙들어 둔 값을 버린다. 답이 막 바뀐 직후에만 부른다
    (첫 실행 질문, `gw where --set`, `gw where --forget`)."""
    global _root_cache
    _root_cache = None


# 예전부터 있던 이름. `WORKSPACE / "_curriculum"` 처럼 상수로 쓰는 모듈이 있어 남겨 둔다.
# 임포트 시점에 굳으므로, 그 뒤에 값이 바뀌면 config._rebind() 가 여기를 다시 박는다.
WORKSPACE = workspace_root()


class Space:
    """한 과목의 작업 공간. 경로 문자열을 코드에 흩뿌리지 않기 위한 껍데기."""

    def __init__(self, slug: str, root: Path | str | None = None):
        self.slug = slug
        # root 가 곧 `--workspace` 다. 주어지면 언제나 이긴다(CONTRACT 6절).
        # 안 주면 그때그때 workspace_root() 에 묻는다 — 모듈 상수를 붙잡아 두면
        # 첫 실행 질문으로 답이 바뀐 뒤에도 옛 자리에 쓴다.
        self.root = Path(root) if root else (workspace_root() / slug)

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
