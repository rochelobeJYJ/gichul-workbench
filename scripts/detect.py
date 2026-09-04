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
  문제지에서 과목을 상속받는다 (_apply_sibling_inheritance).
- 평가원이 통째로 뿌리는 '과학탐구영역_정답표(원본).pdf' 는 8과목 정답표를 한 파일에 담고,
  첫 페이지는 그 중 첫 과목(물리학Ⅰ) 것이다. 폴더명에도 특정 과목이 없다. → 파일명/폴더명/
  **첫 페이지**에는 우리 과목이 안 걸린다. 예전에는 여기서 그냥 버렸는데, 그 대가로
  지구과학Ⅰ 2021·2022 수능이 정답지 축을 통째로 잃었다(실측: 그 두 회차만 files 에 answer
  가 없었고, 2022 수능 5문항은 정답 대조가 1축으로 주저앉았다). 이제는 **쪽마다 있는
  '( 지구과학Ⅰ ) 과목' 머리글**을 찾아 짝짓는다(bundle_subject_page). 단 이 경로로 걸린
  파일은 **최후 수단**이라, 같은 역할에 파일명/폴더로 걸린 후보가 하나라도 있으면 조용히
  물러난다 — 위에 적힌 '역할 중복 배정' 사고를 그대로 피하기 위해서다.

내용으로 문제지/정답지/해설지를 가르는 이유 (kind_from_content):
  README 가 "파일 이름이 제각각이어도 괜찮습니다"라고 약속하는데, 예전 코드는 연도·시험·
  과목만 표지에서 폴백하고 **kind 는 파일명에만 의존**했다. 그래서 '한국지리 기출 (1).pdf'
  / '답 모음.pdf' / '해설 파일.pdf' 같은 평범한 이름 묶음이 done=0 으로 끝났다(실측).
  실패 방향이 중요하다 — 엉뚱한 역할로 배정하면 정답이 통째로 틀린다. 그래서 내용 판별은
  **확실할 때만 답하고 애매하면 기권**한다. 아래 kind_from_content 참조.
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
from common.progress import track
# 정답표의 과목 머리글 패턴과 과목명 정규화는 extract 가 이미 실전에서 벼려 둔 것이다.
# 여기서 다시 만들면 두 모듈이 같은 파일을 다르게 판단한다 — 그게 manifest.py 가 생긴 이유다.
# (fold_name 은 가운뎃점 코드포인트·로마숫자 NFKC 사고를 이미 흡수하고 있다.)
from extractlib.answers import SUBJECT_SECTION_RE
from extractlib.textnorm import fold_name

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
    """(kind, 판정근거). 파일명·확장자만 본다. 못 정하면 (None, "unknown")."""
    low = path.stem.lower()
    for kind, keywords in KIND_KEYWORDS:
        if any(kw in low for kw in keywords):
            return kind, "filename"
    if path.suffix.lower() in IMAGE_EXTS:
        # 실전에서 종류 힌트 없는 이미지는 거의 다 정답 스캔본이었다.
        return "answer", "ext-fallback"
    return None, "unknown"


