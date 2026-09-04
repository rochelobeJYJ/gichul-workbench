# -*- coding: utf-8 -*-
"""`gw quickstart` — 한 명령으로 첫 학습지까지.

왜 이 명령이 있는가
-------------------
README 의 '처음 써보기' 는 명령이 여덟에서 열둘이다. 이 도구를 쓸 사람은 고등학교
교사이고, 터미널을 처음 여는 사람일 수 있다. 명령 열둘을 옮겨 적는 동안 오타가 한 번만
나도 거기서 끝난다. LLM 쪽도 사정이 같다 — 왕복 열두 번이 그대로 비용이다.
그래서 **이미 있는 명령을 순서대로 부르는 껍데기**를 하나 둔다. 새 기능은 없다.
크롭 규칙도, 분류 규칙도 여기에는 한 줄도 없다.

두 얼굴
-------
* **사람이 터미널에서** — 인자가 없으면 묻는다(번호로 고르게). 끝나면 브라우저를 연다.
* **LLM 이 부를 때** — `--subject` 등을 받으면 아무것도 묻지 않는다. 터미널이 아니면
  (파이프·리다이렉트) 애초에 물을 수 없으므로, 모자란 인자는 물음이 아니라 **error** 다.
  브라우저도 열지 않는다 — LLM 세션에서 창이 뜨면 방해다(`--open` 으로만 연다).

판정 기준은 하나다: **stdin 이 터미널인가.** `--yes` 를 주면 터미널이어도 묻지 않는다.

리포트 하나로 끝낸다
--------------------
`reports/quickstart.json` 에 전 단계 요약(단계별 ok·counts·리포트 경로·attention)을
담는다. LLM 이 그 파일 하나만 읽고 다음 행동을 정할 수 있어야 한다 — 그게 왕복 열둘을
하나로 만드는 지점이다. 단계별 원본 리포트(`reports/download.json` …)는 그대로 남는다.

실패했을 때가 더 중요하다
-------------------------
컴맹은 실패 메시지에서 그만둔다. 멈춘 자리와 **다음에 무엇을 하면 되는지**를 한국어
한 문장으로 말하고, 거기까지의 산출물은 그대로 둔다. 다시 돌리면 이미 받은 것·이미
잘라낸 것은 건너뛴다(각 명령이 `--force` 없이는 기존 산출물을 건드리지 않는다).

정하고 넘어간 것들 — 왜 그런지
------------------------------
* **rates(오답률)는 기본 꺼짐.** 학습지 한 장에 필요 없고, 회차마다 목록 조회 + 표 조회로
  네트워크 왕복이 더 붙는다. 첫 실행이 남의 서버 사정으로 멈출 자리를 늘리지 않는다.
  (덤으로 정답 4번째 검증축이 되지만, 그건 첫 학습지 다음의 일이다.) `--rates` 로 켠다.
* **validate 가 실패해도 멈추지 않는다.** validate 는 문지기가 아니라 검사다. 보정 전
  과목이나 옛 회차에서는 error 가 정상적으로 난다. 거기서 멈추면 만들 수 있는 학습지를
  못 만든다. 대신 몇 건인지와 리포트 경로를 알린다.
* **classify 가 auto=0 이어도 멈추지 않는다.** 보정 안 된 과목의 정상 동작이고,
  `build` 는 분류를 요구하지 않는다(회차별 탭). 그 사실을 사용자에게 한 줄로 알린다.
* **map 은 mapping.json 이 있는 과목에서만 돈다.** 없는 과목에서는 건너뛴다.
* 과목별 기본 시험(수능이냐 학평이냐)은 **subject.json 의 providers 에서 읽는다.**
  코드가 과목 이름으로 분기하면 안 된다(CONTRACT 0절). 고3 목록 `subject_id` 가 있으면
  수능·모평이고, 없고 `national` 에 학년별 id 만 있으면 그 학년의 학평이다.
  이 갈래가 없으면 통합과학·통합사회 사용자는 '0건 받음'만 보고 그만둔다.

하위 명령을 부르는 방식
-----------------------
`subprocess` 가 아니라 **gw.py 와 똑같은 디스패치**(모듈 import → `register` → `run`)를
쓴다. 인터프리터를 아홉 번 띄우지 않아 빠르고, 무엇보다 개별 명령을 직접 돌린 것과
결과가 같다는 것이 구조로 보장된다. 하위 명령의 stdout(한 줄 요약)만 삼키고 stderr 의
진행률은 그대로 흘려보낸다 — 진행률은 `common/progress.py` 것을 그대로 쓴다.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import sys
import time
import unicodedata
import webbrowser
from datetime import date
from pathlib import Path

from common import Report, Space, all_subjects, load_subject, normalize_exam
from common.ids import GRADE_BEARING, exam_sort_key

# 사람이 읽는 단계 번호. 최대 9단계라 원문자로 충분하다.
CIRCLED = "①②③④⑤⑥⑦⑧⑨"

# 기본 시험 묶음. 어느 쪽을 쓸지는 subject.json 의 providers 가 정한다(모듈 머리말 참조).
SUSU_EXAMS = "수능,6월모평,9월모평"          # 고3 목록에 있는 과목
NATIONAL_EXAMS = "3월학평,6월학평,9월학평"    # 학평에만 있는 과목(통합과목)

# 멈춘 자리별 한 문장. 컴맹은 여기서 그만두거나 여기서 다시 시작한다.
ADVICE = {
    "download": "기출을 한 건도 받지 못했습니다. 인터넷 연결을 확인하시고, "
                "연도를 최근으로(예: --years {years_hint}) 바꿔 다시 실행해 보세요.",
    "detect": "받아 둔 파일이 몇 학년도 무슨 시험인지 알아내지 못했습니다. "
              "sources 폴더를 지우고 다시 실행하면 새로 받습니다.",
    "crop": "문항을 잘라내지 못했습니다. 이 과목에서 처음 보는 판형일 수 있습니다. "
            "docs/PITFALLS.md 를 보시거나 이슈로 알려 주세요.",
    "build": "학습지 파일을 만들지 못했습니다. 아래 리포트의 attention 을 봐 주세요.",
}


# ══════════════════════════════════════════════════════════════════════════
# 출력 도우미
# ══════════════════════════════════════════════════════════════════════════

class Say:
    """`--quiet` 하나로 사람용 출력을 통째로 끈다(CONTRACT 6절).

    리포트 파일은 어느 쪽이든 항상 쓴다 — LLM 이 읽는 유일한 출력이므로(계약 5절).
    """

    def __init__(self, quiet: bool):
        self.quiet = bool(quiet)

    def __call__(self, text: str = "") -> None:
        if not self.quiet:
            print(text)


def _cell_width(text: str) -> int:
    """한글은 두 칸을 먹는다. 과목 목록을 열 맞춰 보여주려면 이게 필요하다.

    'A'(ambiguous)도 두 칸으로 센다 — 로마숫자 `Ⅰ`(U+2160)이 여기 속하는데, 한국어
    글꼴이 걸린 콘솔에서는 실제로 두 칸을 차지한다. 과목 label 의 절반이 이 글자다.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in "WFA" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _cell_width(text))


