# -*- coding: utf-8 -*-
"""과목 레지스트리. 과목별 차이는 전부 여기를 통해서만 코드에 들어온다.

코드가 과목 이름으로 분기하기 시작하면 이 도구는 지구과학 전용으로 되돌아간다.
docs/CONTRACT.md 0절·3절 참조.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .paths import SUBJECTS

# 크롭·추출 전략. subject.json 의 layout 값이 여기 없으면 등록을 거부한다.
KNOWN_LAYOUTS = {
    "tamgu-1q1block": "탐구 영역 표준 2단 편집, 문항 하나가 한 블록. 검증됨.",
    "passage-group": "지문 하나에 문항 여러 개(국어·영어). 실험적 — docs/LAYOUTS.md 참조.",
    "math-mixed": "객관식 + 단답형, 수식이 벡터. 실험적 — docs/LAYOUTS.md 참조.",
}


@dataclass
class Subject:
    slug: str
    label: str
    area: str
    layout: str
    question_count: int | None = None
    points_total: int | None = None
    curriculum: dict = field(default_factory=dict)
    providers: dict = field(default_factory=dict)
    standard_prefixes: dict = field(default_factory=dict)
    # 이 과목에서만 통하는 글리프 손상 탐지 규칙. [{id, pattern, why, context?, off?}].
    # validate.py 의 기본 목록과 id 로 합쳐진다(같은 id 면 이쪽이 이기고, off:true 면 끈다).
    # 여기 두는 이유: 손상 양상이 과목마다 다른데('90N' 은 지구과학에선 위도, 물리에선
    # 뉴턴이다) 목록을 코드에만 두면 validate 가 과목 이름을 알게 된다(CONTRACT 0절).
    # 비어 있으면 기본 목록만 쓰므로 지금까지 돌던 과목의 판정은 그대로다.
    glyph_smells: list = field(default_factory=list)
    notes: str = ""
    path: Path | None = None

    # --- 파생 경로 ---
    @property
    def keywords_path(self) -> Path:
        return (self.path or SUBJECTS / self.slug) / "keywords.json"

    @property
    def mapping_path(self) -> Path:
        return (self.path or SUBJECTS / self.slug) / "mapping.json"

    @property
    def is_experimental(self) -> bool:
        return self.layout != "tamgu-1q1block"

    def provider(self, name: str) -> dict | None:
        return (self.providers or {}).get(name)

    def keyword_book(self):
        """`keywords.json` 전체(개정 → 코드 → 칸)를 KeywordBook 으로. 없으면 빈 책.

        ※ 지연 임포트하는 이유: keywordsio 는 common.paths 를 쓰는데 이 모듈은
          common 패키지 초기화 도중에 읽힌다. 최상단에서 끌어오면 부분 초기화된
          패키지를 서로 참조하게 된다.
        """
        import keywordsio
        return keywordsio.load(self.keywords_path, self.standard_prefixes or {})

    def keywords(self, revision: str) -> dict[str, dict]:
        """그 **개정**의 {성취기준코드: {"curriculum": [...], "learned": [...]}}.

        개정 인자를 필수로 둔 이유: 사회탐구 5개 과목(경제·윤리와 사상·사회·문화·
        세계지리·세계사)은 2015 와 2022 가 같은 성취기준 접두사를 쓴다. 개정을 묻지
        않고 사전을 통째로 돌려주면 부르는 쪽이 두 교육과정을 섞어 채점하게 된다 —
        에러 없이 틀리는 사고였다. 기본값을 주는 쪽이 더 위험하다(PITFALLS 7-3).
        """
        return self.keyword_book().revision(revision)

    def mapping(self) -> dict:
        if not self.mapping_path.exists():
            return {}
        return json.loads(self.mapping_path.read_text(encoding="utf-8"))

    def readiness(self) -> dict[str, bool]:
        """새 과목이 어디까지 준비됐는지. `gw subjects` 가 이걸 표로 보여준다."""
        return {
            "providers": bool(self.providers),
            "keywords": self.keywords_path.exists(),
            "mapping": self.mapping_path.exists(),
        }


def load_subject(slug: str) -> Subject:
    path = SUBJECTS / slug / "subject.json"
    if not path.exists():
        known = ", ".join(s.slug for s in all_subjects()) or "(없음)"
        raise FileNotFoundError(
            f"과목 정의가 없다: {path}\n등록된 과목: {known}\n"
            f"새로 만들려면 subjects/_template/ 를 복사하고 docs/NEW_SUBJECT.md 를 따른다."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    layout = data.get("layout")
    if layout not in KNOWN_LAYOUTS:
        raise ValueError(
            f"{slug}: 알 수 없는 layout {layout!r}. 가능한 값: {', '.join(KNOWN_LAYOUTS)}"
        )
    known_fields = {f for f in Subject.__dataclass_fields__ if f != "path"}
    return Subject(path=path.parent, **{k: v for k, v in data.items() if k in known_fields})


def all_subjects() -> list[Subject]:
    out = []
    if not SUBJECTS.exists():
        return out
    for d in sorted(SUBJECTS.iterdir()):
        if d.name.startswith("_") or not (d / "subject.json").exists():
            continue
        try:
            out.append(load_subject(d.name))
        except Exception:
            continue
    return out