# --- 내용으로 문제지/정답지/해설지 가르기 -------------------------------
#
# ## 무엇을 근거로 골랐나, 왜 그것이 셋을 확실히 가르나
#
# 세 문서는 "글자가 무엇으로 채워져 있는가"가 서로 배타적이다. 그래서 세 축을 따로 세고,
# **정확히 한 축만 켜질 때만** 답한다.
#
# 1) 선택지 묶음 (choice_runs) — 원문자가 ①②③④⑤ **순서대로 한 바퀴** 도는 횟수.
#    문제지는 문항마다 선택지 다섯 개를 이 순서로 찍으므로 문항 수만큼 나온다.
#    해설지·정답지에도 원문자는 많지만 **정답을 가리키는 낱개**라 순서가 뒤섞여 있어
#    한 바퀴가 잘 안 돈다. 개수(circled)가 아니라 순서를 세는 이유가 이것이다 —
#    해설지 원문자 40개와 문제지 원문자 100개는 임계값 하나로 못 가르지만,
#    한 바퀴 횟수는 20 대 0~1 로 갈린다.
# 2) 해설 머리글 (solution_marks) — '정답 및 해설' '오답 풀이' '[출제의도]' 같은,
#    해설지에만 있는 조판 머리글. 문항마다 반복되므로 몇 개인지도 신호가 된다.
#    **한글 비율이 아니라 이 머리글을 보는 이유**: 2022학년도 수능 해설 PDF 는 본문 한글이
#    전부 깨진 사설 글리프로 나와 한글이 쪽당 7자뿐이다(PITFALLS 3-1). 그런데도 표지의
#    '정답 및 해설' 은 멀쩡히 남아 있었다. 한글 밀도로 갈랐으면 이 파일이 '문장 없는 문서'
#    = 정답지로 배정될 뻔했다 — 바로 그 오배정이 이 저장소가 가장 피하는 실패다.
# 3) 정답표 머리글 (answer_marks) — '정답표' '문항 번호' '( 과목 ) 과목'.
#    여기에 **쪽당 글자 수 상한**을 함께 건다. 정답표는 문장이 없는 격자라 쪽당 230자쯤이고,
#    문제지·해설지는 쪽당 500자 아래로 내려가지 않는다. 위 2)의 글리프 손상 파일도
#    쪽당 1,269자라 이 상한에 원리적으로 못 들어온다.
#
# 임계값은 전부 실측이다 — 지구과학Ⅰ·Ⅱ 원본 104개 + 통합과학·사회탐구·학평 작업공간
# 145개, **PDF 249개**. 오배정 0건, 기권 7건(전부 텍스트 레이어가 아예 없는 스캔본).
# 관찰된 범위(문제지 / 정답지 / 해설지):
#   choice_runs      20 / 0~1 / 0~1
#   solution_marks   0~1 / 0   / 3~23
#   answer_marks     0   / 5~48 / 0
#   쪽당 글자 수     990~1863 / 228~229 / 525~4507
CHOICE_SEQUENCE = "①②③④⑤"
CONTENT_SCAN_PAGES = 16        # 합본 정답표가 사회탐구 9과목까지 한 파일에 들어온다
CHOICE_RUNS_PROBLEM = 10       # 관찰 20 vs 1 — 절반에 걸어도 양쪽 다 여유가 크다
SOLUTION_MARKS_MIN = 3         # 관찰 3 vs 1
ANSWER_MARKS_MIN = 2           # 관찰 5 vs 0
ANSWER_CHARS_PER_PAGE_MAX = 600  # 관찰 229 vs 525

# 이름과 내용이 어긋나 내용을 택했을 때 kind_by 앞에 붙는 꼬리표. 리포트가 이걸 보고
# 사람에게 알린다 — 문자열을 두 곳에 적으면 언젠가 한쪽만 고쳐진다.
_OVERRIDE_PREFIX = "content-overrides-filename("

CIRCLED_RE = re.compile(f"[{CHOICE_SEQUENCE}]")
SOLUTION_HEAD_RE = re.compile(r"정답\s*(?:및|과)\s*해설|해설\s*(?:및|과)\s*정답"
                              r"|오답\s*(?:풀이|피하기)|정답\s*해설|출제\s*의도"
                              r"|\[\s*해\s*설\s*\]")
ANSWER_HEAD_RE = re.compile(r"정\s*답\s*표|문\s*항\s*\n?\s*번\s*호"
                            r"|\(\s*[^)\n]{1,30}?\s*\)\s*과목")


def _choice_runs(text: str) -> int:
    """원문자가 ①②③④⑤ 순서로 한 바퀴 도는 횟수. 중간에 끊기면 처음부터 다시 센다."""
    runs = seen = 0
    for ch in CIRCLED_RE.findall(text):
        value = CHOICE_SEQUENCE.index(ch)
        if value == seen:
            seen += 1
            if seen == len(CHOICE_SEQUENCE):
                runs += 1
                seen = 0
        else:
            # ① 이면 새 묶음의 시작으로 보고, 아니면(선택지 아닌 낱개 원문자) 버린다.
            seen = 1 if value == 0 else 0
    return runs


