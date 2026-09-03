# -*- coding: utf-8 -*-
"""한국교육과정평가원 공식 배포 프로바이더 (suneung.re.kr).

원본: D:/codex_work/programs/kice_down/main.py (1205줄, tkinter GUI). GUI 는 버리고 스크래핑 엔진만
계약에 맞게 재구성했다. **검증된 알고리즘 셋은 그대로 살렸다** — 이게 이 파일의 핵심 자산이다.

  1. 게시판 페이지네이션 (page= 링크의 최댓값까지 순회)
  2. ZIP 내부 파일명의 CP949 복원 — 2010년대 초 ZIP 은 UTF-8 플래그가 꺼져 있어
     zipfile 이 cp437 로 잘못 읽는다. '╗²╣░ I.PDF' → '생물 I.PDF' 로 되돌려야 과목을 못 찾는다.
  3. 로마숫자 토큰화 매칭 — '지구과학I' 필터가 '지구과학II' 파일에 걸리는 사고를 막는다.
  4. 안전 다운로드 (요청 사이 0.5~1.5초 랜덤 딜레이). 공공 사이트다.

한계(설계상 못 고치는 것):
  - **해설지가 없다.** 평가원은 문제지와 정답표만 배포한다. 해설은 EBSi 로 가야 한다.
  - **학평이 없다.** 전국연합학력평가는 시·도교육청 주관이라 이 게시판에 아예 올라오지 않는다.
  - 2005학년도부터다. 그 이전은 게시판에 없다.
"""
from __future__ import annotations

import io
import os
import re
import zipfile

from . import (Candidate, ExamTarget, SourceProvider, match_any_alias, normalize_name)

BASE_URL = "https://www.suneung.re.kr"
LIST_PATH = "/boardCnts/list.do"
FILE_PATH = "/boardCnts/fileDown.do"

BOARD_SUNEUNG = "1500234"   # 대학수학능력시험
BOARD_MOCK = "1500236"      # 수능 모의평가

YEAR_MIN = 2005             # 게시판에 자료가 존재하는 최초 학년도

# 시험 종류 → (게시판, 월 파라미터). 학평은 없다 — provider_chain() 이 아예 EBSi 로 보낸다.
EXAM_ROUTE = {
    "수능": (BOARD_SUNEUNG, None),
    "6월모평": (BOARD_MOCK, "6월"),
    "9월모평": (BOARD_MOCK, "9월"),
}

# 평가원 사이트가 쓰는 **영역명의 연도별 변천표**. 과목 정의가 아니라 사이트 사정이다.
# 2013학년도까지는 언어/수리/외국어였고 2014학년도부터 국어/수학/영어다.
# 한국사는 2017학년도부터 독립 영역이고 그 전에는 사회탐구 ZIP 안에 들어 있다.
AREA_HISTORY = {
    "국어": {"old": "언어", "cutoff": 2014},
    "수학": {"old": "수리", "cutoff": 2014},
    "영어": {"old": "외국어", "cutoff": 2014},
    "한국사": {"old": None, "cutoff": 2017},
}

# 서버 검색이 자주 깨지는 영역. value 코드가 연도별로 어긋나 '자료 없음'이 뜬다.
# 파라미터를 보내지 않고 전체를 받아 클라이언트에서 거른다. (원본 도구가 실전에서 얻은 회피책)
CLIENT_FILTER_AREAS = {
    "제2외국어/한문": ["제2외국어", "한문", "독일어", "프랑스어", "스페인어",
                       "중국어", "일본어", "러시아어", "아랍어", "베트남어"],
}

# 첨부 파일명에서 자료 종류를 읽는다. 듣기 음원·대본은 이 도구가 다루지 않는다.
_ANSWER_TOKENS = ("정답", "answer")
_SKIP_TOKENS = ("듣기", "음원", "대본", "listening")


