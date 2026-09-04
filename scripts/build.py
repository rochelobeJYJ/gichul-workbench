# -*- coding: utf-8 -*-
"""`gw build` — items/ + crops/ 를 읽어 문항집 제작기 HTML 한 장을 만든다.

앱 본체(JS)는 builder/worksheet_builder.html 에 이미 있다(레퍼런스 구현에서 그대로 가져와 정리한 것,
검증된 조판 알고리즘은 건드리지 않았다). 이 모듈이 하는 일은 그 앱이 기대하는 모양의
DATA 를 items/ 에서 조립해 템플릿의 `/*__DATA__*/null` 자리에 박아 넣는 것뿐이다.

DATA 모양(템플릿이 그대로 기대한다):
    {
      "subjects": [ {"name": str, "kind": revision, "units": [
          {"unit": str, "standards": [ {"code": str, "text": str, "questions": [row, ...]} ]}
      ]} , ... ],
      "subjectOrder": [문항 출처 과목 label, ...],   # --subject 순서. 탭(name/kind) 순서와는 별개다.
      "total": int,
      "meta": {"repo": str, "version": str, "remote": str, "builtAt": str},
      "text": {key: "발문 <보기> 선택지"},   # 문항 검색용. 본문 없는 문항은 키 자체가 없다
      "nbox": {key: [l, t, w, h]},          # items 의 number_box(이미지 크기 대비 0~1 비율)
      "err":  {key: 0~100},                 # items 의 ext.error_rate(오답률 %)
    }

부속 세 칸을 row 안이 아니라 **key -> 값 옆표**로 두는 이유: questions 배열은 성취기준마다
같은 문항을 되풀이해 담는다(한 문항이 여러 성취기준에 걸린다). row 에 본문을 넣으면 그
중복만큼 HTML 이 부푼다(레퍼런스 위키 실측: 문항 760개가 배정 1151건). 옆표면 문항당 한 벌이다.
앱은 `DATA.text || {}` 로 읽어서, 빌드가 안 실어 주면 그 기능을 조용히 숨긴다.

row 모양은 builder/worksheet_builder.html 의 qHtml()/render() 가 읽는 필드 그대로:
    key, subject, short, year, exam, grade, label, number, points, answer, topic, img

레퍼런스 구현(wiki_2022_지구시스템과학/build_worksheet_builder.py)은 2015→2022 성취기준
크로스워크(한 과목이 여러 2022 과목으로 쪼개져 매핑됨)까지 처리했다. 이 계약(items/<qid>.json)에는
그 크로스워크가 없다 — classification.<revision>.standard/unit 이 과목별로 이미 확정돼 있으므로
탭은 그냥 "(선택한 과목) × (선택한 개정)" 조합이면 된다. 크로스워크가 필요해지면(예: 2022 신설
과목이 여러 2015 과목의 후신인 경우) subjects/<slug>/mapping.json 을 읽는 별도 조립 경로를
추가해야 한다 — 지금은 그 요구가 없어서 만들지 않았다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import manifest as mf
from common import Space, Report, load_subject, BUILDER, REPO, CURRICULUM_STANDARDS
from common.ids import split_qid, GRADE_BEARING
from common.progress import Progress


def _exam_of(qid: str) -> str:
    """qid → exam_id. 식별자 해석은 common.ids 한 곳만 쓴다(CONTRACT 2절)."""
    try:
        return split_qid(qid)[0]
    except ValueError:
        return ""
from common import split_qid, exam_sort_key

TEMPLATE = BUILDER / "worksheet_builder.html"
PLACEHOLDER = "/*__DATA__*/null"


# ── 판형별 조립 전략 ────────────────────────────────────────────────
# CONTRACT.md 0절: 판형 차이는 subject.json.layout 으로만 분기한다. 지금은 tamgu-1q1block 하나뿐이지만
# 다른 판형이 들어올 자리를 표로 남겨 둔다(docs/LAYOUTS.md 참조). 새 판형을 추가할 사람은
# LAYOUT_ROW_BUILDERS 에 함수를 하나 등록하면 된다 — build() 본체는 손대지 않아도 된다.

def _rows_tamgu_1q1block(subject, space: Space, only: set | None, report: Report,
                         bar: Progress | None = None) -> list[dict]:
    """탐구 판형: 문항 하나 = 크롭 이미지 하나 = items/<qid>.json 하나. 가장 단순한 경우다.

    `bar` 는 진행률(선택). 새 판형 전략을 등록할 때 받지 않아도 되게 기본값을 둔다 —
    진행률 때문에 전략 함수를 못 쓰게 되면 본말이 전도된다.
    """
    if not space.items.exists():
        report.note(subject.slug, "items/ 없음 — download/detect/crop/extract 를 먼저 실행한다", "warn")
        return []
    rows: list[dict] = []
    semantics: dict[str, str] = {}   # exam_id -> 'academic'|'calendar' (manifest 를 회차당 한 번만 읽는다)
    item_paths = list(space.iter_items())
    for item_path in (bar.wrap(item_paths) if bar is not None else item_paths):
        qid = item_path.stem
        # 형제 모듈(crop/extract/classify/validate)의 --only 는 qid 와 exam_id 를 모두 받는다.
        # 여기만 qid 전용이라 `--only 2024_수능` 이 0건으로 끝나고, 리포트는 엉뚱하게
        # "extract 부터 다시 돌려라"로 안내했다(통합 검증에서 발견). 문법을 형제와 맞춘다.
        if only is not None and not (
            qid in only or f"{subject.slug}:{qid}" in only or _exam_of(qid) in only
        ):
            continue
        report.bump("items_scanned")
        try:
            item = json.loads(item_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.note(qid, f"items/{qid}.json 을 읽을 수 없음: {exc}", "error")
            continue
        crop = space.question_png(qid)
        if not crop.exists():
            report.note(qid, "크롭 이미지 없음(crops/questions/) — 문항집에서 제외", "warn")
            continue
        row = _make_row(subject, qid, item, f"assets/{subject.slug}/questions/{qid}.png",
                        _year_semantics(space, _exam_of(qid), semantics, report))
        row["_classification"] = item.get("classification") or {}
        row["_crop_src"] = crop
        row["_extra"] = _extras(item)
        rows.append(row)
    return rows


LAYOUT_ROW_BUILDERS = {
    "tamgu-1q1block": _rows_tamgu_1q1block,
}


def _get_row_builder(layout: str):
    fn = LAYOUT_ROW_BUILDERS.get(layout)
    if fn is not None:
        return fn
    # passage-group(국어·영어)·math-mixed(수학)는 "문항 하나 = 크롭 하나"가 깨진다.
    # 지문 하나에 문항 여러 개가 묶이거나, 단답형이 섞여 카드 조판·정답표 구조 자체가 달라진다.
    # 되는 척하지 말고 여기서 바로 죽는다 — 자세한 사정은 docs/LAYOUTS.md.
    raise NotImplementedError(
        f"layout={layout!r} 은 build 조립 전략이 아직 없다. "
        f"tamgu-1q1block(문항 하나=크롭 하나)과 달리 이 판형은 문항-크롭이 1:1이 아닐 수 있어 "
        f"카드 조판 방식이 다르다. docs/LAYOUTS.md 를 읽고 LAYOUT_ROW_BUILDERS 에 전략 함수를 추가한다."
    )


def _year_semantics(space: Space, exam_id: str, cache: dict, report: Report) -> str:
    """그 회차의 연도가 '학년도'인지 '달력연도'인지. manifest 가 유일한 출처다.

    수능·모평은 학년도로 부른다 — `2025학년도 수능`. 학평(전국연합)은 달력연도다 —
    `2025년 고3 7월학평`. 둘을 같은 말로 찍으면 학평 라벨이 통째로 한 해씩 어긋난 것처럼
    읽힌다(실측 사고: `2025학년도 고3 7월학평 4번` 으로 나왔다).

    manifest 에 `year_semantics` 가 있으면 **무조건 그 값**을 쓴다(download 가 목록에서
    받아 적어 둔 값이라 우리 추론보다 앞선다). 없는 옛 manifest 를 위해서만 시험 종류로
    되짚는데, 학평 목록은 common.ids.GRADE_BEARING 하나뿐이다 — 여기서 목록을 다시
    적으면 시험 종류가 늘 때 조용히 갈라진다.
    """
    if exam_id in cache:
        return cache[exam_id]
    value = mf.load(space, exam_id).raw.get("year_semantics")
    if value not in ("academic", "calendar"):
        kind = exam_id.split("_")[-1]
        value = "calendar" if kind in GRADE_BEARING else "academic"
        report.bump("year_semantics_guessed")
    cache[exam_id] = value
    return value


def _num_box(value):
    """number_box 검사: 0~1 실수 4개가 아니면 받지 않는다.

    모양이 틀린 값을 통과시키면 제작기가 이미지 엉뚱한 자리에 흰 상자를 덮어 놓고
    아무 말도 안 한다 — CONTRACT 0절의 '조용한 기본값' 과 같은 종류의 사고다.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    out = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 <= v <= 1):
            return None
        out.append(float(v))
    return out


