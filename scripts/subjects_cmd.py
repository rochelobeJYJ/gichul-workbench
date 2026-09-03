# -*- coding: utf-8 -*-
"""`gw subjects` — 등록된 과목과 준비 상태를 보여준다."""
from __future__ import annotations

from common import all_subjects, Space
from common.subjects import KNOWN_LAYOUTS


def register(parser) -> None:
    parser.add_argument("--area", help="영역으로 거르기 (과학탐구/사회탐구 등)")
    parser.add_argument("--ready", action="store_true", help="바로 쓸 수 있는 과목만")


def run(args) -> int:
    subjects = all_subjects()
    if args.area:
        subjects = [s for s in subjects if s.area == args.area]
    if not subjects:
        print("등록된 과목이 없다. subjects/_template/ 를 복사해 시작한다.")
        return 1

    rows = []
    for s in subjects:
        r = s.readiness()
        if args.ready and not all(r.values()):
            continue
        space = Space(s.slug)
        n_items = len(list(space.items.glob("*.json"))) if space.items.exists() else 0
        rows.append((s.slug, s.label, s.area,
                     "O" if r["providers"] else "-",
                     "O" if r["keywords"] else "-",
                     "O" if r["mapping"] else "-",
                     str(n_items),
                     "실험적" if s.is_experimental else ""))

    head = ("slug", "과목", "영역", "다운", "키워드", "매핑", "문항", "비고")
    widths = [max(len(str(r[i])) for r in ([head] + rows)) for i in range(len(head))]
    line = "  ".join(h.ljust(w) for h, w in zip(head, widths))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))
    print(f"\n{len(rows)}개 과목. 판형: " + ", ".join(KNOWN_LAYOUTS))
    return 0