def detect_kind(filename: str, parent: str = "") -> str | None:
    """파일명 → problem / answer / None(건너뜀).

    '문제'라는 낱말이 없어도 정답·듣기가 아니면 문제지로 본다. 구 회차 파일명이
    '언어(홀수형).pdf', '과탐(물리 I).pdf' 처럼 종류 표기 없이 오기 때문이다.
    부모 ZIP 이름에서 힌트를 물려받는 것도 같은 이유다('…정답표.zip' 안의 '지구과학Ⅱ.jpg').
    """
    blob = f"{filename} {os.path.basename(parent)}".lower()
    if any(t in blob for t in _SKIP_TOKENS):
        return None
    if any(t in blob for t in _ANSWER_TOKENS):
        return "answer"
    return "problem"


def fix_zip_name(raw: str) -> str:
    """ZIP 내부 파일명의 인코딩 복원.

    UTF-8 플래그가 꺼진 구 ZIP 을 zipfile 은 cp437 로 디코딩해버린다. 되감아서 cp949(euc-kr)로
    다시 읽어야 한글이 나온다. 실측(2010학년도 과학탐구영역.zip): '┴÷▒╕░·╟╨I.PDF' → '지구과학I.PDF'.
    """
    for encoding in ("cp949", "euc-kr"):
        try:
            return raw.encode("cp437").decode(encoding)
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    return raw


def resolve_area(area: str, year: int) -> list[str]:
    """조회에 쓸 영역명 후보를 연도 순으로 돌려준다. 첫 후보가 실패하면 다음을 쓴다."""
    hist = AREA_HISTORY.get(area)
    if not hist or year >= hist["cutoff"]:
        return [area]
    old = hist.get("old")
    return [old, area] if old else [area]


