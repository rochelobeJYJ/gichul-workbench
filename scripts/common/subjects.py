# -*- coding: utf-8 -*-
"""과목 레지스트리. 과목별 차이는 전부 여기를 통해서만 코드에 들어온다.

코드가 과목 이름으로 분기하기 시작하면 이 도구는 지구과학 전용으로 되돌아간다.
docs/CONTRACT.md 0절·3절 참조.
"""
from __future__ import annotations

import json
import re
import sys as _sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .paths import CURRICULUM_STANDARDS, SUBJECTS

# 크롭·추출 전략. subject.json 의 layout 값이 여기 없으면 등록을 거부한다.
KNOWN_LAYOUTS = {
    "tamgu-1q1block": "탐구 영역 표준 2단 편집, 문항 하나가 한 블록. 검증됨.",
    "passage-group": "지문 하나에 문항 여러 개(국어·영어). 실험적 — docs/LAYOUTS.md 참조.",
    "math-mixed": "객관식 + 단답형, 수식이 벡터. 실험적 — docs/LAYOUTS.md 참조.",
}

# subject.json 에서 '없음' 과 '안 적음' 을 가르기 위한 표식.
# JSON 의 null 은 `.get()` 으로는 '키가 없다' 와 구분되지 않는다. 그런데 이 둘은
# 뜻이 정반대다 — `points_unmarked: null` 은 "표기 없는 문항이 아예 없다(전 문항에
# [N점] 이 붙는다)"는 **실측 선언**이고, 키가 없는 것은 "아직 안 적었다"이다.
# 후자를 전자로 읽으면 멀쩡한 문항 전부에 '표기 없음' error 가 쏟아진다.
_UNSET = object()


# ══════════════════════════════════════════════════════════════════════════
# 성취기준 코드 → 개정 판정
#
# 왜 접두사 비교를 그만두는가:
#   `code.startswith("10통과")` 는 2015 통합과학 코드(`10통과01-01`)를 걸러내려는
#   것인데 2022 코드(`10통과1-01-01`)까지 함께 걸린다. 실측 — 두 개정의 코드를
#   한 통에 담고 `10통과` 로 거르면 63개가 걸리고 그중 31개가 2022 코드다
#   (`10통사` 는 59개 중 30개). 이 상태에서 2015 후보 사전·2015 대조 목록에
#   2022 코드가 섞여 들어가면 **2015 문항이 2022 코드로 조용히 확정된다.**
#   error 는 하나도 나지 않는다 — 문자열 비교가 성공했기 때문이다.
#
#   개정은 문자열 모양이 아니라 데이터에 있다. `curriculum/standards/<개정>.json`
#   이 "이 개정에 어떤 코드가 실재하는가" 를 이미 알고 있으므로, 그 목록과의
#   **소속 검사**로 바꾼다. 접두사는 그 목록 안에서 과목을 고르는 데만 쓴다.
#
#   ⚠ 이것으로도 못 가르는 자리가 있다. 2015 와 2022 가 **글자 그대로 같은 코드**를
#     쓰는 과목이 원본 기준 9개다(`12경제 12과사 12사문 12사탐 12생활 12세사
#     12세지 12여지 12윤사`). 그런 코드는 두 개정 목록에 모두 실재하므로 소속
#     검사도 양쪽을 통과한다 — 그건 오류가 아니라 사실이다. 그 자리에서 개정을
#     가르는 것은 코드가 아니라 **자료 구조의 개정 층**이다(CONTRACT 3절,
#     keywords.json 의 개정 층 / items 의 classification.<개정>).
# ══════════════════════════════════════════════════════════════════════════

_CATALOG_CACHE: dict[str, dict[str, str]] = {}
_WS_RE = re.compile(r"\s+")


def _norm_name(name: str) -> str:
    """과목명 대조용 정규화. **NFKC 가 먼저, 접기가 나중이다.**

    순서를 바꾸면 `Ⅰ`(U+2160)이 `ⅰ`(U+2170)로 접혀 라틴 `I` 와 영영 못 만난다
    (PITFALLS 3-12 에서 실제로 겪은 사고다). 공백은 지운다 — 같은 과목명이
    `생활과 윤리` / `생활과윤리` 로 오가는 자리가 있다.
    """
    return _WS_RE.sub("", unicodedata.normalize("NFKC", str(name))).casefold()