def _extras(item: dict) -> dict:
    """행 밖에 옆표로 실을 값만 뽑는다. 없는 칸은 **키 자체를 안 만든다.**

    본문은 발문 + <보기>(boxed) + 선택지만 이어 붙인다. 해설은 일부러 뺐다 — 해설까지
    넣으면 '엘니뇨' 로 찾았을 때 엘니뇨가 답이 아닌 문항까지 다 걸려 검색이 못 쓰게 된다.
    `vision` 회차나 `ext.choices_source == "image"` 문항은 여기서 자연히 빈 문자열이 되고,
    앱이 '본문 없음 N개는 검색 대상 아님' 으로 화면에 알린다.
    """
    out: dict = {}
    text = item.get("text") if isinstance(item.get("text"), dict) else {}
    parts = [text.get("stem") or "", text.get("boxed") or ""]
    choices = text.get("choices")
    if isinstance(choices, list):
        parts += [str(c) for c in choices if c]
    joined = " ".join(p for p in parts if p).strip()
    if joined:
        out["text"] = " ".join(joined.split())

    box = _num_box(item.get("number_box"))
    if box is not None:
        out["nbox"] = box

    ext = item.get("ext") if isinstance(item.get("ext"), dict) else {}
    rate = ext.get("error_rate")
    if not isinstance(rate, bool) and isinstance(rate, (int, float)) and 0 <= rate <= 100:
        out["err"] = float(rate)
    return out