class _Sink:
    """하위 명령의 stdout 을 삼키되 **isatty 는 진짜 stdout 의 것을 그대로 돌려준다.**

    io.StringIO 로 바꿔치기하면 `isatty()` 가 False 가 되고, 그러면
    `common/progress.enabled()` 가 진행률을 끈다(stdout 도 함께 보기 때문이다).
    실행 시간이 제일 긴 download·crop 에서 화면이 멎어 버리므로 이 껍데기가 필요하다.
    """

    def __init__(self, real):
        self._real = real
        self._buf = io.StringIO()

    def write(self, text):
        return self._buf.write(text)

    def flush(self):
        pass

    def writable(self):
        return True

    def isatty(self):
        try:
            return bool(self._real.isatty())
        except (AttributeError, ValueError):
            return False

    def fileno(self):
        # 진짜 fd 를 내주면 삼키려던 출력이 그대로 화면에 나간다.
        raise io.UnsupportedOperation("fileno")

    @property
    def encoding(self):
        return getattr(self._real, "encoding", "utf-8")

    def getvalue(self) -> str:
        return self._buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════
# 과목·연도·시험 정하기
# ══════════════════════════════════════════════════════════════════════════

def _norm(text: str) -> str:
    """과목 이름 대조용. NFKC 가 먼저, 접기가 나중(common/subjects.py 와 같은 이유)."""
    return "".join(unicodedata.normalize("NFKC", str(text)).split()).casefold()


def resolve_subject(token: str):
    """슬러그·과목명·별칭 중 무엇으로 불러도 찾는다.

    컴맹이 `--subject 지구과학2` 라고 적는 것은 오타가 아니라 자연스러운 일이다.
    별칭 목록은 코드가 아니라 subject.json(`providers.kice.aliases`)에 이미 있다.
    """
    try:
        return load_subject(token)
    except (FileNotFoundError, ValueError):
        pass
    want = _norm(token)
    for s in all_subjects():
        names = [s.slug, s.label]
        kice = s.provider("kice") or {}
        names += [a for a in (kice.get("aliases") or []) if isinstance(a, str)]
        if any(_norm(n) == want for n in names):
            return s
    raise FileNotFoundError(
        f"'{token}' 이라는 과목이 없습니다.\n"
        f"등록된 과목: " + ", ".join(f"{s.slug}({s.label})" for s in all_subjects()))


def default_years(today: date | None = None) -> str:
    """최근 3년. 수능·모평은 학년도라 시행연도보다 하나 크다.

    2025년 11월에 치른 것이 2026학년도 수능이므로, 12월이 지나야 학년도가 하나 오른다.
    학평은 달력연도지만 같은 범위로 충분하다(없는 회차는 각 명령이 missing 으로 알린다).
    """
    d = today or date.today()
    latest = d.year + (1 if d.month >= 12 else 0)
    return f"{latest - 2}-{latest}"


def default_exams(subject) -> tuple[str, int | None, str]:
    """이 과목이 실제로 치러지는 시험. **subject.json 만 보고 정한다.**

    반환: (시험 목록 문자열, 학년 또는 None, 왜 그렇게 정했는지)

    고3 목록의 `subject_id` 가 있으면 수능·모평이 있는 과목이다. 없고 `national` 에
    학년별 id 만 있으면 그 과목은 학평에만 존재한다(통합과학·통합사회가 실제로 그렇다).
    이 갈래가 없으면 그 과목 사용자는 '0건 받음' 만 보고 도구를 닫는다.
    """
    ebsi = subject.provider("ebsi") or {}
    if ebsi.get("subject_id"):
        return SUSU_EXAMS, None, "고3 목록에 있는 과목 — 수능·모의평가"
    national = ebsi.get("national") or {}
    grades = sorted(int(g) for g, v in national.items()
                    if g.isdigit() and isinstance(v, dict) and v.get("subject_id"))
    if grades:
        g = grades[0]
        return (NATIONAL_EXAMS, g,
                f"수능에 없는 과목이라 고{g} 전국연합학력평가로 받는다 "
                f"(subject.json 의 providers.ebsi.national)")
    # providers 가 비어 있는 과목. download 가 '실측해서 채워라' 로 안내하므로 그쪽에 맡긴다.
    return SUSU_EXAMS, None, "providers 정보가 없어 기본값을 쓴다 — download 리포트를 확인하라"


