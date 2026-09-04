# -*- coding: utf-8 -*-
"""작업 폴더를 한 번만 묻고 기억한다. `gw where` 명령도 여기 있다.

**왜 저장소 밖인가.**
받은 문제지·해설 PDF 와 크롭 이미지는 과목 하나에 수백 MB, 여러 과목이면 수 GB 가 된다.
이걸 저장소 안(`<저장소>/workspace/`)에 쌓으면 두 가지가 망가진다.
  1. 스킬 폴더로 설치한 경우(`~/.claude/skills/gichul-workbench/`) 스킬 폴더가 수 GB 로 부푼다.
  2. `git pull` 이 매번 거대한 작업 트리를 훑는다.
그래서 사용자가 정한 폴더(기본 `바탕화면/기출작업`)에 쌓고, 그 위치를 **저장소 밖**
`~/.gichul-workbench/config.json` 에 적어 둔다. 설정 파일을 저장소 안에 두면
그것 자체가 git 오염이라 같은 문제가 반복된다.

**왜 묻는 것이 여기 한 곳뿐인가.**
`import common` 은 콘솔 인코딩 방어(console.py) 때문에 거의 모든 진입점에서 일어난다.
임포트 부작용으로 사용자에게 질문이 뜨면 `import` 한 번에 프로세스가 멈춘다.
그래서 **해석(resolve)은 절대 묻지 않고**, 묻는 것은 CLI 진입점(gw.py)이 명시적으로
`bootstrap()` 을 부를 때뿐이다.

해석 순서 — 위가 이긴다:
  1. `--workspace <경로>`   각 명령이 직접 받는다. 언제나 이긴다(기존 동작 유지).
  2. `GW_WORKSPACE` 환경변수
  3. `~/.gichul-workbench/config.json`
  4. `<저장소>/workspace/` 가 이미 비어 있지 않으면 그것    ← 쓰던 사람의 자료를 옮기지 않는다
  5. 기본값 `<바탕화면>/기출작업`

4번이 3번보다 아래인 이유: 설정을 명시적으로 바꾼 사람의 뜻이 이겨야 한다.
4번이 5번보다 위인 이유: 이 도구를 이미 쓰던 사람의 작업 폴더에는 수백 MB 가 들어 있다.
갱신 한 번에 기본값이 바뀌어 빈 폴더를 가리키면 **자료가 사라진 것처럼 보인다.**
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
ENV_VAR = "GW_WORKSPACE"
CONFIG_DIR = Path.home() / ".gichul-workbench"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_DIRNAME = "기출작업"

# 출처 딱지. `gw where` 가 사람에게 보여줄 때 쓴다.
SOURCE_LABELS = {
    "env": f"환경변수 {ENV_VAR}",
    "config": f"설정 파일 ({CONFIG_PATH})",
    "repo-legacy": "저장소 안 workspace/ — 예전부터 쓰던 자리",
    "default": "기본값 (아직 정하지 않았다)",
}

# `where` 는 작업 폴더를 보고 고치는 명령이라 그 자신이 질문을 띄우면 안 된다.
_NO_ASK_COMMANDS = frozenset({"where"})


# ────────────────────────────────────────────────────────────── 기본 자리 찾기

def desktop_dir() -> Path:
    """바탕화면 경로. 없으면 홈.

    **`~/Desktop` 을 그냥 쓰면 안 된다.** 윈도우에서 OneDrive 백업이 켜져 있으면
    진짜 바탕화면은 `~/OneDrive/바탕 화면` 이고 `~/Desktop` 은 아예 없거나
    사용자가 화면에서 볼 수 없는 빈 폴더다. 한국 윈도우에서 흔한 설정이라
    '바탕화면에 만들었다'고 안내해 놓고 사용자는 못 찾는 사고가 난다.
    레지스트리의 셸 폴더 값이 유일하게 믿을 수 있는 답이다(읽기만 한다).
    """
    home = Path.home()
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ) as key:
                raw, _ = winreg.QueryValueEx(key, "Desktop")
            candidate = Path(os.path.expandvars(str(raw)))
            if candidate.is_dir():
                return candidate
        except (OSError, ValueError):
            pass  # 레지스트리를 못 읽는 환경. 아래 후보로 내려간다.
    for name in ("Desktop", "바탕 화면", "바탕화면"):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    return home


def default_workspace() -> Path:
    return desktop_dir() / DEFAULT_DIRNAME


# ────────────────────────────────────────────────────────────── 설정 파일 입출력

def read_config() -> dict:
    """설정을 읽는다. 없거나 깨졌으면 빈 dict.

    깨진 설정으로 멈추지 않는 이유: 이 파일은 사용자가 실수로 열어 고칠 수 있는
    자리에 있고, 여기서 죽으면 도구 전체가 안 뜬다. 값이 없으면 다시 물으면 된다.
    (0절 '조용한 기본값 금지'와 어긋나지 않는다 — 없는 값을 지어내는 게 아니라
    '정해진 적 없음' 상태로 되돌아가 다시 묻는 것이다.)
    """
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_config(root: Path, by: str) -> Path:
    """작업 폴더를 기억한다. `by` 는 어떻게 정해졌는지(prompt / where-set)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "workspace": str(root),
        "set_at": datetime.now().isoformat(timespec="seconds"),
        "set_by": by,
    }
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return CONFIG_PATH


