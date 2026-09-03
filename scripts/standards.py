# -*- coding: utf-8 -*-
"""`gw standards` — 교육과정 PDF에서 성취기준을 뽑고, 키워드 사전 초안을 만든다.

왜 이 모듈에는 과목 이름이 없는가
--------------------------------
성취기준 코드 `[12지과Ⅱ01-01]` 은 그 자체가 (학년군, 과목, 영역, 순번) 을 담고 있다.
그래서 "지구과학Ⅱ 문서를 파싱한다"가 아니라 **"문서에서 코드를 전부 줍고, 코드가 말하는 대로
과목·단원으로 접는다"** 로 방향을 뒤집었다. 그러면 새 과목이 들어와도 코드만 있으면 그냥 나온다.
실제로 이 모듈은 국어·수학·제2외국어까지 한 번에 뽑는다 — 코드를 못 읽는 과목이 없기 때문이다.
docs/CONTRACT.md 0절.

문서 서식(profile)은 `DOC_PROFILES` 로 분기한다. 지금은 국가교육과정 별책 서식 두 가지
(2015 개정 / 2022 개정) 만 있지만, 서식이 다른 문서가 들어올 자리를 테이블로 열어 두었다.
crop/extract 의 `subject.layout` 과 같은 역할이다.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from common import CURRICULUM_PDF, CURRICULUM_STANDARDS, Report, Space, SUBJECTS, load_subject

# ---------------------------------------------------------------------------
# 0. 문자 정규화
# ---------------------------------------------------------------------------
# 2015 별책 PDF 는 어절 사이를 U+0001 로 채운다. 그대로 두면 "지구\x01내부" 가 한 토큰이 되어
# 명사구 추출이 통째로 망가진다. 처음에 이걸 몰라서 2015 키워드만 빈 배열이 나왔다.
_INVISIBLE = dict.fromkeys(map(ord, "\x01\x02\x03​﻿"), " ")
# 가운뎃점 계열. 문서마다 제각각이라 한 글자로 모은 뒤 '글머리표인가'를 판단한다.
_BULLETS = "·⋅∙･‧•◦▪◾※"
_BULLET_MAP = str.maketrans({c: "·" for c in _BULLETS})


def norm_text(s: str) -> str:
    """PDF 원문 한 줄을 비교·추출 가능한 형태로 고른다."""
    s = s.translate(_INVISIBLE).translate(_BULLET_MAP)
    s = s.replace(" ", " ").replace("　", " ")
    s = unicodedata.normalize("NFC", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# 1. 성취기준 코드
# ---------------------------------------------------------------------------
# 계약서에 적힌 정규식 \[(1[0-2][가-힣]{2,4}\d{2}-\d{2})\] 로는 실제 문서의 절반을 놓친다.
# 실측한 반례:
#   [12지과Ⅱ01-01]  2015 과학과는 로마숫자 Ⅰ/Ⅱ 를 쓴다 — [가-힣] 에 안 걸린다.
#   [10공국1-01-01]  2022 공통과목은 과목 뒤에 아라비아 숫자 + 하이픈이 붙는다.
#   [12미적Ⅰ-01-01]  둘이 겹치는 경우.
#   [12정01-01]      과목 약칭이 한 글자인 경우(정보).
# 그래서 "대괄호 안, 두 자리 학년군 + 임의의 과목 약칭 + 두 자리 영역 - 두 자리 순번" 으로 넓혔다.
CODE_RE = re.compile(r"\[\s*(\d{2})([^\[\]\n]{1,12}?)(\d{2})\s*-\s*(\d{2})\s*\]")


@dataclass(frozen=True)
class Code:
    raw: str        # 12지과Ⅱ01-01
    prefix: str     # 12지과Ⅱ   (raw[:-5]. '10공국1-01-01' 이면 '10공국1-')
    unit: int       # 1
    seq: int        # 1

    @staticmethod
    def parse(m: re.Match) -> "Code":
        grade, abbr, unit, seq = m.group(1), m.group(2), m.group(3), m.group(4)
        raw = f"{grade}{abbr}{unit}-{seq}"
        return Code(raw=raw, prefix=raw[:-5], unit=int(unit), seq=int(seq))


def find_code_at_start(line: str) -> Code | None:
    """줄 맨 앞의 코드만 인정한다.

    성취기준 본문은 `[12지구01-01] …` 로 줄이 시작하고, 성취기준 해설은 `· [12지구01-01] …`
    처럼 글머리표가 앞에 붙는다. 이 한 글자 차이가 본문과 해설을 가르는 유일하고 안정적인 신호다.
    (실측: 7개 별책 전부에서 성취기준의 99.7% 가 줄머리에 코드를 둔다.)
    """
    m = CODE_RE.match(line)
    return Code.parse(m) if m else None


# ---------------------------------------------------------------------------
# 2. 문서 서식 프로파일
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DocProfile:
    """교육과정 문서 한 종류의 서식. 새 서식은 여기에 한 줄 추가하면 된다."""
    name: str
    revision: str
    section_head: re.Pattern      # 과목 절의 시작을 알리는 제목
    standards_anchor: re.Pattern  # 이 줄 뒤부터가 성취기준 구역


# 성취기준 구역의 시작. '나. 성취기준' 이 표준이지만 문서에 따라 번호가 흔들려서 넉넉히 잡는다.
_ANCHOR = re.compile(r"^[가-힣]\s*[.．]\s*성취기준\s*$|^\d\s*[.．]\s*내용\s*체계\s*및\s*성취기준")

DOC_PROFILES: dict[str, DocProfile] = {
    "kice-2015": DocProfile(
        name="kice-2015",
        revision="2015",
        # 2015 별책은 '1. 성격' 과 '2. 목표' 가 따로 있다.
        section_head=re.compile(r"^1\s*[.．]\s*성격\s*$"),
        standards_anchor=_ANCHOR,
    ),
    "kice-2022": DocProfile(
        name="kice-2022",
        revision="2022",
        # 2022 별책은 '1. 성격 및 목표' 로 합쳐졌다.
        section_head=re.compile(r"^1\s*[.．]\s*성격\s*및\s*목표\s*$"),
        standards_anchor=_ANCHOR,
    ),
}

def detect_profile(lines: list["Line"]) -> DocProfile | None:
    """문서의 **구조**로 서식을 판정한다.

    처음에는 고시 번호(제2015-74호 / 제2022-33호)로 판정했다가 별책4 통합본에서 틀렸다.
    별책4 부칙에는 2015~2024년 고시가 전부 나열돼 있어서 어느 번호를 고르든 근거가 없다.
    반면 '1. 성격'(2015) 과 '1. 성격 및 목표'(2022) 는 서식 그 자체라 섞일 수 없다.
    문서가 스스로 증명하게 두는 편이 언제나 낫다.
    """
    scores = {name: sum(1 for ln in lines if p.section_head.match(ln.text))
              for name, p in DOC_PROFILES.items()}
    best = max(scores, key=lambda k: scores[k])
    return DOC_PROFILES[best] if scores[best] else None


# ---------------------------------------------------------------------------
# 3. 교과 → 수능 영역
# ---------------------------------------------------------------------------
# 과목이 아니라 **교과**로만 분기한다. 과목이 늘어도 이 표는 안 늘어난다.
# 도덕과가 사회탐구인 이유: 수능 응시 영역 기준으로 '생활과 윤리'·'윤리와 사상'이 사회탐구다.
AREA_BY_DEPARTMENT = {
    "국어": "국어",
    "수학": "수학",
    "영어": "영어",
    "사회": "사회탐구",
    "도덕": "사회탐구",
    "과학": "과학탐구",
    "한문": "제2외국어/한문",
    "제2외국어": "제2외국어/한문",
}
_DEPT_RE = re.compile(r"^(.{1,12}?)과(?:\s*교육과정)?$")


def department_of(header: str) -> str | None:
    """머리글 '과학과 교육과정' / '기술⋅가정과' → '과학' / '기술·가정'."""
    m = _DEPT_RE.match(header.strip())
    if not m:
        return None
    dept = m.group(1).strip()
    # '고등학교 교육과정' 같은 문서 제목이 새어 들어오지 않게 최소한의 방어.
    if not dept or dept.endswith("교육") or "학교" in dept:
        return None
    return dept


def area_of(dept: str | None, fallback: str) -> str:
    if not dept:
        return fallback
    return AREA_BY_DEPARTMENT.get(dept, dept + "과")


# ---------------------------------------------------------------------------
# 4. PDF → 줄 스트림
# ---------------------------------------------------------------------------
@dataclass
class Line:
    page: int      # 1-based PDF 쪽번호 (인쇄 쪽번호가 아니다 — 원문을 다시 찾아갈 때 쓰는 값)
    y: float
    text: str
    chrome: bool = False  # 머리글·꼬리글·쪽번호
    wrapped: bool = False  # 낱말 한가운데서 줄이 바뀌었다


def read_lines(doc) -> list[Line]:
    """PDF 전체를 (쪽, y, 텍스트) 줄 목록으로 편다.

    `wrapped` 가 이 함수의 핵심이다. 한국어 조판은 낱말 한가운데서도 줄을 바꾸는데
    ('… 지권, 수권, 기' / '권이 변화해 왔음을 …'), 줄을 공백으로 이으면 '기 권이' 라는
    없는 낱말이 생긴다. 다행히 조판기는 어절 경계에서 끊을 때만 줄 끝에 공백을 남긴다.
    그래서 **원문 줄이 공백으로 끝나지 않으면 낱말 중간**으로 보고 공백 없이 잇는다.
    (별책9 2022 p.238 [12지시01-02] 에서 처음 발견한 문제다.)
    """
    out: list[Line] = []
    for i in range(doc.page_count):
        page = doc[i]
        h = page.rect.height or 1.0
        for block in page.get_text("dict")["blocks"]:
            if block.get("type", 0) != 0:
                continue
            for ln in block.get("lines", []):
                raw = "".join(s["text"] for s in ln["spans"])
                t = norm_text(raw)
                if t:
                    # \x01 도 공백으로 친다. 2015 별책은 어절 사이를 이 글자로 채우는데,
                    # 줄 끝에도 그대로 남아서 '설명할\x01' + '있다.' → '설명할있다' 가 됐었다.
                    out.append(Line(page=i + 1, y=ln["bbox"][1] / h, text=t,
                                    wrapped=not raw.rstrip("\n").endswith(
                                        (" ", " ", "　", "\x01", "\x02", "\x03"))))
    return out


def join_lines(chunks: list[tuple[str, bool]]) -> str:
    """(텍스트, 이 줄이 낱말 중간에서 끊겼는가) 조각들을 원문 그대로 잇는다."""
    buf, prev_wrapped = "", False
    for text, wrapped in chunks:
        if buf:
            buf += "" if prev_wrapped else " "
        buf += text
        prev_wrapped = wrapped
    return norm_text(buf)


_PAGENO_RE = re.compile(r"^[\divxlcIVXLC]{1,5}$")


_BUCKETS = 50  # 쪽 높이를 50칸으로 나눈다 = 754pt 판형에서 한 칸 15pt, 본문 한 줄보다 크다


def _bucket(y: float) -> int:
    return round(y * _BUCKETS)


def mark_chrome(lines: list[Line], page_count: int) -> None:
    """머리글·꼬리글·쪽번호에 표시를 단다.

    두 번 실패하고 나온 규칙이다.
    (1) 고정 비율 여백으로 자르기 → 실패. 별책 판형이 595x841 과 556x754 로 섞여 있어서
        한쪽에 맞추면 다른 쪽 본문 첫 줄('나. 성취기준')을 먹는다.
    (2) '여러 쪽에 반복되는 문구' → 실패. 별책4 통합본은 교과마다 머리글이 달라서
        '국어과' 는 2215쪽 중 69번밖에 안 나온다. 반복 횟수 문턱을 문서 크기로 잡으면 놓친다.
    지금 규칙: **반복 문구·쪽번호가 모이는 y 칸을 찾아, 그중 가장 위 칸과 가장 아래 칸만 장식으로 본다.**
    머리글이 몇 종류든 세로 위치는 하나라는 조판의 성질을 쓴다.
    """
    from collections import Counter, defaultdict

    repeat = Counter((ln.text, _bucket(ln.y)) for ln in lines)
    pages_at = defaultdict(set)
    for ln in lines:
        if _PAGENO_RE.match(ln.text) or repeat[(ln.text, _bucket(ln.y))] >= 5:
            pages_at[_bucket(ln.y)].add(ln.page)
    need = max(5, page_count * 0.25)
    hot = {b for b, ps in pages_at.items() if len(ps) >= need}
    top = min((b for b in hot if b <= _BUCKETS * 0.30), default=None)
    bottom = max((b for b in hot if b >= _BUCKETS * 0.70), default=None)
    for ln in lines:
        b = _bucket(ln.y)
        if b in (top, bottom) and (_PAGENO_RE.match(ln.text) or repeat[(ln.text, b)] >= 5):
            ln.chrome = True


def running_headers(lines: list[Line]) -> dict[int, str]:
    """쪽 → 그 쪽의 머리글(장식으로 표시된 위쪽 줄 중 첫 번째)."""
    out: dict[int, str] = {}
    for ln in lines:
        if ln.chrome and ln.y < 0.15 and not _PAGENO_RE.match(ln.text):
            out.setdefault(ln.page, ln.text)
    return out


def department_by_page(headers: dict[int, str], page_count: int) -> dict[int, str]:
    """쪽 → 교과. 머리글이 없는 쪽은 앞 쪽의 교과를 물려받는다.

    별책은 홀·짝수 쪽 머리글이 다르고(한쪽은 '과학과 교육과정', 다른 쪽은 '선택 중심 교육과정 …')
    아예 머리글이 없는 쪽도 있다. 앞으로 물려주기만 하면 별책4 통합본처럼 교과가 여러 개
    이어지는 문서에서도 경계가 정확히 잡힌다.
    """
    out: dict[int, str] = {}
    cur: str | None = None
    for p in range(1, page_count + 1):
        dept = department_of(headers.get(p, ""))
        if dept:
            cur = dept
        if cur:
            out[p] = cur
    # 첫 교과가 나오기 전 쪽들은 문서에서 가장 많이 나온 교과로 채운다(표지·목차 구간).
    if out:
        first = out[min(out)]
        for p in range(1, min(out)):
            out[p] = first
    return out


# ---------------------------------------------------------------------------
# 5. 과목 절 나누기
# ---------------------------------------------------------------------------
# 과목명 줄에서 걷어낼 군더더기: 2015 사회과는 '사회<개정2018. 7. 27.>' 처럼 개정 표기가 붙는다.
_TITLE_TRASH = re.compile(r"\s*<[^>]*>\s*$")
# 제목이 아닌 줄: 문서 제목, 번호 매김, 그림 설명, 문장(마침표로 끝난다).
# '독서 토론과 글쓰기' 같은 진짜 과목명이 '…기' 로 끝나므로 어미로 거르면 안 된다 — 마침표로만 거른다.
_TITLE_BAD = re.compile(r"교육과정|^\d|^[가-힣]\s*[.．]|^\(|^\[|[.．]\s*$")


@dataclass
class Section:
    title: str
    start: int
    end: int
    page: int
    dept: str | None


def clean_title(s: str) -> str:
    return _TITLE_TRASH.sub("", s).strip().strip("’‘“”\"'")


def find_sections(lines: list[Line], profile: DocProfile,
                  depts: dict[int, str], headers: dict[int, str]) -> list[Section]:
    """`1. 성격(및 목표)` 바로 앞의 짧은 줄이 과목명이다.

    7개 별책 전부에서 이 규칙이 통했다. 쪽번호·머리글이 과목명과 제목 사이에 끼어 있어서
    네 줄까지 거슬러 올라가며 후보를 고른다. 거르는 것: 쪽번호, 머리글과 같은 문구,
    '…과 교육과정' 류 문서 제목, 번호 매김으로 시작하는 줄, 문장(어미로 끝나는 줄).
    """
    starts: list[Section] = []
    for i, ln in enumerate(lines):
        if not profile.section_head.match(ln.text):
            continue
        title = ""
        for back in (1, 2, 3, 4):
            if i - back < 0:
                break
            prev = lines[i - back]
            # 머리글은 건너뛴다. 2015 별책9 는 **머리글에도 과목명을 찍기 때문에**
            # 문구 비교로 거르면 진짜 제목('화학Ⅰ')까지 같이 날아간다. 위치로 거른다.
            if prev.chrome:
                continue
            cand = clean_title(prev.text)
            if not cand or len(cand) > 30:
                continue
            if _PAGENO_RE.match(cand) or _TITLE_BAD.search(cand):
                continue
            title = cand
            break
        # 제목을 못 찾는 경우가 실제로 있다: 별책4 교양과는 과목명을 **그림으로 인쇄**했다
        # (p.2052 등, 제목 자리에 이미지 블록만 있다). 텍스트 레이어가 없으니 OCR 없이는 못 읽는다.
        # 이런 절도 성취기준은 멀쩡히 나오므로 버리지 않고 자리 표시만 남긴다.
        starts.append(Section(title=title or f"(미상 p.{ln.page})", start=i,
                              end=len(lines), page=ln.page, dept=depts.get(ln.page)))
    for a, b in zip(starts, starts[1:]):
        a.end = b.start
    return starts


# ---------------------------------------------------------------------------
# 6. 단원(영역) 제목
# ---------------------------------------------------------------------------
_UNIT_RE = re.compile(r"^\((\d{1,2})\)\s*(.+)$")
# 문장의 끝. 성취기준 본문을 어디서 끊을지 판단할 때 쓴다.
_SENTENCE_TAIL = re.compile(r"(다|요|음|함|기)\s*[.．]?\s*$")
# 단원 제목이 **아닌** 줄. 어미로 거르면 안 된다 — 실제 단원명이 '듣기·말하기', '읽기',
# '화학의 첫걸음' 처럼 '기/음' 으로 끝난다. 이걸 걸러 버려서 국어·제2외국어·화학Ⅰ의
# 단원 제목이 통째로 비었었다. 문장 부호와 종결어미만 본다.
_NOT_A_UNIT_TITLE = re.compile(r"[.．]\s*$|(한다|있다|없다|된다|이다|하자)\s*$")


def collect_unit_titles(lines: list[Line], lo: int, hi: int) -> dict[int, str]:
    """성취기준 구역에서 `(N) 제목` 을 줍는다. 같은 번호는 **먼저 나온 것**이 단원 제목이다.

    문서 뒤쪽 '교수·학습 및 평가' 절에도 `(1) 교수·학습의 방향` 같은 번호 매김이 있는데,
    성취기준 구역이 항상 앞이므로 선착순 규칙만으로 갈린다.
    """
    titles: dict[int, str] = {}
    for ln in lines[lo:hi]:
        if ln.chrome:
            continue
        m = _UNIT_RE.match(ln.text)
        if not m:
            continue
        no, title = int(m.group(1)), m.group(2).strip()
        if no in titles or len(title) > 40 or _NOT_A_UNIT_TITLE.search(title):
            continue
        titles[no] = title
    return titles


# ---------------------------------------------------------------------------
# 7. 성취기준 본문 모으기
# ---------------------------------------------------------------------------
_STOP_LINE = re.compile(r"^(<[^>]{1,20}>|\([가-힣]\)|[가-힣]\s*[.．]\s|\d\s*[.．]\s|·)")
# '4. 교수·학습 및 평가' 절. 여기부터는 (1)(2) 번호가 단원이 아니라 '교수·학습의 방향'이라
# 통제 어휘를 걷을 때 이 줄에서 멈춘다.
_TEACHING_RE = re.compile(r"^\d\s*[.．]\s*교수")
_TEXT_MAX_LINES = 8


def collect_standards(lines: list[Line], lo: int, hi: int) -> list[dict]:
    """줄머리 코드로 시작해 '…다.' 로 끝날 때까지 이어 붙인다.

    쪽이 넘어가면 내용 체계표 조각이나 머리글이 문장 한가운데로 끼어든다(2015 별책9 p.217 실측).
    그래서 (1) 장식 줄은 건너뛰고 (2) 새 코드/새 항목 머리를 만나면 끊고 (3) '다.' 로 끝나면 끊는다.
    세 조건 중 하나도 안 걸리고 8줄을 넘기면 파싱이 샌 것으로 보고 거기서 자른다.
    """
    out: list[dict] = []
    seen: set[str] = set()
    i = lo
    while i < hi:
        ln = lines[i]
        code = None if ln.chrome else find_code_at_start(ln.text)
        if code is None or code.raw in seen:
            i += 1
            continue
        seen.add(code.raw)
        body = [(CODE_RE.sub("", ln.text, count=1).strip(), ln.wrapped)]
        page = ln.page
        j = i + 1
        while j < hi and len(body) < _TEXT_MAX_LINES:
            if body[-1][0].endswith(".") and _SENTENCE_TAIL.search(body[-1][0]):
                break
            nxt = lines[j]
            if nxt.chrome:
                j += 1
                continue
            if find_code_at_start(nxt.text) or _STOP_LINE.match(nxt.text):
                break
            body.append((nxt.text, nxt.wrapped))
            j += 1
        text = join_lines(body)
        out.append({"code": code.raw, "prefix": code.prefix, "unit": code.unit,
                    "seq": code.seq, "text": text, "page": page})
        i = j
    return out


def collect_commentary(lines: list[Line], lo: int, hi: int) -> dict[str, str]:
    """성취기준 해설(`· [12지구01-01] …`)을 코드별로 모은다.

    키워드 초안의 재료로 쓴다. 해설에는 본문이 줄인 개념어가 풀어져 있어서
    ('중위도 저기압', 'T-S 다이어그램') 초안 품질이 눈에 띄게 올라간다.
    범위 표기 `[12지구01-01∼02]` 는 코드로 안 잡히므로 앞뒤 코드에 나눠 붙이지 않고 버린다.
    """
    out: dict[str, list[tuple[str, bool]]] = {}
    cur: str | None = None
    for ln in lines[lo:hi]:
        if ln.chrome:
            continue
        t = ln.text
        if t.startswith("·"):
            body = t[1:].strip()
            m = CODE_RE.match(body)
            if m:
                cur = Code.parse(m).raw
                out.setdefault(cur, []).append(
                    (CODE_RE.sub("", body, count=1).strip(), ln.wrapped))
                continue
            cur = None
        elif cur:
            out[cur].append((t, ln.wrapped))
    return {k: join_lines(v) for k, v in out.items()}


_LEARNING_HEAD = re.compile(r"^\([가-힣]\)\s*학습\s*요소")
_SUBHEAD = re.compile(r"^\([가-힣]\)|^<")


# 내용 체계표의 행 이름. '지식·이해' 행만 개념어이고 나머지 둘은 어느 과목이나 똑같은 문구다.
_NON_CONTENT_ROW = re.compile(r"^(과정·기능|가치·태도|기능|구분|범주|내용 요소|핵심 아이디어)$")


def collect_vocabulary(lines: list[Line], lo: int, anchor: int, hi: int,
                       titles: dict[int, str]) -> tuple[dict[int, list[str]],
                                                        dict[int, list[str]]]:
    """내용 체계표의 '내용 요소' 와 2015 의 '학습 요소' 를 단원별 통제 어휘로 걷는다.

    사람이 만든 단원 체계(CSAT_WIKI/wiki_earth2/taxonomies/units.json)와 대조해 보면
    그 안의 개념어는 사실상 전부 이 두 곳에서 나온다. 규칙 기반 명사구 추출보다 훨씬 정확하니
    **먼저 이 어휘를 쓰고, 모자란 만큼만 문장에서 뽑는다.**

    돌려주는 값은 두 벌이다.
    - pools : 단원별 어휘 전체. 키 0 은 단원이 정해지기 전 구간 = 내용 체계표(과목 전체).
              **본문에 실제로 나온 낱말을 고르는 용도로만** 쓴다.
    - learning : 2015 의 `(가) 학습 요소` 블록만 따로 걷은 것. 단원 소속이 확실하다.
              성취기준 본문에 안 나오는 낱말까지 단원 전체에 뿌릴 때 이것만 쓴다.

    둘을 나눈 이유: 2015 별책9 는 내용 체계표가 쪽을 넘어가면서 **다른 단원의 표 조각이
    (1) 단원 블록 한가운데에 끼어든다**(p.217). 그대로 뿌렸더니 '지구의 형성과 역장' 성취기준에
    '케플러의 세 가지 법칙' 이 붙었다. 학습 요소 블록은 경계가 분명해서 이런 오염이 없다.
    """
    pools: dict[int, list[str]] = {0: []}
    learning: dict[int, list[str]] = {}
    cur, buf, owner = 0, [], 0
    label: list[str] = []          # 내용 체계표에서 방금 지나온 '범주'(=영역) 이름 조각
    flat_titles = {re.sub(r"\s+", "", t): n for n, t in titles.items() if t}

    def unit_for_label() -> int | None:
        """내용 체계표의 범주 이름을 단원 번호로 되짚는다.

        2022 서식은 '학습 요소' 가 없어서 단원별 통제 어휘를 얻을 데가 내용 체계표뿐이다.
        다행히 표의 범주 이름이 성취기준의 단원 제목과 같은 말이라 이름으로 이을 수 있다.
        (예: 범주 '대기와 해양의 상호작용' ↔ 단원 '(1) 대기와 해양의 상호작용')
        """
        if not label or _NON_CONTENT_ROW.match(label[-1]):
            return None
        key = re.sub(r"\s+", "", " ".join(label))
        if len(key) < 4:
            return None
        # 표를 세로로 읽으면 앞 행의 범주 이름이 버퍼에 남아 있다. **가장 나중에 나온** 이름이
        # 지금 줄의 주인이다. 먼저 찾은 것을 쓰면 '해수의 운동' 어휘가 앞 단원으로 붙는다.
        best, best_pos = None, -1
        for title, no in flat_titles.items():
            if len(title) < 4:
                continue
            pos = key.rfind(title)
            if pos > best_pos:
                best, best_pos = no, pos
        return best

    def flush_learning():
        # 학습 요소는 쉼표로 이어진 긴 목록이라 줄을 넘긴다. 줄 단위로 자르면
        # '실체파와 표면' / '파, 주시 곡선' 처럼 낱말이 두 동강 난다. 블록째 이어 붙인 뒤 쪼갠다.
        nonlocal buf
        if buf:
            joined = join_lines(buf).lstrip("· ")
            learning.setdefault(owner, []).extend(
                t.strip(" .·") for t in re.split(r"[,，、]", joined))
            buf = []

    for idx in range(lo, hi):
        ln = lines[idx]
        if ln.chrome:
            continue
        # 단원 번호 갈아타기는 **성취기준 구역 안에서만** 인정한다. 앞쪽 '나. 목표' 에도
        # (1)(2)(3)(4) 번호가 있어서, 그걸 단원으로 오해하면 내용 체계표 어휘가 통째로
        # 엉뚱한 단원(실측: 4번)으로 들어간다.
        m = _UNIT_RE.match(ln.text) if idx >= anchor else None
        if m and not _NOT_A_UNIT_TITLE.search(m.group(2)):
            flush_learning()
            cur = int(m.group(1))
            pools.setdefault(cur, [])
            continue
        if _LEARNING_HEAD.match(ln.text):
            flush_learning()
            owner = cur
            buf = [("", False)]      # 블록 시작 표시
            continue
        if buf and _SUBHEAD.match(ln.text):
            flush_learning()
        elif buf:
            buf.append((ln.text, ln.wrapped))
        # **글머리표로 시작하는 줄만** 본다. 이 조건을 안 걸었더니 '(다) 교수·학습 방법 및 유의 사항'
        # 같은 제목 줄이 가운뎃점에서 쪼개져 '학습 방법 및 유의 사항' 이 통제 어휘로 들어왔다.
        if not ln.text.startswith("·"):
            # 표 안에서는 범주 이름이 두세 줄로 쪼개져 온다('대기와 해양의' / '상호작용').
            label = (label + [ln.text])[-3:] if len(ln.text) <= 20 else []
            continue
        owner_unit = unit_for_label() if cur == 0 else None
        for chunk in ln.text.split("·"):
            for piece in re.split(r"[,，、]", chunk):
                t = piece.strip(" .·")
                if 2 <= len(t) <= 20:
                    pools.setdefault(cur, []).append(t)
                    if owner_unit is not None:
                        learning.setdefault(owner_unit, []).append(t)
    flush_learning()
    return pools, {k: [t for t in v if 2 <= len(t) <= 20] for k, v in learning.items()}


# ---------------------------------------------------------------------------
# 8. 한 문서 파싱
# ---------------------------------------------------------------------------
@dataclass
class ParsedSubject:
    name: str
    prefix: str
    area: str
    dept: str | None
    source_pdf: str
    units: dict[int, str] = field(default_factory=dict)
    standards: list[dict] = field(default_factory=list)
    commentary: dict[str, str] = field(default_factory=dict)
    vocabulary: dict[int, list[str]] = field(default_factory=dict)  # 0 = 과목 전체
    learning: dict[int, list[str]] = field(default_factory=dict)    # 2015 '학습 요소'


@dataclass
class ParsedDoc:
    path: Path
    profile: DocProfile
    subjects: list[ParsedSubject] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)


def parse_pdf(path: Path, area_fallback: str = "미분류") -> ParsedDoc:
    import fitz  # 지연 임포트 — 다른 명령은 PyMuPDF 없이도 떠야 한다.

    doc = fitz.open(path)
    try:
        lines = read_lines(doc)
        profile = detect_profile(lines)
        if profile is None:
            raise NotImplementedError(
                f"{path.name}: 과목 절 제목을 하나도 못 찾아 문서 서식을 판정할 수 없다.\n"
                f"       아는 서식: {', '.join(DOC_PROFILES)}\n"
                f"       새 서식이면 scripts/standards.py 의 DOC_PROFILES 에 프로파일을 한 줄 추가한다."
            )
        mark_chrome(lines, doc.page_count)
        headers = running_headers(lines)
        depts = department_by_page(headers, doc.page_count)
        sections = find_sections(lines, profile, depts, headers)
        out = ParsedDoc(path=path, profile=profile)
        for sec in sections:
            anchor = sec.start
            for k in range(sec.start, sec.end):
                if profile.standards_anchor.match(lines[k].text):
                    anchor = k
                    break
            stds = collect_standards(lines, anchor, sec.end)
            if not stds:
                continue
            titles = collect_unit_titles(lines, anchor, sec.end)
            comm = collect_commentary(lines, anchor, sec.end)
            teach = next((k for k in range(anchor, sec.end)
                          if _TEACHING_RE.match(lines[k].text)), sec.end)
            vocab, learning = collect_vocabulary(lines, sec.start, anchor, teach, titles)
            area = area_of(sec.dept, area_fallback)
            # 한 절에 과목이 둘 붙어 있는 경우('통합과학1, 통합과학2', '한국사1, 한국사2').
            groups: dict[str, list[dict]] = {}
            for s in stds:
                groups.setdefault(s["prefix"], []).append(s)
            names = [n.strip() for n in sec.title.split(",")] if "," in sec.title else [sec.title]
            if len(names) != len(groups):
                names = [sec.title] * len(groups)
                if len(groups) > 1:
                    out.problems.append(
                        (sec.title, f"한 절에서 코드 접두사 {len(groups)}개가 나왔다 — 과목명을 확인해라"))
            for name, (prefix, items) in zip(names, sorted(groups.items())):
                if len(groups) > 1 and names.count(name) > 1:
                    name = f"{name}({prefix})"
                out.subjects.append(ParsedSubject(
                    name=name, prefix=prefix, area=area, dept=sec.dept, source_pdf=path.name,
                    units={n: t for n, t in titles.items()
                           if n in {s["unit"] for s in items}},
                    standards=items, commentary=comm,
                    vocabulary=vocab, learning=learning))
        return out
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# 9. 저장 스키마
# ---------------------------------------------------------------------------
def to_schema(revision: str, docs: list[ParsedDoc],
              order: list[str]) -> tuple[dict, list[tuple[str, str]]]:
    """개정연도 하나치를 `curriculum/standards/<revision>.json` 모양으로 접는다.

    같은 과목이 여러 별책에 겹쳐 나오면(별책4 통합본 ↔ 개별 별책) **개별 별책을 신뢰한다.**
    별책4는 2215쪽짜리 통합본이라 표가 더 자주 본문에 끼어들어 문장이 깨진다 — 실측으로 확인했다.
    """
    rank = {name: i for i, name in enumerate(order)}
    notes: list[tuple[str, str]] = []
    chosen: dict[str, tuple[int, ParsedSubject]] = {}
    for doc in docs:
        r = rank.get(doc.path.name, len(order))
        for ps in doc.subjects:
            prev = chosen.get(ps.prefix)
            if prev is None or r < prev[0]:
                if prev is not None:
                    notes.append((ps.prefix,
                                  f"{prev[1].source_pdf} 대신 {ps.source_pdf} 를 채택"))
                chosen[ps.prefix] = (r, ps)

    subjects: dict[str, dict] = {}
    for _r, ps in sorted(chosen.values(), key=lambda x: (x[1].area, x[1].prefix)):
        by_unit: dict[int, list[dict]] = {}
        for s in sorted(ps.standards, key=lambda s: (s["unit"], s["seq"])):
            by_unit.setdefault(s["unit"], []).append(
                {"code": s["code"], "text": s["text"], "page": s["page"]})
        name = ps.name
        if name in subjects:                      # 이름이 겹치면 접두사로 구분한다
            name = f"{ps.name}({ps.prefix})"
        subjects[name] = {
            "code_prefix": ps.prefix,
            "area": ps.area,
            "department": ps.dept,
            "source_pdf": ps.source_pdf,
            "units": [{"no": n, "title": ps.units.get(n, ""), "standards": v}
                      for n, v in sorted(by_unit.items())],
        }
    payload = {
        "revision": revision,
        "sources": [{"pdf": d.path.name, "profile": d.profile.name,
                     "subjects": len(d.subjects),
                     "standards": sum(len(s.standards) for s in d.subjects)}
                    for d in docs],
        "subjects": subjects,
    }
    return payload, notes


# ---------------------------------------------------------------------------
# 10. 키워드 초안 — 규칙 기반 명사구 추출
# ---------------------------------------------------------------------------
# 형태소 분석기를 쓰지 않는다(설치 부담). 대신 한국어의 두 가지 성질만 이용한다.
#   (1) 조사는 어절 끝에 붙는다 → 긴 것부터 벗겨 내면 명사가 남는다.
#   (2) 전문 용어는 띄어쓴 명사 연쇄다 → 연속한 명사를 이어 붙이면 그게 용어다.
# 관형격 '의' 만은 벗기지 않고 **이어 붙인다** — '한반도의 지사', '암석의 조직' 처럼
# 사람이 만든 단원 어휘가 실제로 '의' 를 포함하기 때문이다.
PARTICLES = tuple(sorted((
    "으로부터", "로부터", "에서의", "에게서", "이라는", "으로써", "으로서", "에서는", "에서도",
    "이라고", "등으로", "등에서", "라는", "로써", "로서", "에서", "에게", "까지", "부터",
    "보다", "처럼", "마다", "만큼", "이나", "으로", "와의", "과의", "에는", "에도", "등의",
    "등을", "등이", "들의", "들을", "들이", "등과", "등에", "이며",
    "를", "을", "이", "가", "은", "는", "과", "와", "에", "로", "나", "등", "들",
), key=len, reverse=True))
# 목록에서 뺀 조사와 그 이유 — 전부 실측으로 걸러낸 것들이다.
#   '도' : 한반도·위도·경도·밀도·온도·습도·속도·고도·광도·일기도가 '한반'·'위'·'경' 으로 잘린다.
#   '만' : 에크만 수송이 '에크' 가 된다.
#   '의' : 관형격이라 벗기지 않고 이어 붙인다(strip_particle 참고).

# 용언(동사·형용사)의 활용형. 조사를 벗기기 **전에** 원형 어절에 대고 판정한다.
# 순서를 반대로 했다가 '형성되는' → 조사 '는' 을 벗겨 '형성되' 라는 유령 명사가 키워드에 올라왔다.
# 앞부분은 널리 쓰이는 용언 어간, 뒷부분은 어미. 형태소 분석기 없이 잡을 수 있는 최대치다.
_VERB_STEM = ("하", "되", "지", "이", "기", "리", "우", "추", "히", "시키", "나", "가", "오", "보",
              "주", "받", "내", "들", "쓰", "맺", "얻", "알", "있", "없", "삼", "짓", "다루", "따르",
              "이르", "미치", "나타나", "만들", "생기", "이루", "구하", "넣", "놓", "떨어지")
# '기' 를 어미 목록에서 뺀 이유: 국어과의 '듣기·말하기·읽기·쓰기' 와 '주기·대기·초기' 가
# 전부 날아간다. 명사형 어미 '기' 는 이득보다 손해가 크다.
_VERB_END = ("어서", "아서", "여서", "으며", "면서", "지만", "는", "은", "을", "여", "어", "아",
             "고", "며", "면", "서", "지", "게", "도록", "므로", "니", "다", "자", "라", "던", "든")
VERB_FORM = re.compile(
    "(?:" + "|".join(sorted(_VERB_STEM, key=len, reverse=True)) + ")"
    "(?:" + "|".join(sorted(_VERB_END, key=len, reverse=True)) + ")$")

# 서술어·평가 동사·기능어. 이게 키워드에 섞이면 분류기가 모든 성취기준에 반응한다.
STOPWORDS = {
    "이해", "이해한다", "설명", "설명한다", "탐구", "탐구한다", "조사", "발표", "토의", "토론",
    "분석", "해석", "추론", "비교", "구분", "파악", "제시", "예측", "활용", "적용", "표현",
    "인식", "판단", "평가", "확인", "도출", "논증", "서술", "계산", "측정", "관찰", "실험",
    "설계", "수행", "실천", "참여", "제안", "탐색", "선택", "결정", "정리", "요약", "발견",
    "학생", "교사", "수업", "학습", "교수", "단원", "과목", "교육", "과정", "성취기준",
    "자료", "방법", "과정", "결과", "내용", "사례", "예시", "경우", "관계", "영향", "특징",
    "특성", "종류", "차이", "변화", "문제", "능력", "태도", "가치", "의미", "중요성", "필요성",
    "이용", "사용", "다양", "다양한", "여러", "주요", "기본", "실제", "우리", "현재", "미래",
    "과거", "최근", "관련", "특정", "각각", "일부", "전체", "부분", "모습", "정도", "수준",
    "때문", "통해", "대해", "위해", "따라", "의해", "관해", "인해",
    "그것", "이것", "하나", "여기", "거기", "동안",
    "생활", "일상", "중심", "바탕", "기초",
    # 관형어. 뒤에 오는 명사를 꾸미기만 해서 단독으로는 아무 문항도 못 가른다.
    "물리적", "화학적", "정량적", "정성적", "구조적", "과학적", "직접적", "간접적", "대표적",
    "체계적", "종합적", "효과적", "구체적", "일반적", "상호적", "지속적",
    # 두 글자라 VERB_TAIL_LONG 에 안 걸리는 용언·지시어. 하나씩 실측으로 걸러 넣었다.
    "통한", "의한", "위한", "대한", "관한", "인한", "있음", "없음", "왔음", "했음", "안다",
    "이와", "그와", "이러한", "그러한", "어떻게", "이때", "그때", "이러", "그러",
}
# 교과명(국어·수학·과학…)은 AREA_BY_DEPARTMENT 표에서 그대로 가져온다.
# 손으로 적으면 그게 곧 과목 하드코딩이다 — docs/CONTRACT.md 0절.
STOPWORDS |= set(AREA_BY_DEPARTMENT)
# 어미·관형사로 끝나면 명사가 아니다. VERB_FORM 이 못 잡는 굳은 형태만 따로 적는다.
# 끝의 [를을은는가] 는 조사를 못 벗긴 짧은 어절('예를', '빛을')을 버리기 위한 것이다.
# '이/의' 는 일부러 뺐다 — '높이', '깊이', '길이' 가 전부 날아간다.
VERB_TAIL = re.compile(r"(함|할|된|함으로써|같은|다른|새로운|어떤|모든|여러|각|않는|아닌|"
                       r"어진|여진|워진|적인|적으로|[를을은는가])$")
# 세 글자 이상일 때만 어미로 보는 꼬리. '원인'·'요인'·'얼음' 같은 두 글자 명사를 지키려는 장치다.
# '다' 로 끝나는 세 글자 이상은 사실상 전부 용언이다('수립한다', '토론한다', '제시한다').
# 두 글자를 살려 둔 이유는 '바다' 하나 때문이다.
VERB_TAIL_LONG = re.compile(r"(한|음|인|임|짐|됨|림|다)$")
# 통제 어휘 중 과정·기능/가치·태도 행에서 온 것들. '…하기', '…기르기' 로 끝난다.
VOCAB_DROP = re.compile(r"(하기|되기|기르기|태도|역량|소양|윤리성|개방성|감수성|유용성|창의성|"
                        r"의사소통|문제해결|지식|이해|기능|범주|영역|사항|방법 및)$")
# 통제 어휘에 섞인 문장 조각. 표 칸이 줄바꿈되면서 잘려 들어온다('지구 역사를 통해 지').
# '와/과' 는 일부러 뺐다 — '판구조와 플룸', '천해파와 심해파', '조석과 기조력' 처럼
# 내용 요소 자체가 접속 조사를 품고 있어서, 넣으면 멀쩡한 통제 어휘가 4분의 1쯤 날아간다.
VOCAB_FRAGMENT = re.compile(r"(을|를|은|는|이|가|에|에서|으로|로)\s|"
                            r"\s(통해|위해|대해|따라|관해|같이|등)\b|\s\S$")
# 명사구는 구두점을 넘지 못한다. '지균풍, 경도풍, 지상풍의 발생 원리' 를 통째로 한 구로 묶어
# 버린 적이 있어서 **먼저 구두점으로 자르고** 그 안에서만 어절을 잇는다.
SEGMENT_SPLIT = re.compile(r"[,，、;；:：/·‧()（）\[\]{}<>《》「」『』“”‘’\"']+")
TOKEN_SPLIT = re.compile(r"\s+")
KEEP_CHAR = re.compile(r"^[가-힣A-Za-z0-9Ⅰ-Ⅹ\-−–ㆍ%°]+$")
MAX_KEYWORDS = 14


def strip_particle(tok: str) -> tuple[str, bool]:
    """(어간, 어절이 여기서 끊기는가). '의' 는 끊지 않고 이어 붙인다.

    '해수의 성질', '한반도의 지사' 처럼 사람이 만든 단원 어휘가 관형격 '의' 를 품고 있어서
    '의' 에서 끊으면 오히려 용어가 부서진다.
    """
    if tok.endswith("의") and len(tok) > 2:
        return tok, False
    for p in PARTICLES:
        if tok.endswith(p) and len(tok) - len(p) >= 2:
            return tok[: -len(p)], True
    return tok, False


def _is_noun(tok: str) -> bool:
    if len(tok) < 2 or not KEEP_CHAR.match(tok):
        return False
    if tok in STOPWORDS or VERB_TAIL.search(tok) or VERB_FORM.search(tok):
        return False
    if len(tok) >= 3 and VERB_TAIL_LONG.search(tok):
        return False
    # 조사를 붙인 채 남은 어절. strip_particle 이 어간이 한 글자라 못 벗긴 것들이라
    # ('합으로' → '합', '등으로' → '등') 명사로 쓸 수 없다.
    if any(tok.endswith(p) and 0 < len(tok) - len(p) < 2 for p in PARTICLES):
        return False
    return not tok.isdigit()


def extract_phrases(text: str) -> list[str]:
    """문장에서 명사구 후보를 뽑는다. 긴 구와 그 안의 단일 명사를 함께 낸다."""
    phrases: list[str] = []
    chain: list[str] = []

    def flush():
        if chain:
            if len(chain) > 1:
                phrases.append(" ".join(chain))
            for t in chain:
                t = t[:-1] if t.endswith("의") and len(t) > 2 else t
                if _is_noun(t):
                    phrases.append(t)
            chain.clear()

    for segment in SEGMENT_SPLIT.split(text):
        for raw in TOKEN_SPLIT.split(segment):
            raw = raw.strip(".…")
            # 용언 판정은 **조사를 벗기기 전**에 한다. 순서가 바뀌면 '형성되는' 이 '형성되' 가 된다.
            if not raw or VERB_FORM.search(raw):
                flush()
                continue
            tok, hard_break = strip_particle(raw)
            if not _is_noun(tok if not tok.endswith("의") else tok[:-1]):
                flush()
                continue
            chain.append(tok)
            if hard_break or len(chain) >= 4:
                flush()
        flush()
    # 등장 순서를 지키며 중복 제거
    seen, out = set(), []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def clean_vocabulary(pools: dict[int, list[str]]) -> dict[int, list[str]]:
    """내용 요소·학습 요소에서 걷은 낱말을 단원별 통제 어휘로 다듬는다."""
    out: dict[int, list[str]] = {}
    seen: set[str] = set()
    for unit in sorted(pools):
        keep: list[str] = []
        for t in pools[unit]:
            t = t.strip(" ·.")
            if len(t) < 2 or len(t) > 20 or t in seen:
                continue
            if t in STOPWORDS or VOCAB_DROP.search(t) or VERB_TAIL.search(t):
                continue
            if VOCAB_FRAGMENT.search(t) or not _is_noun(t.split()[-1]):
                continue
            if not KEEP_CHAR.match(t.replace(" ", "")):
                continue
            seen.add(t)
            keep.append(t)
        out[unit] = keep
    return out


# 이보다 적으면 같은 단원의 학습 요소로 메운다. 6/8/10 을 실측해서 8 을 골랐다:
# 사람이 정리한 내용 요소 재현율이 6→81%, 8→92%, 10→92% 였다. 8 이후로는 잡음만 는다.
# (대조 기준: CSAT_WIKI/curriculum/2015개정_지구과학2/units.md)
MIN_KEYWORDS = 8


def draft_for_standard(text: str, commentary: str, general: list[str],
                       unit_vocab: list[str], unit_learning: list[str]) -> list[str]:
    """한 성취기준의 키워드 초안. 정확한 것부터 쌓고, 모자라면 넓힌다.

    1) 통제 어휘(내용 요소·학습 요소) 중 본문/해설에 **실제로 나온 것** — 정확도가 가장 높다.
    2) 본문에서 규칙으로 뽑은 명사구 — 성취기준 고유의 표현을 살린다.
    3) 그래도 6개가 안 되면 같은 단원의 통제 어휘로 메운다. 성취기준 본문은 한 문장이라
       '미행성체' 처럼 교과서에서 실제로 다루는 말이 아예 안 나오는 일이 흔하다.
       빈 초안을 주느니 단원 수준 어휘라도 주는 편이 사람의 손질 비용이 낮다.

    해설은 어휘 매칭에만 쓰고 명사구 추출에는 쓰지 않는다. 해설 문장은 길고 교수·학습 지시가
    섞여 있어서 그대로 뽑으면 잡음이 절반을 넘는다(실측).
    """
    corpus = f"{text} {commentary}"
    picked: list[str] = [v for v in (general + unit_vocab) if v in corpus]
    picked.sort(key=len, reverse=True)
    for p in extract_phrases(text):
        if len(picked) >= MAX_KEYWORDS * 2:
            break
        if p not in picked:
            picked.append(p)

    def prune(cands: list[str]) -> list[str]:
        # 더 긴 키워드에 이미 포함된 짧은 조각은 버린다 — 초안을 사람이 읽을 수 있게 유지한다.
        out: list[str] = []
        for p in cands:
            if any(p != q and p in q for q in cands):
                continue
            if p not in out:
                out.append(p)
        return out

    result = prune(picked)[:MAX_KEYWORDS]
    if len(result) < MIN_KEYWORDS:
        for t in unit_learning:
            if len(result) >= MIN_KEYWORDS:
                break
            if t not in result and not any(t in r for r in result):
                result.append(t)
    return result


# ---------------------------------------------------------------------------
# 11. 명령
# ---------------------------------------------------------------------------
# 별책 우선순위. 개별 교과 별책이 통합본(별책4)보다 정확하다는 실측 결과를 담은 데이터다.
# 파일 이름이 다르면 그냥 뒤로 밀릴 뿐 동작은 한다.
SOURCE_PRIORITY = [
    "[별책6] 도덕과 교육과정.pdf",
    "[별책7] 사회과 교육과정.pdf",
    "별책7_사회과 교육과정(제2018-162호).pdf",
    "[별책9] 과학과 교육과정(2022).pdf",
    "[별책9] 과학과 교육과정(2015).pdf",
    "[별책20] 과학 계열 선택 과목 교육과정.pdf",
]
# 성취기준이 없는 문서. 열어 봐야 시간만 쓴다.
SKIP_HINT = re.compile(r"총론|해설서")


def register(parser) -> None:
    parser.add_argument("--revision", default="all", choices=["2015", "2022", "all"],
                        help="처리할 개정 연도 (기본 all)")
    parser.add_argument("--draft-keywords", action="store_true",
                        help="성취기준에서 keywords.json 초안을 만든다 (--subject 필요)")
    parser.add_argument("--subject", help="과목 슬러그 (--draft-keywords 에 필요)")
    parser.add_argument("--pdf", help="특정 PDF 만 처리 (파일명 일부)")
    parser.add_argument("--force", action="store_true", help="기존 결과를 덮어쓴다")
    parser.add_argument("--dry-run", action="store_true", help="쓰지 않고 결과만 센다")
    parser.add_argument("--quiet", action="store_true", help="요약 한 줄만 남긴다")


def _report(args) -> Report:
    """과목 무관 명령이라 리포트를 둘 곳이 계약에 없다.

    --subject 가 있으면 그 과목 작업 공간에, 없으면 workspace/_curriculum/ 에 남긴다.
    (반환값 contract_gaps 에 적어 두었다.)
    """
    slug = args.subject if getattr(args, "subject", None) else "_curriculum"
    return Report("standards", slug, Space(slug))


def _finish(rep: Report, quiet: bool) -> int:
    """--quiet 이면 리포트 경로 한 줄만 남긴다. 요약 출력은 Report.finish() 안에 있어서 삼킨다."""
    if not quiet:
        return rep.finish()
    import contextlib
    import io
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        code = rep.finish()
    print(rep.space.report(rep.step))
    return code


def run(args) -> int:
    rep = _report(args)
    quiet = bool(getattr(args, "quiet", False))
    try:
        if args.draft_keywords:
            return cmd_draft_keywords(args, rep, quiet)
        return cmd_build(args, rep, quiet)
    except NotImplementedError as exc:
        rep.note("profile", str(exc), "error")
        rep.next = "curriculum/pdf/ 의 문서 서식을 확인한다"
        return _finish(rep, quiet)
    except ImportError as exc:
        rep.note("deps", f"PyMuPDF 가 필요하다: {exc}", "error")
        rep.next = "pip install -r requirements.txt"
        return _finish(rep, quiet)
    except (FileNotFoundError, ValueError) as exc:
        # 과목 정의가 없거나 layout 이 이상한 경우. 역추적 대신 리포트를 남긴다 — 계약 5절.
        rep.note("subject", str(exc).splitlines()[0], "error")
        rep.next = "python scripts/gw.py subjects"
        return _finish(rep, quiet)


def cmd_build(args, rep: Report, quiet: bool = False) -> int:
    wanted = {"2015", "2022"} if args.revision == "all" else {args.revision}
    pdfs = sorted(p for p in CURRICULUM_PDF.glob("*.pdf")
                  if not SKIP_HINT.search(p.name)
                  and (not args.pdf or args.pdf.lower() in p.name.lower()))
    if not pdfs:
        rep.note("input", f"{CURRICULUM_PDF} 에 교육과정 PDF 가 없다", "error")
        rep.next = "NCIC(https://www.ncic.re.kr/)에서 교육과정 별책 PDF 를 받아 curriculum/pdf/ 에 넣어라"
        return _finish(rep, quiet)

    by_rev: dict[str, list[ParsedDoc]] = {}
    for path in pdfs:
        try:
            doc = parse_pdf(path)
        except NotImplementedError as exc:
            rep.note(path.name, str(exc).splitlines()[0], "warn")
            rep.bump("pdf_skipped")
            continue
        rep.bump("pdf_read")
        if doc.profile.revision not in wanted:
            continue
        for title, why in doc.problems:
            rep.note(f"{path.name}:{title}", why, "info")
        by_rev.setdefault(doc.profile.revision, []).append(doc)

    CURRICULUM_STANDARDS.mkdir(parents=True, exist_ok=True)
    total_std = 0
    for rev in sorted(wanted):
        docs = by_rev.get(rev, [])
        if not docs:
            rep.note(rev, f"{rev} 개정 문서를 하나도 못 찾았다", "warn")
            continue
        payload, notes = to_schema(rev, docs, SOURCE_PRIORITY)
        n_std = sum(len(u["standards"]) for s in payload["subjects"].values()
                    for u in s["units"])
        total_std += n_std
        rep.count(**{f"subjects_{rev}": len(payload["subjects"]),
                     f"standards_{rev}": n_std})
        for prefix, why in notes[:5]:
            rep.note(prefix, why, "info")
        # 단원 제목이 비면 문항집 제목을 못 만든다. 반드시 눈에 띄게 남긴다.
        for name, s in payload["subjects"].items():
            missing = [u["no"] for u in s["units"] if not u["title"]]
            if missing:
                rep.note(f"{rev}:{name}", f"단원 제목 없음 {missing}", "warn")
        # 문장이 마침표로 안 끝나면 쪽을 넘길 때 뒷부분을 놓친 것이다.
        # 원인은 PyMuPDF 의 블록 읽기 순서 — 쪽 아래에서 잘린 문장의 뒷줄이 다음 쪽의
        # '탐구 주제' 블록보다 뒤에 놓이는 경우가 있다. 자동 복구 대신 사람에게 넘긴다.
        broken = [(s["code"], name) for name, sub in payload["subjects"].items()
                  for u in sub["units"] for s in u["standards"]
                  if not s["text"].rstrip().endswith((".", "요", "다"))]
        for code, name in broken[:5]:
            rep.note(f"{rev}:{code}", f"본문이 문장으로 안 끝난다({name}) — 쪽 넘김에서 잘렸다. "
                     f"curriculum/standards/{rev}.json 에서 직접 고쳐라", "warn")
        if broken:
            rep.count(**{f"broken_text_{rev}": len(broken)})
        unknown = [n for n in payload["subjects"] if n.startswith("(미상")]
        if unknown:
            rep.note(f"{rev}:과목명", f"과목명을 못 읽은 절 {len(unknown)}개 — 제목이 그림으로 "
                     f"인쇄된 구간이다(별책4 교양과). 수능 과목은 아니다: {unknown[:4]}", "warn")
        out = CURRICULUM_STANDARDS / f"{rev}.json"
        if out.exists() and not args.force and not args.dry_run:
            rep.note(str(out.name), "이미 있다 — --force 로 덮어쓴다", "info")
            rep.bump("skipped")
            continue
        if not args.dry_run:
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        rep.artifact(f"curriculum/standards/{rev}.json")
        rep.bump("would_write" if args.dry_run else "written")

    rep.count(standards_total=total_std)
    for rev, missing in missing_departments().items():
        # 한쪽 개정에만 있는 교과 = 원본 PDF 가 빠졌다는 뜻이다.
        # 과목 이름을 코드에 적지 않고 **두 개정의 교과 목록을 비교해서** 알아낸다.
        # (지금 저장소에서는 2015 개정 도덕과 — 생활과 윤리·윤리와 사상 — 가 여기에 걸린다.)
        for dept in sorted(missing):
            rep.note(f"{rev}-{dept}과", f"{rev} 개정 {dept}과 교육과정이 없다. "
                     f"NCIC(https://www.ncic.re.kr/)에서 받아 curriculum/pdf/ 에 넣어라", "warn")
    rep.next = "python scripts/gw.py standards --draft-keywords --subject <slug>"
    return _finish(rep, quiet)


def _departments_in(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {s["department"] for s in data.get("subjects", {}).values() if s.get("department")}


def missing_departments() -> dict[str, set[str]]:
    """{개정: 그 개정에만 빠진 교과}. 두 개정 다 있어야 비교가 성립한다."""
    have = {rev: _departments_in(CURRICULUM_STANDARDS / f"{rev}.json")
            for rev in ("2015", "2022")}
    if not all(have.values()):
        return {}
    # 수능 응시 영역이 있는 교과만 본다. 음악·미술·체육까지 세면 경고가 열댓 줄 쏟아져서
    # 정작 봐야 할 '2015 도덕과 없음' 이 묻힌다.
    scope = set(AREA_BY_DEPARTMENT)
    return {rev: (set().union(*have.values()) & scope) - depts
            for rev, depts in have.items()}


def _load_revision(rev: str) -> dict:
    path = CURRICULUM_STANDARDS / f"{rev}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_subject_entries(subject, rev: str, data: dict) -> tuple[list[tuple[str, dict]], str | None]:
    """과목 정의 ↔ 파싱 결과를 잇는다. 접두사 우선, 실패하면 교육과정 과목명으로 되짚는다.

    subject.json 의 standard_prefixes 가 틀릴 수 있다(실제로 earth-science-* 의 2015 값이
    '12지구' 로 적혀 있는데 2015 개정 코드는 '12지과Ⅰ/Ⅱ' 다). 도구가 조용히 빈 결과를 내는 대신
    이름으로 한 번 더 찾아보고, 불일치를 리포트에 남긴다.
    """
    subjects = data.get("subjects", {})
    prefixes = set((subject.standard_prefixes or {}).get(rev) or [])
    hits = [(n, s) for n, s in subjects.items() if s["code_prefix"] in prefixes]
    if hits:
        return hits, None
    label = (subject.curriculum or {}).get(rev)
    if label:
        hits = [(n, s) for n, s in subjects.items() if n == label]
        if hits:
            found = ", ".join(s["code_prefix"] for _n, s in hits)
            return hits, (f"standard_prefixes.{rev} = {sorted(prefixes) or '[]'} 로는 못 찾았다. "
                          f"과목명 '{label}' 로 찾아 {found} 를 썼다 — subject.json 을 고쳐라")
    return [], None


def cmd_draft_keywords(args, rep: Report, quiet: bool = False) -> int:
    if not args.subject:
        rep.note("args", "--draft-keywords 에는 --subject 가 필요하다", "error")
        rep.next = "python scripts/gw.py standards --draft-keywords --subject <slug>"
        return _finish(rep, quiet)
    subject = load_subject(args.subject)
    revs = ["2015", "2022"] if args.revision == "all" else [args.revision]
    keywords: dict[str, list[str]] = {}
    n_vocab_hit = 0
    for rev in revs:
        data = _load_revision(rev)
        if not data:
            rep.note(rev, f"curriculum/standards/{rev}.json 이 없다", "info")
            continue
        entries, warn = resolve_subject_entries(subject, rev, data)
        if warn:
            rep.note(f"{args.subject}:{rev}", warn, "warn")
        if not entries:
            rep.note(f"{args.subject}:{rev}", f"{rev} 개정에 대응되는 과목이 없다", "info")
            continue
        for name, entry in entries:
            vocab, learning, comm = _materials_for(entry, rep)
            flat = {t for terms in vocab.values() for t in terms}
            flat |= {t for terms in learning.values() for t in terms}
            for unit in entry["units"]:
                for std in unit["standards"]:
                    kw = draft_for_standard(std["text"], comm.get(std["code"], ""),
                                            vocab.get(0, []), vocab.get(unit["no"], []),
                                            learning.get(unit["no"], []))
                    keywords[std["code"]] = kw
                    n_vocab_hit += sum(1 for k in kw if k in flat)
            rep.bump("subjects_matched")
            rep.note(f"{rev}:{name}", f"{entry['code_prefix']} "
                     f"{sum(len(u['standards']) for u in entry['units'])}개", "info")

    if not keywords:
        rep.note(args.subject, "성취기준을 하나도 못 찾았다", "error")
        rep.next = "python scripts/gw.py standards --revision all --force"
        return _finish(rep, quiet)

    empty = [c for c, v in keywords.items() if len(v) < 3]
    for c in empty[:10]:
        rep.note(c, "키워드가 3개 미만 — 사람이 채워야 한다", "warn")
    rep.count(standards=len(keywords),
              keywords=sum(len(v) for v in keywords.values()),
              from_vocabulary=n_vocab_hit, thin=len(empty))

    path = subject.keywords_path
    if path.exists() and not args.force and not args.dry_run:
        rep.note(path.name, "이미 있다 — --force 로 덮어쓴다", "error")
        rep.next = f"python scripts/gw.py standards --draft-keywords --subject {args.subject} --force"
        return _finish(rep, quiet)
    # --force 로 덮어쓰더라도 `classify --learn` 이 라벨된 문항에서 배운 용어는 살린다.
    # 초안은 교육과정 문장에서 뽑은 것이라 언제든 다시 만들 수 있지만, 학습분은
    # 사람이 판정한 라벨이 있어야만 다시 만들 수 있다. 잃으면 되돌리기 비싸다.
    keywords, kept = _preserve_learned(path, keywords)
    if kept:
        rep.count(learned_kept=kept)
        rep.note(path.name, f"학습된 용어 {kept}개를 보존했다 (classify --learn 산출물)", "info")
    if not args.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(keywords, ensure_ascii=False, indent=2), encoding="utf-8")
    rep.artifact(str(path.relative_to(SUBJECTS.parent).as_posix()))
    rep.next = (f"subjects/{args.subject}/keywords.json 를 사람이 훑은 뒤 "
                f"python scripts/gw.py classify --subject {args.subject}")
    return _finish(rep, quiet)



def _preserve_learned(path, draft: dict) -> tuple[dict, int]:
    """초안으로 덮어쓸 때 기존 파일의 학습분(learned)과 그 밖의 칸을 지키지 않는다.

    keywords.json 은 두 형태를 오간다.
      평면형   {"코드": ["용어", ...]}                      — 초안만 있는 상태
      구조형   {"코드": {"curriculum": [...], "learned": [...]}}  — classify --learn 이후
    초안은 항상 curriculum 칸에 들어간다. learned 와 _meta 같은 다른 칸은 그대로 둔다.
    기존 파일이 평면형이면 배운 것이 없다는 뜻이라 그냥 초안으로 바꾼다.
    """
    if not path.exists():
        return draft, 0
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return draft, 0  # 읽을 수 없는 파일을 지키려다 초안까지 못 쓰면 곤란하다
    if not isinstance(old, dict):
        return draft, 0

    merged: dict = {}
    kept = 0
    for code, terms in draft.items():
        prev = old.get(code)
        if isinstance(prev, dict):
            entry = dict(prev)
            entry["curriculum"] = terms
            kept += len(entry.get("learned") or [])
            merged[code] = entry
        else:
            merged[code] = terms
    # 초안에 없는 키(_meta, 이번 개정에 없는 코드 등)는 손대지 않고 옮긴다.
    for code, value in old.items():
        if code not in merged:
            merged[code] = value
            if isinstance(value, dict):
                kept += len(value.get("learned") or [])
    return merged, kept


_MATERIAL_CACHE: dict[str, "ParsedDoc"] = {}


def _materials_for(entry: dict, rep: Report) -> tuple[dict[int, list[str]],
                                                      dict[int, list[str]], dict[str, str]]:
    """초안 재료(단원별 통제 어휘·성취기준 해설)를 원본 PDF 에서 다시 긁는다.

    통제 어휘와 해설은 standards JSON 스키마에 없다 — 사람이 읽는 산출물이 아니라 초안 재료라서다.
    한 과목이 여러 개정에 걸치면 같은 PDF 를 두 번 읽게 되므로 문서 단위로 캐시한다.
    """
    path = CURRICULUM_PDF / entry["source_pdf"]
    if not path.exists():
        rep.note(entry["source_pdf"], "원본 PDF 가 없어 통제 어휘 없이 초안을 만든다", "warn")
        return {}, {}, {}
    doc = _MATERIAL_CACHE.get(path.name) or parse_pdf(path)
    _MATERIAL_CACHE[path.name] = doc
    for ps in doc.subjects:
        if ps.prefix == entry["code_prefix"]:
            return clean_vocabulary(ps.vocabulary), clean_vocabulary(ps.learning), ps.commentary
    return {}, {}, {}