def parse_exams(value: str) -> list[str]:
    """쉼표 목록을 정규화한다. 여기서 걸러야 아홉 단계를 돌고 나서 틀린 것을 알지 않는다."""
    items = [p.strip() for p in str(value).split(",") if p.strip()]
    if not items:
        raise ValueError("--exams 가 비어 있습니다")
    return [normalize_exam(e) for e in items]


def check_years(value: str) -> None:
    """download 와 **같은 해석기**로 미리 검사한다. 문법을 두 곳에 두지 않는다."""
    import download  # 지연 임포트 — requests 등이 없으면 의존성 안내가 먼저 나가야 한다
    download.parse_years(value)


# ══════════════════════════════════════════════════════════════════════════
# 대화형 — 번호로만 고르게 한다
# ══════════════════════════════════════════════════════════════════════════

class NoAnswer(Exception):
    """물었는데 대답할 사람이 없다(입력이 끊겼다). 트레이스백 대신 한 줄 안내로 끝낸다."""


def _ask(prompt: str, default: str | None = None) -> str | None:
    try:
        raw = input(prompt)
    except EOFError:
        raise NoAnswer("입력이 끊겼습니다")
    raw = raw.strip()
    return raw or default


def ask_subject(say: Say):
    subjects = all_subjects()
    if not subjects:
        raise FileNotFoundError("등록된 과목이 없습니다. subjects/_template/ 를 복사해 시작하세요.")
    groups: dict[str, list] = {}
    for s in sorted(subjects, key=lambda s: s.label):
        # 슬러그순(chemistry-i…)이 아니라 과목명순이다. 교사는 슬러그를 모른다.
        groups.setdefault(s.area or "기타", []).append(s)

    say("어느 과목인가요?")
    numbered: list = []
    for area in sorted(groups):
        say(f"\n  [{area}]")
        row: list[str] = []
        for s in groups[area]:
            numbered.append(s)
            row.append(f"{len(numbered):2d}) {s.label}")
            if len(row) == 3:
                say("   " + "".join(_pad(c, 20) for c in row).rstrip())
                row = []
        if row:
            say("   " + "".join(_pad(c, 20) for c in row).rstrip())

    while True:
        answer = _ask("\n  번호를 적어 주세요 (또는 과목 이름): ")
        if not answer:
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(numbered):
            return numbered[int(answer) - 1]
        try:
            return resolve_subject(answer)
        except FileNotFoundError:
            say("  그런 번호나 과목이 없습니다. 위 목록의 번호를 적어 주세요.")


def ask_years(say: Say, suggested: str) -> str:
    lo = suggested.split("-")[0]
    hi = suggested.split("-")[-1]
    say(f"\n몇 년치를 받을까요?  (그냥 엔터를 치시면 {suggested} — 최근 3년)")
    say(f"  · 숫자 하나만 적으시면 그만큼의 최근 연도입니다. 예: 5 → 최근 5년")
    say(f"  · 연도로 적으셔도 됩니다. 예: {lo}-{hi} 또는 {hi}")
    while True:
        answer = _ask("  년치 또는 연도 [엔터=기본]: ", suggested)
        if answer is None:
            return suggested
        answer = answer.strip()
        # '3' 은 3년치다. 연도로 읽힐 수 있는 값(네 자리)과 겹치지 않는다.
        if answer.isdigit() and len(answer) <= 2 and 1 <= int(answer) <= 30:
            latest = int(hi)
            n = int(answer)
            answer = f"{latest - n + 1}-{latest}" if n > 1 else str(latest)
        try:
            check_years(answer)
            return answer
        except ValueError as exc:
            say(f"  연도를 알아듣지 못했습니다({exc}). {suggested} 처럼 적어 주세요.")


def ask_exams(say: Say, suggested: str, grade: int | None) -> tuple[str, int | None]:
    say(f"\n어떤 시험을 받을까요?")
    say(f"   1) {suggested}   (기본 — 그냥 엔터)")
    first = suggested.split(",")[0]
    say(f"   2) {first} 만")
    say(f"   3) 직접 적기")
    while True:
        answer = _ask("  번호 [엔터=1]: ", "1")
        if answer == "1":
            return suggested, grade
        if answer == "2":
            return first, grade
        if answer == "3":
            typed = _ask("  시험 이름을 쉼표로 적어 주세요 (예: 수능,6월모평): ")
            if not typed:
                continue
            try:
                exams = parse_exams(typed)
            except ValueError as exc:
                say(f"  {exc}")
                continue
            g = grade
            if any(e in GRADE_BEARING for e in exams) and g is None:
                g = ask_grade(say)
            return ",".join(exams), g
        say("  1, 2, 3 중에서 골라 주세요.")


def ask_grade(say: Say) -> int:
    say("\n학력평가는 같은 달에 학년별 시험이 따로 있습니다.")
    while True:
        answer = _ask("  몇 학년 시험인가요? (1/2/3): ")
        if answer in ("1", "2", "3"):
            return int(answer)
        say("  1, 2, 3 중에서 골라 주세요.")


# ══════════════════════════════════════════════════════════════════════════
# 하위 명령 호출 — gw.py 와 같은 디스패치
# ══════════════════════════════════════════════════════════════════════════

