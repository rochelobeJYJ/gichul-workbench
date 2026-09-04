# -*- coding: utf-8 -*-
"""식별자 생성의 유일한 출처. exam_id / qid 는 반드시 여기를 거친다.

과목·시험 종류가 늘어나도 규칙이 흩어지지 않도록 한 곳에 모아둔다.
docs/CONTRACT.md 2절 참조.
"""
from __future__ import annotations

import re

# 시험 종류 정규화 표. 입력이 무엇이든 오른쪽 값으로 통일한다.
#
# 학평(전국연합학력평가) 종류가 여덟인 이유 — **주관 시·도교육청마다 시행 시기가 다르고,
# 학년마다 치르는 회차 자체가 다르기 때문이다.** 2026-09-04 EBSi 목록 실측:
#   고3(D300) : 3월(서울) · 4월/5월(경기) · 7월(인천) · 10월(서울)   [+ 평가원 6·9월 모평, 수능]
#   고2(D200) : 3월(서울) · 6월(부산) · 9월(인천) · 10월/11월(경기)
#   고1(D100) : 고2와 같은 네 회차 (다만 과목이 통합사회·통합과학뿐)
# 경기 주관 회차는 이름이 해마다 옮겨다닌다 — 고3은 2024년까지 '4월 학평', 2025·2026년은
# '5월 학평'이고, 고2는 2023년 '11월 학평', 2024·2025년은 '10월 학평'이다. 그래서 4·5월과
# 10·11월이 **둘 다** 있어야 한다. 하나만 두면 그 해 회차가 조회 단계에서 통째로 사라진다.
# (실측 사고: '5월학평'이 없어 2025·2026 고3 경기 회차를 계속 건너뛰고 있었다.)
# '9월학평'은 고2·고1 인천 주관 회차다. 같은 9월이라도 고3의 '9월모평'(평가원)과는 다른 시험이다.
EXAM_ALIASES = {
    "수능": "수능", "대학수학능력시험": "수능", "csat": "수능", "11": "수능",
    "6월모평": "6월모평", "6월 모의평가": "6월모평", "6": "6월모평", "06": "6월모평",
    "9월모평": "9월모평", "9월 모의평가": "9월모평", "9": "9월모평", "09": "9월모평",
    "3월학평": "3월학평", "4월학평": "4월학평", "5월학평": "5월학평", "6월학평": "6월학평",
    "7월학평": "7월학평", "9월학평": "9월학평", "10월학평": "10월학평", "11월학평": "11월학평",
}

# 학년이 exam_id 에 들어가야 하는 시험(전국연합학력평가). 같은 달에 학년별 시험이 따로 있다.
# 예: 2025-03-26 하루에 고1·고2·고3 3월학평이 함께 치러진다 → 2025_고3_3월학평 / 2025_고2_3월학평.
GRADE_BEARING = {"3월학평", "4월학평", "5월학평", "6월학평",
                 "7월학평", "9월학평", "10월학평", "11월학평"}

_QID_RE = re.compile(r"^(?P<exam>.+)_(?P<num>\d{2})$")


def normalize_exam(exam: str) -> str:
    """'6월 모의평가', '06', '6월모평' → '6월모평'."""
    key = str(exam).strip()
    if key in EXAM_ALIASES:
        return EXAM_ALIASES[key]
    key2 = key.replace(" ", "")
    if key2 in EXAM_ALIASES:
        return EXAM_ALIASES[key2]
    raise ValueError(f"알 수 없는 시험 종류: {exam!r}")


def make_exam_id(year: int | str, exam: str, grade: int | str | None = None) -> str:
    """`2024_수능`, `2025_고2_3월학평` 을 만든다.

    학평은 같은 달에 고1·고2·고3 시험이 따로 있으므로 학년이 반드시 들어간다.
    """
    kind = normalize_exam(exam)
    year = int(year)
    if kind in GRADE_BEARING:
        if grade is None:
            raise ValueError(f"{kind} 은 --grade 가 필요하다")
        return f"{year}_고{int(grade)}_{kind}"
    return f"{year}_{kind}"


def make_qid(exam_id: str, number: int | str) -> str:
    """`2024_수능` + 7 → `2024_수능_07`."""
    return f"{exam_id}_{int(number):02d}"


def split_qid(qid: str) -> tuple[str, int]:
    """`2024_수능_07` → ('2024_수능', 7)."""
    m = _QID_RE.match(qid)
    if not m:
        raise ValueError(f"qid 형식이 아니다: {qid!r}")
    return m.group("exam"), int(m.group("num"))


def exam_sort_key(exam_id: str) -> tuple:
    """시간순 정렬용 키. 학년도 → 시행 순서.

    한 해 안의 시행 순서는 3월 → 4월 → 5월 → 6월 → 7월 → 9월 → 10월 → 수능 → 11월.
    같은 달에 모평과 학평이 함께 있으면 평가원(모평)을 앞에 둔다 — 고3 6·9월이 그렇다.

    **11월학평이 수능보다 뒤인 것은 오타가 아니다.** 고2 경기 주관 회차이고
    실제 시행일이 수능 뒤다(실측: 2023-12-19 시행, EBSi 제목은 '고2 11월 학평(경기)').
    """
    order = {"3월학평": 1, "4월학평": 2, "5월학평": 3,
             "6월모평": 4, "6월학평": 5, "7월학평": 6,
             "9월모평": 7, "9월학평": 8, "10월학평": 9,
             "수능": 10, "11월학평": 11}
    parts = exam_id.split("_")
    year = int(parts[0])
    kind = parts[-1]
    grade = 0
    if len(parts) == 3 and parts[1].startswith("고"):
        grade = int(parts[1][1:])
    return (year, order.get(kind, 99), grade)