def kind_from_content(page_texts: list[str]) -> tuple[str | None, str]:
    """PDF 본문으로 문제지/정답지/해설지를 가른다. (kind, 근거). 못 가르면 (None, 이유).

    **확실할 때만 답한다.** 세 축 중 정확히 하나가 켜졌을 때만 kind 를 주고,
    0개거나 2개 이상이면 기권한다. 지시사항의 ★ 그대로다 — 엉뚱한 역할로 배정하면
    정답이 통째로 틀리는 쪽으로 실패하는데, 기권은 사람이 파일 이름만 고치면 되는
    쪽으로 실패한다. 두 실패의 값이 다르다.
    """
    text = "".join(page_texts)
    pages = max(len(page_texts), 1)
    if not text.strip():
        # 스캔본(텍스트 레이어 없음). 쪽수로 정답지/문제지를 찍는 방법이 있긴 하지만
        # 실측에서 1쪽짜리 스캔 정답지와 4쪽짜리 스캔 문제지가 둘 다 나온다 —
        # 쪽수는 신호가 아니라 우연이라, 여기서 찍으면 오배정이 된다.
        return None, "텍스트 레이어가 없어 내용으로는 못 가른다"

    runs = _choice_runs(text)
    solution_marks = len(SOLUTION_HEAD_RE.findall(text))
    answer_marks = len(ANSWER_HEAD_RE.findall(text))
    chars_per_page = len(text) // pages

    votes: list[str] = []
    if (answer_marks >= ANSWER_MARKS_MIN and solution_marks == 0
            and runs <= 2 and chars_per_page <= ANSWER_CHARS_PER_PAGE_MAX):
        votes.append("answer")
    if solution_marks >= SOLUTION_MARKS_MIN and runs <= 5:
        votes.append("solution")
    if (runs >= CHOICE_RUNS_PROBLEM and answer_marks < ANSWER_MARKS_MIN
            and solution_marks < SOLUTION_MARKS_MIN):
        votes.append("problem")

    evidence = (f"선택지묶음 {runs} · 해설머리글 {solution_marks} · "
                f"정답표머리글 {answer_marks} · 쪽당 {chars_per_page}자")
    if len(votes) == 1:
        return votes[0], evidence
    if votes:
        return None, f"{evidence} — 두 종류 신호가 함께 잡혀 확정하지 않는다"
    return None, f"{evidence} — 셋 중 어느 쪽에도 확실히 안 걸린다"


def resolve_kind(path: Path, page_texts) -> tuple[str | None, str]:
    """파일 이름과 내용을 함께 보고 종류를 정한다. (kind, 판정근거).

    **내용이 확정을 내면 파일 이름보다 내용을 믿는다.** 이름은 사람이 붙이는 것이고
    실제로 자주 틀린다(해설지를 '정답.pdf' 로 저장해 둔 더미가 실전에 있다).
    반대로 내용 판정은 실측 249개에서 오배정 0건이고, 조금이라도 헷갈리면 기권한다 —
    **기권할 때는 이름이 그대로 이긴다.** 그래서 이 순서가 어느 쪽으로도 손해가 아니다.

    이미지는 내용을 읽을 수 없으니 확장자 규칙에서 끝난다.
    `page_texts` 는 인자 없는 호출 가능 객체다(느긋한 읽기) — PDF 가 아니면 안 연다.
    """
    named, by = detect_kind(path)
    if path.suffix.lower() != ".pdf":
        return named, by
    found, why = kind_from_content(page_texts())
    if found is None:
        return (named, by) if named else (None, f"unknown({why})")
    if named is None:
        return found, f"content({why})"
    if named == found:
        return named, by
    # 이름과 내용이 어긋났다. 내용을 택하되 **반드시 리포트에 남긴다** — 사람이 파일을
    # 잘못 둔 것일 수도, 우리가 처음 보는 판형일 수도 있어서 조용히 넘기면 안 된다.
    return found, f"{_OVERRIDE_PREFIX}이름은 {named}, 내용은 {found}: {why})"