def invoke(command: str, argv: list[str]) -> tuple[int, str]:
    """`python scripts/gw.py <command> <argv>` 와 **같은 일**을 한 프로세스 안에서 한다.

    같은 경로(모듈 import → register → parse_args → run)를 그대로 밟기 때문에,
    quickstart 로 돈 결과가 개별 명령을 따로 돌린 결과와 달라질 여지가 없다.
    """
    import gw  # 명령 → 모듈 표는 gw.py 한 곳에만 있다. 여기서 복사하지 않는다.
    module_name = gw.COMMANDS[command][0]
    module = importlib.import_module(module_name)
    parser = argparse.ArgumentParser(prog=f"gw {command}", add_help=False)
    module.register(parser)
    args = parser.parse_args(argv)
    sink = _Sink(sys.stdout)
    try:
        with contextlib.redirect_stdout(sink):
            code = module.run(args)
    finally:
        captured = sink.getvalue()
    return int(code or 0), captured


def read_report(space: Space, step: str) -> dict:
    path = space.report(step)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# ══════════════════════════════════════════════════════════════════════════
# 공정 계획
# ══════════════════════════════════════════════════════════════════════════

def plan_steps(subject, opts, ws: list[str]) -> list[dict]:
    """부를 명령을 순서대로. 여기 있는 것은 전부 이미 존재하는 명령이다.

    `fatal` 은 '여기서 멈추면 학습지를 못 만든다'는 뜻이다. extract·classify·validate 는
    실패해도 학습지가 나온다 — 크롭 이미지가 학습지의 본체이기 때문이다.
    """
    slug = subject.slug
    grade = ["--grade", str(opts.grade)] if opts.grade else []
    steps = [
        {"key": "download", "report": "download", "title": "기출 내려받기", "fatal": True,
         "argv": ["--subject", slug, "--years", opts.years, "--exams", opts.exams,
                  "--kinds", opts.kinds] + grade + ws},
        {"key": "detect", "report": "detect", "title": "회차 정리", "fatal": True,
         "argv": ["--subject", slug] + ws},
        {"key": "crop", "report": "crop", "title": "문항 잘라내기", "fatal": True,
         "argv": ["--subject", slug] + ws},
        {"key": "extract", "report": "extract", "title": "정답·배점 읽기", "fatal": False,
         "argv": ["--subject", slug] + ws},
    ]
    if opts.rates:
        steps.append({"key": "rates", "report": "rates", "title": "오답률 가져오기",
                      "fatal": False, "argv": ["--subject", slug] + ws})
    steps.append({"key": "classify", "report": "classify", "title": "단원 나누기",
                  "fatal": False, "argv": ["--subject", slug] + ws})
    if subject.mapping_path.exists():
        # 매핑 데이터가 있는 과목(지구과학Ⅰ·Ⅱ·통합과학·통합사회)에서만. 없으면 부를 이유가 없다.
        steps.append({"key": "map", "report": "map", "title": "교육과정 매핑", "fatal": False,
                      "argv": ["--subject", slug, "--revision", "2022"] + ws})
    steps.append({"key": "validate", "report": "validate", "title": "확인", "fatal": False,
                  "argv": ["--subject", slug] + ws})
    # build 에 --force 를 주는 이유: 다시 돌렸을 때 "출력 파일이 이미 있다" 로 멈추면
    # 컴맹에게는 고장으로 보인다. 덮어써도 잃는 것이 없다(items 가 원천이다).
    steps.append({"key": "build", "report": "build", "title": "학습지 만들기", "fatal": True,
                  "argv": ["--subject", slug, "--force"] + ws})
    return steps


def step_ok(key: str, code: int, data: dict) -> tuple[bool, str]:
    """종료코드만으로는 부족한 자리를 메운다. (성공인가, 아니면 왜 아닌가)

    download 는 한 건도 못 받아도 종료코드가 0 이다(failed 가 아니라 missing 이라서).
    그대로 넘기면 detect 가 '파일이 없다' 로 실패하고, 사용자는 진짜 원인인
    '그 연도에는 그 시험이 없다' 를 영영 못 본다.
    """
    counts = data.get("counts") or {}
    if key == "download":
        got = counts.get("done", 0) + counts.get("skipped", 0)
        if got <= 0:
            return False, (f"받은 파일이 0개다 (못 찾음 {counts.get('missing', 0)}건, "
                           f"실패 {counts.get('failed', 0)}건)")
        return True, ""
    if key == "detect":
        if counts.get("done", 0) <= 0:
            return False, "회차를 하나도 인식하지 못했다"
        return True, ""
    if key == "crop":
        if code != 0:
            return False, "크롭이 실패로 끝났다"
        if counts.get("done", 0) + counts.get("skipped", 0) <= 0:
            return False, "잘라낸 문항이 0개다"
        return True, ""
    if key == "build":
        if code != 0 or not data.get("open"):
            return False, "학습지 HTML 이 만들어지지 않았다"
        return True, ""
    # validate 는 검사지 문지기가 아니다. classify 는 auto=0 이 정상 동작이다.
    return (code == 0), ("" if code == 0 else "종료코드 1")


# ══════════════════════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════════════════════

def _can_ask() -> bool:
    """물어도 되는 자리인가.

    stdin 만 보면 안 된다. `gw quickstart > log.txt` 처럼 **stdout 이 파일·파이프면
    사람이 화면을 보고 있지 않다** — 질문을 던져도 화면에 안 나오고 그대로 멎는다.
    common/progress.py 가 진행률을 켤지 정할 때 쓰는 판정과 같은 규칙이다.
    """
    for stream in (sys.stdin, sys.stdout):
        try:
            if stream is None or not stream.isatty():
                return False
        except (AttributeError, ValueError):
            return False
    return True