def _walk_catalog(node, subject_name: str | None, out: dict[str, str]) -> None:
    """`{"code": ...}` 레코드를 주우면서 그것을 감싼 **과목명**을 물고 내려간다.

    층 이름(`subjects`)을 믿지 않는 이유는 classify._load_standards_meta 와 같다 —
    이 파일의 스키마는 CONTRACT 에 못박혀 있지 않고 한 번 바뀐 적이 있다. 과목명은
    필드가 아니라 dict 의 **키**라서, 값이 과목 블록처럼 생겼을 때(`units` 나
    `code_prefix` 를 갖고 있다) 그 키를 과목명으로 삼는다.
    """
    if isinstance(node, dict):
        code = node.get("code")
        if isinstance(code, str):
            out.setdefault(code, subject_name)
            return
        for key, value in node.items():
            looks_like_subject = isinstance(value, dict) and (
                "units" in value or "code_prefix" in value)
            _walk_catalog(value, key if looks_like_subject else subject_name, out)
    elif isinstance(node, list):
        for value in node:
            _walk_catalog(value, subject_name, out)


def standards_catalog(revision: str) -> dict[str, str]:
    """`curriculum/standards/<개정>.json` → {성취기준코드: 과목명}. 없으면 빈 dict.

    파일이 없는 것은 '틀렸다'가 아니라 '모른다'다(교육과정 원본을 아직 안 넣었을 수
    있다). 그래서 예외를 던지지 않고 빈 dict 를 준다 — 부르는 쪽이 접두사 폴백으로
    내려가고 그 사실을 리포트에 남긴다.
    """
    revision = str(revision)
    cached = _CATALOG_CACHE.get(revision)
    if cached is not None:
        return cached
    path = CURRICULUM_STANDARDS / f"{revision}.json"
    out: dict[str, str] = {}
    if path.exists():
        try:
            _walk_catalog(json.loads(path.read_text(encoding="utf-8")), None, out)
        except (json.JSONDecodeError, OSError):
            out = {}
    _CATALOG_CACHE[revision] = out
    return out


@dataclass(frozen=True)
class CodeScope:
    """'이 과목의 이 개정에 속한 성취기준 코드' 판정기. 여섯 자리가 이것만 쓴다.

    `basis` 가 판정 근거다.
      · `catalog` — 개정 목록에서 실제로 뽑은 코드 집합. 이것이 정상 경로다.
      · `prefix`  — 개정 목록을 못 구해 옛 접두사 비교로 떨어진 상태. `why` 가 이유다.
      · `any`     — 접두사도 목록도 없다. 예전과 같이 전부 통과시킨다(과목 등록 초기).
    폴백을 **조용히** 하지 않으려고 `why` 를 함께 들고 다닌다. 접두사 폴백은 통합과목
    같은 자리에서 다시 조용히 틀리는 경로이므로, 리포트에 뜨지 않으면 의미가 없다.
    """
    slug: str
    revision: str
    codes: frozenset = frozenset()
    prefixes: tuple = ()
    basis: str = "any"
    why: str | None = None

    def __contains__(self, code: object) -> bool:
        if not isinstance(code, str):
            return False
        if self.basis == "catalog":
            return code in self.codes
        if self.basis == "prefix":
            return any(code.startswith(p) for p in self.prefixes)
        return True

    def __call__(self, code: object) -> bool:
        return code in self

    def filter(self, codes):
        return [c for c in codes if c in self]