class KiceOfficialProvider(SourceProvider):
    name = "kice-official"
    kinds = frozenset({"problem", "answer"})     # 해설은 평가원이 배포하지 않는다
    exams = frozenset(EXAM_ROUTE)                # 학평은 이 게시판에 없다

    def __init__(self, http=None):
        super().__init__(http)
        self._zip_cache: tuple[str, bytes] | None = None   # 직전 ZIP 하나만 들고 있는다(메모리 방어)

    # ------------------------------------------------------------------ 게시판 조회

    def _fetch_posts(self, board_id: str, year: int, month: str | None,
                     area: str | None) -> list[dict]:
        """조건에 맞는 게시물을 **모든 페이지**에서 모은다."""
        from bs4 import BeautifulSoup

        posts: list[dict] = []
        page, max_page = 1, 1
        client_filter = CLIENT_FILTER_AREAS.get(area or "")
        while page <= max_page and page <= 30:
            params = {"boardID": board_id, "m": "0403", "s": "suneung",
                      "page": str(page), "C01": str(year)}
            if board_id == BOARD_MOCK and month:
                params["C02"] = month
            if area and not client_filter:
                params["C03" if board_id == BOARD_MOCK else "C02"] = area
            text = self.http.get_text(BASE_URL + LIST_PATH, params=params,
                                      headers={"Referer": f"{BASE_URL}/main.do?s=suneung"})
            soup = BeautifulSoup(text, "html.parser")
            table = soup.find("table")
            if not table:
                break
            body = table.find("tbody")
            rows = body.find_all("tr") if body else table.find_all("tr")[1:]
            if not rows:
                break
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 5:
                    continue
                if client_filter and not any(k in row.get_text() for k in client_filter):
                    continue
                if board_id == BOARD_MOCK:
                    post_area = cols[3].get_text(strip=True)
                    file_col = cols[6] if len(cols) > 7 else cols[-1]
                else:
                    post_area = cols[2].get_text(strip=True)
                    file_col = cols[-1]
                files = []
                for a in file_col.find_all("a"):
                    m = re.search(r"fn_fileDown\('([a-f0-9]+)'\)", a.get("onclick", "") or "")
                    if not m:
                        continue
                    fname = a.get("title") or ""
                    if not fname:
                        img = a.find("img")
                        fname = (img.get("alt") or "") if img else ""
                    files.append({"file_seq": m.group(1), "filename": fname,
                                  "url": f"{BASE_URL}{FILE_PATH}?fileSeq={m.group(1)}"})
                posts.append({"board_seq": cols[0].get_text(strip=True),
                              "year": cols[1].get_text(strip=True),
                              "area": post_area,
                              "title": " ".join(c.get_text(strip=True) for c in cols[3:5]),
                              "files": files})
            for a in soup.find_all("a", href=re.compile(r"page=\d+")):
                m = re.search(r"page=(\d+)", a.get("href", ""))
                if m:
                    max_page = max(max_page, int(m.group(1)))
            page += 1
        return posts

    # ------------------------------------------------------------------ discover

    def discover(self, subject, targets: list[ExamTarget], kinds: set[str]) -> list[Candidate]:
        conf = subject.provider("kice") or {}
        area = conf.get("area") or subject.area
        aliases = list(conf.get("aliases") or []) or [subject.label]
        want = {k for k in kinds if k in self.kinds}
        if not want:
            return []

        out: list[Candidate] = []
        for target in targets:
            route = EXAM_ROUTE.get(target.exam)
            if not route:
                self.note(target.exam_id,
                          "전국연합학력평가는 평가원 게시판에 없다(시·도교육청 주관). EBSi 로 받는다.",
                          "info")
                continue
            if target.year < YEAR_MIN:
                self.note(target.exam_id,
                          f"평가원 게시판은 {YEAR_MIN}학년도부터다. 그 이전 회차는 여기서 못 받는다.",
                          "warn")
                continue
            board, month = route
            posts: list[dict] = []
            for candidate_area in resolve_area(area, target.year):
                posts = self._fetch_posts(board, target.year, month, candidate_area)
                if posts:
                    if candidate_area != area:
                        self.note(target.exam_id,
                                  f"영역명 '{area}' 로는 자료가 없어 구 명칭 '{candidate_area}' 로 찾았다.",
                                  "info")
                    break
            if not posts:
                continue
            for post in posts:
                for f in post["files"]:
                    kind = detect_kind(f["filename"])
                    if kind is None or kind not in want:
                        continue
                    ext = os.path.splitext(f["filename"])[1].lower()
                    is_zip = ext == ".zip"
                    # ZIP 이 아닌 낱개 파일은 이 자리에서 과목을 판별할 수 있다.
                    # ZIP 은 열어봐야 알기 때문에 alias 판정을 fetch 로 미룬다.
                    alias_hit = None if is_zip else match_any_alias(f["filename"], aliases)
                    out.append(Candidate(
                        provider=self.name, exam_id=target.exam_id, kind=kind,
                        url=f["url"], title=f'{post["title"]} / {f["filename"]}'.strip(" /"),
                        sitting_date=None,
                        ext_hint=".pdf" if not is_zip or kind == "problem" else ".pdf",
                        extra={"attachment": f["filename"], "board_seq": post["board_seq"],
                               "area": post["area"], "zip": is_zip,
                               "alias_match": alias_hit,
                               "_aliases": aliases, "_area": area}))
        return out

    # ------------------------------------------------------------------ fetch

    def fetch(self, cand: Candidate) -> bytes:
        data = self._download(cand.url)
        if not cand.extra.get("zip"):
            return data
        return self._extract(cand, data)

    def _download(self, url: str) -> bytes:
        if self._zip_cache and self._zip_cache[0] == url:
            return self._zip_cache[1]
        data = self.http.get_bytes(url, headers={"Referer": f"{BASE_URL}/main.do?s=suneung"})
        self.http.polite()
        if data[:2] == b"PK":
            self._zip_cache = (url, data)
        return data

    def _extract(self, cand: Candidate, blob: bytes) -> bytes:
        """탐구 영역 ZIP 에서 이 과목 파일 하나만 꺼낸다.

        평가원은 탐구 8~9과목을 ZIP 하나로 묶어 올린다. 과목 파일명이 해마다 다르고 오기도 잦아서
        (2020: '과탐(지구 과학 I)', 2026: '08 지구과학Ⅱ_문제지', 2010: cp949 '지구과학II.PDF')
        정규식이 아니라 subject.json 의 별칭표로 맞춘다.
        """
        aliases = cand.extra.get("_aliases") or []
        area = cand.extra.get("_area") or ""
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile as exc:
            raise RuntimeError(f"ZIP 이 아니거나 손상됨: {cand.url} ({exc})") from exc

        members = []
        for info in zf.infolist():
            name = info.filename
            if name.endswith(("/", "\\")) or not os.path.splitext(name)[1]:
                continue
            # UTF-8 플래그(0x800)가 꺼진 구 ZIP 만 되돌린다. 켜져 있으면 이미 제대로 읽힌 것이다.
            display = name if (info.flag_bits & 0x800) else fix_zip_name(name)
            if detect_kind(display, cand.extra.get("attachment", "")) != cand.kind:
                continue
            members.append((info, display))
        if not members:
            raise RuntimeError(f"ZIP 안에 {cand.kind} 파일이 없다: {cand.extra.get('attachment')}")

        hits = [(i, d, match_any_alias(d, aliases)) for i, d in members]
        matched = [(i, d, a) for i, d, a in hits if a]
        if len(matched) == 1:
            info, display, alias = matched[0]
        elif len(matched) > 1:
            # 별칭이 너무 헐거우면 여기 걸린다. 조용히 하나 고르면 엉뚱한 과목이 저장된다.
            info, display, alias = sorted(matched, key=lambda x: x[1])[0]
            self.note(cand.exam_id,
                      f"ZIP 안에서 별칭에 걸린 파일이 {len(matched)}개다 "
                      f"({', '.join(d for _i, d, _a in matched)}). '{display}' 를 골랐다 — "
                      f"subject.json 의 providers.kice.aliases 를 좁혀야 한다.", "warn")
        else:
            # 과목별로 안 쪼개고 영역 통짜로 올린 회차. 실제로 존재한다.
            area_wide = [(i, d) for i, d in members
                         if normalize_name(area) and normalize_name(area) in normalize_name(d)]
            if len(members) == 1:
                info, display = members[0]
            elif len(area_wide) == 1:
                info, display = area_wide[0]
            else:
                raise RuntimeError(
                    f"ZIP 안에서 과목을 못 찾았다. 별칭={aliases} / 내용="
                    f"{[d for _i, d in members][:12]}")
            alias = None
            self.note(cand.exam_id,
                      f"과목별 파일이 없어 영역 통짜 파일 '{display}' 을 저장했다. "
                      f"이후 단계에서 해당 과목 쪽수만 잘라내야 한다.", "warn")

        cand.extra["zip_member"] = display
        cand.extra["zip_alias"] = alias
        cand.extra["zip_members_total"] = len(members)
        return zf.read(info)

    # ------------------------------------------------------------------ probe

    def probe(self, area: str, year: int | None = None,
              exam: str | None = None, grade: int | None = None) -> list[dict]:
        """평가원 게시판 쪽 실측. 그 회차 게시물과 **첨부 파일명**을 그대로 보여준다.

        subject.json 의 providers.kice.aliases 를 채우려면 ZIP 안의 실제 파일명을 알아야 하는데,
        여기서는 게시물 첨부명까지만 보여준다(ZIP 을 내려받으면 십수 MB 라 probe 가 무거워진다).
        내부 파일명은 실제로 한 번 `gw download` 를 돌려 attention 메시지로 확인한다.
        """
        if not (year and exam):
            return []
        route = EXAM_ROUTE.get(exam)
        if not route:
            return [{"note": f"{exam} 은 평가원 게시판에 없다(교육청 주관)."}]
        board, month = route
        rows = []
        for candidate_area in resolve_area(area, year):
            posts = self._fetch_posts(board, year, month, candidate_area)
            for p in posts:
                rows.append({
                    "area_queried": candidate_area,
                    "board_seq": p["board_seq"],
                    "year": p["year"],
                    "title": p["title"],
                    "attachments": [f["filename"] for f in p["files"]],
                })
            if posts:
                break
        return rows