def _make_row(subject, qid: str, item: dict, img_rel: str, semantics: str = "academic") -> dict:
    """items/<qid>.json 한 개 → 앱이 쓰는 문항 카드 row 하나.

    key 를 slug:qid 로 만드는 이유: qid 는 과목마다 20번까지 반복된다
    (`2024_수능_07` 이 earth-science-i 에도 earth-science-ii 에도 있을 수 있다).
    slug 를 붙이지 않으면 여러 과목을 한 HTML 에 담을 때 체크박스 선택 상태(off Set)가 섞인다.
    """
    exam_id, number = split_qid(qid)
    year, _rank, grade = exam_sort_key(exam_id)
    grade = grade or None  # exam_sort_key 는 학평이 아니면 0을 돌려준다 — JSON에는 없는 편이 깔끔하다
    kind = exam_id.split("_")[-1]  # '2024_수능' → '수능', '2025_고2_3월학평' → '3월학평'
    grade_txt = f" 고{grade}" if grade else ""
    return {
        "key": f"{subject.slug}:{qid}",
        "subject": subject.label,
        # 과목 약칭(예: '지Ⅰ')은 사람이 정한 관용 표기라 자동으로 만들 수 없다. subject.json 에
        # 별도 필드가 없어 label 을 그대로 쓴다 — todo(contract_gaps)에 남겨 뒀다.
        "short": subject.label,
        "year": year,
        "exam": kind,
        "grade": grade,
        # 학평은 달력연도라 '학년도' 가 아니다(_year_semantics 참조).
        "label": f"{year}{'년' if semantics == 'calendar' else '학년도'}{grade_txt} {kind} {number}번",
        "number": number,
        "points": item.get("points") or 0,
        "answer": item.get("answer_symbol") or "",
        "topic": "",
        "img": img_rel,
    }