def register(parser) -> None:
    parser.add_argument("--subject", help="과목 슬러그·과목명·별칭 (예: earth-science-ii, 지구과학Ⅱ, 지구과학2)")
    parser.add_argument("--years", help="'2024-2026' 또는 '2025'. 생략하면 최근 3년")
    parser.add_argument("--exams", help=f"쉼표 구분. 생략하면 과목에 맞는 기본값 "
                                        f"(수능 과목 '{SUSU_EXAMS}', 학평 과목 '{NATIONAL_EXAMS}')")
    parser.add_argument("--grade", type=int, choices=[1, 2, 3],
                        help="학력평가일 때만. 같은 달에 학년별 시험이 따로 있다")
    parser.add_argument("--kinds", default="problem,answer,solution",
                        help="내려받을 자료 종류 (기본: 문제지·정답지·해설지)")
    parser.add_argument("--rates", action="store_true",
                        help="EBSi 오답률까지 수집한다 (네트워크가 더 필요해 기본은 꺼짐)")
    parser.add_argument("--open", dest="open_", action="store_true",
                        help="끝나면 브라우저로 연다 (대화형에서는 기본 켜짐)")
    parser.add_argument("--no-open", dest="no_open", action="store_true",
                        help="브라우저를 열지 않는다")
    parser.add_argument("--yes", action="store_true",
                        help="터미널이어도 묻지 않고 기본값으로 진행한다")
    parser.add_argument("--dry-run", action="store_true",
                        help="무엇을 할지 계획만 리포트로 남기고 아무것도 실행하지 않는다")
    parser.add_argument("--quiet", action="store_true", help="stdout 에 리포트 경로만 남긴다")
    parser.add_argument("--workspace", help="작업 공간 경로 직접 지정 (기본 workspace/<slug>)")