# ══════════════════════════════════════════════════════════════════════════
# 회차 단위 불변식 오버라이드
#
# 왜 필요한가: `question_count` 는 과목당 스칼라 하나인데 한 슬러그 안에 판형이
# 둘 있다. 통합과목은 2023~2025.3 이 20문항, 2025.6~ 이 25문항이고 `points_total`
# 은 두 판형 모두 50 이라 **`question_count` 만 갈린다**(실측). 스칼라 하나로는
# 담을 수 없어서 지금은 25 를 적어 두었고, 그 대가로 20문항 회차를 돌리면 크롭이
# 없는 번호 21~25 를 찾다 실패하고 정답 축이 통째로 죽는다.
#
# 왜 '표지에서 세기'가 아니라 '선언'인가 (판단 근거):
#   ① 내려받은 문제지에는 표지가 없다. 실측 — 2025 고1 3·6·9월 통합과학/통합사회
#      6권 전부 1쪽이 1번 문항부터 시작하고, 어디에도 문항 수가 인쇄돼 있지 않다.
#      머리글은 `고1 과학탐구영역 (통합과학) 1` 이 전부다.
#   ② 그래서 '표지에서 센다'는 결국 '본문의 번호 앵커를 센다'가 되는데, 그건
#      question_count 가 감시하려는 바로 그 대상이다. 같은 문서에서 기대치를 뽑으면
#      번호 13을 놓친 회차는 24문항이 '정답'이 되고, 정답지 원문자 픽셀 대조의
#      자기검증(`찾은 원문자 수 == question_count`)까지 함께 무력화된다.
#      CONTRACT 4-4 가 이 불변식을 "가장 강력한 자동 검증"이라 부르는 이유가
#      **바깥에서 온 기대치**라는 점이다. 안에서 만들면 검증이 아니라 요약이 된다.
#   ③ 인쇄된 판별자 중 바깥에 있는 것(머리글 과목명, `[1.5점]` 표기)은 두 판형을
#      가르지 못한다. 2023·2024 회차도 머리글이 `(통합과학)` 인데 20문항이다.
#      `[1.5점]` 은 25문항 판형에만 있지만 그것도 같은 문서 안이다.
#   → 그래서 회차 목록을 **데이터로 선언**한다. 대신 선언이 조용히 빗나가지 않도록
#     ⓐ 매칭 규칙을 exam_id 정규식으로 명시하고, ⓑ `why` 를 필수로 받고,
#     ⓒ 어떤 회차에도 안 걸린 오버라이드를 리포트에 올린다(validate).
#
# 왜 '기본값이 현행 판형'인가: 새 회차(2026~)는 어느 규칙에도 안 걸리고 기본값으로
# 떨어진다. 그러니 기본값은 **현행·향후 판형**이어야 실패 방향이 안전하다. 경계는
# 과거에 있다 — 2025년 6월 이전만 20문항이다. `달로 판정하지 마라`(2026년은 3월부터
# 25문항)는 여기서 '정규식에 연도를 함께 적어라'로 지켜진다.
# ══════════════════════════════════════════════════════════════════════════

# 오버라이드가 갈아끼울 수 있는 값. 화이트리스트인 이유: `"questions": 20` 같은
# 오타를 조용히 무시하면 20문항 회차가 25문항 기대치로 돌면서 '설정했는데 왜
# 안 먹지' 를 아무도 못 찾는다. 모르는 키는 등록 자체를 거부한다.
OVERRIDE_FIELDS = ("question_count", "points_total", "point_tiers", "points_unmarked")


@dataclass(frozen=True)
class Invariants:
    """한 **회차**의 검증 불변식. `subject.invariants(exam_id)` 가 만든다."""
    exam_id: str | None
    question_count: int | None
    points_total: object
    point_tiers: tuple | None          # 선언된 배점 계단. None 이면 부르는 쪽이 역산한다.
    points_unmarked: object            # `[N점]` 표기가 없는 문항의 정상 배점
    points_unmarked_declared: bool     # 위 값이 선언된 것인지(=null 도 선언이다)
    source: str                        # "subject.json" 또는 "overrides[i]"
    why: str | None                    # 오버라이드가 스스로 적은 이유

    @property
    def overridden(self) -> bool:
        return self.source != "subject.json"