def clear_config() -> bool:
    """기억을 지운다. 다음 실행에서 다시 묻는다."""
    try:
        CONFIG_PATH.unlink()
        return True
    except OSError:
        return False


# ────────────────────────────────────────────────────────────── 해석 (묻지 않는다)

def _legacy_root(repo: Path | None) -> Path | None:
    """저장소 안 `workspace/` 에 이미 자료가 있으면 그 경로."""
    if repo is None:
        return None
    legacy = Path(repo) / "workspace"
    try:
        if legacy.is_dir() and any(legacy.iterdir()):
            return legacy
    except OSError:
        return None
    return None


def resolve(repo: Path | None = None) -> tuple[Path, str]:
    """작업 폴더 루트와 그 출처. **어떤 경우에도 사용자에게 묻지 않는다.**

    `--workspace` 는 여기 없다. 그건 명령마다 받는 인자라 `Space(slug, args.workspace)`
    에서 이미 이긴다 — 이 함수는 그것이 없을 때의 답만 낸다.
    """
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        return Path(env).expanduser(), "env"

    saved = read_config().get("workspace")
    if isinstance(saved, str) and saved.strip():
        return Path(saved.strip()).expanduser(), "config"

    legacy = _legacy_root(repo)
    if legacy is not None:
        return legacy, "repo-legacy"

    return default_workspace(), "default"


# ────────────────────────────────────────────────────────────── 첫 실행에 한 번 묻기