def _public_row(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _load_standard_texts(revision: str) -> dict[str, str]:
    """{성취기준코드: 문장} 를 알면 사이드바에 문장을 보여줄 수 있다.

    items/<qid>.json 은 classification.<rev>.unit(단원명)까지만 갖고 있고 성취기준 '문장'은
    없다(CONTRACT.md 4절). 문장은 `gw standards` 가 만드는
    curriculum/standards/<revision>.json 에서 주워 온다.

    ── 왜 재귀로 훑는가 ──
    이 훅을 처음 걸 때는 그 파일이 아직 없어서 스키마를 `{코드: 문장}` 평면 dict 로
    **추측**했다. 통합 검증에서 실제 파일을 붙이자마자
    `AttributeError: 'list' object has no attribute 'get'` 로 build 가 죽었다 —
    실제 모양은 3단 중첩이다:
        {"revision": "2015", "sources": [...],
         "subjects": {"지구과학Ⅱ": {"code_prefix": ..., "units":
             [{"no": 1, "title": ..., "standards": [{"code": ..., "text": ..., "page": ...}]}]}}}
    스키마가 아직 CONTRACT 에 못박혀 있지 않으므로(그래서 한 번 틀렸다) 특정 층을
    이름으로 파고들지 않고 **{"code","text"} 쌍을 가진 dict 를 트리 전체에서 줍는다.**
    validate.py 의 _collect_codes 도 같은 이유로 같은 방식을 쓴다. 이렇게 하면
    standards 쪽이 층을 한 겹 더 얹어도 여기는 안 깨진다.
    파일이 없거나 모양을 못 알아보면 조용히 빈 dict — 코드만으로도 표시는 동작한다.
    """
    path = CURRICULUM_STANDARDS / f"{revision}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    texts: dict[str, str] = {}
    _collect_standard_texts(data, texts)
    if texts:
        return texts
    # 옛 형태(평면 dict) 하위 호환. 값이 문장이거나 {"text": ...} 인 경우.
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    return {}


def _collect_standard_texts(node, out: dict[str, str]) -> None:
    """중첩 구조 어디에 있든 {"code": ..., "text": ...} 레코드를 줍는다."""
    if isinstance(node, dict):
        code, text = node.get("code"), node.get("text")
        if isinstance(code, str) and isinstance(text, str):
            out.setdefault(code, text)
        for v in node.values():
            _collect_standard_texts(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_standard_texts(v, out)


def _build_units(rows: list[dict], revision: str, std_texts: dict[str, str]) -> list[dict]:
    """이 과목·이 개정에 해당하는 문항들을 단원→성취기준 트리로 묶는다.

    단원·성취기준 순서를 매길 근거(교육과정 목차 순서)가 이 계약 안에는 없다. 성취기준 코드가
    보통 장·절 번호를 그대로 담고 있어서(예: 12지구03-02) 코드 알파벳 순 ≈ 목차 순인 경우가
    많다는 걸 근거로 코드순 정렬을 기본값으로 쓴다. 어긋나는 과목이 나오면 todo 참조.
    """
    buckets: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        cls = (row["_classification"] or {}).get(revision)
        if not cls or not cls.get("standard"):
            continue
        code = cls["standard"]
        unit = cls.get("unit") or "(단원 미분류)"
        buckets[unit][code].append(row)

    units = []
    for unit_text in sorted(buckets, key=lambda u: min(buckets[u])):
        stds = []
        for code in sorted(buckets[unit_text]):
            stds.append({
                "code": code,
                "text": std_texts.get(code, ""),
                "questions": [_public_row(r) for r in buckets[unit_text][code]],
            })
        units.append({"unit": unit_text, "standards": stds})
    return units


def _sync_images(pairs, asset_root: Path, bar: Progress | None = None) -> int:
    """crops/questions/<qid>.png 를 output 옆 assets/<slug>/questions/ 로 복사한다.

    ── 왜 복사인가(과제 지시의 "어느 쪽을 골랐는지 주석에 남겨라") ──
    사용자가 output/ 폴더만 통째로 복사해도 안 깨져야 한다는 요구가 있다. crops/ 는
    workspace/<slug>/ 아래(= output/ 바깥)에 있으므로, output/ 기준 상대경로로 그쪽을 가리키면
    output/ 만 복사했을 때 반드시 깨진다. 그래서 이미지를 output/assets/ 안으로 복사해
    output/ 트리 자체가 자기완결적이게 만든다. 단일 파일(base64 인라인)은 380문항이면
    파일이 수백 MB가 돼서 배제했다(과제 지시).

    바뀐 파일만 복사하고(크기 비교), 이번 빌드에 없는 이미지는 지워서 폴더를 깔끔하게 유지한다
    — 레퍼런스 구현의 mirror_earth2_snapshots() 와 같은 발상이다.
    """
    keep_by_slug: dict[str, set[str]] = defaultdict(set)
    copied = 0
    for slug, qid, src in (bar.wrap(pairs) if bar is not None else pairs):
        dest_dir = asset_root / slug / "questions"
        dest = dest_dir / f"{qid}.png"
        keep_by_slug[slug].add(dest.name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)
            copied += 1
    for slug, keep in keep_by_slug.items():
        qdir = asset_root / slug / "questions"
        if not qdir.exists():
            continue
        for stale in qdir.glob("*.png"):
            if stale.name not in keep:
                stale.unlink()
    return copied


def _git_meta() -> dict:
    """출처 표기줄(화면에만, 인쇄 제외)에 쓸 저장소 이름·버전·링크. 실패해도 빌드를 막지 않는다."""
    def _run(*args) -> str:
        try:
            r = subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                                text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""
    return {
        "repo": "기출 문항집 작업기 (gichul-workbench)",
        "version": _run("rev-parse", "--short", "HEAD") or "dev",
        "remote": _run("remote", "get-url", "origin"),  # 없으면 빈 문자열 — 템플릿이 링크 없이 표시
        "builtAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def register(parser) -> None:
    parser.add_argument("--subject", required=True,
                         help="쉼표로 여러 과목 슬러그 (예: earth-science-i,earth-science-ii). "
                              "표시 순서는 이 순서를 그대로 따른다")
    parser.add_argument("--revision",
                         help="쉼표로 포함할 교육과정 개정 (예: 2022,2015). 생략하면 각 과목의 "
                              "subject.json.curriculum 에 등록된(null 아닌) 개정을 전부 담는다")
    parser.add_argument("--out",
                         help="출력 HTML 경로. 생략하면 과목이 하나면 "
                              "workspace/<slug>/output/문항집제작기.html, 여럿이면 "
                              "workspace/_combined/output/문항집제작기.html")
    parser.add_argument("--only", help="쉼표로 특정 qid(또는 slug:qid)만 포함 — 디버그·표본용")
    parser.add_argument("--dry-run", action="store_true",
                         help="HTML/이미지를 쓰지 않고 무엇이 담길지만 리포트로 남긴다")
    parser.add_argument("--force", action="store_true",
                         help="출력 HTML이 이미 있어도 덮어쓴다")
    parser.add_argument("--quiet", action="store_true", help="stdout 요약 줄을 생략한다")
    parser.add_argument("--workspace",
                         help="작업 공간 경로 직접 지정 (기본 workspace/<slug>). "
                              "과목을 여러 개 준 경우에만 <경로>/<slug> 로 과목별로 나뉜다")


def _space(args, slug: str, many: bool) -> Space:
    """--workspace 를 Space 로. 7개 명령이 같은 문법을 쓰기 위한 유일한 해석 지점.

    다른 여섯 명령은 과목이 하나라 `--workspace P` 가 곧 그 과목의 작업 공간 루트다.
    build 만 과목을 여러 개 받는데, 한 경로에 여러 과목의 items/·crops/ 를 겹쳐 놓을
    수는 없다. 그래서 **과목이 둘 이상일 때만** P 를 부모로 보고 P/<slug> 로 나눈다.
    과목이 하나면 나머지 명령과 완전히 같은 의미다(P 자체가 루트).
    """
    root = getattr(args, "workspace", None)
    if not root:
        return Space(slug)
    return Space(slug, Path(root) / slug if many else Path(root))


def run(args) -> int:
    slugs = [s.strip() for s in args.subject.split(",") if s.strip()]
    if not slugs:
        print("[FAIL] build: --subject 가 비어 있다")
        return 1
    try:
        subjects = [load_subject(s) for s in slugs]
    except FileNotFoundError as exc:
        print(f"[FAIL] build: {exc}")
        return 1

    many = len(slugs) > 1
    report = Report("build", "+".join(slugs))
    # 리포트는 첫 과목의 workspace 에 남긴다. CONTRACT.md 는 과목 하나를 전제로 경로를 정해서
    # 여러 과목을 합칠 때 어디에 남길지 계약에 명문화가 없다 — todo(contract_gaps) 참조.
    report_path = _space(args, slugs[0], many).report("build")

    def finish(ok: bool) -> int:
        if args.quiet:
            report.write(report_path, ok=ok)
            return 0 if ok else 1
        return report.finish(report_path, ok=ok)

    only = None
    if args.only:
        only = {x.strip() for x in args.only.split(",") if x.strip()}

    # 개정 선택: 명시 안 하면 선택한 과목들의 curriculum 에 등록된(값이 null 아닌) 개정을
    # 먼저 나온 과목 순서대로 모은다. 예: 지Ⅱ가 {"2015":..., "2022":null} 이면 2015만 후보.
    if args.revision:
        requested = [r.strip() for r in args.revision.split(",") if r.strip()]
    else:
        requested = []
        for s in subjects:
            # curriculum.<개정> 은 문자열 하나일 수도, 이름 여럿일 수도 있다
            # (2022 통합과학 = 통합과학1·통합과학2). isinstance 갈래는
            # Subject.curriculum_names() 한 곳에만 둔다.
            for rev in (s.curriculum or {}):
                if s.curriculum_names(rev) and rev not in requested:
                    requested.append(rev)
    if not requested:
        report.note("revision", "선택한 과목 어디에도 등록된 교육과정 개정이 없다 "
                                 "(subject.json.curriculum 확인)", "error")
        report.next = "subjects/<slug>/subject.json 의 curriculum 필드를 채운다"
        return finish(False)

    out_path = Path(args.out) if args.out else (
        _space(args, slugs[0], many).output / "문항집제작기.html" if not many
        else _space(args, "_combined", False).output / "문항집제작기.html"
    )

    if out_path.exists() and not args.force and not args.dry_run:
        report.note(str(out_path), "출력 파일이 이미 있다 — 덮어쓰려면 --force", "error")
        report.next = (f"python scripts/gw.py build --subject {args.subject} "
                        f"--out \"{out_path}\" --force")
        return finish(False)

    # ── 과목별 row 조립 (판형 전략 표로 분기) ──
    rows_by_slug: dict[str, list[dict]] = {}
    # 총량을 먼저 세어 둔다. 과목마다 set_total 로 늘리면 퍼센트가 뒤로 가서 더 헷갈린다.
    scan_total = sum(len(list(_space(args, s.slug, many).items.glob("*.json")))
                     for s in subjects)
    bar = Progress(scan_total, "문항", label="build", args=args).open()
    try:
        for subject in subjects:
            space = _space(args, subject.slug, many)
            builder_fn = _get_row_builder(subject.layout)
            bar.detail(subject.slug if many else "")
            rows_by_slug[subject.slug] = builder_fn(subject, space, only, report, bar)
    except NotImplementedError as exc:
        # note(ident, why, ...) 순서 — ident 는 짧게, why 에 긴 설명을 넣는다(다른 note 호출과 통일).
        report.note(f"{subject.slug}:layout", str(exc), "error")
        report.next = "docs/LAYOUTS.md 를 읽고 build.py 의 LAYOUT_ROW_BUILDERS 에 전략을 추가한다"
        bar.close()
        return finish(False)
    finally:
        bar.close()   # 어느 갈래로 나가도 줄은 지운다(두 번 불러도 안전)

    included = sum(len(v) for v in rows_by_slug.values())
    if included == 0:
        report.note("items", "포함할 문항이 0개다", "error")
        report.next = ("python scripts/gw.py extract --subject " + args.subject +
                        " && python scripts/gw.py classify --subject " + args.subject)
        return finish(False)

    # ── 탭 조립: (선택한 과목 × 선택한 개정) 조합. subject.json 에 그 개정이 없으면(null) 건너뛴다 ──
    std_texts_cache: dict[str, dict[str, str]] = {}
    subject_entries = []
    for subject in subjects:
        rows = rows_by_slug[subject.slug]
        for revision in requested:
            # 한 개정에서 과목이 둘로 갈린 경우(2022 통합과학1·통합과학2) 탭 이름에는
            # 둘을 함께 적는다. 탭을 둘로 쪼개지는 않는다 — 문항의 classification 은
            # 개정 하나당 한 칸이고, 성취기준 트리가 두 과목을 이미 단원으로 나눈다.
            names = subject.curriculum_names(revision)
            if not names:
                continue
            curriculum_name = " · ".join(names)
            std_texts = std_texts_cache.setdefault(revision, _load_standard_texts(revision))
            units = _build_units(rows, revision, std_texts)
            n = sum(len(st["questions"]) for u in units for st in u["standards"])
            if n == 0:
                report.note(f"{subject.slug}:{revision}",
                            f"{curriculum_name} — 이 개정으로 분류된 문항이 없음(items/의 "
                            f"classification.{revision} 확인)", "info")
            subject_entries.append({
                "name": f"{curriculum_name} · {revision}개정" if len(requested) > 1 else curriculum_name,
                "kind": revision,
                "units": units,
            })

    if not subject_entries:
        report.note("tabs", "탭이 하나도 만들어지지 않았다 — 선택한 과목·개정 조합을 확인", "error")
        report.next = f"python scripts/gw.py build --subject {args.subject} --revision <가능한 개정>"
        return finish(False)

    total_placed = sum(len(st["questions"]) for e in subject_entries
                        for u in e["units"] for st in u["standards"])
    if total_placed == 0:
        # 탭은 생겼지만(과목·개정 조합 자체는 유효) 그 안에 문항이 하나도 안 들어갔다 — 크롭은
        # 있는데 classification 이 전부 비어 있는 경우다. 이대로 HTML을 만들면 "성공"이라고
        # 보고하면서 빈 페이지를 내놓는 꼴이라 여기서 확실히 실패로 끊는다.
        report.note("classification", "탭은 만들어졌지만 분류된 문항이 0개다 — items/의 "
                    "classification 이 비어 있다", "error")
        report.next = f"python scripts/gw.py classify --subject {args.subject}"
        return finish(False)

    # 크롭은 있지만(그래서 rows 에는 들어갔지만) 요청한 개정 어디로도 분류되지 않은 문항은
    # 어떤 탭에도 나타나지 않는다. items_included 와 total_questions 가 크게 벌어지는데
    # 이유를 리포트에 안 남기면 "문항이 사라졌다"로 오인하기 쉽다. 문항별로 적으면 380문항
    # 규모에서 attention 30건 상한을 그냥 다 태우므로, 하나로 합쳐 남긴다.
    unclassified = sum(
        1 for rows in rows_by_slug.values() for r in rows
        if not any((r["_classification"] or {}).get(rev, {}).get("standard") for rev in requested)
    )
    if unclassified:
        report.note(
            "classification",
            f"크롭은 있지만 선택한 개정({','.join(requested)})으로 분류되지 않은 문항 {unclassified}개 "
            f"— 문항집에서 빠졌다. gw classify 진행 상황을 확인한다",
            "info",
        )

    payload = {
        "subjects": subject_entries,
        "subjectOrder": [s.label for s in subjects],
        "total": sum(len(st["questions"]) for e in subject_entries
                     for u in e["units"] for st in u["standards"]),
        "meta": _git_meta(),
    }

    # 실제로 어느 탭에든 걸린 문항만 이미지를 복사한다 — 크롭은 있어도 분류가 안 돼 탭에서
    # 빠진 문항(items_unclassified)까지 복사하면 output/assets/ 에 못 쓰는 이미지만 쌓인다.
    used_keys = {q["key"] for e in subject_entries for u in e["units"]
                 for st in u["standards"] for q in st["questions"]}

    # ── 부속 옆표: 실제로 탭에 걸린 문항 것만 싣는다 ──
    # 분류가 안 돼 어느 탭에도 안 들어간 문항의 본문까지 싣는 건 순수한 낭비다.
    texts, nboxes, errs = {}, {}, {}
    for rows in rows_by_slug.values():
        for r in rows:
            if r["key"] not in used_keys:
                continue
            extra = r.get("_extra") or {}
            if extra.get("text"):
                texts[r["key"]] = extra["text"]
            if extra.get("nbox"):
                nboxes[r["key"]] = extra["nbox"]
            if extra.get("err") is not None:
                errs[r["key"]] = extra["err"]
    payload["text"], payload["nbox"], payload["err"] = texts, nboxes, errs

    copied = 0
    if not args.dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        asset_root = out_path.parent / "assets"
        pairs = [(slug, r["key"].split(":", 1)[1], r["_crop_src"])
                 for slug, rows in rows_by_slug.items() for r in rows if r["key"] in used_keys]
        # 크롭 PNG 를 output/assets/ 로 복사하는 구간. 380장이면 수백 MB라 체감된다.
        with Progress(len(pairs), "이미지", label="build", args=args) as copy_bar:
            copied = _sync_images(pairs, asset_root, copy_bar)
        html = TEMPLATE.read_text(encoding="utf-8").replace(
            PLACEHOLDER, json.dumps(payload, ensure_ascii=False))
        out_path.write_text(html, encoding="utf-8")
        report.artifact(str(out_path))
        report.artifact(str(asset_root))
    else:
        report.note("dry-run", "--dry-run 이라 HTML/이미지는 쓰지 않았다. 아래 counts 는 계산만 한 값",
                     "info")

    report.count(
        subjects=len(subjects),
        revisions=len(requested),
        tabs=len(subject_entries),
        items_included=included,
        items_unclassified=unclassified,
        images_copied=copied,
        total_questions=payload["total"],
        # 0 이면 그 기능이 화면에서 조용히 사라진다는 뜻이다. 사고가 아니라 '데이터가 아직
        # 안 왔다' 이므로 리포트에서 바로 구별되게 세 칸을 따로 센다.
        with_text=len(texts),
        with_number_box=len(nboxes),
        with_error_rate=len(errs),
    )
    report.next = None if not args.dry_run else (
        f"python scripts/gw.py build --subject {args.subject} --out \"{out_path}\"")
    if not args.dry_run:
        report.extra["open"] = str(out_path)
    return finish(not report.has_error)
