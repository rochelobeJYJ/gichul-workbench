# -*- coding: utf-8 -*-
"""다운로드 프로바이더 공통 골격.

프로바이더가 둘(EBSi, 평가원 공식) 이상이 된 이유는 **한쪽만으로는 필요한 자료가 다 모이지 않기 때문**이다.
- 해설지는 평가원이 아예 배포하지 않는다. 해설이 필요하면 EBSi 말고 길이 없다.
- 반대로 EBSi 는 2005~2013 구 교육과정 회차를 목록에 거의 올려주지 않고, 원본 화질도 평가원 배포본이 낫다.
그래서 "무엇을 받느냐"에 따라 프로바이더를 갈아끼울 수 있어야 하고, 그 접점이 이 파일이다.

프로바이더는 세 가지만 지킨다.
  1. discover(subject, targets, kinds) -> [Candidate]   목록을 훑어 후보를 찾는다. 파일은 받지 않는다.
  2. fetch(candidate) -> bytes                          후보 하나를 실제로 내려받는다.
  3. probe(...) -> [dict]                               (선택) 사이트가 실제로 뭘 갖고 있는지 실측한다.
discover 와 fetch 를 갈라놓은 이유: --dry-run 이 네트워크 목록 조회까지는 하되 파일은 건드리지 않아야
계획이 현실과 맞는지 확인할 수 있기 때문이다. 계획만 출력하고 목록을 안 보는 dry-run 은 쓸모가 없다.
"""
from __future__ import annotations

import hashlib
import html
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

# 받을 수 있는 자료 종류. 저장 파일 이름의 어간이기도 하다 (problem.pdf / answer.png / solution.pdf).
KINDS = ("problem", "answer", "solution")

# 학년도(academic year) 로 세는 시험과 달력연도(calendar year) 로 세는 시험.
# EBSi 도 평가원도 목록은 '시행일' 기준이라, 이 구분이 없으면 회차를 통째로 놓친다.
ACADEMIC_YEAR_EXAMS = frozenset({"수능", "6월모평", "9월모평"})

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------- 자료 구조

@dataclass(frozen=True)
class ExamTarget:
    """받고 싶은 회차 하나. exam_id 는 반드시 common.ids.make_exam_id() 가 만든 값이다."""
    exam_id: str
    year: int            # 수능·모평이면 학년도, 학평이면 달력연도
    exam: str            # 정규화된 시험 종류 ('수능', '9월모평', '3월학평' …)
    grade: int | None = None

    @property
    def is_academic(self) -> bool:
        return self.exam in ACADEMIC_YEAR_EXAMS

    @property
    def calendar_year(self) -> int:
        """시행 달력연도. 2021학년도 수능은 2020년(12월)에 치렀다."""
        return self.year - 1 if self.is_academic else self.year


@dataclass
class Candidate:
    """목록에서 찾아낸 파일 후보 하나. 아직 내려받지 않았다."""
    provider: str
    exam_id: str
    kind: str
    url: str
    title: str = ""                 # 목록에 적혀 있던 원문 제목. 나중에 오탐을 추적하는 유일한 증거다.
    sitting_date: str | None = None  # YYYYMMDD 시행일. 학년도 혼동을 잡아내는 근거.
    ext_hint: str = ".pdf"
    extra: dict = field(default_factory=dict)   # 프로바이더 고유 정보(zip 내부 경로 등)

    def evidence(self) -> dict:
        """manifest 에 남길 출처 정보. 파일명만으로는 나중에 아무것도 재현할 수 없다."""
        out = {
            "provider": self.provider,
            "source_url": self.url,
            "record_title": self.title,
            "sitting_date": self.sitting_date,
        }
        out.update({k: v for k, v in self.extra.items() if not k.startswith("_")})
        return out


class SourceProvider:
    """프로바이더 인터페이스. 구현체는 name / kinds 를 채우고 discover·fetch 를 덮어쓴다."""

    name = "?"
    kinds: frozenset[str] = frozenset()
    #: 이 프로바이더가 다룰 수 있는 시험 종류. 비면 제한 없음.
    exams: frozenset[str] | None = None

    def __init__(self, http: "Http | None" = None):
        self.http = http or Http()
        self.notes: list[tuple[str, str, str]] = []   # (id, why, severity) — 리포트로 그대로 넘어간다

    def note(self, ident: str, why: str, severity: str = "warn") -> None:
        self.notes.append((ident, why, severity))

    def supports(self, kind: str, exam: str) -> bool:
        if kind not in self.kinds:
            return False
        return self.exams is None or exam in self.exams

    def discover(self, subject, targets: list[ExamTarget], kinds: set[str]) -> list[Candidate]:
        raise NotImplementedError

    def fetch(self, cand: Candidate) -> bytes:
        raise NotImplementedError

    def probe(self, area: str, year: int | None = None,
              exam: str | None = None, grade: int | None = None) -> list[dict]:
        raise NotImplementedError(f"{self.name} 프로바이더는 --probe 를 지원하지 않는다")