def bundle_subject_page(page_texts: list[str], aliases: list[str]) -> int | None:
    """정답표 안에서 우리 과목 머리글이 있는 쪽 번호(1부터). 없으면 None.

    평가원 정답표는 한 교시 8~9과목을 한 파일에 담고 쪽마다 '( 지구과학Ⅰ ) 과목' 을
    찍는다. 파일명·폴더명에 과목이 없어도 **파일 안에는 있다.**

    대조는 substring 이 아니라 fold_name 완전일치다. 부분열로 비교하면
    '지구과학Ⅰ' 별칭이 '지구과학Ⅱ' 머리글에 걸려 **남의 과목 정답표를 우리 것으로**
    가져온다 — 정답이 20개 통째로 틀리는 경로다.
    """
    wanted = {fold_name(a) for a in aliases if a}
    if not wanted:
        return None
    for index, text in enumerate(page_texts):
        for match in SUBJECT_SECTION_RE.finditer(text):
            if fold_name(match.group(1)) in wanted:
                return index + 1
    return None


def _doc_texts(path: Path, limit: int = CONTENT_SCAN_PAGES) -> list[str]:
    """앞쪽 `limit` 쪽의 텍스트를 쪽별로. PDF 가 아니거나 못 열면 빈 목록.

    쪽별로 돌려주는 이유: 합본 정답표는 '몇 쪽이 우리 과목인가'가 답이라 쪽 경계가 필요하고,
    kind 판별은 합쳐서 쓰기만 하면 된다. 한 번 읽어 두 곳에 쓴다.
    """
    if path.suffix.lower() != ".pdf":
        return []
    try:
        import fitz  # PyMuPDF — requirements.txt 에 이미 있다.
        doc = fitz.open(str(path))
        try:
            return [doc[i].get_text("text") for i in range(min(len(doc), limit))]
        finally:
            doc.close()
    except Exception:
        # 손상된 PDF, 텍스트 레이어 없는 스캔본 등. 폴백이 실패할 뿐 파이프라인은 계속 간다.
        return []


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
    # 합본 정답표에서 우리 과목 머리글이 있던 쪽(1부터). 그 경로로 걸렸을 때만 채운다.
    subj_page: int | None = None
    # 파일이 제 안에서 과목 이름을 대는데 그게 우리 과목이 아니다 = 확실히 남의 것.
    # '모른다'와 '남의 것이다'는 다르다 — 상속을 허용할지가 여기서 갈린다.
    names_other_subject: bool = False


# 'answer-table' 은 정답표 안의 과목 머리글로 확인한 것이라 표지 폴백보다 뒤,
# 형제 파일에서 물려받은 것보다는 앞이다(제 파일 안에서 읽은 증거이므로).
_SOURCE_RANK = {"filename": 0, "folder": 1, "cover-text": 2,
                "answer-table": 3, "inherited": 4, None: 9}

# 합본 정답표는 **최후 수단**이다. 같은 역할에 다른 경로로 걸린 후보가 있으면 물러난다.
# 물러날 때 dropped 에 남기지도 않는다 — 실제 원본에서는 과목 전용 정답지와 합본 정답표가
# 같은 폴더에 늘 함께 있어서, 이걸 '역할 후보 중복' 으로 경고하면 정상 상태가 매 회차
# warn 이 되고 verified 가 꺼진다(지구과학Ⅱ 19회차 전부가 그렇게 될 뻔했다).
_LAST_RESORT_SUBJ_BY = "answer-table"


def _rank(h: Hit) -> tuple:
    """같은 회차·같은 역할 후보가 여럿일 때 누구를 남길지 정하는 순서.
    파일명에서 바로 읽은 게 가장 신뢰도가 높고, 형제 파일에서 상속받은 게 가장 낮다."""
    return (_SOURCE_RANK.get(h.subj_by), _SOURCE_RANK.get(h.sig_by), str(h.path))


