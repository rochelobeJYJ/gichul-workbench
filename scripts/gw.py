# -*- coding: utf-8 -*-
"""gichul-workbench 단일 진입점.

    python scripts/gw.py <command> --subject <slug> [옵션]

각 명령은 같은 폴더의 모듈 하나가 담당하며, 반드시
`register(subparsers)` 와 `run(args) -> int` 를 노출한다.
모듈을 지연 임포트하므로 일부 명령의 의존성이 빠져 있어도 나머지는 동작한다.
docs/CONTRACT.md 6절 참조.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # 한국어 윈도우 콘솔은 cp949 라 이게 없으면 gw.py 자신의 출력이 깨진다.
    from common import console as _console
    _console.setup()
except Exception:
    pass

try:  # 작업 폴더를 기억하는 층. 의존성이 덜 깔려 있어도 도움말은 떠야 해서 감싼다.
    from common import config as _config
    from common.paths import REPO as _REPO
except Exception:
    _config, _REPO = None, None

COMMANDS = {
    "quickstart": ("quickstart", "한 명령으로 첫 학습지까지 (처음이면 이것부터)"),
    "subjects": ("subjects_cmd", "등록된 과목 목록과 준비 상태"),
    "download": ("download", "문제지·정답·해설 내려받기"),
    "detect": ("detect", "메타데이터 인식 + 문제지↔정답 짝짓기"),
    "crop": ("crop", "문항·자료 크롭"),
    "extract": ("extract", "본문·정답·배점 추출"),
    "rates": ("rates", "EBSi 문항별 오답률 수집 (items 의 ext.error_rate)"),
    "classify": ("classify", "키워드 1차 분류 + LLM 큐"),
    "map": ("mapping", "성취기준 매핑 적용"),
    "build": ("build", "문항집 제작기 HTML 생성"),
    "validate": ("validate", "구조·정답·불변식 검증"),
    "standards": ("standards", "교육과정 PDF → 성취기준 JSON"),
    "standards-md": ("curriculum_md", "성취기준 JSON → 과목별 마크다운"),
    # 작업 폴더를 보고 바꾸는 명령. 구현이 common/config.py 안에 있는 이유는,
    # 그 파일이 작업 폴더를 정하는 규칙의 유일한 자리이기 때문이다.
    # 규칙과 그것을 설명하는 화면이 갈라지면 언젠가 서로 다른 말을 한다.
    "where": ("common.config", "작업 폴더가 어디인지 보고, 바꾸기"),
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gw", description="기출 문항집 작업기 (gichul-workbench)")
    sub = parser.add_subparsers(dest="command", metavar="command")
    for name, (module, help_text) in COMMANDS.items():
        sub.add_parser(name, help=help_text, add_help=False)

    args, rest = parser.parse_known_args(argv)
    if not args.command:
        parser.print_help()
        print("\n명령 목록:")
        for name, (_m, help_text) in COMMANDS.items():
            print(f"  {name:12s} {help_text}")
        if _config is not None:
            root, source = _config.resolve(_REPO)
            print(f"\n작업 폴더: {root}"
                  + ("   (아직 정하지 않았습니다 — 첫 명령에서 여쭤봅니다)"
                     if source == "default" else ""))
        return 2

    # 작업 폴더는 명령 모듈을 불러오기 **전에** 정해야 한다. 모듈이 임포트되면서
    # 경로 상수를 붙잡는 자리가 있어서, 그 뒤에 물으면 이미 늦다.
    # 물을 수 없는 환경(LLM·CI·파이프)에서는 묻지 않고 기본값을 쓴다 — config.bootstrap 참조.
    if _config is not None:
        _config.bootstrap(rest, args.command, _REPO)

    module_name = COMMANDS[args.command][0]
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        print(f"[FAIL] '{args.command}' 명령을 불러올 수 없다: {exc}")
        print("       python scripts/bootstrap.py 로 의존성을 확인한다.")
        return 1

    inner = argparse.ArgumentParser(prog=f"gw {args.command}",
                                    description=COMMANDS[args.command][1])
    module.register(inner)
    return module.run(inner.parse_args(rest))


if __name__ == "__main__":
    raise SystemExit(main())
