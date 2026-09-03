# -*- coding: utf-8 -*-
"""`gw detect` — 손으로 넣은 PDF 더미에서 회차·과목을 알아내고
문제지↔정답지↔해설지를 짝지어 sources/<exam_id>/manifest.json 을 만든다.

사용자가 파일을 손으로 정리해 넣는 경로를 살리는 모듈이라, "파일명은 자주 틀린다"는
전제로 설계했다: 파일명 → 같은 폴더 이름 → PDF 첫 페이지 표지 텍스트 순으로 신호를
겹쳐 쓰고, 그래도 안 맞으면 억지로 배정하지 않고 건너뛴다 (지시사항 원칙).

흡수한 원본과 왜 그대로 안 썼는지:
- CSAT_Clipper/core/metadata_extractor.py: 학년도/월/학년/과목 정규식은 그대로 쓸 만했다.
  다만 `subject.replace('1','Ⅰ')` 식 치환은 '2015학년도'의 숫자까지 건드릴 수 있는 버그라
  걷어내고, 과목 판정은 subjects/<slug>/subject.json 의 providers.kice.aliases 하나만 본다.
- CSAT_Clipper/utils/file_utils.py: 문제지↔정답지 1:1 그리디 매칭 아이디어(연도·월·과목 완전
  일치 우선, 아니면 유사도)는 살리되, 여기서는 "정답"과 "해설"을 별개 역할로 다뤄야 해서
  (문제지/정답/해설 3역할) 파일명 유사도 매칭 대신 (연도,시험,학년) 서명으로 그룹을 짓는다.
- kice_down/main.py 의 _normalize_roman: '화학I' 별칭이 '화학II' 문자열의 부분열이라 그냥
  substring 검사하면 오매칭난다는 걸 그 프로젝트가 실전에서 겪었다. 로마숫자를 서로 겹치지
  않는 토큰으로 바꾼 뒤 비교하는 트릭을 그대로 가져왔다.

실제 데이터(2015개정_지구과학2, 19회차)에서 실측한 특이 케이스 — 아래 코드 곳곳의 근거:
- '해설.pdf' 파일명에는 과목명이 안 붙는다 (예: '2021_6월모평_해설.pdf'). → 같은 폴더의
  문제지에서 과목을 상속받는다 (_apply_solution_inheritance).
- 평가원이 통째로 뿌리는 '과학탐구영역_정답표(원본).pdf' 는 8과목 정답표를 한 파일에 담고,
  첫 페이지는 그 중 첫 과목(물리학Ⅰ) 것이다. 폴더명에도 특정 과목이 없다. → 파일명/폴더명/
  첫 페이지 어디에도 우리 과목이 안 걸려서 자연히 건너뛴다. 정답 역할은 이미 있는
  '..._지구과학Ⅱ_정답.pdf' 가 채운다. (일부러 상속 대상에서 '정답' 역할은 뺐다 — 이 파일이
  상속을 받아버리면 정답 역할이 중복 배정된다.)
"""
from __future__ import annotations

import contextlib
import io
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import manifest as mf
from common import Report, Space, load_subject
from common.ids import GRADE_BEARING, make_exam_id, normalize_exam

# ── 스캔 대상 확장자 ─────────────────────────────────────────────────────
# 정답만 스캔 이미지로 오는 실전 사례가 흔해 CONTRACT 1절이 answer.png 를 허용한다.
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
SCAN_EXTS = {".pdf"} | IMAGE_EXTS

# 파일 종류(kind) 키워드. 튜플 순서가 우선순위 — '해설' 파일 안엔 "정답과 해설"처럼
# '정답'이라는 글자가 같이 나오는 경우가 많아 해설을 먼저 봐야 정답으로 안 새는다.
KIND_KEYWORDS = (
    ("solution", ("해설", "solution")),
    ("answer", ("정답", "답안", "answer")),
    ("problem", ("문제지", "문제", "problem")),
)