def run(args) -> int:
    say = Say(args.quiet)
    interactive = _can_ask() and not (args.yes or args.quiet)

    # ── 과목 ──────────────────────────────────────────────────────────────
    try:
        if args.subject:
            subject = resolve_subject(args.subject)
        elif interactive:
            say("\n  기출 학습지 만들기\n" + "  " + "─" * 44)
            subject = ask_subject(say)
        else:
            # 리포트를 남길 작업 공간조차 정할 수 없는 단계다. 형제 명령과 같은 한 줄로 끝낸다.
            print("[FAIL] quickstart: --subject 가 없습니다.")
            print("       예: python scripts/gw.py quickstart --subject earth-science-ii "
                  "--years 2024-2026 --exams 수능,6월모평,9월모평")
            print("       과목 목록: python scripts/gw.py subjects")
            return 2
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] quickstart: {exc}")
        return 2
    except NoAnswer:
        # 터미널인 줄 알고 물었는데 입력이 끊겼다. 트레이스백 대신 부를 명령을 알려준다.
        print("\n[FAIL] quickstart: 입력이 끊겨 과목을 고를 수 없습니다.")
        print("       인자로 주세요: python scripts/gw.py quickstart --subject earth-science-ii")
        return 2
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        return 130

    space = Space(subject.slug, getattr(args, "workspace", None)).ensure()
    report = Report("quickstart", subject.slug, space)

    def finish(ok: bool | None = None) -> int:
        path = report.write(ok=ok)
        data = report.to_dict(ok=ok)
        if args.quiet:
            print(path)
            return 0 if data["ok"] else 1
        say(f"\n  전체 요약: {path}")
        return 0 if data["ok"] else 1

    # ── 연도·시험 ─────────────────────────────────────────────────────────
    suggested_years = default_years()
    exams_default, grade_default, exams_why = default_exams(subject)
    try:
        if interactive:
            years = args.years or ask_years(say, suggested_years)
            if args.exams:
                exams, grade = args.exams, args.grade
            else:
                say(f"\n  ({subject.label}: {exams_why})")
                exams, grade = ask_exams(say, exams_default, args.grade or grade_default)
        else:
            years = args.years or suggested_years
            exams = args.exams or exams_default
            grade = args.grade or (grade_default if not args.exams else None)
    except NoAnswer:
        # 물을 수 없게 됐으면 기본값으로 간다. 여기까지 왔으면 과목은 이미 정해졌고,
        # 기본값이 무엇인지는 리포트에 그대로 적힌다(조용한 기본값이 아니다).
        years = args.years or suggested_years
        exams = args.exams or exams_default
        grade = args.grade or grade_default
        interactive = False
    except KeyboardInterrupt:
        print("\n중단했습니다. 받아 둔 것은 그대로 있습니다.")
        return 130

    def bail(ident: str, detail: str, human: str, next_cmd: str) -> int:
        """인자가 틀렸을 때 멈추는 유일한 자리.

        화면에는 **한국어 한 문장 + 그대로 복사해 쓸 명령**만 보이고, 파이썬 원문
        메시지(`invalid literal for int()...`)는 리포트에만 남긴다. 영어 한 줄이
        화면에 뜨면 컴맹은 거기서 그만둔다 — 이 도구가 지려는 바로 그 자리다.
        """
        report.note(ident, detail, "error")
        report.next = next_cmd
        say(f"\n  [멈춤] {human}")
        say(f"  이렇게 해 보세요:")
        say(f"    {next_cmd}")
        return finish(ok=False)

    # 인자 검사는 여덟 단계를 돌기 **전에** 한다. 다 돌고 나서 틀린 것을 알면 늦다.
    def _cmd(y: str, e: str, g: int | None = None) -> str:
        return (f"python scripts/gw.py quickstart --subject {subject.slug} "
                f"--years {y} --exams {e}" + (f" --grade {g}" if g else ""))

    try:
        exam_list = parse_exams(exams)
        exams = ",".join(exam_list)
    except ValueError as exc:
        return bail("--exams", str(exc),
                    f"'{exams}' 이라는 시험은 없습니다. 수능 · 6월모평 · 9월모평 · "
                    f"3월학평 처럼 적어 주세요.",
                    _cmd(years, exams_default, grade_default))

    if any(e in GRADE_BEARING for e in exam_list) and not grade:
        try:
            grade = ask_grade(say) if interactive else None
        except (NoAnswer, KeyboardInterrupt):
            grade = None
        if not grade:
            return bail("--grade", "학력평가는 같은 달에 학년별 시험이 따로 있어 "
                                   "--grade 1|2|3 이 필요하다",
                        "학력평가는 고1·고2·고3 시험이 따로 있습니다. "
                        "몇 학년 시험인지 알려 주세요.",
                        _cmd(years, exams, 3))

    try:
        check_years(years)
    except ValueError as exc:
        return bail("--years", str(exc),
                    f"'{years}' 를 연도로 읽지 못했습니다. "
                    f"{suggested_years} 처럼 적어 주세요(한 해만 받으시려면 "
                    f"{suggested_years.split('-')[-1]}).",
                    _cmd(suggested_years, exams, grade))
    except ImportError as exc:
        # requests 등이 없다. 아래 의존성 점검이 더 친절한 메시지를 내므로 그쪽으로 넘긴다.
        report.note("의존성", f"모듈을 불러오지 못했다: {exc}", "error")

    report.extra["subject"] = {"slug": subject.slug, "label": subject.label, "area": subject.area}
    report.extra["years"] = years
    report.extra["exams"] = exams
    if grade:
        report.extra["grade"] = grade
    report.extra["mode"] = "interactive" if interactive else "noninteractive"
    report.extra["exams_reason"] = exams_why if not args.exams else "사용자가 --exams 로 지정"
    report.extra["rates"] = {
        "ran": bool(args.rates),
        "why": ("--rates 로 켰다" if args.rates else
                "기본 꺼짐 — 학습지 한 장에는 필요 없고 회차마다 네트워크 왕복이 더 붙는다. "
                "필요하면 gw rates --subject <과목> 로 나중에 따로 받으면 된다"),
    }

    # ── 의존성 ────────────────────────────────────────────────────────────
    missing = _missing_required()
    if missing:
        report.note("의존성", f"필요한 파이썬 패키지가 없다: {', '.join(missing)}", "error")
        report.next = f"{sys.executable} -m pip install {' '.join(missing)}"
        say("\n  [멈춤] 준비물이 빠져 있습니다. 아래 한 줄을 복사해서 실행해 주세요.\n")
        say(f"    {sys.executable} -m pip install {' '.join(missing)}\n")
        say("  그 다음 이 명령을 다시 실행하시면 됩니다.")
        return finish(ok=False)

    if subject.is_experimental:
        report.note(subject.slug,
                    f"판형 '{subject.layout}' 은 아직 검증되지 않았다 — 결과가 이상할 수 있다 "
                    f"(docs/LAYOUTS.md)", "warn")

    ws = ["--workspace", str(args.workspace)] if args.workspace else []
    if args.quiet:
        ws = ws + ["--quiet"]
    steps = plan_steps(subject, _Opts(years, exams, grade, args.kinds, args.rates), ws)
    report.extra["steps_planned"] = [s["key"] for s in steps]

    if args.dry_run:
        report.count(steps=len(steps), done=0, failed=0, skipped=len(steps))
        report.extra["dry_run"] = True
        # 계획을 '부를 수 있는 명령'으로 적는다 — 사용자가 한 단계만 다시 돌리고 싶을 때
        # 그대로 복사해 쓰라고. step["key"] 는 gw 의 명령 이름과 같다.
        report.extra["plan"] = [
            {"step": s["key"],
             "command": f"python scripts/gw.py {s['key']} " + " ".join(s["argv"])}
            for s in steps]
        report.next = (f"python scripts/gw.py quickstart --subject {subject.slug} "
                       f"--years {years} --exams {exams}" + (f" --grade {grade}" if grade else ""))
        say(f"\n  계획만 세웠습니다(--dry-run). {len(steps)}단계.")
        for i, s in enumerate(steps):
            say(f"   {CIRCLED[i]} {s['title']}")
        return finish(ok=True)

    # ── 실행 ──────────────────────────────────────────────────────────────
    say("")
    say(f"  {subject.label} · {years} · {exams}"
        + (f" · 고{grade}" if grade else ""))
    say("  " + "─" * 44)

    results: list[dict] = []
    stopped_at: str | None = None
    stopped_title = ""
    n_done = n_failed = 0

    for i, step in enumerate(steps):
        say(f"\n  {CIRCLED[i]} {step['title']}")
        started = time.time()
        try:
            code, captured = invoke(step["key"], step["argv"])
            crashed = None
        except KeyboardInterrupt:
            say("\n  중단했습니다. 여기까지 만든 것은 그대로 있습니다.")
            report.note(step["key"], "사용자가 중단했다(Ctrl+C)", "warn")
            stopped_at, stopped_title = step["key"], f"{CIRCLED[i]} {step['title']}"
            break
        except Exception as exc:                                   # noqa: BLE001
            code, captured, crashed = 1, "", f"{type(exc).__name__}: {exc}"
        elapsed = round(time.time() - started, 1)

        data = read_report(space, step["report"])
        ok, why = step_ok(step["key"], code, data)
        if crashed:
            ok, why = False, crashed
        entry = {
            "step": step["key"], "ok": ok, "elapsed_sec": elapsed,
            "counts": data.get("counts") or {},
            "report": str(space.report(step["report"])),
            "next": data.get("next"),
        }
        # 단계별 attention 은 심각한 것 셋만 올린다. 아홉 단계를 다 담으면 30건 상한에 걸려
        # 정작 error 가 밀려난다(계약 5절).
        att = [a for a in (data.get("attention") or [])
               if a.get("severity") in ("error", "warn")][:3]
        if att:
            entry["attention"] = att
        if why:
            entry["why"] = why
        results.append(entry)

        say("     " + _summary_line(step["key"], ok, why, data, elapsed))
        _extra_hint(say, step["key"], data)

        if ok:
            n_done += 1
            continue

        n_failed += 1
        severity = "error" if step["fatal"] else "warn"
        report.note(step["key"], f"{step['title']} 실패 — {why or '리포트의 attention 참조'}",
                    severity)
        for a in att:
            report.note(f"{step['key']}:{a.get('id', '?')}", str(a.get("why", ""))[:300],
                        a.get("severity", "warn"))
        if step["fatal"]:
            stopped_at = step["key"]
            stopped_title = f"{CIRCLED[i]} {step['title']}"
            # 리포트조차 못 남기고 터진 경우에만 하위 명령의 stdout 을 보여준다.
            # 리포트가 있으면 그쪽이 훨씬 정확하고, 겹쳐 찍으면 '[OK]' 와 '멈춤' 이
            # 나란히 나와 무엇이 참인지 알 수 없게 된다(실측).
            if not data and captured:
                for line in captured.splitlines():
                    if line.strip():
                        say(f"     │ {line}")
            break

    report.extra["steps"] = results
    report.count(steps=len(steps), done=n_done, failed=n_failed,
                 skipped=len(steps) - len(results))

    # ── 산출물 ────────────────────────────────────────────────────────────
    outputs = _collect_outputs(space)
    report.extra["outputs"] = outputs
    for p in outputs.get("contact_sheets", []):
        report.artifact(p)
    if outputs.get("builder_html"):
        report.artifact(outputs["builder_html"])

    if stopped_at:
        failed_entry = next((r for r in results if r["step"] == stopped_at), {})
        if stopped_at == "download":
            # download 가 0건이면 원인은 둘뿐이다 — 그 연도에 그 시험이 없거나,
            # 네트워크다. download 자신의 next(`--provider ebsi 로 재시도`)는 앞의
            # 경우에 똑같이 실패한다. 최근 연도로 다시 도는 명령이 두 경우 모두에
            # 맞는 답이다(연도가 이미 최근이면 그대로 재시도가 된다).
            report.next = (f"python scripts/gw.py quickstart --subject {subject.slug} "
                           f"--years {suggested_years} --exams {exams}"
                           + (f" --grade {grade}" if grade else ""))
        else:
            report.next = failed_entry.get("next") or (
                f"python scripts/gw.py {stopped_at} --subject {subject.slug}")
        advice = ADVICE.get(stopped_at, "리포트의 attention 을 봐 주세요.").format(
            years_hint=suggested_years)
        say("")
        say("  " + "─" * 44)
        # 순서가 중요하다 — 할 일이 먼저, 사연은 나중이다. 컴맹은 첫 두 줄만 읽는다.
        say(f"  [멈춤] {stopped_title or stopped_at} 에서 멈췄습니다.")
        say(f"  {advice}")
        say(f"  다음에 이렇게 해 보세요:")
        say(f"    {report.next}")
        say(f"  여기까지 만든 것은 지워지지 않았습니다: {space.root}")
        if failed_entry.get("report"):
            say(f"\n  자세한 내용: {failed_entry['report']}")
        for a in (failed_entry.get("attention") or [])[:3]:
            say(f"    · {a.get('id', '')}: {str(a.get('why', ''))[:160]}")
        return finish(ok=False)

    # ── 성공 ──────────────────────────────────────────────────────────────
    build_data = read_report(space, "build")
    report.next = build_data.get("next")
    say("")
    say("  " + "─" * 44)
    sheets = outputs.get("contact_sheets") or []
    if sheets:
        say(f"  ✓ 확인해 주세요 → {sheets[-1]}")
        say(f"      한 장에 문항이 모여 있는 확인용 이미지입니다.")
        say(f"      ⑤번 선택지가 잘린 문항이 없는지만 봐 주세요 — 이것만은 기계가 못 잡습니다.")
    html = outputs.get("builder_html")
    if html:
        say(f"  ✓ 학습지     → {html}")

    want_open = args.open_ or (interactive and not args.no_open)
    if args.no_open:
        want_open = False
    opened = _open_files(say, [sheets[-1] if sheets else None, html], want_open)
    outputs["opened"] = opened
    if want_open and not opened:
        say("      브라우저가 자동으로 열리지 않았습니다. 위 파일을 더블클릭해 주세요.")
    elif opened:
        say("      브라우저를 열었습니다. 왼쪽에서 고르고 [문항집 만들기] 를 누르세요.")

    return finish(ok=not report.has_error)


