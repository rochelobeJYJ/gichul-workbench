# -*- coding: utf-8 -*-
"""의존성과 환경을 점검한다. 처음 한 번 실행한다.

설치를 마음대로 하지 않고 무엇이 없는지 알려준 뒤 명령을 제시한다.
남의 컴퓨터에 조용히 패키지를 까는 도구가 되면 안 된다.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

try:  # 콘솔 인코딩 방어를 최대한 먼저. common 이 깨져 있어도 bootstrap 은 돌아야 한다.
    from common import console as _console
    _console.setup()
except Exception:
    pass

# (임포트 이름, pip 이름, 무엇에 쓰는지, 없으면 치명적인가)
REQUIRED = [
    ("fitz", "PyMuPDF", "PDF 렌더·텍스트 추출 — 크롭과 추출의 핵심", True),
    ("PIL", "Pillow", "이미지 다듬기·대지 생성", True),
    ("requests", "requests", "기출 내려받기", True),
    ("bs4", "beautifulsoup4", "목록 페이지 파싱", True),
    ("pdfplumber", "pdfplumber", "정답표 3중 대조의 세 번째 축", False),
    ("numpy", "numpy", "여백 픽셀 측정", False),
    ("openpyxl", "openpyxl", "키워드 사전 엑셀 왕복", False),
]


def main() -> int:
    print(f"gichul-workbench  {REPO}")
    print(f"Python {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 9):
        print("\n[치명] Python 3.9 이상이 필요하다.")
        return 1

    missing_hard, missing_soft = [], []
    print("\n의존성")
    for mod, pkg, why, hard in REQUIRED:
        have = importlib.util.find_spec(mod) is not None
        mark = "O" if have else ("X" if hard else "-")
        print(f"  [{mark}] {pkg:16s} {why}")
        if not have:
            (missing_hard if hard else missing_soft).append(pkg)

    if missing_hard or missing_soft:
        pkgs = " ".join(missing_hard + missing_soft)
        print(f"\n설치:\n  {sys.executable} -m pip install {pkgs}")
        print(f"또는:\n  {sys.executable} -m pip install -r {REPO / 'requirements.txt'}")

    # 교육과정 PDF는 저작권 때문에 저장소에 없다. 사용자가 넣어야 한다.
    print("\n교육과정 자료")
    pdf_dir = REPO / "curriculum" / "pdf"
    pdfs = sorted(pdf_dir.glob("*.pdf")) if pdf_dir.exists() else []
    std_dir = REPO / "curriculum" / "standards"
    stds = sorted(std_dir.glob("*.json")) if std_dir.exists() else []
    print(f"  원본 PDF {len(pdfs)}개  ·  파싱된 성취기준 {len(stds)}개")
    if not pdfs and not stds:
        print("  교육과정 PDF가 없다. https://www.ncic.re.kr/ 에서 받아")
        print(f"  {pdf_dir} 에 넣고 `python scripts/gw.py standards` 를 돌린다.")

    print("\n등록된 과목")
    try:
        from common import all_subjects
        subjects = all_subjects()
        ready = [s for s in subjects if all(s.readiness().values())]
        print(f"  {len(subjects)}과목 등록  ·  바로 쓸 수 있는 과목 {len(ready)}개")
    except Exception as exc:
        print(f"  [X] 레지스트리를 읽을 수 없다: {exc}")
        return 1

    # 업데이트 확인. git 저장소가 아니거나 원격이 없으면 조용히 넘어간다.
    try:
        out = subprocess.run(["git", "-C", str(REPO), "log", "HEAD..origin/main", "--oneline"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            n = len(out.stdout.strip().splitlines())
            print(f"\n[업데이트] 원격에 새 커밋 {n}개가 있다. `git pull` 을 권한다.")
    except Exception:
        pass

    if missing_hard:
        print("\n필수 패키지가 없어 아직 실행할 수 없다.")
        return 1
    print("\n준비됐다.  다음:  python scripts/gw.py subjects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
