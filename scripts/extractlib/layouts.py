# -*- coding: utf-8 -*-
"""판형 전략 테이블.

subject.layout 하나로 갈라진다. 코드가 과목 이름을 보고 분기하기 시작하면
이 도구는 곧바로 지구과학 전용으로 되돌아간다 (docs/CONTRACT.md 0절).

지금 실동작하는 것은 tamgu-1q1block 뿐이지만, 나머지 판형이 들어올 **자리**를
비워 둔다. 자리를 비워 두는 방식이 중요하다 — 조용히 tamgu 로 떨어지면
국어 지문 묶음을 문항 하나로 잘라 놓고도 성공했다고 보고하게 된다.
그래서 미구현 판형은 명확히 NotImplementedError 로 멈추고 무엇을 해야 하는지 말한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import tamgu
from .tamgu import ParsedQuestion


@dataclass
class LayoutStrategy:
    """한 판형이 extract 에 제공해야 하는 것들."""

    name: str
    columns: int                       # 지면 단 수. OCR 읽기 순서 복원에 쓴다.
    choice_count: int                  # 선택지 개수 (0 이면 검사하지 않음)
    clean: Callable[[str, object], str]
    split: Callable[[str, int], dict]
    parse: Callable[[str], ParsedQuestion]
    points_from_marks: Callable[[dict, int, "int | None"], tuple]


def _tamgu_parse(block: str) -> ParsedQuestion:
    return tamgu.parse_question(block, choice_count=5)


TAMGU_1Q1BLOCK = LayoutStrategy(
    name="tamgu-1q1block",
    columns=2,
    choice_count=5,
    clean=tamgu.clean_text,
    split=tamgu.split_questions,
    parse=_tamgu_parse,
    points_from_marks=tamgu.points_from_marks,
)

# 아직 구현되지 않은 판형과, 구현할 사람이 알아야 할 것.
UNIMPLEMENTED = {
    "passage-group": (
        "지문 하나에 문항이 여러 개 달린다(국어·영어). "
        "문항 분리 전에 지문 묶음을 먼저 잘라야 하고, 같은 지문을 여러 문항이 "
        "공유하므로 items 에 지문 참조 필드가 필요하다. "
        "split() 이 dict[int,str] 이 아니라 (지문, [문항]) 을 돌려주는 형태가 될 것이다."
    ),
    "math-mixed": (
        "객관식과 단답형이 섞이고 수식이 벡터로 들어간다. "
        "단답형은 선택지가 없으므로 choice_count 검사를 꺼야 하고, "
        "정답이 1~5 가 아니라 정수 전체가 된다 — cross_check 의 값 범위 가정을 손봐야 한다."
    ),
}

STRATEGIES = {TAMGU_1Q1BLOCK.name: TAMGU_1Q1BLOCK}


def get_strategy(layout: str) -> LayoutStrategy:
    strategy = STRATEGIES.get(layout)
    if strategy is not None:
        return strategy
    hint = UNIMPLEMENTED.get(layout)
    if hint:
        raise NotImplementedError(
            f"판형 '{layout}' 의 추출 전략이 아직 없다.\n  {hint}\n"
            f"  구현 자리: scripts/extractlib/layouts.py 의 STRATEGIES 에 등록한다."
        )
    raise NotImplementedError(
        f"알 수 없는 판형 '{layout}'. 가능한 값: "
        f"{', '.join(sorted(set(STRATEGIES) | set(UNIMPLEMENTED)))}"
    )