def detect_hit(path: Path, aliases: list[str]) -> Hit:
    # 쪽 텍스트는 파일당 한 번만 읽는다. kind 폴백·표지 폴백·합본 과목 폴백이 같이 쓴다.
    cache: list[list[str]] = []

    def pages_text() -> list[str]:
        if not cache:
            cache.append(_doc_texts(path))
        return cache[0]

    def cover_text() -> str:
        pages = pages_text()
        return pages[0] if pages else ""

    kind, kind_by = resolve_kind(path, pages_text)

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
    subj_ok, subj_by, subj_page, names_other_subject = False, None, None, False
    if matches_subject(path.stem, aliases):
        subj_ok, subj_by = True, "filename"
    elif matches_subject(path.parent.name, aliases):
        subj_ok, subj_by = True, "folder"
    elif matches_subject(cover_text(), aliases):
        subj_ok, subj_by = True, "cover-text"
    elif kind == "answer":
        # 마지막 자리: 여러 과목이 든 정답표. 첫 쪽은 남의 과목이라 위 셋이 전부 빗나간다.
        # kind 가 answer 로 확정된 파일에만 적용한다 — 문제지·해설지에서 이 머리글을
        # 찾을 일이 없고, 범위를 좁혀야 오배정 여지도 좁아진다.
        page = bundle_subject_page(pages_text(), aliases)
        if page is not None:
            subj_ok, subj_by, subj_page = True, _LAST_RESORT_SUBJ_BY, page
        else:
            names_other_subject = any(SUBJECT_SECTION_RE.search(t) for t in pages_text())

    return Hit(path=path, kind=kind, kind_by=kind_by, sig=sig, sig_by=sig_by,
               subj_ok=subj_ok, subj_by=subj_by, subj_page=subj_page,
               names_other_subject=names_other_subject)


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


def _inherit_subject(h: Hit, donor: Hit) -> None:
    """형제 파일(같은 폴더의 확정된 문제지)에서 과목을, 필요하면 회차까지 물려준다."""
    h.subj_ok, h.subj_by = True, "inherited"
    if not h.sig.complete and donor.sig.complete:
        h.sig, h.sig_by = donor.sig, "inherited"


def _apply_sibling_inheritance(hits: list[Hit]) -> list[tuple[str, list[str]]]:
    """'해설.pdf'류 파일명에는 과목명이 안 붙는 경우가 실전에 있다(모듈 docstring 참조).
    같은 폴더에 우리 과목으로 확정된 문제지가 있으면 거기서 과목을 물려받는다.
    돌려주는 것은 **일부러 상속하지 않은** 자리들이다(아래 2번 조건) — 리포트가 알린다.

    해설은 조건 없이, **정답은 조건부로** 물려받는다. 예전에는 정답을 아예 뺐는데,
    그 대가가 컸다(실측): EBSi 정답지는 텍스트가 0자인 PNG 라 표지 폴백이 원리적으로
    안 통하고, 파일명(`answer.png`)에도 폴더명(`2025_고3_3월학평`)에도 과목이 없다.
    그래서 **손대지 않은 원본 파일명으로도** 학평 회차의 정답 축이 통째로 사라졌다.

    정답을 다시 들이면서 원래 걱정하던 사고(합본 정답표가 상속을 받아 역할을 가로챔)는
    두 가지 조건으로 막는다.
      1) 그 파일이 **다른 과목을 자기 안에서 이름 대고 있으면** 절대 상속하지 않는다.
         합본 정답표는 쪽마다 '( 물리학Ⅰ ) 과목' 을 찍는다 — 우리 과목이 그중에 있으면
         detect_hit 가 이미 subj_ok 로 만들었을 것이고, 없다면 그건 확실히 남의 것이다.
      2) 주인 없는 정답 후보가 폴더에 **둘 이상이면 아무것도 상속하지 않는다.**
         8과목 정답 스캔을 한 폴더에 쏟아부은 경우가 여기다. 그때 하나를 고르는 것은
         동전 던지기이고, 틀리면 20문항 정답이 통째로 남의 과목 것이 된다."""
    refused: list[tuple[str, list[str]]] = []
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
                _inherit_subject(h, donor)
        orphans = [h for h in group
                   if h.kind == "answer" and not h.subj_ok and not h.names_other_subject]
        if len(orphans) == 1:
            _inherit_subject(orphans[0], donor)
        elif orphans:
            refused.append((orphans[0].path.parent.name, [h.path.name for h in orphans]))
    return refused


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
        # 역할 순서를 mf.ROLES 로 고정한다. 예전에는 폴더 스캔 순서가 그대로 manifest 의
        # 키 순서가 됐다 — 같은 회차를 파일 이름만 바꿔 다시 넣으면 내용이 똑같은데도
        # manifest 바이트가 달라진다. 재현 대조를 바이트로 하는 저장소에서 이건 잡음이다.
        ordered = [r for r in mf.ROLES if r in by_kind] + [k for k in by_kind if k not in mf.ROLES]
        for kind in ordered:
            kind_hits = by_kind[kind]
            # 합본 정답표(최후 수단)는 다른 경로로 걸린 후보가 하나도 없을 때만 쓴다.
            # 물러난 것은 dropped 에 넣지 않는다 — 위 _LAST_RESORT_SUBJ_BY 주석 참조.
            pool = [h for h in kind_hits if h.subj_by != _LAST_RESORT_SUBJ_BY] or kind_hits
            pool.sort(key=_rank)
            g.files[kind] = pool[0]
            for dropped in pool[1:]:
                g.dropped.append((dropped, f"{exam_id} 의 {kind} 역할 후보가 여럿이라 "
                                            f"{pool[0].path.name} 을(를) 선택했다"))
        groups.append(g)

    return groups


