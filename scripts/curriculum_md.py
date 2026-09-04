# -*- coding: utf-8 -*-
"""성취기준 JSON → 과목별 마크다운 참고자료.

**왜 이 파일이 있나**
교육과정 원본 PDF 는 다 합쳐 45MB 다. 저장소에 넣으면 클론이 무거워지는데,
정작 필요한 것은 그 안의 성취기준 텍스트뿐이다. 그래서 PDF 는 배포하지 않고
여기서 뽑아낸 마크다운을 배포한다. 사람도 읽고 LLM 도 읽는다.

**왜 JSON 과 MD 를 둘 다 두나**
- JSON: 코드(classify/validate/build)가 읽는 기계용. 구조가 고정이라 파싱이 안전하다.
- MD: LLM 과 사람이 읽는 참고용. 과목 하나가 파일 하나라서, 판정할 때
  필요한 과목만 열면 된다. 2022 개정 전체를 한 파일로 두면 2,537개를 통째로 읽게 된다.
둘 다 같은 파싱 결과에서 나오므로 어긋날 일이 없다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from common import CURRICULUM_STANDARDS, WORKSPACE, all_subjects

MD_ROOT = CURRICULUM_STANDARDS / "md"

# 파일명에 못 쓰는 문자. 과목명에 '사회·문화' 처럼 가운뎃점이 있어 그대로는 안전하지 않다.
_UNSAFE = re.compile(r'[\/:*?"<>|]')


def _safe(name: str) -> str:
    return _UNSAFE.sub("_", name).strip()


def _walk(node, out: list) -> None:
    """스키마 층 이름을 믿지 않고 {code, text} 레코드를 트리에서 줍는다.

    standards.json 의 층 구조가 한 번 바뀐 적이 있어(build 가 그걸로 크래시했다)
    이름 대신 모양으로 찾는다.
    """
    if isinstance(node, dict):
        if "code" in node and "text" in node:
            out.append(node)
            return
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def wanted_subjects() -> dict[str, set[str]]:
    """레지스트리가 실제로 참조하는 과목만 고른다. {개정: {과목명}}

    2022 개정에는 168개 과목이 있는데 대부분 이 도구와 상관없다.
    전부 만들면 파일 목록만 어지러워진다. --all 로 강제할 수 있다.
    """
    want: dict[str, set[str]] = {}
    for s in all_subjects():
        # 한 개정에서 과목이 둘로 갈리는 자리가 있다(2022 통합과학 = 통합과학1·통합과학2).
        # 예전에는 이 필드가 문자열 하나만 받아서 `"통합과학1·통합과학2"` 라는 없는
        # 과목명이 들어와 있었고, 그 문자열은 성취기준 JSON 의 어느 과목과도 안 맞아
        # 두 과목의 마크다운이 **조용히** 이름 경로로는 안 만들어졌다.
        # 리스트를 그대로 `set.add()` 하면 TypeError 로 19과목 전부가 멈춘다 —
        # 그래서 isinstance 갈래는 Subject.curriculum_names() 한 곳에만 둔다.
        for rev in (s.curriculum or {}):
            for name in s.curriculum_names(rev):
                want.setdefault(rev, set()).add(name)
    return want


def _prefix_index(data: dict) -> dict[str, str]:
    return {v.get("code_prefix"): k for k, v in (data.get("subjects") or {}).items()
            if v.get("code_prefix")}


def render(revision: str, name: str, block: dict, source: str | None = None) -> str:
    units = block.get("units") or []
    total = sum(len(u.get("standards") or []) for u in units)
    lines = [
        f"# {name} — {revision} 개정 교육과정 성취기준",
        "",
        f"- 코드 접두사: `{block.get('code_prefix', '')}`",
        f"- 단원 {len(units)}개 · 성취기준 {total}개",
    ]
    if block.get("area"):
        lines.append(f"- 영역: {block['area']}")
    if block.get("department"):
        lines.append(f"- 교과: {block['department']}")
    # 개정 전체의 별책 목록이 아니라 이 과목이 실제로 나온 문서를 적는다.
    # (한 개정에 별책이 여럿이라 전부 나열하면 틀린 출처를 다는 셈이 된다.)
    src = block.get("source_pdf") or source
    if src:
        lines.append(f"- 출처: {src}")
    lines += ["",
              "> 이 파일은 `gw standards --md` 가 교육과정 원본에서 생성한다. 손으로 고치지 마라.",
              ""]
    for u in units:
        title = u.get("title") or ""
        no = u.get("no")
        head = f"({no}) {title}" if no else title
        lines += [f"## {head}".rstrip(), ""]
        for st in u.get("standards") or []:
            page = st.get("page")
            tail = f" <sub>p.{page}</sub>" if page else ""
            lines.append(f"- `{st['code']}` {st['text']}{tail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate(revisions: list[str] | None = None, all_subjects_flag: bool = False,
             dry_run: bool = False) -> dict:
    want = wanted_subjects()
    written, skipped, missing = [], 0, []
    index: list[tuple[str, str, str, int]] = []

    for path in sorted(CURRICULUM_STANDARDS.glob("*.json")):
        revision = path.stem
        if revisions and revision not in revisions:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        by_prefix = _prefix_index(data)
        sources = {s.get("pdf") for s in (data.get("sources") or [])}
        src = ", ".join(sorted(x for x in sources if x)) or None
        targets = want.get(revision, set())
        # standard_prefixes 로만 참조되는 과목(예: 2022 지구시스템과학)도 포함한다.
        for s in all_subjects():
            for pref in (s.standard_prefixes or {}).get(revision, []) or []:
                if pref in by_prefix:
                    targets.add(by_prefix[pref])

        for name, block in (data.get("subjects") or {}).items():
            if not all_subjects_flag and name not in targets:
                skipped += 1
                continue
            body = render(revision, name, block, src)  # src 는 폴백
            out = MD_ROOT / revision / f"{_safe(name)}.md"
            if not dry_run:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(body, encoding="utf-8")
            n = sum(len(u.get("standards") or []) for u in block.get("units") or [])
            written.append(out)
            index.append((revision, name, block.get("code_prefix", ""), n))

        for name in sorted(targets - set((data.get("subjects") or {}))):
            missing.append(f"{revision}:{name}")

    if written and not dry_run:
        _write_index(index)
    return {"written": [str(p) for p in written], "skipped": skipped, "missing": missing}


def _write_index(index: list[tuple[str, str, str, int]]) -> None:
    lines = ["# 교육과정 성취기준 (마크다운)", "",
             "원본 PDF 는 저장소에 넣지 않는다(다 합쳐 45MB). 여기 있는 것이 배포본이다.",
             "기계가 읽는 형태는 같은 폴더의 `<개정>.json` 이다.", "",
             "원본을 다시 받아 재생성하려면 `curriculum/pdf/README.md` 를 보라.", ""]
    for rev in sorted({r for r, _, _, _ in index}):
        rows = sorted((n, p, c) for r, n, p, c in index if r == rev)
        lines += [f"## {rev} 개정", "", "| 과목 | 코드 접두사 | 성취기준 |", "|---|---|---|"]
        for name, pref, n in rows:
            lines.append(f"| [{name}]({rev}/{_safe(name)}.md) | `{pref}` | {n} |")
        lines.append("")
    (MD_ROOT / "README.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def register(parser) -> None:
    parser.add_argument("--revision", action="append",
                        help="개정 연도(2015/2022). 여러 번 줄 수 있다. 없으면 전부.")
    parser.add_argument("--all", action="store_true",
                        help="레지스트리가 참조하지 않는 과목까지 전부 생성")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")


def run(args) -> int:
    from common import Report
    rep = Report("standards-md", "-")
    result = generate(args.revision, args.all, args.dry_run)
    rep.count(written=len(result["written"]), skipped_unreferenced=result["skipped"])
    for m in result["missing"][:10]:
        rep.note(m, "레지스트리가 참조하는데 성취기준 JSON 에 없다 — 교육과정 원본이 빠졌다", "warn")
    rep.artifact(str(MD_ROOT))
    rep.next = "git add curriculum/standards/md"
    # 리포트는 산출물이 아니라 작업 기록이다. workspace 로 보낸다(계약 1절).
    path = WORKSPACE / "_curriculum" / "reports" / "standards-md.json"
    if args.dry_run:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
        return 0
    return rep.finish(path)
