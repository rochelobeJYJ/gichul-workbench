# -*- coding: utf-8 -*-
"""윈도우 콘솔 인코딩 방어.

한국어 윈도우의 기본 콘솔 코드페이지는 cp949 라서, 리포트에 흔히 쓰는
em dash(—)나 로마숫자(Ⅱ) 하나 때문에 UnicodeEncodeError 로 프로세스가 죽는다.
실제로 bootstrap 첫 실행에서 이걸로 죽었다. 모든 모듈에 걸리는 문제라 공통층에서 막는다.

common 을 임포트하면 자동으로 적용된다(임포트 부작용이지만, 대안이
모든 진입점에서 호출을 잊지 않는 것뿐이라 이쪽이 안전하다).
"""
from __future__ import annotations

import sys

_done = False


def setup() -> None:
    global _done
    if _done:
        return
    _done = True
    for stream in (sys.stdout, sys.stderr):
        try:
            # 콘솔이 못 그리는 글자는 대체 문자로 흘려보낸다. 죽는 것보다 낫다.
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # 파이프로 리다이렉트된 경우 등. 무시해도 된다.