# ── --input 모드: 복사해 넣기 ────────────────────────────────────────────

def _run_from_input(input_dir: Path, subject, space: Space, report: Report,
                     only: set[str] | None, dry_run: bool, force: bool,
                     quiet: bool = False) -> None:
    if not input_dir.exists():
        report.note("--input", f"폴더가 없다: {input_dir}", "error")
        report.count(found=0, groups=0, done=0)
        return

    files = sorted(p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in SCAN_EXTS)
    aliases = subject_aliases(subject)
    report.count(found=len(files))

    # 파일 하나하나를 열어 표지 텍스트까지 본다(PDF 면 첫 쪽 렌더). 수백 개면 오래 걸린다.
    hits = [detect_hit(p, aliases)
            for p in track(files, "파일", label="detect", quiet=quiet,
                           detail=lambda x: x.name)]
    _demote_bulk_image_exports(hits)
    for folder, names in _apply_sibling_inheritance(hits):
        # 골랐다면 동전 던지기였을 자리다. 무엇을 왜 안 골랐는지는 말해 줘야 한다.
        report.note(folder, f"과목을 알 수 없는 정답 후보가 {len(names)}개라 어느 것도 쓰지 않았다"
                            f" ({', '.join(names[:4])}) — 우리 과목 것 하나만 남기거나"
                            f" 파일명에 과목을 적어라", "warn")

    subj_mismatch = sum(1 for h in hits if not h.subj_ok)
    kind_unknown = sum(1 for h in hits if h.subj_ok and h.kind is None)
    sig_incomplete = sum(1 for h in hits if h.subj_ok and h.kind and not h.sig.complete)
    report.count(skipped_subject=subj_mismatch, skipped_kind=kind_unknown,
                 skipped_signature=sig_incomplete)

    # 과목은 맞는데 다른 이유로 걸러진 파일은 사람이 볼 가치가 있다 (30건 상한 안에서).
    # 단 _demote_bulk_image_exports 가 일부러 접은 변환 산출물은 **한 줄로 묶는다.**
    # 실측(earth-science-i): 그 파일이 536개였고 파일마다 warn 을 내니 상한 30건을 그것만으로
    # 다 써서, 정작 사람이 손댈 수 있는 나머지 510건이 통째로 잘려 나갔다. 이미 코드가
    # 의도적으로 건너뛴 것을 파일 단위로 또 경고하면 리포트가 읽히지 않는다(CONTRACT 5절).
    bulk_dirs: dict[str, int] = {}
    for h in hits:
        if not h.subj_ok:
            continue
        if h.kind is None:
            if h.kind_by == "bulk-export":
                bulk_dirs[h.path.parent.name] = bulk_dirs.get(h.path.parent.name, 0) + 1
                continue
            # 무엇을 보고 못 갈랐는지까지 적는다. 이 한 줄이 곧 사람이 할 일이어야 한다
            # (CONTRACT 5절) — 여기서 멈춘 사람에게 다음 행동이 남지 않으면 데드엔드다.
            why = h.kind_by[len("unknown("):-1] if h.kind_by.startswith("unknown(") else h.kind_by
            report.note(str(h.path.name),
                        f"과목은 맞는 듯한데 문제지/정답/해설 구분이 안 됨 ({why}). "
                        f"파일 이름에 '문제'·'정답'·'해설' 중 하나를 넣으면 확정된다", "warn")
        elif not h.sig.complete:
            report.note(str(h.path.name),
                        f"연도/시험 인식 실패 (year={h.sig.year}, exam={h.sig.exam}, grade={h.sig.grade})",
                        "warn")
        if h.kind_by.startswith(_OVERRIDE_PREFIX):
            report.note(str(h.path.name), "파일 이름과 내용이 어긋나 내용을 따랐다 — "
                                          f"{h.kind_by[len(_OVERRIDE_PREFIX):-1]}", "warn")

    if bulk_dirs:
        sample = ", ".join(sorted(bulk_dirs)[:3])
        report.note("bulk-export",
                    f"이미지만 든 변환 산출물 폴더 {len(bulk_dirs)}곳({sum(bulk_dirs.values())}장)을 "
                    f"건너뛰었다 — 예: {sample}", "info")

    groups = group_hits(hits, report)
    if only:
        groups = [g for g in groups if g.exam_id in only]

    done = 0
    for g in track(groups, "회차", label="detect", quiet=quiet, detail=lambda x: x.exam_id):
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

        # 합본 정답표를 쓴 회차는 사람이 알아야 한다 — 복사된 answer.pdf 를 열면 첫 쪽이
        # 남의 과목이라 "잘못 짝지었나" 싶어진다. extract 는 과목 머리글로 우리 쪽만 읽는다.
        for kind, h in g.files.items():
            if h.subj_by == _LAST_RESORT_SUBJ_BY:
                report.note(g.exam_id,
                            f"{h.path.name} 은(는) 여러 과목이 든 정답표다 — "
                            f"{h.subj_page}쪽의 과목 머리글로 확인하고 {kind} 로 썼다", "info")

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