def _as_number(value, where: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: 숫자여야 한다 (받은 값 {value!r})")
    return value


def _check_overrides(slug: str, raw) -> tuple:
    """`overrides` 블록을 등록 시점에 검사한다. 틀리면 **여기서 멈춘다.**

    조용히 무시하면 오버라이드가 없는 것과 구분되지 않는다 — 20문항 회차가
    25문항 기대치로 돌면서 리포트에는 '앵커를 못 찾았다' 라는 엉뚱한 진단만 남는다.
    """
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{slug}: overrides 는 배열이어야 한다 (받은 값 {type(raw).__name__})")
    out = []
    for i, row in enumerate(raw):
        where = f"{slug}: overrides[{i}]"
        if not isinstance(row, dict):
            raise ValueError(f"{where}: 객체여야 한다")
        when = row.get("when")
        if not isinstance(when, dict) or not when.get("exam_id"):
            raise ValueError(f"{where}: when.exam_id (정규식) 이 필요하다")
        unknown = set(when) - {"exam_id"}
        if unknown:
            raise ValueError(f"{where}: when 에 모르는 키 {sorted(unknown)} — "
                             f"지금 가를 수 있는 축은 exam_id 하나다")
        try:
            pattern = re.compile(str(when["exam_id"]))
        except re.error as exc:
            raise ValueError(f"{where}: when.exam_id 정규식이 깨졌다 — {exc}") from exc
        if not row.get("why"):
            # 이유 없는 예외는 반년 뒤에 아무도 손대지 못한다. 계약 값으로 둔다.
            raise ValueError(f"{where}: why 가 필요하다 — 이 회차들이 왜 다른지 한 줄로 적어라")
        declared = {k: v for k, v in row.items() if k not in ("when", "why")}
        unknown = set(declared) - set(OVERRIDE_FIELDS)
        if unknown:
            raise ValueError(f"{where}: 모르는 키 {sorted(unknown)} — "
                             f"갈아끼울 수 있는 값은 {list(OVERRIDE_FIELDS)} 뿐이다")
        if not declared:
            raise ValueError(f"{where}: 갈아끼우는 값이 하나도 없다")
        if "question_count" in declared:
            n = declared["question_count"]
            if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
                raise ValueError(f"{where}: question_count 는 양의 정수여야 한다 ({n!r})")
        if "points_total" in declared:
            _as_number(declared["points_total"], f"{where}: points_total")
        if "point_tiers" in declared:
            tiers = declared["point_tiers"]
            if not isinstance(tiers, list) or not tiers:
                raise ValueError(f"{where}: point_tiers 는 비어 있지 않은 배열이어야 한다")
            for t in tiers:
                _as_number(t, f"{where}: point_tiers 원소")
            if "points_unmarked" not in declared:
                # 계단을 새로 선언했으면 '표기 없는 문항의 정상 배점' 도 함께 말해야 한다.
                # 추측하면(예: 가장 낮은 계단) 통합과목 25문항 판형에서 통째로 틀린다 —
                # 그 판형은 전 문항에 표기가 붙어서 '표기 없는 정상 배점' 이 아예 없다.
                raise ValueError(f"{where}: point_tiers 를 선언했으면 points_unmarked 도 "
                                 f"함께 적어라(표기 없는 문항의 정상 배점, 그런 문항이 "
                                 f"없으면 null)")
        if declared.get("points_unmarked") is not None:
            _as_number(declared["points_unmarked"], f"{where}: points_unmarked")
        out.append((pattern, str(row["why"]), declared, f"overrides[{i}]"))
    return tuple(out)


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
    # 배점 계단. 없으면 validate 가 `points_total // question_count` 로 (기본, 기본+1)
    # 두 계단을 역산한다 — 탐구 20문항 50점이면 (2, 3). 계단이 셋 이상인 판형
    # (통합과목 25문항: 1.5 / 2 / 2.5)은 역산이 성립하지 않아 여기에 적어야 한다.
    point_tiers: list | None = None
    # `[N점]` 표기가 없는 문항의 정상 배점. _UNSET(키 없음)과 None(그런 문항이
    # 없다는 선언)은 다르다 — 위 _UNSET 주석 참조.
    points_unmarked: object = _UNSET
    # 회차 단위 불변식 예외. [{when:{exam_id: 정규식}, why: "...", question_count: 20, ...}]
    overrides: list = field(default_factory=list)
    notes: str = ""
    path: Path | None = None

    # 파생값 캐시. subject.json 에는 없는 필드이므로 load_subject 가 채우지 않는다.
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

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

    # --- 개정 판정 ---------------------------------------------------------
    def curriculum_names(self, revision: str) -> list[str]:
        """그 개정의 교육과정 과목명 목록. **문자열 하나든 여럿이든 여기서 편다.**

        한 개정에서 과목이 둘로 갈리는 자리가 실재한다 — 2022개정 통합과학은
        통합과학1·통합과학2 **두 과목**이다. 스키마가 문자열 하나만 받아서
        `"통합과학1·통합과학2"` 라는 없는 과목명으로 우회하고 있었고, 그 문자열은
        `curriculum/standards/2022.json` 의 어느 과목과도 안 맞아 마크다운 자동
        생성이 조용히 비껴갔다. `str | list[str]` 을 모두 받고, 부르는 쪽은
        isinstance 를 다시 하지 않는다.
        """
        value = (self.curriculum or {}).get(str(revision))
        if not value:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value if v]
        return [str(value)]

    def code_scope(self, revision: str) -> CodeScope:
        """이 과목·이 개정에 속한 성취기준 코드 판정기. 접두사 비교의 대체물이다.

        코드 집합은 **개정 목록 안에서만** 고른다 —
          ① `curriculum.<개정>` 이 가리키는 과목의 코드,
          ② `standard_prefixes.<개정>` 접두사에 걸리는 코드.
        둘의 합집합을 쓴다. 어느 한쪽이 비어 있는 과목이 실재하기 때문이다
        (예: chemistry-i 는 2022 과목명이 없고 접두사만 있다).
        실측으로 확인한 것 — 이름이 있는 19과목 × 2개정 전부에서 두 집합이 정확히
        같았다. 즉 합집합은 지금 등록된 과목의 판정을 바꾸지 않는다. 바뀌는 것은
        하나뿐이다: **그 개정 목록에 없는 코드는 이제 걸리지 않는다.**
        """
        revision = str(revision)
        cached = self._cache.get(("scope", revision))
        if cached is not None:
            return cached

        prefixes = tuple((self.standard_prefixes or {}).get(revision) or [])
        catalog = standards_catalog(revision)
        names = {_norm_name(n) for n in self.curriculum_names(revision)}

        if not catalog:
            why = (f"curriculum/standards/{revision}.json 이 없어 개정 목록을 못 읽었다 — "
                   f"접두사 비교로 떨어진다. 접두사만으로는 개정을 못 가르는 과목이 있다"
                   if prefixes else None)
            scope = CodeScope(self.slug, revision, frozenset(), prefixes,
                              "prefix" if prefixes else "any", why)
        else:
            codes = {code for code, owner in catalog.items()
                     if (owner is not None and _norm_name(owner) in names)
                     or (prefixes and code.startswith(prefixes))}
            if codes:
                scope = CodeScope(self.slug, revision, frozenset(codes), prefixes, "catalog", None)
            elif prefixes or names:
                # 목록은 있는데 이 과목 것이 하나도 안 잡힌다. 접두사가 틀렸거나
                # 과목명이 원본과 다르다(실측 사고: 지구과학Ⅱ 접두사를 `12지구` 로
                # 적었는데 진짜는 `12지과Ⅱ` 였고, `12지구` 는 2022 '지구과학' 의
                # 실재 코드라 대조를 통과할 뻔했다). 옛 동작(접두사)으로 떨어뜨리되
                # 반드시 말한다 — 조용히 통과시키면 그 사고가 그대로 재발한다.
                scope = CodeScope(
                    self.slug, revision, frozenset(), prefixes,
                    "prefix" if prefixes else "any",
                    f"curriculum/standards/{revision}.json 에 이 과목 코드가 하나도 없다 "
                    f"(curriculum.{revision}={self.curriculum_names(revision) or None}, "
                    f"standard_prefixes.{revision}={list(prefixes) or None}) — "
                    f"둘 중 하나가 원본과 다르다. 접두사 비교로 떨어졌다")
            else:
                scope = CodeScope(self.slug, revision, frozenset(), (), "any", None)

        self._cache[("scope", revision)] = scope
        return scope

    # --- 회차 단위 불변식 ---------------------------------------------------
    def invariants(self, exam_id: str | None = None) -> Invariants:
        """이 회차에 적용할 문항 수·배점 불변식. 오버라이드는 **첫 일치가 이긴다.**

        `exam_id` 를 안 주면 과목 기본값(= 현행 판형)이다. 기본값이 현행 판형이어야
        새 회차가 아무 규칙에도 안 걸렸을 때 실패 방향이 안전하다(위 절 참조).
        """
        base = dict(question_count=self.question_count, points_total=self.points_total,
                    point_tiers=self.point_tiers, points_unmarked=self.points_unmarked)
        source, why = "subject.json", None
        if exam_id:
            for pattern, rule_why, declared, name in self._override_rules():
                if pattern.search(str(exam_id)):
                    base.update(declared)
                    source, why = name, rule_why
                    break
        tiers = base["point_tiers"]
        unmarked = base["points_unmarked"]
        return Invariants(
            exam_id=exam_id,
            question_count=base["question_count"],
            points_total=base["points_total"],
            point_tiers=tuple(tiers) if tiers else None,
            points_unmarked=None if unmarked is _UNSET else unmarked,
            points_unmarked_declared=unmarked is not _UNSET,
            source=source, why=why)

    def question_count_for(self, exam_id: str | None) -> int | None:
        """이 회차의 문항 수. crop·extract·rates 가 부르는 자리다."""
        return self.invariants(exam_id).question_count

    def _override_rules(self) -> tuple:
        cached = self._cache.get("overrides")
        if cached is None:
            cached = _check_overrides(self.slug, self.overrides)
            self._cache["overrides"] = cached
        return cached

    def override_coverage(self, exam_ids) -> list[dict]:
        """오버라이드 규칙별로 '이 회차들에 걸렸다' 를 돌려준다.

        **한 건도 안 걸린 규칙을 찾기 위한 것이다.** 규칙 하나가 오타로 아무 회차에도
        안 걸리면 그 회차는 기본 판형으로 조용히 돌아가고, 리포트에는 '앵커를 못
        찾았다' 같은 엉뚱한 진단만 남는다. 규칙이 헛돌고 있다는 사실 자체를 올린다.
        """
        exam_ids = list(exam_ids or [])
        rows = []
        for pattern, why, declared, name in self._override_rules():
            matched = [e for e in exam_ids if pattern.search(str(e))]
            rows.append({"rule": name, "pattern": pattern.pattern, "why": why,
                         "sets": declared, "matched": matched})
        return rows

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
    known_fields = {f for f in Subject.__dataclass_fields__ if not f.startswith("_")
                    and f != "path"}
    subject = Subject(path=path.parent,
                      **{k: v for k, v in data.items() if k in known_fields})
    # 오버라이드는 **등록 시점에** 검사한다. 첫 회차를 돌 때까지 미루면 오타 하나가
    # '기대치가 왜 안 바뀌지' 로 나타나고, 그때 리포트에 남는 것은 앵커 실패뿐이다.
    # (`points_unmarked: null` 은 위 dict 컴프리헨션을 그대로 통과해 None 으로 들어온다.
    #  키가 아예 없을 때만 기본값 _UNSET 이 남는다 — 그 둘을 가르려고 센티넬을 쓴다.)
    subject._override_rules()
    if data.get("point_tiers") is not None and "points_unmarked" not in data:
        # overrides 쪽과 같은 규칙. 계단을 선언했으면 '표기 없는 문항의 정상 배점'
        # 도 말해야 한다 — 그것을 역산하면(예: 가장 낮은 계단) 전 문항에 [N점] 이
        # 붙는 판형에서 통째로 틀린 규칙이 만들어진다.
        raise ValueError(f"{slug}: point_tiers 를 선언했으면 points_unmarked 도 함께 적어라 "
                         f"(표기 없는 문항의 정상 배점, 그런 문항이 없으면 null)")
    return subject


def all_subjects() -> list[Subject]:
    out = []
    if not SUBJECTS.exists():
        return out
    for d in sorted(SUBJECTS.iterdir()):
        if d.name.startswith("_") or not (d / "subject.json").exists():
            continue
        try:
            out.append(load_subject(d.name))
        except Exception as exc:                       # noqa: BLE001
            # 예전에는 통째로 삼켰다. 그러면 subject.json 오타 하나로 그 과목이
            # 목록에서 **소리 없이 사라지고**, `gw subjects` 는 19 대신 18 을 찍으며
            # 성공으로 끝난다 — 무엇이 빠졌는지 아무 데도 안 남는다.
            # 목록 자체는 계속 돌려준다(한 과목이 17과목 작업을 막으면 안 된다).
            # 대신 stderr 로 말한다. stdout 은 리포트·표 전용이다(CONTRACT 5절).
            print(f"[warn] subjects/{d.name}/subject.json 을 읽지 못해 목록에서 뺐다: {exc}",
                  file=_sys.stderr)
            continue
    return out
