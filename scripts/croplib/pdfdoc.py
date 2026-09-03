# -*- coding: utf-8 -*-
"""PDF 조회 캐시와 판면(본문 영역) 기하 계산.

크롭은 같은 페이지의 워드·도형을 수십 번 다시 읽는다. 원본 구현
(CSAT_WIKI/wiki_earth2/build_question_snapshots.py)은 모듈 전역 dict 에
`id(page.parent)` 를 키로 캐시했는데, 문서를 닫고 다음 회차를 열면 CPython 이
같은 주소를 재사용해 **다른 PDF 의 워드가 그대로 살아 있는** 사고가 날 수 있다
(원본은 회차마다 수동으로 캐시를 clear() 해서 피했다). 여기서는 캐시를 문서
객체에 묶어 그 위험 자체를 없앴다.
"""
from __future__ import annotations

from pathlib import Path

import fitz

# 워드를 같은 줄로 묶을 y 허용 오차(pt). 본문 행간이 12pt 안팎이라 4pt 면 충분하다.
LINE_TOL_PT = 4.0

# 판면 상단 괘선 탐지
HEADER_ZONE_FRAC = 0.30   # 페이지 상단 30% 안에서만 찾는다
HEADER_RULE_MAX_H = 3.0   # '선'으로 볼 최대 두께(pt)
HEADER_RULE_MIN_W_FRAC = 0.6  # 페이지 폭의 60% 이상 = 전폭 괘선
HEADER_PAD_PT = 3.0       # 괘선 아래 여유
HEADER_FALLBACK_PT = 142.0  # 괘선을 못 찾았을 때의 상단 한계(실행 헤더 y≈111~130 아래)


class Doc:
    """한 회차 문제지 PDF. with 문으로 쓰면 닫힌다."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.doc = fitz.open(str(path))
        self._words: dict[int, list] = {}
        self._lines: dict[tuple, list] = {}
        self._hrules: dict[int, list] = {}
        self._hdr: dict[int, float] = {}
        self._body: dict[int, tuple[float, float]] = {}

    # --- 수명 ---
    def __enter__(self) -> "Doc":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.doc.close()
        except Exception:
            pass

    def __len__(self) -> int:
        return len(self.doc)

    def page(self, pidx: int):
        return self.doc[pidx]

    # --- 텍스트 ---
    def words(self, pidx: int) -> list:
        if pidx not in self._words:
            self._words[pidx] = self.doc[pidx].get_text("words")
        return self._words[pidx]

    def has_text_layer(self) -> bool:
        """어느 페이지에든 워드가 하나라도 있으면 텍스트 레이어가 있다고 본다.

        2025학년도 수능 지구과학Ⅱ 문제지는 글자가 전부 벡터 도형으로 아웃라인화되어
        있어 워드가 0개다(페이지당 도형 1100개 이상). 이 경우 문항 번호를 텍스트로
        찾을 수 없어 vision 경로로 빠진다.
        """
        return any(self.words(i) for i in range(len(self)))

    def lines(self, pidx: int, x0: float | None = None, x1: float | None = None) -> list[dict]:
        """워드를 y 기준으로 묶은 텍스트 라인 [{x0,x1,y0,y1,text}].

        x0/x1 을 주면 그 컬럼에 걸치는 워드만 쓴다. 2단 조판에서는 좌·우 컬럼의
        글자가 같은 y 를 공유하므로, 컬럼을 나누지 않으면 서로 다른 단의 문장이
        한 줄로 합쳐져 오탐이 난다(좌단 본문 + 우단 '* 확인 사항' 이 한 줄이 되어
        본문 한가운데를 꼬리로 오인해 잘라 버린 사고가 있었다).
        """
        key = (pidx, x0, x1)
        if key in self._lines:
            return self._lines[key]
        ws = self.words(pidx)
        if x0 is not None:
            ws = [w for w in ws if w[2] > x0 and w[0] < x1]
        ws = sorted(ws, key=lambda w: (round(w[1], 1), w[0]))
        groups: list[list] = []
        cur: list = []
        for w in ws:
            if cur and w[1] - cur[0][1] > LINE_TOL_PT:
                groups.append(cur)
                cur = []
            cur.append(w)
        if cur:
            groups.append(cur)
        out = []
        for g in groups:
            g.sort(key=lambda w: w[0])
            out.append({
                "x0": min(w[0] for w in g),
                "x1": max(w[2] for w in g),
                "y0": min(w[1] for w in g),
                "y1": max(w[3] for w in g),
                "text": " ".join(w[4] for w in g),
            })
        self._lines[key] = out
        return out

    # --- 도형 ---
    def hrules(self, pidx: int) -> list[tuple[float, float, float]]:
        """가로 괘선 [(x0, x1, y)] — 두께 2pt 이하, 폭 20pt 이상."""
        if pidx not in self._hrules:
            out = []
            for d in self.doc[pidx].get_drawings():
                r = d["rect"]
                if r.height <= 2.0 and r.width >= 20.0:
                    out.append((r.x0, r.x1, r.y0))
            self._hrules[pidx] = out
        return self._hrules[pidx]

    def _wide_top_rules(self, pidx: int) -> list:
        page = self.doc[pidx]
        limit = page.rect.height * HEADER_ZONE_FRAC
        out = []
        for d in page.get_drawings():
            r = d["rect"]
            if (r.height <= HEADER_RULE_MAX_H
                    and r.width > page.rect.width * HEADER_RULE_MIN_W_FRAC
                    and r.y0 < limit):
                out.append(r)
        return out

    def header_bottom(self, pidx: int) -> float:
        """머리글 아래, 본문 컬럼이 시작하는 y.

        페이지 상단 30% 안의 전폭 가로 괘선 중 가장 아래 것을 경계로 본다.
        첫 장은 표제·수험번호란 밑의 괘선(y≈247), 나머지 장은 판면 상단 괘선(y≈147).
        """
        if pidx not in self._hdr:
            best = HEADER_FALLBACK_PT
            for r in self._wide_top_rules(pidx):
                best = max(best, r.y0 + HEADER_PAD_PT)
            self._hdr[pidx] = best
        return self._hdr[pidx]

    def body_x(self, pidx: int) -> tuple[float, float]:
        """판면(본문 조판 영역)의 좌·우 끝 x.

        **오른쪽 여백 세로쓰기 과목명 대응.** 마지막 컬럼의 오른쪽 경계를
        '페이지 폭 - 여유' 로 잡으면, 바깥 여백에 인쇄된 과목명 세로쓰기가
        크롭 안으로 들어온다. 판면 상단 전폭 괘선은 정확히 판면 폭만큼 그어져
        있으므로(실측 A3 문제지 88.0 ~ 754.5pt) 이것을 판면 폭의 근거로 쓴다.
        과목 이름 키워드로 사이드바를 거르는 방법(CSAT_Clipper 방식)은 쓰지
        않았다 — 과목명을 코드에 박는 순간 이 저장소의 제1원칙이 깨진다.

        괘선이 없는 판형을 대비해 워드/도형 잉크 범위로 물러난다.
        """
        if pidx in self._body:
            return self._body[pidx]
        page = self.doc[pidx]
        rules = self._wide_top_rules(pidx)
        if rules:
            x0 = min(r.x0 for r in rules)
            x1 = max(r.x1 for r in rules)
        else:
            xs = [(w[0], w[2]) for w in self.words(pidx)]
            if xs:
                x0 = min(a for a, _ in xs)
                x1 = max(b for _, b in xs)
            else:
                x0, x1 = page.rect.x0, page.rect.x1
        self._body[pidx] = (x0, x1)
        return self._body[pidx]
