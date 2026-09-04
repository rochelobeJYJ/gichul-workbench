# -*- coding: utf-8 -*-
"""오래 걸리는 명령의 진행률. **그리는 코드는 이 파일 하나뿐이다.**

왜 한 곳인가: 진행률은 출력 계약(docs/CONTRACT.md 5절)을 깨기 가장 쉬운 물건이다.
모듈마다 각자 그리면 어느 하나가 stdout 으로 새거나, `--quiet` 를 빼먹거나,
파이프로 넘길 때 380줄짜리 로그를 남긴다. 규칙을 한 군데에 몰아 둔다.

지키는 것 넷:

1. **stdout 을 절대 건드리지 않는다.** stdout 은 리포트 경로와 한 줄 요약이 전부다(계약 5절).
   진행률은 stderr 로만 나간다.
2. **터미널이 아니면 아무것도 쓰지 않는다.** 판정은 `sys.stderr.isatty()` 이되
   **stdout 도 함께 본다** — `gw crop ... | cat` 처럼 stdout 만 파이프로 넘겨도
   사람이 화면을 보고 있지 않은 실행이기 때문이다(그 경우 stderr 는 여전히 tty 라
   stderr 만 보면 진행률이 계속 그려진다). 둘 중 하나라도 tty 가 아니면 끈다.
3. **한 줄을 덮어쓴다.** `\r` 로 되감고 남은 자리를 공백으로 지운다. 줄바꿈을 내보내지
   않으므로 380문항을 돌려도 로그가 한 줄이다. 끝나면 그 한 줄마저 지운다 —
   명령이 남기는 것은 리포트 요약뿐이어야 한다.
4. **모르는 것은 말하지 않는다.** 남은 시간은 표본이 충분할 때만 붙인다(아래 ETA_MIN_*).
   틀린 추정은 없느니만 못하다.

외부 라이브러리를 쓰지 않는다(tqdm 등). requirements 를 늘리지 않기 위해서다.

## 쓰는 법

    from common.progress import Progress, track

    # ① 반복문이 단순하면 track 하나로 끝난다.
    for exam_id in track(exams, "회차", args=args, label="detect"):
        ...

    # ② 진행 단위와 반복 단위가 다르면(회차를 돌며 문항을 센다) Progress 를 직접 연다.
    with Progress(total=380, unit="문항", args=args, label="crop") as bar:
        for exam_id in exams:
            bar.detail(exam_id)          # 지금 무엇을 하는 중인지
            for q in questions:
                ...
                bar.advance()

`args` 를 넘기면 `--quiet` 를 알아서 읽는다. 없으면 `quiet=` 로 직접 준다.

## 시험용 스위치

환경변수 `GW_PROGRESS=1` 이면 tty 가 아니어도 그리고, `GW_PROGRESS=0` 이면 무조건 끈다.
tty 를 만들 수 없는 자동 테스트에서 실제 출력 프레임을 확인하려고 둔 것이다.
사람이 쓰는 경로에는 영향을 주지 않는다(설정하지 않으면 자동 판정).
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import time
import unicodedata

# 다시 그리는 최소 간격(초). 380문항을 문항마다 그리면 터미널 I/O 가 실제 작업보다
# 비싸질 수 있다. 초당 12번이면 사람 눈에는 연속으로 보인다.
MIN_REDRAW_SEC = 0.08

# 남은 시간을 붙이기 위한 최소 조건.
# 총량이 작으면(예: 문서 6개) 항목마다 걸리는 시간이 제각각이라 추정이 거짓말이 된다.
ETA_MIN_TOTAL = 8      # 이보다 적은 항목이면 추정하지 않는다
ETA_MIN_DONE = 3       # 최소 3개는 끝나야 평균을 말할 수 있다
ETA_MIN_ELAPSED = 2.0  # 2초도 안 지났으면 표본이 아니다

BAR_WIDTH = 14
_FILL, _EMPTY = "█", "░"

# 진행률 막대 스택. 바깥 막대(문서)와 안쪽 막대(쪽)를 한 줄에 같이 그리기 위해 쌓는다.
# 줄이 하나뿐이라 그리는 주체도 하나여야 한다 — 그게 이 리스트다.
_stack: list["Progress"] = []
_last_width = 0          # 지울 때 필요한 직전 줄의 표시 폭
_last_line = ""          # 같은 문자열이면 다시 안 그린다(터미널 깜빡임·비용 방어)


# ── 표시 폭 ────────────────────────────────────────────────────────────────
def _dwidth(text: str) -> int:
    """터미널이 차지하는 칸 수. 한글·전각은 두 칸이다.

    len() 으로 자르면 '문항 137/380' 처럼 한글이 섞인 줄에서 폭을 절반으로 잘못 재고,
    그러면 지우다 만 글자가 다음 프레임에 남는다.
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _truncate(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if _dwidth(text) <= limit:
        return text
    out, w = [], 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > limit:
            break
        out.append(ch)
        w += cw
    return "".join(out)


def _human_sec(sec: float) -> str:
    sec = int(sec + 0.5)
    if sec < 60:
        return f"{sec}초"
    if sec < 3600:
        return f"{sec // 60}분 {sec % 60}초"
    return f"{sec // 3600}시간 {(sec % 3600) // 60}분"


# ── 켤지 말지 ──────────────────────────────────────────────────────────────
def enabled(quiet: bool = False, stream=None) -> bool:
    """이 실행에서 진행률을 그려도 되는가.

    `--quiet` 는 '요약을 삼키고 리포트 경로만 남긴다'는 뜻이므로(계약 6절)
    진행률도 당연히 삼킨다.
    """
    env = os.environ.get("GW_PROGRESS")
    if env in ("0", "no", "off", "false"):
        return False
    if quiet:
        return False
    if env in ("1", "yes", "on", "true"):
        return True
    out, err = sys.stdout, stream or sys.stderr
    if out is None or err is None:
        return False
    try:
        # stdout 까지 보는 이유는 모듈 설명 2번 참조(`| cat` 로 넘겨도 stderr 는 tty 다).
        return bool(err.isatty()) and bool(out.isatty())
    except (AttributeError, ValueError):
        return False  # 닫힌 스트림 등. 못 그리면 안 그린다.


# ── 본체 ───────────────────────────────────────────────────────────────────
class Progress:
    """한 줄짜리 진행률. `with` 로 쓰거나(권장) `open()` … `close()` 로 쓴다.

    단위(unit)는 **사용자가 이해하는 것**으로 준다 — '문항', '회차', '쪽', '파일'.
    내부 반복 횟수를 세면 숫자가 무엇을 뜻하는지 아무도 모른다.
    """

    def __init__(self, total: int | None, unit: str, *, label: str | None = None,
                 args=None, quiet: bool | None = None, stream=None):
        self.total = total if (total is None or total > 0) else 0
        self.unit = unit
        self.label = label
        self.done = 0
        self._detail = ""
        self._stream = stream or sys.stderr
        if quiet is None:
            quiet = bool(getattr(args, "quiet", False))
        self._on = enabled(quiet, self._stream)
        self._started = time.monotonic()
        self._last_draw = 0.0
        self._open = False

    @property
    def on(self) -> bool:
        """지금 그리는 중인가. 부르는 쪽이 **표시용 문자열을 만들기 전에** 물어보라고 둔다."""
        return self._on

    # -- 생명주기 --
    def __enter__(self) -> "Progress":
        if self._on:
            _stack.append(self)
            self._open = True
            self._draw(force=True)
        return self

    def open(self) -> "Progress":
        """`with` 를 쓸 수 없을 때 여는 형태. 반드시 close() 로 닫는다.

        이미 긴 반복문(갈래마다 early return 이 있는)을 진행률 하나 때문에 통째로 다시
        들여쓰면, diff 가 '로직을 고쳤는지 들여쓰기만 옮겼는지' 구분되지 않는다.
        진행률은 장식이므로 본문 모양을 바꾸지 않고 붙일 길을 열어 둔다.
        닫는 걸 잊어도 프로세스 종료 때 _cleanup 이 줄을 지운다(터미널은 지킨다).
        """
        return self.__enter__()

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if not self._open:
            return
        self._open = False
        if self in _stack:
            _stack.remove(self)
        if _stack:
            _redraw(force=True)   # 바깥 막대를 다시 보여준다
        else:
            _erase(self._stream)  # 마지막 막대가 닫히면 줄 자체를 지운다

    # -- 갱신 --
    def advance(self, n: int = 1, detail: str | None = None) -> None:
        self.done += n
        if not self._on:
            return
        if detail is not None:
            self._detail = detail
        self._draw()

    def detail(self, text: str | None) -> None:
        """지금 처리 중인 대상(회차 이름 등). 숫자만으로는 어디쯤인지 감이 안 온다."""
        if not self._on:
            return
        self._detail = text or ""
        self._draw()

    def wrap(self, iterable):
        """이미 열어 둔 막대로 반복문을 감싼다(`track` 과 같지만 막대를 새로 만들지 않는다).

        `continue` 가 여러 갈래인 반복문에 갈래마다 advance() 를 심으면 하나만 빠뜨려도
        숫자가 조용히 어긋난다. 반복 자체를 감싸면 어느 갈래로 나가든 한 번씩 오른다.
        """
        for obj in iterable:
            yield obj
            self.advance()

    def set_total(self, total: int) -> None:
        """총량을 나중에 알게 되는 경우(목록을 다 훑은 뒤)."""
        self.total = total
        if self._on:
            self._draw(force=True)

    # -- 그리기 --
    def _draw(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_draw) < MIN_REDRAW_SEC:
            return
        self._last_draw = now
        _redraw(force=force)

    # -- 조각 --
    def _segment(self, root: bool) -> str:
        if self.total:
            seg = f"{self.unit} {self.done}/{self.total}"
        else:
            seg = f"{self.unit} {self.done}"
        if root and self.total:
            pct = min(100, int(self.done * 100 / self.total))
            filled = min(BAR_WIDTH, int(BAR_WIDTH * self.done / self.total))
            seg += f" {pct:>3d}% {_FILL * filled}{_EMPTY * (BAR_WIDTH - filled)}"
        return seg

    def _eta(self) -> str:
        """남은 시간. 표본이 모자라면 **빈 문자열** — 모르면 말하지 않는다."""
        if not self.total or self.total < ETA_MIN_TOTAL or self.done < ETA_MIN_DONE:
            return ""
        elapsed = time.monotonic() - self._started
        if elapsed < ETA_MIN_ELAPSED or self.done >= self.total:
            return ""
        return f"남은 {_human_sec(elapsed / self.done * (self.total - self.done))}"


# ── 줄 하나를 그리고 지우는 곳(여기 말고는 stderr 에 쓰지 않는다) ────────────
def _erase(stream) -> None:
    global _last_width, _last_line
    if _last_width:
        try:
            stream.write("\r" + " " * _last_width + "\r")
            stream.flush()
        except (OSError, ValueError):
            pass
    _last_width, _last_line = 0, ""


def _redraw(force: bool = False) -> None:
    global _last_width, _last_line
    if not _stack:
        return
    root = _stack[0]
    stream = root._stream

    parts: list[str] = []
    if root.label:
        parts.append(f"[{root.label}]")
    parts.append(root._segment(True))
    for bar in _stack[1:]:
        parts.append(bar._segment(False))
    # 가장 안쪽에서 알려 준 대상이 지금 실제로 하는 일이다.
    detail = next((b._detail for b in reversed(_stack) if b._detail), "")
    if detail:
        parts.append(detail)
    eta = root._eta()   # 추정은 전체 작업을 아는 바깥 막대만 한다
    if eta:
        parts.append(eta)

    line = "  ".join(parts)
    width = max(20, shutil.get_terminal_size((80, 24)).columns - 1)
    line = _truncate(line, width)
    if line == _last_line and not force:
        return

    pad = max(0, _last_width - _dwidth(line))
    try:
        stream.write("\r" + line + " " * pad)
        stream.flush()
    except (OSError, ValueError):
        return
    _last_width, _last_line = _dwidth(line), line


# ── 편의 ───────────────────────────────────────────────────────────────────
def _cleanup() -> None:
    """열린 채로 끝났으면(예외로 빠져나갔거나 close() 를 잊었으면) 줄이라도 지운다.

    안 지우면 프롬프트가 진행률 줄 위에 겹쳐 찍힌다. 사용자 터미널이 망가지는 건
    진행률의 잘못이지 사용자의 잘못이 아니다.
    """
    if _stack:
        stream = _stack[0]._stream
        del _stack[:]
        _erase(stream)


atexit.register(_cleanup)


def track(iterable, unit: str, *, total: int | None = None, label: str | None = None,
          args=None, quiet: bool | None = None, detail=None):
    """반복 단위가 곧 진행 단위일 때 쓰는 한 줄짜리 감싸개.

    `detail` 은 항목 → 표시 문자열 함수다(예: `detail=lambda p: p.name`).
    현재 처리 중인 항목을 먼저 보여주고, 몸통이 끝난 뒤에 카운트를 올린다 —
    반대로 하면 '20/20' 을 띄워 놓고 마지막 항목을 한참 처리하는 그림이 된다.
    """
    if total is None:
        try:
            total = len(iterable)
        except TypeError:
            total = None
    with Progress(total, unit, label=label, args=args, quiet=quiet) as bar:
        for obj in iterable:
            # detail(obj) 를 먼저 부르지 않는다 — 꺼져 있을 때 문자열을 만들 이유가 없다.
            if detail is not None and bar.on:
                bar.detail(detail(obj))
            yield obj
            bar.advance()