class _Opts:
    """plan_steps 에 넘기는 값 묶음. argparse Namespace 를 그대로 넘기면
    어떤 필드를 쓰는지가 안 보인다."""

    def __init__(self, years, exams, grade, kinds, rates):
        self.years, self.exams, self.grade = years, exams, grade
        self.kinds, self.rates = kinds, rates


# ══════════════════════════════════════════════════════════════════════════
# 보조
# ══════════════════════════════════════════════════════════════════════════

def _missing_required() -> list[str]:
    """없으면 공정이 아예 안 도는 패키지. 목록은 bootstrap.py 한 곳에만 있다."""
    import importlib.util
    try:
        from bootstrap import REQUIRED
    except Exception:                                              # noqa: BLE001
        return []
    return [pkg for mod, pkg, _why, hard in REQUIRED
            if hard and importlib.util.find_spec(mod) is None]


def _summary_line(key: str, ok: bool, why: str, data: dict, elapsed: float) -> str:
    counts = data.get("counts") or {}
    mark = "✓" if ok else "✗"
    if key == "download":
        body = (f"{counts.get('done', 0)}개 받음"
                + (f" · 이미 있던 것 {counts.get('skipped', 0)}개" if counts.get("skipped") else "")
                + (f" · 못 찾음 {counts.get('missing', 0)}건" if counts.get("missing") else ""))
    elif key == "detect":
        body = f"{counts.get('done', 0)}회차"
    elif key == "crop":
        body = (f"{counts.get('done', 0)}문항"
                + (f" · 이미 있던 것 {counts.get('skipped', 0)}문항" if counts.get("skipped") else ""))
    elif key == "extract":
        body = f"{counts.get('done', 0)}문항"
    elif key == "rates":
        # 이 단계가 세는 것은 '오답률을 적어 넣은 문항 수'(written)다. EBSi 가 회차당
        # 상위 15문항만 공개하므로 20문항 중 15가 정상값이다(계약 4절).
        written, kept = counts.get("written", 0), counts.get("skipped", 0)
        body = f"{written}문항" if written or not kept else f"이미 받아 둠 {kept}문항"
    elif key == "classify":
        auto, queued = counts.get("auto", 0), counts.get("queued", 0)
        if auto == 0 and queued == 0 and counts.get("skipped"):
            body = f"이미 분류돼 있음 {counts['skipped']}문항"   # 다시 돌렸을 때
        else:
            body = f"자동 {auto} · 확인대기 {queued}"
    elif key == "map":
        body = f"{counts.get('applied', 0)}문항"
    elif key == "validate":
        n_err = sum(1 for a in (data.get("attention") or []) if a.get("severity") == "error")
        body = ("살펴볼 것 없음" if n_err == 0 else f"살펴볼 것 {n_err}건")
        mark = "✓" if n_err == 0 else "!"
    elif key == "build":
        body = f"{counts.get('total_questions', counts.get('items_included', 0))}문항"
    else:
        body = ""
    if not ok:
        # 실패했을 때 세부 숫자는 아래 멈춤 안내가 다시 말한다. 여기서 겹쳐 적으면
        # 한 줄이 두 문장이 되어 읽히지 않는다.
        body = why or body
    return f"{mark} {body}  ({elapsed}초)"


