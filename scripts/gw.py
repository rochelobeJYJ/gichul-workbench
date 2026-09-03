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

COMMANDS = {
    "subjects": ("subjects_cmd", "등록된 과목 목록과 준비 상태"),
    "download": ("download", "문제지·정답·해설 내려받기"),
    "detect": ("detect", "메타데이터 인식 + 문제지↔정답 짝짓기"),
    "crop": ("crop", "문항·자료 크롭"),
    "extract": ("extract", "본문·정답·배점 추출"),
    "classify": ("classify", "키워드 1차 분류 + LLM 큐"),
    "map": ("mapping", "성취기준 매핑 적용"),
    "build": ("build", "문항집 제작기 HTML 생성"),
    "validate": ("validate", "구조·정답·불변식 검증"),
    "standards": ("standards", "교육과정 PDF → 성취기준 JSON"),
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gw", description="기출 문항 작업대 (gichul-workbench)")
    sub = parser.add_subparsers(dest="command", metavar="command")
    for name, (module, help_text) in COMMANDS.items():
        sub.add_parser(name, help=help_text, add_help=False)

    args, rest = parser.parse_known_args(argv)
    if not args.command:
        parser.print_help()
        print("\n명령 목록:")
        for name, (_m, help_text) in COMMANDS.items():
            print(f"  {name:10s} {help_text}")
        return 2

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