YEAR_RE = re.compile(r"(?<!\d)(20\d{2})\s*(?:학년도)?(?!\d)")
GRADE_RE = re.compile(r"고\s*([1-3])(?:\s*학년)?|고등학교\s*([1-3])\s*학년")
SUNEUNG_RE = re.compile(r"대학수학능력시험|(?<![가-힣A-Za-z])수능(?![가-힣A-Za-z])")
MOPYEONG_RE = re.compile(r"(\d{1,2})\s*월\s*모의\s*평가|(\d{1,2})\s*월\s*모평")
HAKPYEONG_RE = re.compile(r"(\d{1,2})\s*월\s*(?:전국연합)?학력\s*평가|(\d{1,2})\s*월\s*학평")

# 응시생이 전부 고3인 시험. 학년이 명시 안 되어 있어도 detected.grade 를 채워준다
# (exam_id 자체에는 안 쓰인다 — ids.GRADE_BEARING 이 아니므로).
ALWAYS_GRADE3 = {"수능", "6월모평", "9월모평"}

_ROMAN_CHARS = {"Ⅰ": "#1#", "ⅰ": "#1#", "Ⅱ": "#2#", "ⅱ": "#2#", "Ⅲ": "#3#", "ⅲ": "#3#"}
_CLEAN_RE = re.compile(r"[\s_\-·()（）.]")


# ── 과목 별칭 매칭 ───────────────────────────────────────────────────────