def _extra_hint(say: Say, key: str, data: dict) -> None:
    """한 줄 안내. 컴맹이 '고장 났나' 하고 멈출 자리에만 붙인다."""
    counts = data.get("counts") or {}
    if key == "classify" and counts.get("auto", 0) == 0 and counts.get("queued", 0) > 0:
        say("       이 과목은 아직 단원 자동분류가 보정되지 않았습니다. 고장이 아닙니다.")
        say("       학습지는 지금 만들어집니다 — 단원 대신 시험 회차로 고르시면 됩니다.")
    if key == "rates" and counts.get("written"):
        # 20문항 중 15개만 채워지는 것이 정상인데, 모르면 '5개를 놓쳤다'로 읽힌다.
        say("       EBSi 는 회차마다 어려운 상위 15문항만 공개합니다. 나머지가 비는 건 정상입니다.")
    if key == "validate":
        n_err = sum(1 for a in (data.get("attention") or []) if a.get("severity") == "error")
        if n_err:
            say("       학습지 제작은 계속됩니다. 위 리포트는 나중에 보셔도 됩니다.")


def _collect_outputs(space: Space) -> dict:
    """만들어진 것들의 **절대 경로**. 컴맹은 상대 경로를 어디서 세는지 모른다."""
    out: dict = {"contact_sheets": [], "builder_html": None}
    crop_report = read_report(space, "crop")
    sheets = [a for a in (crop_report.get("artifacts") or []) if "_contact_sheet" in str(a)]
    if not sheets and space.questions.exists():
        sheets = [space.rel(p) for p in space.questions.glob("_contact_sheet_*.png")]
    resolved = []
    for rel in sheets:
        p = Path(rel)
        if not p.is_absolute():
            p = space.root / rel
        if p.exists():
            resolved.append(p)
    # 최신 회차가 마지막에 오게. 확인은 새 회차 한 장만 보면 충분하다.
    resolved.sort(key=lambda p: _sheet_sort_key(p.stem))
    out["contact_sheets"] = [str(p) for p in resolved]

    build_report = read_report(space, "build")
    html = build_report.get("open")
    if html and Path(html).exists():
        out["builder_html"] = str(Path(html).resolve())
    return out


def _sheet_sort_key(stem: str):
    exam_id = stem.replace("_contact_sheet_", "", 1)
    try:
        return (0, exam_sort_key(exam_id))
    except Exception:                                              # noqa: BLE001
        return (1, (stem,))


def _open_files(say: Say, paths, want: bool) -> bool:
    if not want:
        return False
    opened = False
    for p in paths:
        if not p:
            continue
        try:
            webbrowser.open(Path(p).resolve().as_uri())
            opened = True
        except Exception as exc:                                   # noqa: BLE001
            say(f"      (열지 못했습니다: {exc})")
    return opened