# --------------------------------------------------------------------------- HTTP

class Http:
    """세션 하나로 재시도와 예의(딜레이)를 함께 관리한다.

    딜레이를 옵션이 아니라 기본값으로 둔 이유: 상대는 공공 사이트이고, 이 도구는 한 번에
    수십 회차를 긁는다. 원본 kice_down 이 0.5~1.5초 랜덤 딜레이를 넣은 판단을 그대로 살렸다.
    """

    def __init__(self, delay: tuple[float, float] = (0.5, 1.5),
                 list_pause: float = 0.2, retries: int = 3, timeout: int = 60):
        import requests  # 지연 임포트 — 다른 명령이 requests 없이도 뜨게 한다
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        self.delay = delay
        self.list_pause = list_pause
        self.retries = retries
        self.timeout = timeout

    def polite(self) -> None:
        """파일 하나를 받은 뒤 쉰다. 차단당하면 회차 전체를 다시 받아야 한다."""
        lo, hi = self.delay
        if hi > 0:
            time.sleep(random.uniform(lo, hi))

    def _request(self, method: str, url: str, **kw) -> bytes:
        kw.setdefault("timeout", self.timeout)
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.request(method, url, **kw)
                resp.raise_for_status()
                return resp.content
            except Exception as exc:      # 네트워크 예외 종류는 requests 버전마다 달라 넓게 잡는다
                last = exc
                if attempt < self.retries:
                    time.sleep(min(2 ** (attempt - 1), 4))
        raise RuntimeError(f"{self.retries}회 시도 실패: {url}: {last}") from last

    def get_bytes(self, url: str, **kw) -> bytes:
        return self._request("GET", url, **kw)

    def post_bytes(self, url: str, **kw) -> bytes:
        return self._request("POST", url, **kw)

    def get_text(self, url: str, **kw) -> str:
        return decode_response(self.get_bytes(url, **kw))

    def post_text(self, url: str, **kw) -> str:
        return decode_response(self.post_bytes(url, **kw))


def decode_response(data: bytes) -> str:
    """평가원·EBSi 는 페이지마다 인코딩이 섞여 있다. 순서대로 시도하고 마지막엔 손실 허용."""
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- 검증

def detect_extension(data: bytes) -> str | None:
    """바이트 머리로 실제 형식을 판정한다. 확장자는 거짓말을 하지만 매직넘버는 안 한다.

    EBSi 정답은 확장자가 .png 인데 실제로는 .jpg 인 경우가 있었고, 로그인 리다이렉트로
    HTML 이 내려오는데 이름만 .pdf 인 경우도 있었다. 그래서 헤더를 반드시 본다.
    """
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data.startswith(b"PK\x03\x04"):
        return ".zip"
    return None


def verify_bytes(data: bytes, kind: str, url: str = "") -> dict:
    """manifest v2 검증. 원본 EBSi 구현의 판정 기준을 그대로 살렸다.

    문제·해설은 PDF 만 인정하고, 정답은 PDF/PNG/JPG 를 모두 인정한다.
    (EBSi 정답은 png 이미지, 평가원 구 회차 정답표는 jpg 다.)
    1KiB 하한은 '오류 안내 페이지'가 파일인 척 저장되는 것을 막는다.
    """
    actual = detect_extension(data)
    allowed = {".pdf"} if kind in {"problem", "solution"} else {".pdf", ".png", ".jpg"}
    header_ok = actual is not None
    format_ok = actual in allowed
    return {
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "expected_extension": extension_from_url(url, kind),
        "actual_extension": actual,
        "header_ok": header_ok,
        "format_ok": format_ok,
        "verified": bool(header_ok and format_ok and len(data) > 1024),
    }


