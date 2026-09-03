# -*- coding: utf-8 -*-
"""EBSi 기출문제 프로바이더.

**해설지를 받을 수 있는 유일한 경로다.** 평가원은 해설을 배포하지 않는다.
목록은 로그인 없이 AJAX 한 방으로 나오고, 문제/정답/해설 다운로드 URL 이 onclick 안에 그대로 박혀 있다.

원본: ~/.codex/skills/exam-source-downloader/scripts/download_exam_sources.py
그 구현에서 가져온 것 — manifest v2 검증, goDownLoad 인자 파싱, 과목ID 대조.
그 구현에서 고친 것 — 아래 두 가지. 둘 다 실제로 회차를 통째로 놓친 사고였다.

────────────────────────────────────────────────────────────────────────────
버그 1. 페이지네이션이 없었다.
    AJAX 응답은 한 페이지에 15행만 담는다. 원본은 1페이지만 읽고 끝내서, 16번째 이후 회차를
    "존재하지 않음"으로 보고했다. 실측(2025-09, 지구과학Ⅱ, 2020~2025년): 총 18건 중 3건이
    2페이지에 있었고 그게 정확히 2021학년도 수능·9월모평·6월모평이었다.
    페이징 파라미터는 응답 안의 hidden input 이름 그대로 `currentPage` 다.

함정 2. EBSi 목록은 학년도가 아니라 **시행일** 기준이다.
    - 2021학년도 수능 : 코로나로 2020-12-03 시행 → 목록의 연/월은 2020 / 12
    - 2023학년도 9월모평 : 2022-08-31 시행 → 2022 / 08
    원본은 `--months 06,09,11` 로 월을 못 박아 걸렀기 때문에 위 두 회차를 조회 자체에서 날렸다.
    그래서 여기서는 **월로 거르지 않는다.** 넉넉한 월 범위로 조회한 뒤, 회차 종류는 목록 제목
    ('대학수학능력시험' / '9월 모평(평가원)' / '3월 학력평가(교육청)') 으로 판정한다.
    학년도는 목록 연도 + 1 로 환산하되, 제목에 '2021학년도'가 있으면 그쪽을 믿는다.

바로잡음 (2026-09 실측). 과목ID 를 가르는 축은 '계열'이 아니라 '학년 목록(targetCd)' 이다.
    고3 목록(D300) 의 ID 하나가 수능·모평·고3 학평을 전부 담는다. 옛 표가 '학평용'이라 부르던
    값(지구과학Ⅰ 17041, 한국지리 17029 …)은 실은 고2 목록(D200) 용이다. 자세한 근거는
    _conf_for 의 주석과 docs/SUBJECT_IDS.md 1절에 있다.
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import math
import re
import urllib.parse

from . import (Candidate, ExamTarget, SourceProvider, clean_html, extension_from_url,
               ACADEMIC_YEAR_EXAMS)

# exam_id 는 반드시 common.ids 한 곳에서만 만든다(CONTRACT 2절). 프로바이더가 직접 문자열을
# 조립하기 시작하면 학평의 학년 표기 같은 규칙이 곧바로 갈라진다.
try:
    from common import make_exam_id
    from common.ids import EXAM_ALIASES
except ImportError:                                    # 프로바이더만 단독으로 임포트한 경우
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from common import make_exam_id
    from common.ids import EXAM_ALIASES

AJAX_URL = "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperListAjax.ajax"
LIST_URL = "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperList.ebs"
DOWNLOAD_PREFIX = "https://wdown.ebsi.co.kr/W61001/01exam"

PAGE_SIZE = 15          # 응답 한 장의 행 수. 총건수와 대조해 마지막 페이지를 판단한다.
HARD_PAGE_CAP = 60      # 목록 구조가 바뀌어 무한루프에 빠지는 것을 막는 안전핀

# 영역 정의. area_order 는 조회 파라미터 arOrd 값이고, area_hidden 은 같은 값을 한 번 더 실어야
# 하는 폼 필드다(사이트 스크립트가 그렇게 만든다). 실제 값은 probe 가 목록 페이지에서 실측하며,
# 이 표는 사이트가 응답하지 않을 때의 기본값이다.
AREAS = [
    # (영역명, form_field, area_order, area_hidden)
    ("국어", "sFormPartKor", "1", "korArOrd"),
    ("수학", "sFormPartMath", "2", "mathArOrd"),
    ("영어", "sFormPartEng", "3", "engArOrd"),
    ("한국사", "sFormPartHis", "4", "hisArOrd"),
    ("사회탐구", "sFormPartSoc", "5", "srch1ArOrd"),
    ("과학탐구", "sFormPartSci", "6", "srch2ArOrd"),
    ("직업탐구", "sFormPartCareer", "7", "jobArOrd"),
    ("제2외국어/한문", "sFormPartLang", "8", "scndForgnlngArOrd"),
]
AREA_BY_FORM_FIELD = {form: (label, order, hidden) for label, form, order, hidden in AREAS}
AREA_BY_LABEL = {label: (form, order, hidden) for label, form, order, hidden in AREAS}
AREA_HIDDEN_BY_ORDER = {order: hidden for _l, _f, order, hidden in AREAS}

# onclick 함수 이름 → 자료 종류
KIND_BY_CALL = {"P": "problem", "J": "answer", "J2": "answer", "H": "solution"}

# 학년별 목록 대상 코드. 고3 목록(D300)에 평가원 모평과 교육청 학평이 함께 들어 있다.
TARGET_BY_GRADE = {1: "D100", 2: "D200", 3: "D300"}

# 시험 종류별로 조회할 시행월 후보. **정답 필터가 아니라 조회 범위**다.
# 실제 판정은 제목으로 하고, 여기서는 "이 달에는 절대 없다"는 달만 빼서 응답 크기를 줄인다.
# 넉넉하게 잡은 근거: 2021학년도 수능 12월, 2023학년도 9월모평 8월,
# 2020년 고3 3월 학평은 코로나로 4월 24일 시행되었다. 연기는 늘 있었다.
MONTH_HINTS = {
    "수능": ["11", "12"],
    "6월모평": ["05", "06", "07"],
    "9월모평": ["08", "09", "10"],
    "3월학평": ["03", "04", "05"],
    "4월학평": ["04", "05", "06"],
    "7월학평": ["06", "07", "08"],
    "10월학평": ["09", "10", "11"],
}


# --------------------------------------------------------------------------- 파싱 보조

def _split_blocks(page: str) -> list[str]:
    return re.findall(
        r'<div class="qus_box\b.*?(?=<div class="qus_box\b|<!-- //board_list -->)',
        page, re.S)


def _block_title(block: str) -> str:
    for pattern in (r'<div class="qus_tit">(.*?)</div>',
                    r'<strong class="tit">(.*?)</strong>',
                    r'<p class="tit">(.*?)</p>'):
        m = re.search(pattern, block, re.S)
        if m:
            return clean_html(m.group(1))
    return ""


def _normalize_url(value: str) -> str:
    if value.startswith(("http://", "https://")):
        # wdown 은 https 를 지원한다. 예전 회차 링크가 http 로 박혀 있어도 올려서 쓴다.
        return "https://" + value[len("http://"):] if value.startswith("http://") else value
    return DOWNLOAD_PREFIX + value if value.startswith("/") else value


def _infer_date(url: str, irecord: str) -> str | None:
    """시행일 YYYYMMDD. URL 경로(/20201203/)가 1순위, 없으면 레코드 ID 앞 8자리."""
    m = re.search(r"/(\d{8})/", url)
    if m:
        return m.group(1)
    return irecord[:8] if re.match(r"^20\d{6}", irecord) else None


def _infer_grade(irecord: str, title: str) -> int | None:
    """레코드 ID 9번째 자리가 학년이다(예 202511133 → 3). 없으면 제목의 '고3'."""
    if len(irecord) >= 9 and irecord[8] in "123":
        return int(irecord[8])
    m = re.search(r"고\s*([123])", title)
    return int(m.group(1)) if m else None


def classify_exam(title: str, month: str) -> str | None:
    """목록 제목으로 시험 종류를 판정한다. **월로 판정하지 않는 것이 이 함수의 존재 이유다.**

    고3 목록(D300)에는 평가원 모평과 교육청 학평이 섞여 있고, 시행일은 해마다 밀린다.
    다행히 제목은 일관되다 — '대학수학능력시험' / '9월 모평(평가원)' / '3월 학력평가(교육청)'.
    """
    t = title.replace(" ", "")
    if "대학수학능력시험" in t or "수능" in t:
        return "수능"
    # 월은 **공백을 지우지 않은** 제목에서 읽는다. '고3 9월 모평' 의 공백을 지우면 '고39월' 이 되어
    # 두 자리 월 '39' 로 잘못 읽힌다(실제로 모평 회차를 통째로 놓쳤던 자리다).
    # 앞이 숫자면 매치하지 않게 막아 '2020년'·'고3' 의 숫자가 붙어드는 것도 함께 차단한다.
    m = re.search(r"(?<!\d)(\d{1,2})\s*월", title)
    label_month = m.group(1) if m else str(int(month)) if month.isdigit() else None
    if not label_month:
        return None
    # 평가원 판정을 먼저 한다. '모평'과 '학평'은 두 글자만 다르고 둘 다 '평'으로 끝나서
    # 순서를 뒤집으면 모평이 학평으로 새는 자리다.
    if "평가원" in t or "모평" in t or "모의평가" in t:
        kind = f"{int(label_month)}월모평"
        return kind if kind in ACADEMIC_YEAR_EXAMS else None
    # 학평 제목은 주관 시·도가 붙는다 — '고3 3월 학평(서울)', '고3 5월 학평(경기)'.
    # '전국연합학력평가' 라는 정식 명칭이 제목에 안 나오는 회차가 있어 '학평'도 받아야 한다.
    if "학평" in t or "학력평가" in t or "교육청" in t or "전국연합" in t:
        return f"{int(label_month)}월학평"
    return None


def academic_year_of(title: str, kind: str, calendar_year: int) -> int:
    """회차의 연도. 수능·모평은 학년도, 학평은 달력연도.

    수능 제목에는 '2021학년도'가 들어 있어 그것이 가장 확실한 근거다.
    모평 제목에는 학년도가 없으므로 '시행 달력연도 + 1' 로 환산한다
    (6월·9월 모평은 언제나 그 학년도 시작 해에 치른다).
    """
    if kind in ACADEMIC_YEAR_EXAMS:
        m = re.search(r"(20\d{2})\s*학년도", title)
        if m:
            return int(m.group(1))
        return calendar_year + 1
    return calendar_year


# --------------------------------------------------------------------------- 프로바이더

class EbsiProvider(SourceProvider):
    name = "ebsi"
    kinds = frozenset({"problem", "answer", "solution"})

    def __init__(self, http=None):
        super().__init__(http)
        self._unknown_exams: set[str] = set()   # 같은 안내를 회차마다 반복하지 않기 위한 기록

    # ------------------------------------------------------------------ 조회

    def _referer(self, target_cd: str) -> str:
        return f"{LIST_URL}?targetCd={target_cd}"

    def _post_fields(self, target_cd: str, years: list[int], months: list[str],
                     area_order: str, form_field: str, subject_id: str,
                     page: int, national: bool) -> list[tuple[str, str]]:
        area_hidden = AREA_HIDDEN_BY_ORDER.get(area_order, "srch2ArOrd")
        fields: list[tuple[str, str]] = [
            ("targetCd", target_cd),
            ("yearList", ",".join(str(y) for y in years)),
            ("monthList", ",".join(months)),
            ("arOrd", area_order),
            ("subjIdList", subject_id),
            ("sort", "recent"),
            ("paperId", ""),
            ("paperNo", ""),
            ("lvl", ""),
            ("currentPage", str(page)),      # ← 원본에 없던 페이징 파라미터
            (area_hidden, area_order),
            (form_field, subject_id),
        ]
        if national:
            fields.append(("yearAll", "all"))
        fields += [("year", str(y)) for y in years]
        fields += [("month", m) for m in months]
        return fields

    def _fetch_listing(self, target_cd: str, years: list[int], months: list[str],
                       area_order: str, form_field: str, subject_id: str,
                       national: bool) -> tuple[list[tuple[str, str]], list[str]]:
        """목록 전 페이지를 훑는다. 반환: [(제목, 블록HTML)], [원본 HTML 페이지들].

        멈추는 조건은 셋이다. 총건수를 다 모았거나, goPage 링크가 더 없거나, 안전핀(60쪽).
        하나만 믿지 않는 이유: 총건수 표시가 사라지거나 페이징 위젯이 창(window) 형태로
        바뀌어도 나머지 조건으로 버텨야 하기 때문이다.
        """
        blocks: list[tuple[str, str]] = []
        pages_raw: list[str] = []
        page, max_page, total = 1, 1, None
        seen_blocks = 0
        while page <= max_page and page <= HARD_PAGE_CAP:
            body = urllib.parse.urlencode(
                self._post_fields(target_cd, years, months, area_order,
                                  form_field, subject_id, page, national)).encode("utf-8")
            text = self.http.post_text(
                AJAX_URL, data=body,
                headers={"Referer": self._referer(target_cd),
                         "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                         "X-Requested-With": "XMLHttpRequest"})
            pages_raw.append(text)
            if total is None:
                m = re.search(r'<em class="tot">\s*(\d+)', text)
                total = int(m.group(1)) if m else None
            found = _split_blocks(text)
            if not found and page == 1 and "goDownLoad" in text:
                # 마크업이 바뀐 경우. 원본 구현의 관용(페이지 전체를 한 블록으로 취급)을 살리되
                # 조용히 넘기지 않고 반드시 알린다 — 파싱이 깨진 채 '정상'으로 보고되면 최악이다.
                self.note(f"{form_field}:{subject_id}",
                          "EBSi 목록 마크업이 바뀐 듯하다(qus_box 없음). 페이지 전체를 한 블록으로 파싱했다.",
                          "warn")
                found = [text]
            if not found:
                break
            seen_blocks += len(found)
            blocks.extend((_block_title(b), b) for b in found)
            pages = [int(n) for n in re.findall(r"goPage\((\d+)\)", text)]
            if pages:
                max_page = max(max_page, max(pages))
            if total is not None:
                max_page = min(max(max_page, math.ceil(total / PAGE_SIZE)), HARD_PAGE_CAP)
                if seen_blocks >= total:
                    break
            page += 1
            self.http_list_pause()
        if total is not None and seen_blocks < total:
            self.note(f"{form_field}:{subject_id}",
                      f"목록 총 {total}건 중 {seen_blocks}건만 읽었다. 페이지네이션을 확인하라.",
                      "warn")
        return blocks, pages_raw

    def http_list_pause(self) -> None:
        import time
        if self.http.list_pause:
            time.sleep(self.http.list_pause)

    # ------------------------------------------------------------------ discover

    def _conf_for(self, subject, target_cd: str) -> dict:
        """학년 목록별 조회 설정.

        **같은 과목이라도 EBSi 과목ID 가 학년 목록(targetCd)마다 다르다.**
        지구과학Ⅰ은 고3 목록(D300)에서 154, 고2 목록(D200)에서 17041 이다.

        예전 주석은 이 차이를 '계열(수능·모평 / 학평)' 탓으로 적었는데 **실측 결과 틀렸다.**
        2026-09 실측(고3 목록, 17과목 전수):
          - 고3 목록의 subject_id 하나가 수능·6월모평·9월모평·고3 학평을 **전부** 담는다.
            (예: 한국지리 141 로 '2026학년도 수능'과 '고3 3월 학평(서울)'이 같이 나온다)
          - 옛 표의 '학평 계열 ID'(한국지리 17029, 지구과학Ⅰ 17041 …)는 고3 목록에서 2019년까지
            거슬러 올라가도 0건이고, 고2 목록(D200)에서는 2015~2025년 44건이 정상 조회된다.
            즉 그 값들은 '학평용'이 아니라 **고2 목록용**이었다.
        계열로 갈랐던 예전 코드는 고3 학평을 고2 ID 로 조회해 0건을 받고
        "national 이 낡았을 수 있다"는 **틀린 진단**을 붙였다. 그래서 축을 학년으로 바꾼다.

        subject.json 의 providers.ebsi.national 은 이제 '고1·고2 목록 전용 override' 다.
        (키 이름은 기존 파일과의 호환을 위해 유지한다 — 뜻은 이 주석이 정한다.)
        """
        base = dict(subject.provider("ebsi") or {})
        override = base.get("national")
        # 고3 목록(D300)에는 override 를 적용하지 않는다. 고3 은 상위 값 하나로 전부 조회된다.
        if target_cd != "D300" and isinstance(override, dict):
            base.update({k: v for k, v in override.items() if v})
        base.pop("national", None)
        return base

    def discover(self, subject, targets: list[ExamTarget], kinds: set[str]) -> list[Candidate]:
        # 목록을 가르는 축은 둘이다 — 계열(수능·모평 / 학평)과 학년(targetCd).
        # 계열이 다르면 과목ID 자체가 다르므로 설정부터 따로 푼다.
        groups: dict[tuple[bool, str], list[ExamTarget]] = {}
        for t in targets:
            national = t.exam not in ACADEMIC_YEAR_EXAMS
            grade = (t.grade or 3) if national else 3
            groups.setdefault((national, TARGET_BY_GRADE.get(grade, "D300")), []).append(t)

        wanted = {t.exam_id: t for t in targets}
        out: list[Candidate] = []
        for (national, target_cd), group in groups.items():
            conf = self._conf_for(subject, target_cd)
            subject_id = str(conf.get("subject_id") or "").strip()
            if not subject_id:
                where = ("providers.ebsi.national.subject_id" if target_cd != "D300"
                         else "providers.ebsi.subject_id")
                self.note(subject.slug,
                          f"subject.json 에 {where} 가 없어 "
                          f"{', '.join(t.exam_id for t in group[:4])} … 를 조회하지 못했다. "
                          f"`gw download --probe --area {subject.area}"
                          + (f" --grade {group[0].grade}" if national and group[0].grade else "")
                          + "` 으로 실측해 채운다.", "error")
                continue
            # 예전엔 이 두 줄의 기본값이 ('sFormPartSci', '6') — **과학탐구** 값이었다.
            # 지구과학이 사는 영역을 기본값으로 박아 둔 셈이라, 영역명이 표에 없는 과목
            # (오타, 새 영역, 아직 안 채운 subject.json)은 조용히 과학탐구 목록을 뒤지고
            # "0건 — 과목ID 가 낡았을 수 있다"는 엉뚱한 진단을 받는다. 모르면 모른다고 말한다.
            form_field = conf.get("form_field")
            area_order = conf.get("area_order")
            if not form_field or not area_order:
                fallback = AREA_BY_LABEL.get(subject.area)
                if fallback is None:
                    self.note(subject.slug,
                              f"영역 {subject.area!r} 이 EBSi 영역표에 없고 providers.ebsi 에 "
                              f"form_field/area_order 도 없다 — 어느 영역 목록을 조회할지 알 수 없다. "
                              f"아는 영역: {', '.join(AREA_BY_LABEL)}. "
                              f"subject.json 의 area 를 고치거나 form_field/area_order 를 직접 채운다.",
                              "error")
                    continue
                form_field = form_field or fallback[0]
                area_order = area_order or fallback[1]
            area_order = str(area_order)
            years = sorted({t.calendar_year for t in group})
            months = sorted({m for t in group for m in MONTH_HINTS.get(t.exam, [])})

            # override(고1·고2 목록용 ID)로 0건이면 상위 ID 로 한 번 더 시도하는 안전핀.
            # 두 목록이 언젠가 다시 합쳐지거나 override 가 낡았을 때를 대비한 것이지,
            # 지금 구조상 정상 경로는 아니다 — 그래서 성공하면 원인을 단정하지 말고 사실만 알린다.
            candidates_id = [subject_id]
            base_id = str((subject.provider("ebsi") or {}).get("subject_id") or "").strip()
            if target_cd != "D300" and base_id and base_id != subject_id:
                candidates_id.append(base_id)
            blocks: list = []
            used_id = subject_id
            for idx, sid in enumerate(candidates_id):
                blocks, _raw = self._fetch_listing(target_cd, years, months, area_order,
                                                   form_field, sid, national)
                used_id = sid
                if blocks:
                    if idx:
                        self.note(subject.slug,
                                  f"{target_cd} 목록에서 providers.ebsi.national.subject_id "
                                  f"{candidates_id[0]} 로는 0건이라 상위 subject_id {sid} 로 다시 찾았다. "
                                  f"두 값 중 어느 쪽이 맞는지는 "
                                  f"`gw download --probe --area {subject.area} --grade "
                                  f"{group[0].grade or 3}` 로 확인하라.", "warn")
                    break
            if not blocks:
                # 0건은 '그 회차가 없다'일 수도, '과목ID 가 틀렸다'일 수도 있다. 둘을 구별할 방법이
                # 사용자에게 없으므로 어떤 값으로 물었는지를 그대로 알려준다.
                self.note(subject.slug,
                          f"EBSi 목록 0건 (targetCd={target_cd}, subject_id={used_id}, "
                          f"arOrd={area_order}, 연도={years}, 월={months}). 과목ID 가 낡았을 수 있다 — "
                          f"`gw download --probe --area {subject.area}"
                          + (f" --grade {group[0].grade}" if national and group[0].grade else "")
                          + "` 로 실측하라.", "warn")
            out.extend(self._rows_to_candidates(blocks, wanted, kinds, used_id))
        return out

    def _rows_to_candidates(self, blocks, wanted: dict[str, ExamTarget],
                            kinds: set[str], subject_id: str) -> list[Candidate]:
        call_re = re.compile(r"goDownLoad(J2|[PJH])\((.*?)\);", re.S)
        seen: set[tuple[str, str, str]] = set()
        out: list[Candidate] = []
        for title, block in blocks:
            for call, arg_text in call_re.findall(block):
                kind = KIND_BY_CALL[call]
                if kind not in kinds:
                    continue
                args = re.findall(r"'([^']*)'", arg_text)
                if not args:
                    continue
                # 인자 6번째가 과목ID다. 다른 과목 행이 섞여 들어오는 것을 여기서 막는다.
                if len(args) > 5 and args[5] and args[5] != subject_id:
                    continue
                irecord = args[2] if len(args) > 2 else ""
                url = _normalize_url(args[0])
                date = _infer_date(url, irecord)
                if not date:
                    continue
                calendar_year, month = int(date[:4]), date[4:6]
                exam = classify_exam(title, month)
                if not exam:
                    continue
                if exam not in EXAM_ALIASES:
                    # EBSi 에는 있는데 CONTRACT 2절의 시험 목록에 없는 회차(예: 5월·6월·11월 학평).
                    # 조용히 버리면 "왜 안 받아지지?" 가 되므로 종류마다 한 번씩 알린다.
                    if exam not in self._unknown_exams:
                        self._unknown_exams.add(exam)
                        self.note(exam,
                                  f"EBSi 목록에 '{title}' 회차가 있으나 exam_id 규칙(CONTRACT 2절)에 "
                                  f"'{exam}' 이 없어 건너뛴다. 필요하면 common/ids.py 의 "
                                  f"EXAM_ALIASES 에 추가해야 한다.", "info")
                    continue
                grade = _infer_grade(irecord, title)
                year = academic_year_of(title, exam, calendar_year)
                try:
                    exam_id = make_exam_id(year, exam, grade)
                except ValueError:
                    # 학평인데 목록에서 학년을 못 읽은 경우 등. 조용히 버리지 않고 남긴다.
                    self.note(f"{year}_{exam}",
                              f"exam_id 를 만들 수 없어 건너뛴다(학년 판정 실패). 제목: {title}",
                              "warn")
                    continue
                target = wanted.get(exam_id)
                if target is None:
                    continue
                key = (exam_id, kind, url)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Candidate(
                    provider=self.name, exam_id=exam_id, kind=kind, url=url,
                    title=title, sitting_date=date,
                    ext_hint=extension_from_url(url, kind),
                    extra={"irecord": irecord, "subject_id": subject_id,
                           "call": f"goDownLoad{call}"}))
        return out

    # ------------------------------------------------------------------ fetch

    def fetch(self, cand: Candidate) -> bytes:
        target_cd = "D300"
        data = self.http.get_bytes(cand.url, headers={"Referer": self._referer(target_cd)})
        self.http.polite()
        return data

    # ------------------------------------------------------------------ probe

    def probe(self, area: str, year: int | None = None,
              exam: str | None = None, grade: int | None = None) -> list[dict]:
        """영역 안의 과목 목록을 **사이트에서 직접 읽어** (과목명, subject_id, form_field, area_order) 로 돌려준다.

        이 도구를 새 과목으로 확장하려면 subject_id 가 필요한데, 그 값은 어디에도 문서화되어 있지
        않고 교육과정이 바뀔 때마다 달라진다. 그래서 표를 코드에 박아두는 대신 실측한다.
        목록 페이지의 과목 체크박스가 곧 진실이다:
            <input type="checkbox" name="sFormPartSoc" value="141"> <label>한국지리</label>
        year/exam 을 주면 그 회차에 실제로 자료가 있는지까지 한 과목씩 조회해 확인한다.
        """
        target_cd = TARGET_BY_GRADE.get(grade or 3, "D300")
        page = self.http.get_text(f"{LIST_URL}?targetCd={target_cd}")

        # arOrd 값도 페이지에서 실측한다(하드코딩 표는 최후의 수단).
        live_order = {m.group(1): m.group(2) for m in
                      re.finditer(r'<input[^>]*name="(\w*ArOrd)"[^>]*value="(\d+)"', page)}

        rows: list[dict] = []
        pattern = re.compile(
            r'<input[^>]*name="(sFormPart\w+)"[^>]*value="([^"]+)"[^>]*>\s*'
            r'<label[^>]*>(.*?)</label>', re.S)
        for form_field, subject_id, label in pattern.findall(page):
            label = clean_html(label)
            info = AREA_BY_FORM_FIELD.get(form_field)
            if not info:
                continue
            area_label, area_order, area_hidden = info
            if area and area_label != area:
                continue
            if not subject_id.isdigit():
                # 'socPast', 'sciPast' 같은 묶음 값. 개별 과목ID가 아니라 '이전 과목 전체'다.
                rows.append({"label": label, "subject_id": subject_id, "form_field": form_field,
                             "area": area_label, "area_order": live_order.get(area_hidden, area_order),
                             "area_hidden": area_hidden, "target_cd": target_cd,
                             "kind": "bundle",
                             "note": "개별 과목ID가 아니라 '이전 교육과정 과목 전체' 묶음 값이다."})
                continue
            row = {
                "label": label,
                "subject_id": subject_id,
                "form_field": form_field,
                "area": area_label,
                "area_order": live_order.get(area_hidden, area_order),
                "area_hidden": area_hidden,
                "target_cd": target_cd,
                "kind": "subject",
            }
            row["subject_json"] = {
                "ebsi": {"family": "kice" if (exam or "수능") in ACADEMIC_YEAR_EXAMS else "national",
                         "subject_id": subject_id,
                         "form_field": form_field,
                         "area_order": row["area_order"]},
                "kice": {"area": area_label, "aliases": [label]},
            }
            rows.append(row)

        if year and exam:
            self._probe_verify(rows, year, exam, grade, target_cd)
        return rows

    def _probe_verify(self, rows: list[dict], year: int, exam: str,
                      grade: int | None, target_cd: str) -> None:
        """과목별로 실제 회차 조회를 한 번씩 때려 자료 유무를 확인한다."""
        national = exam not in ACADEMIC_YEAR_EXAMS
        calendar_year = year - 1 if not national else year
        months = MONTH_HINTS.get(exam, ["03", "04", "06", "07", "09", "10", "11", "12"])
        for row in rows:
            if row.get("kind") != "subject":
                continue
            try:
                blocks, _ = self._fetch_listing(target_cd, [calendar_year], months,
                                                row["area_order"], row["form_field"],
                                                row["subject_id"], national)
            except Exception as exc:
                row["probe_error"] = str(exc)
                continue
            hits, kinds_found, titles = 0, set(), []
            for title, block in blocks:
                if classify_exam(title, months[0]) != exam:
                    continue
                if grade and f"고{grade}" not in title.replace(" ", "") and national:
                    continue
                hits += 1
                titles.append(title)
                for call, _args in re.findall(r"goDownLoad(J2|[PJH])\((.*?)\);", block, re.S):
                    kinds_found.add(KIND_BY_CALL[call])
            row["rows_found"] = hits
            row["kinds_found"] = sorted(kinds_found)
            row["titles"] = titles[:3]
            self.http_list_pause()
