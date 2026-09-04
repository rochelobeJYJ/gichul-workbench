# -*- coding: utf-8 -*-
"""배점 한 곳 — 표기를 읽는 정규식과, 실수 배점을 비교하는 규칙.

**왜 모듈로 뺐나.** 같은 정규식이 세 곳에 각자 복사돼 있었고
(extractlib/tamgu.py, scripts/validate.py, scripts/providers/ebsi.py)
그중 두 곳이 **한 자리 숫자만 잡는 같은 결함**을 갖고 있었다.
복사본은 고쳐도 같이 안 고쳐진다 — 실제로 그렇게 됐다.

막으려는 조용한 실패 (실측, 통합과학·통합사회 2025 6·9월 + 2026 3·6·9월):
25문항 판형의 배점은 `[1.5점]` `[2점]` `[2.5점]` 세 계단인데 옛 정규식 `\\[\\s*(\\d)\\s*점\\s*\\]`
은 한 자리만 잡아 소수 표기 16개를 **못 본 척**했다. 남은 9개(`[2점]`)만 표기로 인정하고
나머지 16문항을 `(50-18)/16 = 2` 로 역산해 채우면 합이 정확히 50, 문항 수도 25 —
**모든 불변식을 통과한다.** 4회차 100문항 중 64문항이 그렇게 조용히 2점이 됐다.

값이 실수가 되면 합계 비교에 부동소수 오차가 끼어든다. 비교는 전부 여기 `points_equal`
한 곳을 거친다. 근거는 아래 POINT_EPS 주석에 적었다.
"""
from __future__ import annotations

import re

# 발문의 배점 표기. 소수점 이하를 받는다.
#   실측 표기 형태 — `[3점]`(20문항 판형) / `[1.5점]` `[2점]` `[2.5점]`(25문항 판형).
#   같은 회차 안에서 `[2점]`과 `[2.0점]`이 섞여 나온다(2025 고1 9월 통합과학은 9문항 전부 `[2.0점]`,
#   같은 달 통합사회는 `[2점]`). 그래서 소수부는 있어도 되고 없어도 되는 것으로 잡는다.
POINT_MARK_RE = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*점\s*\]")

# 표의 한 칸이 통째로 배점인 자리(EBSi 오답률 표의 '배점' 열)에서 쓴다.
# 여기서는 '점' 글자가 없다 — 열 이름이 이미 배점이라 값만 온다.
POINT_CELL_RE = re.compile(r"\d+(?:\.\d+)?")

# 배점 격자. 실측된 계단은 1.5 / 2 / 2.5 / 3 으로 전부 0.5 의 배수다.
# 역산한 기본 배점이 이 격자에 안 떨어지면 표기를 잘못 읽은 것으로 본다.
POINT_GRID = 0.5

# 합계 비교 허용 오차. **왜 이 값인가** —
# 지금 실재하는 배점(0.5의 배수)은 이진 부동소수로 오차 없이 표현되므로 25개를 더해도
# 50.0 이 정확히 나온다. 그래도 == 대신 허용오차를 쓰는 이유는, 앞으로 0.5 격자에서
# 벗어난 계단(예: 1/3점)이 들어왔을 때 `!=` 한 줄이 **멀쩡한 회차를 error 로 만들기** 때문이다.
# Decimal 을 안 쓴 이유: items/<qid>.json 은 JSON 숫자로 저장되고 리포트도 JSON 이라
# 어차피 float 로 왕복한다. 왕복하는 값에 Decimal 을 쓰면 정확성은 경계에서만 살고
# 코드는 두 표현 사이를 계속 오가게 된다.
# 1e-6 은 배점 격자(0.5)의 50만분의 1이라 '진짜 다른 값'과 절대 겹치지 않는다.
POINT_EPS = 1e-6


def normalize_points(value):
    """정수로 떨어지는 배점은 **int 로** 돌려준다. 아니면 float.

    JSON 에 `3` 이 아니라 `3.0` 으로 적히면 그 자체가 회귀다 — 이미 확정된
    items/<qid>.json 380개의 배점 표기가 전부 바뀌고, 파일 해시로 무변화를 확인하는
    검증이 통째로 무의미해진다. 읽는 쪽에서는 3 == 3.0 이라 아무 신호도 안 뜬다.
    """
    if value is None:
        return None
    number = float(value)
    if abs(number - round(number)) <= POINT_EPS:
        return int(round(number))
    return number


def points_equal(left, right) -> bool:
    """배점(또는 배점 합) 두 값이 같은가. 실수 오차를 허용한다."""
    if left is None or right is None:
        return left is right
    return abs(float(left) - float(right)) <= POINT_EPS


def on_point_grid(value) -> bool:
    """배점 격자(0.5의 배수) 위의 값인가."""
    if value is None:
        return False
    scaled = float(value) / POINT_GRID
    return abs(scaled - round(scaled)) <= POINT_EPS


def read_point_mark(text: str):
    """문자열에서 `[N점]` / `[N.N점]` 표기 하나를 읽는다. 없으면 None."""
    match = POINT_MARK_RE.search(text or "")
    return normalize_points(match.group(1)) if match else None


def read_point_cell(cell: str):
    """표의 배점 칸 하나를 읽는다. 배점으로 볼 수 없으면 None.

    상한 10 은 '배점 칸이 아닌 것'을 거르기 위한 것이다 — 오답률 표는 열 구성이
    문마다 달라서(providers/ebsi.py parse_rate_rows 주석) 자리로 잡은 칸에
    엉뚱한 수(순위·오답률)가 들어올 수 있다. 한 문항이 10점인 시험은 없다.
    """
    token = (cell or "").strip()
    if not POINT_CELL_RE.fullmatch(token):
        return None
    value = float(token)
    if not 0 < value <= 10:
        return None
    return normalize_points(value)