def _normalize_roman(s: str) -> str:
    """전각 로마숫자와 반각 I/II/III 를 서로 겹치지 않는 토큰으로 바꾼다.

    kice_down 프로젝트가 실전에서 겪은 문제: 별칭 '화학I' 는 '화학II' 문자열의
    부분열이라 단순 substring 검사로는 '화학II' 안에서 '화학I'가 거짓으로 매칭된다.
    """
    for ch, tok in _ROMAN_CHARS.items():
        s = s.replace(ch, tok)
    s = re.sub(r"(?<![A-Za-z#])III(?![A-Za-z#])", "#3#", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<![A-Za-z#])II(?![A-Za-z#])", "#2#", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<![A-Za-z#])I(?![A-Za-z#])", "#1#", s, flags=re.IGNORECASE)
    return s


def _clean(s: str) -> str:
    return _normalize_roman(_CLEAN_RE.sub("", s or ""))


def subject_aliases(subject) -> list[str]:
    """subject.json 의 providers.kice.aliases + label. 숫자↔로마숫자 치환은 하지 않는다 —
    그 치환이 '2015학년도'의 숫자까지 망가뜨리는 원본의 버그였다. 필요한 표기 변형은
    subject.json 에 데이터로 미리 다 적어두게 한다 (CONTRACT 0절: 과목 하드코딩 금지)."""
    prov = subject.provider("kice") or {}
    aliases = list(prov.get("aliases") or [])
    if subject.label and subject.label not in aliases:
        aliases.append(subject.label)
    return aliases


def matches_subject(text: str, aliases: list[str]) -> bool:
    clean_text = _clean(text)
    if not clean_text:
        return False
    return any(_clean(a) in clean_text for a in aliases)


# ── 연도·시험·학년 서명 ──────────────────────────────────────────────────

@dataclass
class Signature:
    year: int | None = None
    exam: str | None = None  # ids.normalize_exam() 을 거친 정규형
    grade: int | None = None

    @property
    def complete(self) -> bool:
        if self.year is None or self.exam is None:
            return False
        return self.grade is not None if self.exam in GRADE_BEARING else True

    def filled_by(self, other: "Signature") -> "Signature":
        """비어 있는 필드만 other 값으로 채운 새 서명. 이미 있는 값은 안 덮는다 —
        더 앞선(신뢰도 높은) 출처에서 얻은 값을 뒤 출처가 뭉개면 안 되기 때문."""
        return Signature(
            year=self.year if self.year is not None else other.year,
            exam=self.exam if self.exam is not None else other.exam,
            grade=self.grade if self.grade is not None else other.grade,
        )


def parse_signature(text: str) -> Signature:
    """텍스트 한 덩이(파일명/폴더명/표지)에서 학년도·시험·학년을 뽑는다.

    CSAT_Clipper.metadata_extractor 의 정규식을 가져오되, 시험 종류 정규화는
    여기서 다시 만들지 않고 ids.normalize_exam() 에 위임한다 — 표기 규칙이
    common/ids.py 한 곳만 벗어나면 안 된다 (CONTRACT 2절).
    """
    if not text:
        return Signature()

    year = None
    m = YEAR_RE.search(text)
    if m:
        year = int(m.group(1))

    exam = None
    if SUNEUNG_RE.search(text):
        exam = "수능"
    else:
        m = MOPYEONG_RE.search(text)
        if m:
            month = int(m.group(1) or m.group(2))
            try:
                exam = normalize_exam(f"{month}월모평")
            except ValueError:
                exam = None
        if exam is None:
            m = HAKPYEONG_RE.search(text)
            if m:
                month = int(m.group(1) or m.group(2))
                try:
                    exam = normalize_exam(f"{month}월학평")
                except ValueError:
                    exam = None

    grade = None
    gm = GRADE_RE.search(text)
    if gm:
        grade = int(gm.group(1) or gm.group(2))

    return Signature(year=year, exam=exam, grade=grade)


# ── 파일 종류(kind) 판정 ─────────────────────────────────────────────────

def detect_kind(path: Path) -> tuple[str | None, str]:
    """(kind, 판정근거). 파일명에 힌트가 없으면 확장자로 최후 추정한다."""
    low = path.stem.lower()
    for kind, keywords in KIND_KEYWORDS:
        if any(kw in low for kw in keywords):
            return kind, "filename"
    if path.suffix.lower() in IMAGE_EXTS:
        # 실전에서 종류 힌트 없는 이미지는 거의 다 정답 스캔본이었다.
        return "answer", "ext-fallback"
    return None, "unknown"


def _first_page_text(path: Path) -> str:
    if path.suffix.lower() != ".pdf":
        return ""
    try:
        import fitz  # PyMuPDF — requirements.txt 에 이미 있다.
        doc = fitz.open(str(path))
        try:
            if len(doc) == 0:
                return ""
            return doc[0].get_text("text")
        finally:
            doc.close()
    except Exception:
        # 손상된 PDF, 텍스트 레이어 없는 스캔본 등. 표지 텍스트 폴백이 실패할 뿐,
        # 파이프라인 전체가 죽으면 안 된다.
        return ""


def _page_count(path: Path) -> int | None:
    if path.suffix.lower() != ".pdf":
        return None
    try:
        import fitz
        doc = fitz.open(str(path))
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return None


# ── 파일 하나에 대한 판정 결과 ───────────────────────────────────────────

@dataclass
class Hit:
    path: Path
    kind: str | None
    kind_by: str
    sig: Signature
    sig_by: str | None
    subj_ok: bool
    subj_by: str | None


_SOURCE_RANK = {"filename": 0, "folder": 1, "cover-text": 2, "inherited": 3, None: 9}


def _rank(h: Hit) -> tuple:
    """같은 회차·같은 역할 후보가 여럿일 때 누구를 남길지 정하는 순서.
    파일명에서 바로 읽은 게 가장 신뢰도가 높고, 형제 파일에서 상속받은 게 가장 낮다."""
    return (_SOURCE_RANK.get(h.subj_by), _SOURCE_RANK.get(h.sig_by), str(h.path))


def detect_hit(path: Path, aliases: list[str]) -> Hit:
    kind, kind_by = detect_kind(path)

    text_cache: list[str] = []

    def cover_text() -> str:
        if not text_cache:
            text_cache.append(_first_page_text(path))
        return text_cache[0]

    # --- 연도/시험/학년: 파일명 → 부모 폴더명 → 표지 텍스트 순으로 겹쳐 채운다 ---
    # sig_by 는 "마지막으로 뭔가를 보태준 출처" 라벨이다. filename 만으로 이미
    # 완전하면 filename, 부족해서 folder/표지가 빈 칸을 채웠으면 그쪽으로 넘어간다.
    sig = parse_signature(path.stem)
    sig_by = "filename"
    if not sig.complete:
        merged = sig.filled_by(parse_signature(path.parent.name))
        if merged != sig:
            sig_by = "folder"
        sig = merged
    if not sig.complete:
        merged = sig.filled_by(parse_signature(cover_text()))
        if merged != sig:
            sig_by = "cover-text"
        sig = merged
    if sig.exam in ALWAYS_GRADE3 and sig.grade is None:
        sig = Signature(year=sig.year, exam=sig.exam, grade=3)

    # --- 과목: 파일명 → 부모 폴더명 → 표지 텍스트 ---
    subj_ok, subj_by = False, None
    if matches_subject(path.stem, aliases):
        subj_ok, subj_by = True, "filename"
    elif matches_subject(path.parent.name, aliases):
        subj_ok, subj_by = True, "folder"
    elif matches_subject(cover_text(), aliases):
        subj_ok, subj_by = True, "cover-text"

    return Hit(path=path, kind=kind, kind_by=kind_by, sig=sig, sig_by=sig_by,
               subj_ok=subj_ok, subj_by=subj_by)


def _demote_bulk_image_exports(hits: list[Hit]) -> None:
    """확장자만으로 종류를 추정한(ext-fallback) 이미지가 한 폴더에 여럿이면 접는다.

    실측(earth-science-i, 노이즈 많은 원본): PDF→마크다운 변환 도구가 남긴
    '..._images/imageFile1.png' 같은 폴더에 페이지당 이미지가 수십 장씩 있었다.
    이런 폴더가 하필 원본 파일명을 그대로 이어받아('2021_수능_지구과학Ⅰ_문제지_images')
    연도·시험·과목까지 다 걸려버리면, 진짜 정답 스캔 한 장과 '정답' 역할을 놓고
    후보가 수십 개로 불어난다. 결과 자체는 순위 로직(_rank)이 진짜 파일을 골라 지켜내지만
    verified 를 흐리고 attention 을 필요 없이 채운다. 정답 스캔은 보통 폴더에 한 장이지
    수십 장일 수 없으므로, 힌트 없는 이미지가 한 폴더에 3장 넘게 있으면 애초에
    종류 추정 자체를 접어 후보에서 뺀다."""
    by_dir: dict[Path, list[Hit]] = {}
    for h in hits:
        if h.kind_by == "ext-fallback":
            by_dir.setdefault(h.path.parent, []).append(h)
    for group in by_dir.values():
        if len(group) > 2:
            for h in group:
                h.kind, h.kind_by = None, "bulk-export"


def _apply_solution_inheritance(hits: list[Hit]) -> None:
    """'해설.pdf'류 파일명에는 과목명이 안 붙는 경우가 실전에 있다(모듈 docstring 참조).
    같은 폴더에 우리 과목으로 확정된 문제지가 있으면 해설만 거기서 상속받는다.

    정답 파일은 상속 대상에서 뺐다: 평가원이 뿌리는 합본 정답표('과학탐구영역_정답표
    (원본).pdf')가 같은 폴더에 있을 때 그것까지 상속을 받아버리면, 진짜 정답 파일과
    역할이 중복돼 버린다. 문제지가 없으면(=이미 정답 역할이 있다는 신호가 없으면)
    상속하지 않는 편이 안전하다."""
    by_dir: dict[Path, list[Hit]] = {}
    for h in hits:
        by_dir.setdefault(h.path.parent, []).append(h)
    for group in by_dir.values():
        donors = [h for h in group if h.kind == "problem" and h.subj_ok]
        if not donors:
            continue
        donor = donors[0]
        for h in group:
            if h.kind == "solution" and not h.subj_ok:
                h.subj_ok, h.subj_by = True, "inherited"
                if not h.sig.complete and donor.sig.complete:
                    h.sig, h.sig_by = donor.sig, "inherited"


# ── exam_id 그룹핑 ───────────────────────────────────────────────────────

def _sig_key(sig: Signature) -> tuple:
    grade = sig.grade if sig.exam in GRADE_BEARING else None
    return (sig.year, sig.exam, grade)


@dataclass
class ExamGroup:
    exam_id: str
    sig: Signature
    files: dict[str, Hit] = field(default_factory=dict)  # kind -> 채택된 Hit
    dropped: list[tuple[Hit, str]] = field(default_factory=list)  # (버려진 Hit, 이유)


def group_hits(hits: list[Hit], report: Report) -> list[ExamGroup]:
    candidates = [h for h in hits if h.subj_ok and h.kind and h.sig.complete]

    by_sig: dict[tuple, list[Hit]] = {}
    for h in candidates:
        by_sig.setdefault(_sig_key(h.sig), []).append(h)

    groups: list[ExamGroup] = []
    for key, bucket in by_sig.items():
        year, exam, grade = key
        try:
            exam_id = make_exam_id(year, exam, grade)
        except ValueError as exc:
            report.note(f"{year}_{exam}", f"exam_id 를 못 만들었다: {exc}", "error")
            continue

        sig = bucket[0].sig
        g = ExamGroup(exam_id=exam_id, sig=sig)
        by_kind: dict[str, list[Hit]] = {}
        for h in bucket:
            by_kind.setdefault(h.kind, []).append(h)
        for kind, kind_hits in by_kind.items():
            kind_hits.sort(key=_rank)
            g.files[kind] = kind_hits[0]
            for dropped in kind_hits[1:]:
                g.dropped.append((dropped, f"{exam_id} 의 {kind} 역할 후보가 여럿이라 "
                                            f"{kind_hits[0].path.name} 을(를) 선택했다"))
        groups.append(g)

    return groups


# ── --input 모드: 복사해 넣기 ────────────────────────────────────────────

def _run_from_input(input_dir: Path, subject, space: Space, report: Report,
                     only: set[str] | None, dry_run: bool, force: bool) -> None:
    if not input_dir.exists():
        report.note("--input", f"폴더가 없다: {input_dir}", "error")
        report.count(found=0, groups=0, done=0)
        return

    files = sorted(p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in SCAN_EXTS)
    aliases = subject_aliases(subject)
    report.count(found=len(files))

    hits = [detect_hit(p, aliases) for p in files]
    _demote_bulk_image_exports(hits)
    _apply_solution_inheritance(hits)

    subj_mismatch = sum(1 for h in hits if not h.subj_ok)
    kind_unknown = sum(1 for h in hits if h.subj_ok and h.kind is None)
    sig_incomplete = sum(1 for h in hits if h.subj_ok and h.kind and not h.sig.complete)
    report.count(skipped_subject=subj_mismatch, skipped_kind=kind_unknown,
                 skipped_signature=sig_incomplete)

    # 과목은 맞는데 다른 이유로 걸러진 파일은 사람이 볼 가치가 있다 (30건 상한 안에서).
    for h in hits:
        if not h.subj_ok:
            continue
        if h.kind is None:
            report.note(str(h.path.name), "과목은 맞는 듯한데 문제지/정답/해설 구분이 안 됨", "warn")
        elif not h.sig.complete:
            report.note(str(h.path.name),
                        f"연도/시험 인식 실패 (year={h.sig.year}, exam={h.sig.exam}, grade={h.sig.grade})",
                        "warn")

    groups = group_hits(hits, report)
    if only:
        groups = [g for g in groups if g.exam_id in only]

    done = 0
    for g in groups:
        for h, why in g.dropped:
            report.note(g.exam_id, why, "warn")

        if "problem" not in g.files:
            report.note(g.exam_id, "문제지를 못 찾아서 이 회차는 건너뛴다", "warn")
            continue
        # answer/solution 이 없는 채로도 manifest 는 만든다(문제지만으로도 crop 은 진행할 수
        # 있다) — 다만 이유를 남긴다. 실측 사례: 어떤 회차는 과목별 정답지가 없고 평가원이
        # 뿌린 '과학탐구_정답.pdf'(전 과목 합본, 표지가 다른 과목) 만 있었다. 그 파일은
        # 우리 과목으로 안 걸려서(의도대로) 건너뛰었는데, 그러면 여기 답이 비게 된다 —
        # 억지로 합본을 배정하지 않은 대가라, 사람이 보게 알려야 한다.
        for missing in ("answer", "solution"):
            if missing not in g.files:
                report.note(g.exam_id, f"{missing} 파일을 못 찾음 — 과목 전용 파일이 없거나"
                                        f" 파일명/폴더명에 과목 표기가 없을 수 있다. 직접 확인 필요",
                            "warn")

        dest_dir = space.source_dir(g.exam_id)
        dest_files: dict[str, str] = {}
        pages: dict[str, int] = {}
        copy_failed = False
        for kind, h in g.files.items():
            dest_name = f"{kind}{h.path.suffix.lower()}"
            dest_files[kind] = dest_name
            n = _page_count(h.path)
            if n is not None:
                pages[kind] = n
            if dry_run:
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / dest_name
            if dest_path.exists() and not force:
                continue
            try:
                shutil.copy2(h.path, dest_path)
            except OSError as exc:
                report.note(g.exam_id, f"{kind} 복사 실패: {exc}", "error")
                copy_failed = True
        if copy_failed:
            continue

        # manifest 의 키 이름·형태는 scripts/manifest.py 한 곳에만 있다.
        # 예전에는 여기서 dict 리터럴로 만들고 crop·extract 가 각자 다르게 추측해
        # 읽었다 — 그 추측들이 커버하는 형태가 서로 달랐다(manifest.py 모듈 설명 참조).
        payload = mf.build(
            exam_id=g.exam_id, slug=subject.slug, label=subject.label,
            files=dest_files,
            detected={"year": g.sig.year, "exam": g.sig.exam, "grade": g.sig.grade,
                      "by": g.files["problem"].sig_by or "filename"},
            pages=pages, provider="manual",
            # 3역할이 다 모이고 역할 중복도 없을 때만 자동으로 verified 를 켠다.
            verified={"problem", "answer", "solution"} <= set(dest_files) and not g.dropped,
        )
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            mf.write(space, g.exam_id, payload)
        done += 1

    # manifest 를 회차마다 나열하면 과목이 여럿 쌓였을 때 리포트가 커진다
    # (CONTRACT 5절: LLM 이 읽는 유일한 출력이라 컨텍스트를 아껴야 한다).
    # 폴더 하나로 요약하고, 회차별 내용은 각 manifest.json 을 열어서 보게 한다.
    if done and not dry_run:
        report.artifact(space.rel(space.sources))

    report.count(groups=len(groups), done=done)


# ── --input 없는 모드: 이미 있는 sources/ 갱신·검증 ──────────────────────

def _split_exam_id(exam_id: str) -> Signature | None:
    """ids.make_exam_id() 의 역변환. 폴더 이름이 이미 exam_id 형식이라는 전제로
    거꾸로 푼다 — 파일 스캔 없이 신뢰할 수 있는 값이라 회차마다 텍스트 인식을
    다시 돌릴 필요가 없다."""
    parts = exam_id.split("_")
    try:
        if len(parts) == 3 and parts[1].startswith("고"):
            year, grade_tok, exam = parts
            return Signature(year=int(year), exam=normalize_exam(exam), grade=int(grade_tok[1:]))
        if len(parts) == 2:
            year, exam = parts
            exam_norm = normalize_exam(exam)
            grade = 3 if exam_norm in ALWAYS_GRADE3 else None
            return Signature(year=int(year), exam=exam_norm, grade=grade)
    except (ValueError, IndexError):
        return None
    return None


def _run_refresh(subject, space: Space, report: Report, only: set[str] | None, dry_run: bool) -> None:
    if not space.sources.exists():
        report.note("sources", "sources/ 가 비어 있다. --input 으로 먼저 채워라", "warn")
        report.count(found=0, groups=0, done=0)
        return

    exam_dirs = sorted(p for p in space.sources.iterdir() if p.is_dir())
    if only:
        exam_dirs = [p for p in exam_dirs if p.name in only]
    report.count(found=len(exam_dirs))

    done = 0
    for d in exam_dirs:
        sig = _split_exam_id(d.name)
        if sig is None:
            report.note(d.name, "폴더 이름이 exam_id 형식이 아니다 (예: 2024_수능)", "warn")
            continue

        files = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in SCAN_EXTS)
        by_kind: dict[str, Path] = {}
        for p in files:
            kind, _by = detect_kind(p)
            if kind is None:
                report.note(d.name, f"종류를 알 수 없는 파일: {p.name}", "info")
                continue
            if kind in by_kind:
                report.note(d.name, f"{kind} 역할 파일이 둘 이상: {by_kind[kind].name}, {p.name}"
                                     f" — {by_kind[kind].name} 을(를) 유지한다", "warn")
                continue
            by_kind[kind] = p

        if "problem" not in by_kind:
            report.note(d.name, "problem.* 파일이 없다", "error")
            continue
        for missing in ("answer", "solution"):
            if missing not in by_kind:
                report.note(d.name, f"{missing}.* 파일이 없다", "warn")

        # 이미 있는 manifest 는 download 가 쓴 것일 수 있다(schema_version 2).
        # provider 를 이어받고, sha256·source_url 같은 우리가 모르는 키는
        # mf.write(preserve=True) 가 통째로 살려 준다 — 원본 재현의 유일한 단서다.
        prev = mf.load(space, d.name)
        prev_provider = prev.provider or "manual"

        dest_files = {k: p.name for k, p in by_kind.items()}
        pages = {k: n for k, p in by_kind.items() if (n := _page_count(p)) is not None}
        payload = mf.build(
            exam_id=d.name, slug=subject.slug, label=prev.label or subject.label,
            files=dest_files,
            detected={"year": sig.year, "exam": sig.exam, "grade": sig.grade, "by": "exam_id"},
            pages=pages, provider=prev_provider,
            verified={"problem", "answer", "solution"} <= set(dest_files),
        )
        if not dry_run:
            mf.write(space, d.name, payload)
        # manifest 를 새로 썼는데도 남는 문제(가리키는 파일이 없다 등)는 사람이 봐야 한다.
        for why in mf.problems(mf.parse(payload), d):
            report.note(d.name, why, "warn")
        done += 1

    if done and not dry_run:
        report.artifact(space.rel(space.sources))

    report.count(groups=len(exam_dirs), done=done)


# ── gw.py 표준 인터페이스 ────────────────────────────────────────────────

def register(parser) -> None:
    parser.add_argument("--subject", required=True, help="과목 슬러그 (subjects/<slug>)")
    parser.add_argument("--input", help="PDF 더미가 있는 폴더. 생략하면 이미 sources/ 에 "
                                          "있는 것만 검증·갱신한다")
    parser.add_argument("--dry-run", action="store_true", help="복사·쓰기 없이 리포트만 만든다")
    parser.add_argument("--force", action="store_true",
                         help="--input 모드에서 이미 있는 sources 파일도 덮어쓴다")
    parser.add_argument("--only", help="이 회차만 처리 (exam_id 콤마 구분, 예: 2024_수능,2025_수능)")
    parser.add_argument("--quiet", action="store_true",
                         help="stdout 에 리포트 경로만 남긴다")
    parser.add_argument("--workspace", help="작업 공간 경로 직접 지정 (기본 workspace/<slug>)")


def run(args) -> int:
    # 과목 정의를 못 읽으면 리포트를 남길 workspace 경로조차 정할 수 없다.
    # gw.py 는 예외를 그대로 흘려보내서 LLM 이 리포트 대신 traceback 을 읽게 된다
    # (통합 검증에서 7개 명령 중 5개가 그랬다). 형제 모듈(build/classify)과 같은
    # 한 줄 안내로 끝낸다 — load_subject 의 메시지가 등록된 과목 목록까지 담고 있다.
    try:
        subject = load_subject(args.subject)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] detect: {exc}")
        return 1
    # --workspace 는 7개 명령 공통 옵션이다. 공유 workspace/<slug> 를 여러 실행이
    # 서로 밟아 산출물을 지우는 사고가 실제로 여러 번 났다 — 격리 실행이 가능해야 한다.
    space = Space(args.subject, getattr(args, "workspace", None)).ensure()
    report = Report("detect", args.subject, space)
    only = {x.strip() for x in args.only.split(",") if x.strip()} if getattr(args, "only", None) else None

    if args.input:
        _run_from_input(Path(args.input), subject, space, report,
                         only=only, dry_run=args.dry_run, force=args.force)
    else:
        _run_refresh(subject, space, report, only=only, dry_run=args.dry_run)

    done = report.counts.get("done", 0)
    if done > 0:
        report.next = f"python scripts/gw.py crop --subject {args.subject}"
    elif args.input:
        report.next = "attention 을 보고 파일명/폴더명을 손보거나 subject.json 의 aliases 를 늘려라"
    else:
        report.next = f"python scripts/gw.py detect --subject {args.subject} --input <PDF 더미 폴더>"

    # --quiet 은 선언만 돼 있고 실제로는 아무 일도 하지 않았다(통합 검증에서 발견).
    # 형제 모듈 crop.py 와 같은 규약으로 맞춘다 — 리포트 경로 한 줄만 남긴다.
    if getattr(args, "quiet", False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = report.finish()
        print(space.report("detect"))
        return code
    return report.finish()