def extension_from_url(url: str, kind: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    if suffix in {".pdf", ".png", ".jpg", ".jpeg"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".png" if kind == "answer" else ".pdf"


# --------------------------------------------------------------------------- 텍스트 유틸

def clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def normalize_roman(text: str) -> str:
    """로마숫자를 고유 토큰(#1#/#2#/#3#)으로 바꾼다.

    이게 없으면 '지구과학I' 필터가 '지구과학II' 파일에 걸린다(부분 문자열). 실제로 원본 도구에서
    Ⅰ 과 Ⅱ 가 섞여 내려받아지는 사고가 났고, 그래서 나온 처리다.
    전각(Ⅰ/Ⅱ) → 반각(I/II) 로 먼저 통일한 뒤, 긴 것부터 치환해야 II 가 I+I 로 쪼개지지 않는다.
    """
    text = text.replace("Ⅲ", "III").replace("ⅲ", "III")
    text = text.replace("Ⅱ", "II").replace("ⅱ", "II")
    text = text.replace("Ⅰ", "I").replace("ⅰ", "I")
    text = re.sub(r"(?<![a-zA-Z#])[Ii]{3}(?![a-zA-Z#])", "#3#", text)
    text = re.sub(r"(?<![a-zA-Z#])[Ii]{2}(?![a-zA-Z#])", "#2#", text)
    text = re.sub(r"(?<![a-zA-Z#])[Ii](?![a-zA-Z#])", "#1#", text)
    # 한글 바로 뒤에 붙은 I/i (예: '물리i', '화학I') 는 위 lookbehind 로는 안 걸린다.
    text = re.sub(r"(?<=[가-힣])[Ii]{3}", "#3#", text)
    text = re.sub(r"(?<=[가-힣])[Ii]{2}", "#2#", text)
    text = re.sub(r"(?<=[가-힣])[Ii](?![a-zA-Z#])", "#1#", text)
    return text


def normalize_name(value: str) -> str:
    """파일명 비교용 정규화. 공백·구분자·괄호를 지우고 로마숫자를 토큰화한다."""
    # 가운뎃점은 코드포인트가 여러 종류다. ･(U+FF65)를 넣은 근거: 2024학년도 수능 사회·문화
    # 문제지(EBSi 배포본) 머리글이 '사회･문화' 로 이 반각 문자를 쓴다. 파일명에서 같은 표기를
    # 만나면 '사회문화' 별칭이 걸리지 않아 ZIP 안에서 과목을 못 찾고 통째로 실패한다.
    cleaned = re.sub(r"[\s_\-·∙・･‧()（）\[\]]", "", value).lower()
    return normalize_roman(cleaned)


def match_any_alias(filename: str, aliases: list[str]) -> str | None:
    """파일명이 별칭 중 하나에 걸리면 그 별칭을 돌려준다.

    평가원 파일명은 해마다 다르고 오기도 잦다('생물 I.PDF', '과탐(지구 과학 I).pdf',
    '04 지구과학Ⅰ_문제지.pdf'). 그래서 정규식이 아니라 **과목별 별칭표**로 맞춘다.
    별칭은 코드가 아니라 subjects/<slug>/subject.json 의 providers.kice.aliases 에 있다.
    """
    target = normalize_name(filename)
    for alias in aliases:
        if not alias:
            continue
        if normalize_name(alias) in target:
            return alias
    return None


def safe_component(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", "_", value.strip())
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).rstrip(". ")
    return value or "unnamed"


# --------------------------------------------------------------------------- 레지스트리

PROVIDER_NAMES = ("ebsi", "kice-official")


def get_provider(name: str, http: Http | None = None) -> SourceProvider:
    """이름으로 프로바이더를 만든다. 지연 임포트라 한쪽 의존성이 없어도 다른 쪽은 돈다."""
    if name == "ebsi":
        from .ebsi import EbsiProvider
        return EbsiProvider(http)
    if name in ("kice-official", "kice_official", "kice"):
        from .kice_official import KiceOfficialProvider
        return KiceOfficialProvider(http)
    raise ValueError(f"알 수 없는 프로바이더: {name!r} (가능: {', '.join(PROVIDER_NAMES)})")


def provider_chain(kind: str, exam: str, requested: str = "auto") -> list[str]:
    """(자료 종류, 시험 종류) 에 맞는 프로바이더 시도 순서.

    auto 의 판단 근거:
    - 해설(solution) 은 평가원이 배포하지 않는다 → EBSi 뿐.
    - 문제·정답은 평가원 공식본이 원본이다 → 평가원 먼저, 없으면 EBSi.
    - 전국연합학력평가(학평) 는 시·도교육청 주관이라 평가원 게시판에 아예 없다 → EBSi 뿐.
    """
    if requested and requested != "auto":
        return [requested]
    if kind == "solution":
        return ["ebsi"]
    if exam not in ACADEMIC_YEAR_EXAMS:
        return ["ebsi"]
    return ["kice-official", "ebsi"]


__all__ = [
    "KINDS", "ACADEMIC_YEAR_EXAMS", "ExamTarget", "Candidate", "SourceProvider", "Http",
    "verify_bytes", "detect_extension", "extension_from_url", "decode_response",
    "clean_html", "normalize_roman", "normalize_name", "match_any_alias", "safe_component",
    "get_provider", "provider_chain", "PROVIDER_NAMES",
]