def _interactive() -> bool:
    """사람이 앞에 앉아 있는가.

    stdin·stdout 을 **둘 다** 본다. LLM 이나 CI 는 보통 파이프로 붙는데, 한쪽만
    검사하면 `gw ... < /dev/null` 같은 조합에서 뚫린다. 질문이 한 번 뜨면
    비대화형 실행은 영원히 멈춘다 — 뚫리는 쪽보다 안 묻는 쪽으로 기운다.
    """
    try:
        return bool(sys.stdin and sys.stdin.isatty()
                    and sys.stdout and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _argv_has_workspace(argv) -> bool:
    """`--workspace` 가 명령줄에 있는가. 있으면 그것이 이기므로 물을 이유가 없다."""
    for token in argv or ():
        if token == "--workspace" or str(token).startswith("--workspace="):
            return True
    return False


def _argv_wants_help(argv) -> bool:
    """도움말만 보려는 참인가. 도움말 보려다 질문에 걸리면 안 된다."""
    return any(token in ("-h", "--help") for token in (argv or ()))


def _clean_input(raw: str) -> str:
    """붙여넣은 경로 다듬기.

    탐색기에서 '경로로 복사'하면 따옴표가 함께 붙는다. 컴맹 기준으로 그걸
    지우라고 안내하는 것보다 도구가 벗기는 편이 낫다.
    """
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def _prepare(root: Path) -> Path:
    """작업 폴더를 실제로 만든다. 실패하면 예외를 그대로 올린다."""
    root = Path(root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _ask(default: Path, repo: Path | None) -> Path | None:
    """작업 폴더를 묻는다. 정해졌으면 그 경로, 포기했으면 None."""
    print()
    print("작업 폴더를 정합니다. (처음 한 번만 묻습니다)")
    print()
    print("  내려받은 기출 PDF와 잘라낸 문항 이미지가 여기 쌓입니다.")
    print("  과목이 늘면 수 GB가 되기 때문에 프로그램 폴더 밖에 둡니다.")
    print()
    print(f"  기본값:  {default}")
    print()
    print("  그냥 Enter 를 치시면 기본값을 씁니다.")
    print("  다른 곳에 두시려면 폴더 경로를 붙여넣고 Enter 를 치세요.")
    print()

    for _ in range(3):
        try:
            answer = _clean_input(input("작업 폴더> "))
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        target = Path(answer).expanduser() if answer else default
        try:
            made = _prepare(target)
        except OSError as exc:
            print(f"  그 폴더를 만들 수 없습니다: {exc}")
            print("  다른 경로를 넣어 주시거나, 그냥 Enter 를 쳐서 기본값을 쓰세요.")
            continue
        if repo is not None and _is_inside(made, Path(repo)):
            # 막지는 않는다 — 그렇게 쓰고 싶은 사람이 있을 수 있다. 대신 대가를 알려준다.
            print("  ⚠ 프로그램 폴더 안입니다. 자료가 쌓이면 `git pull` 이 느려집니다.")
        return made

    print("  기본값을 씁니다.")
    try:
        return _prepare(default)
    except OSError:
        return None


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _rebind(root: Path) -> None:
    """방금 정한 값을 이미 임포트된 모듈에 다시 박는다.

    `common/__init__.py` 는 콘솔 인코딩 방어 때문에 CLI 시작 즉시 임포트되고,
    그 과정에서 `WORKSPACE` 가 **묻기 전의 답**으로 굳는다.
    질문을 임포트보다 앞세울 수는 없다(질문 자체가 한글이라 인코딩 방어가 먼저여야 한다).
    그래서 순서를 뒤집는 대신, 답이 바뀐 뒤에 굳은 값을 갱신한다.
    `Space` 는 `workspace_root()` 를 그때그때 부르므로 캐시만 비우면 되고,
    `WORKSPACE` 상수를 직접 쓰는 모듈(curriculum_md)을 위해 이름도 함께 고친다.
    """
    paths = sys.modules.get("common.paths")
    if paths is not None:
        invalidate = getattr(paths, "invalidate", None)
        if callable(invalidate):
            invalidate()
        paths.WORKSPACE = root
    package = sys.modules.get("common")
    if package is not None and hasattr(package, "WORKSPACE"):
        package.WORKSPACE = root


def bootstrap(argv, command: str | None, repo: Path | None) -> Path:
    """CLI 진입점이 명령 모듈을 부르기 직전에 한 번 호출한다.

    이미 정해져 있으면 아무것도 하지 않는다. 정해진 적이 없고 사람이 앞에 있으면
    한 번 묻고 기억한다. 사람이 없으면(LLM·CI·파이프) **묻지 않고** 기본값을 쓰되
    어디를 썼는지 stderr 로 남긴다 — 여기서 멈추면 자동 실행이 통째로 죽는다.
    """
    root, source = resolve(repo)

    if (command in _NO_ASK_COMMANDS or _argv_has_workspace(argv)
            or _argv_wants_help(argv)):
        return root
    if source != "default":
        return root  # 환경변수·설정·예전 자리. 이미 답이 있다.

    if not _interactive():
        # 비대화형에서는 설정 파일을 **쓰지 않는다.** 여기서 기억해 버리면
        # 사용자가 직접 고를 기회를 영영 뺏는다. 매번 한 줄로 알리고 넘어간다.
        print(f"[작업 폴더] {root}  (기본값 — 물어볼 수 없는 환경이라 그대로 씁니다. "
              f"바꾸려면 gw where --set <경로>)", file=sys.stderr)
        return root

    chosen = _ask(root, repo)
    if chosen is None:
        print(f"[작업 폴더] {root}  (기본값)", file=sys.stderr)
        return root

    write_config(chosen, by="prompt")
    _rebind(chosen)
    print()
    print(f"작업 폴더: {chosen}")
    print(f"기억했습니다. 바꾸시려면:  gw where --set \"<새 경로>\"")
    print()
    return chosen


# ────────────────────────────────────────────────────────────── `gw where`

def register(parser) -> None:
    parser.add_argument("--set", dest="set_to", metavar="경로",
                        help="작업 폴더를 이 경로로 바꾼다 (폴더가 없으면 만든다)")
    parser.add_argument("--forget", action="store_true",
                        help="기억을 지운다. 다음 실행에서 다시 묻는다")


def _describe(root: Path) -> str:
    """작업 폴더 안에 무엇이 들어 있는지 한 줄. 비싼 계산은 하지 않는다."""
    if not root.exists():
        return "아직 없음 (첫 명령을 실행하면 만들어집니다)"
    try:
        subs = [p.name for p in sorted(root.iterdir()) if p.is_dir()]
    except OSError as exc:
        return f"읽을 수 없음: {exc}"
    if not subs:
        return "비어 있음"
    shown = ", ".join(subs[:6]) + (" …" if len(subs) > 6 else "")
    return f"{len(subs)}개 폴더 — {shown}"


def run(args) -> int:
    from .paths import REPO, invalidate

    if args.forget:
        if clear_config():
            invalidate()
            print(f"기억을 지웠습니다: {CONFIG_PATH}")
        else:
            print("기억해 둔 것이 없습니다.")
        print()

    if args.set_to:
        try:
            root = _prepare(Path(_clean_input(args.set_to)))
        except OSError as exc:
            print(f"[FAIL] 그 폴더를 쓸 수 없습니다: {exc}")
            return 1
        write_config(root, by="where-set")
        invalidate()
        if _is_inside(root, REPO):
            print("⚠ 프로그램 폴더 안입니다. 자료가 쌓이면 `git pull` 이 느려집니다.")
        print(f"작업 폴더를 바꿨습니다: {root}")
        print()

    root, source = resolve(REPO)
    print(f"작업 폴더   {root}")
    print(f"들어 있는 것 {_describe(root)}")
    print(f"어떻게 정해졌나 {SOURCE_LABELS.get(source, source)}")
    print(f"프로그램 폴더 {REPO}")
    print()
    print("바꾸기      gw where --set \"D:\\기출작업\"")
    print("다시 묻게   gw where --forget")
    print("한 번만     gw <명령> --subject <과목> --workspace \"<경로>\"")
    if source == "default":
        print()
        print("아직 정하지 않았습니다. 다음 명령을 실행하시면 한 번 여쭤봅니다.")
    return 0