def _run_refresh(subject, space: Space, report: Report, only: set[str] | None,
                 dry_run: bool, quiet: bool = False) -> None:
    if not space.sources.exists():
        report.note("sources", "sources/ 가 비어 있다. --input 으로 먼저 채워라", "warn")
        report.count(found=0, groups=0, done=0)
        return

    exam_dirs = sorted(p for p in space.sources.iterdir() if p.is_dir())
    if only:
        exam_dirs = [p for p in exam_dirs if p.name in only]
    report.count(found=len(exam_dirs))

    done = 0
    # 회차마다 PDF 를 열어 쪽수를 센다 — 19회차면 눈에 띄게 걸린다.
    for d in track(exam_dirs, "회차", label="detect", quiet=quiet, detail=lambda p: p.name):
        sig = _split_exam_id(d.name)
        if sig is None:
            report.note(d.name, "폴더 이름이 exam_id 형식이 아니다 (예: 2024_수능)", "warn")
            continue

        files = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in SCAN_EXTS)
        by_kind: dict[str, Path] = {}
        for p in files:
            # sources/ 안이라고 파일 이름이 규약대로라는 보장은 없다(사람이 손으로 넣는다).
            # --input 모드와 같은 판별기를 쓴다 — 두 경로가 다른 답을 내면 안 된다.
            kind, by = resolve_kind(p, lambda p=p: _doc_texts(p))
            if kind is None:
                why = by[len("unknown("):-1] if by.startswith("unknown(") else by
                report.note(d.name, f"종류를 알 수 없는 파일: {p.name} ({why})", "info")
                continue
            if by.startswith(_OVERRIDE_PREFIX):
                report.note(d.name, f"{p.name} 은(는) 이름과 내용이 어긋나 내용을 따랐다 — "
                                    f"{by[len(_OVERRIDE_PREFIX):-1]}", "warn")
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
                         only=only, dry_run=args.dry_run, force=args.force,
                         quiet=bool(getattr(args, "quiet", False)))
    else:
        _run_refresh(subject, space, report, only=only, dry_run=args.dry_run,
                     quiet=bool(getattr(args, "quiet", False)))

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
