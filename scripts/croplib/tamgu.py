# -*- coding: utf-8 -*-
"""판형 `tamgu-1q1block` — 문항 하나가 한 블록인 탐구 영역 표준 조판.

문항 번호 토큰의 x 좌표를 클러스터링해 컬럼을 찾고, 읽기 순서(페이지→컬럼→y)로
앵커를 늘어놓은 뒤 '이 앵커부터 다음 앵커 직전까지'를 문항 영역으로 삼는다.
**모든 'n.' 이 문항 번호인 것은 아니다** — 자료 상자 안의 '1930년대'·'1. 2. 3.' 은
컬럼 개수를 늘리고 앵커를 훔친다. 그 걸름은 `column_layout`·`_on_column` 에 있다.
19회차 380문항으로 검증된 알고리즘(CSAT_WIKI/wiki_earth2/build_question_snapshots.py)을
계약 형태로 재구성한 것이다. 임계값은 실측으로 얻은 값이라 그대로 살렸다.

## 왜 '다음 앵커까지' 인가 (다른 구현과의 선택)
D:/codex_work/programs/CSAT_Clipper/core/question_extractor.py 는 선지 마커(①~⑤)의
y 분산이 작으면(CHOICE_SPREAD_THRESHOLD=25pt) '선지 줄에서 딱 끊는다'. 크롭이
군더더기 없이 예뻐지지만 **선지가 두 줄로 접히거나 선지 아래에 그림이 더 있는
문항에서 마지막 선지를 잘라 먹는다** — 이 저장소가 가장 두려워하는 사고다.
그래서 경계는 '다음 문항 앵커 직전'(잘릴 수 없는 상한)으로 잡고, 선지 마커
분석은 경계 계산이 아니라 **사후 검증 신호**로만 쓴다(croplib/qa.py).
반대로 CSAT_Clipper 에서 가져온 것도 있다: 하단 푸터 존 개념과 4면 콘텐츠
기반 여백 정리는 렌더 후 이미지 도메인에서 수행한다(croplib/imaging.py).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

import fitz

from .pdfdoc import Doc

COL_TOL = 45             # 같은 컬럼으로 볼 x 좌표 오차(pt)
MIN_SEG_PT = 30          # 이어지는 세그먼트 최소 높이(머리글 슬리버 방지)
MIN_FIRST_SEG_PT = 8     # 첫 세그먼트는 짧아도 살린다(한 줄짜리 꼬리 문항)
GUTTER_PAD = 10          # 다음 컬럼 시작점 바로 앞까지 잘라내기
COL_LEFT_PAD = 16        # 번호 토큰 왼쪽으로 확보할 여유(pt)
BODY_EDGE_PAD = 2.0      # 판면 좌·우 끝에서 남기는 여유(pt)
BOTTOM_MARGIN_PT = 28    # 컬럼 마지막 문항의 기본 하단 한계

# ── 하단 꼬리 쓰레기 탐지 ────────────────────────────────
# 페이지 맨 아래 '* 확인 사항' 안내 박스·쪽번호 박스·저작권 문구. 문항에 붙어
# 있으면 자르지 않고, 독립 박스일 때만 그 위에서 끊는다.
TAIL_ZONE_FRAC = 0.72    # 페이지 하단 28%만 검사
TAIL_TEXT_RE = re.compile(r"확인\s*사항|저작권|한국교육과정평가원|문제지에\s*관한")
PAGENUM_RE = re.compile(r"^[-–—]?\s*(\d{1,3})\s*[-–—]?\.?$")
PAGENUM_BOTTOM_PT = 130  # 쪽번호는 페이지 하단 130pt 안에만 존재
PAGENUM_RULE_MAX_W = 170  # 쪽번호 박스 상단 가로선 최대 폭(pt)
TAIL_RULE_LOOKUP = 60    # 꼬리 문구 위쪽에서 감싸는 가로선을 찾는 거리(pt)
TAIL_PAD = 5.0           # 절단선 여유(pt)

# ── 컬럼 구분 세로 실선 ──────────────────────────────────
RULE_MAX_W_PT = 3.0      # PDF 도형 기준 '얇은' 세로선 폭
RULE_MIN_H_FRAC = 0.5    # 페이지 높이의 50% 이상 관통
RULE_CLEAR_PT = 1.5      # 실선 바깥쪽으로 확보할 여유(pt)

# ── 여백 과목 탭(회색 세로 박스) ─────────────────────────
# 평가원 문제지는 첫 장 바깥 여백에 과목 표시 세로 탭을 회색 채움 박스로 인쇄한다.
# 판면 바깥이므로 크롭에서 제외한다. 과목명 문자열이 아니라 '바깥 여백에 있는
# 좁고 긴 무채색 박스'라는 기하 조건으로만 판별한다(과목 하드코딩 금지).
TAB_MIN_H_PT = 40.0
TAB_MAX_W_PT = 60.0
TAB_GRAY_LO = 0.30       # 무채색 채움 밝기 하한(검정 박스 제외)
TAB_GRAY_HI = 0.97       # 상한(흰 박스 제외)
TAB_MARGIN_FRAC = 0.10   # 페이지 좌/우 바깥 10% 여백 안에 완전히 들어와야 함

# ── 진짜 컬럼과 가짜 번호 토큰을 가르는 값 ───────────────
# 자료 상자 안의 '1930년대'·'1. 2. 3.' 같은 토큰이 컬럼으로 승격되면 크롭이 세로
# 띠로 잘린다. 아래 값들이 그것을 막는다. 근거는 column_layout 의 주석에 있다.
COL_PAGE_SHARE = 0.5     # 컬럼이라면 번호가 있는 쪽의 이 비율 이상에 나타나야 한다
COL_PITCH_TOL = 0.10     # 컬럼 간격이 서로 이보다 더 벌어지면 격자가 아니다
COL_WEAK_SHARE = 0.5     # 토큰 수가 중앙값의 이 비율 미만이면 '근거가 약한' 후보
# 컬럼 시작 x 에서 이만큼 떨어진 'n.' 은 앵커가 아니다(들여쓰기된 상자 안 번호).
# 실측 근거: 6과목 116개 클러스터에서 **진짜 번호 토큰의 x 편차는 예외 없이 0pt**
# 였고(한 자리·두 자리 번호가 같은 x 에 선다), 가짜 위성은 +23~+45pt 였다.
ANCHOR_X_TOL = 12.0

# ── 다음 문항 시작선 보정 ────────────────────────────────
# 다음 문항 첫 줄에 분수(분자가 번호 토큰보다 위로 솟음)나 위첨자가 있으면
# 번호 토큰 y 만으로 자를 때 그 윗부분이 크롭에 침입한다.
NEXT_RAISE_PT = 20.0     # 번호 토큰 위쪽으로 살펴볼 거리(pt)
NEXT_OVERLAP_PT = 4.0    # 번호 토큰 아래까지 이만큼 내려와야 '같은 줄'로 인정
ANCHOR_LIFT_PT = 5.0     # 앵커 세그먼트 상단 여유

_NUM_STRICT = re.compile(r"^(\d{1,2})\.$")
_NUM_LOOSE = re.compile(r"^(\d{1,2})\.(?=\D|$)")


@dataclass
class ColumnLayout:
    """컬럼 판정 결과. **좌표가 두 벌인 것이 핵심이다.**

    - `cols`     크롭 왼쪽 경계를 잡을 때 쓰는 컬럼 시작 x. 클러스터의 **최솟값**이다
                 (PITFALLS 2-4: 평균을 쓰면 오른쪽 가짜 토큰이 컬럼을 밀어 번호를 자른다).
    - `anchors_x` 그 컬럼에서 **진짜 문항 번호가 서는 x**. 클러스터의 **최빈값**이다.
                 최솟값을 앵커 판정에 쓰면 왼쪽에 가짜가 하나만 있어도 진짜 번호가 전부
                 '컬럼에서 벗어난 토큰'이 되어 회차가 통째로 날아간다. 두 값의 실패
                 방향이 반대라 한 값으로 겸할 수 없다.
    - `dropped`  컬럼에서 제외한 후보 무리. **리포트에 올릴 유일한 증거**다 —
                 이 사고는 텍스트 추출이 멀쩡해서 다른 신호가 하나도 남지 않는다.
    """
    cols: list[float]
    anchors_x: list[float]
    dropped: list[dict] = field(default_factory=list)


@dataclass
class Segment:
    """문항이 차지하는 (페이지, 컬럼, 사각형) 조각. 컬럼/페이지를 넘어가면 여러 개."""
    page: int          # 0-based
    col: int
    rect: fitz.Rect


@dataclass
class QuestionPlan:
    number: int
    segments: list[Segment] = field(default_factory=list)
    # 문항 번호 토큰이 PDF 위에서 차지하는 자리. 학습지에서 번호를 1,2,3… 으로 다시
    # 매기려면 크롭 이미지 안의 어디를 덮어야 하는지 알아야 해서 남긴다.
    # 크롭 단계가 이것을 크롭 사각형 대비 비율(`items.number_box`)로 환산한다.
    # 못 찾았으면 None 이다 — 계약 0절대로 그럴듯한 자리를 지어내지 않는다.
    num_page: int | None = None
    num_rect: tuple[float, float, float, float] | None = None


@dataclass
class ExamPlan:
    columns: list[float]
    rules: list[tuple[float, float]]
    tabs: dict[int, list[tuple[float, float]]]
    questions: list[QuestionPlan]
    missing: list[int]
    duplicated: list[int]
    # 컬럼에서 제외한 가짜 번호 무리. 비어 있는 것이 정상이고, 차 있으면 크롭 폭이
    # 그만큼 달라졌다는 뜻이라 크롭 명령이 리포트에 올린다(기본값이라 옛 호출부 무해).
    dropped_columns: list[dict] = field(default_factory=list)


# ══════════════════════════════════════════════════════════
# 문항 번호 앵커 / 컬럼
# ══════════════════════════════════════════════════════════
def number_tokens(doc: Doc, max_number: int,
                  loose: bool = False) -> list[tuple[int, float, float, int, tuple | None]]:
    """[(page_idx, x, y, n, bbox)] — 'n.' 형태 워드. bbox 는 PDF 좌표 (x0,y0,x1,y1).

    strict 는 워드 하나가 통째로 'n.' 인 것만 인정한다. 번호와 발문이 한 워드로
    붙어 나오는 판형(제목 줄이 따로 있는 조판)을 위해 loose 재시도를 둔다.
    loose 는 오탐(본문 속 '3.5' 같은 것)이 늘어나므로 strict 가 실패했을 때만 쓴다.

    **loose 토큰의 bbox 는 None 이다.** 워드 전체가 'n.발문…' 이라 워드 사각형은
    번호가 아니라 문장 첫 덩어리의 자리다. 글자 수 비례로 앞부분을 떼어 내는 근사는
    한글·숫자 폭이 달라 조용히 틀리므로(계약 0절), 자리를 모른다고 말하는 쪽을 택했다.
    앵커 자체는 종전대로 loose 로도 찾는다 — 크롭은 되고 번호 자리만 비는 것이다.
    """
    pat = _NUM_LOOSE if loose else _NUM_STRICT
    out = []
    for pidx in range(len(doc)):
        for w in doc.words(pidx):
            m = pat.match(w[4])
            if m and 1 <= int(m.group(1)) <= max_number:
                box = None if loose else (w[0], w[1], w[2], w[3])
                out.append((pidx, w[0], w[1], int(m.group(1)), box))
    return out


def _x_clusters(tokens) -> list[dict]:
    """번호 토큰을 x 로 묶은 후보 무리. 판정에 필요한 근거를 함께 담아 돌려준다.

    묶는 규칙(정렬 → COL_TOL 이내면 같은 무리)과 대표값(최솟값)은 예전과 **글자 그대로
    같다**. 검증된 회차의 크롭 좌표가 1px 도 달라지면 안 되기 때문이다.
    """
    xs = sorted({round(t[1]) for t in tokens})
    if not xs:
        return []
    buckets, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] <= COL_TOL:
            cur.append(x)
        else:
            buckets.append(cur)
            cur = [x]
    buckets.append(cur)

    out = []
    for b in buckets:
        keys = set(b)
        ts = [t for t in tokens if round(t[1]) in keys]
        freq = Counter(round(t[1]) for t in ts)
        top = max(freq.values())
        out.append({
            "start": float(min(b)),                                     # 크롭 좌단(최솟값)
            "anchor_x": float(min(x for x, c in freq.items() if c == top)),  # 앵커 x(최빈값)
            "tokens": ts,
            "pages": {t[0] for t in ts},
        })
    return out


def _drop_note(group: dict, why: str) -> dict:
    return {"x": group["start"], "tokens": len(group["tokens"]),
            "pages": sorted(group["pages"]),
            "numbers": sorted({t[3] for t in group["tokens"]}), "why": why}


def column_layout(tokens) -> ColumnLayout:
    """번호 토큰에서 **진짜 컬럼 격자**를 골라낸다.

    컬럼 수를 '페이지 절반' 같은 상수로 가정하지 않는 이유: 실제 문제지는 A3 2단이
    기본이지만 판형이 바뀌면 1단·3단도 나온다. 토큰 클러스터링은 그때도 그대로
    동작한다(CSAT_Clipper 의 detect_column_layout 은 mid_x=page_width/2 를 전제해
    이 조건에서 무너진다).

    ## 왜 클러스터 평균이 아니라 **최솟값**인가 (실측 사고)

    예전에는 클러스터 평균을 컬럼 대표값으로 썼다. 그런데 이 값이 곧바로
    `x0 = cols[i] - COL_LEFT_PAD` 로 크롭 왼쪽 경계가 되므로, 클러스터에 **오른쪽으로
    치우친 가짜 토큰**이 하나라도 섞이면 컬럼 전체가 오른쪽으로 밀려 번호가 잘린다.
    2025학년도 수능 사회·문화 문제지가 그랬다 — 20번 <조건> 상자 안의 번호 목록
    ('1.' '2.' '3.', x=474.8)이 오른쪽 컬럼(x=436.6) 클러스터에 섞여 평균이 456.0 이
    됐고, 그 컬럼의 여덟 문항(4·5·9·10·14·15·19·20) 전부에서 두 자리 번호의 앞자리가
    잘렸다('14.'→'4.'). **자동 검증으로는 안 잡힌다. 대지에서 눈으로 봤다.**

    최솟값은 실패 방향이 안전하다. 가짜 토큰이 오른쪽에 있으면 무시되고, 왼쪽에
    있으면 크롭이 조금 넓어질 뿐이다 — 넓어지는 것보다 잘리는 것이 훨씬 나쁘다
    (모듈 서두의 '가장 두려워하는 사고' 참조).

    ## 그런데 최솟값으로도 못 막는 사고가 따로 있다 (이 함수가 있는 이유)

    가짜 토큰이 **COL_TOL(45pt) 밖으로 멀리 들여쓰기되면** 같은 무리에 안 섞이고
    **자기 혼자 새 컬럼이 된다.** 그러면 컬럼이 밀리는 게 아니라 **개수가 늘어나고**,
    `col_x_range` 가 다음 컬럼 시작 앞에서 자르므로 크롭이 세로 띠가 된다.

    | 회차(통합사회) | 진짜 컬럼 | 잘못 잡은 컬럼 | 파손 |
    |---|---|---|---|
    | 2025 고1 9월 | [48, 289] | [48, **158**, **218**, 289] | 13/25장, 폭 322~423px(정상 945~995) |
    | 2022 고1 3월 | [88, 429] | [88, **137**, 429] | 6/20장, 폭 170~173px(높이 1082~2126) |
    | 2022 고1 6월 | [88, 429] | [88, **293**, 429] | 11/20장, 폭 823px(정상 1383) |
    | 2026 고1 9월 | [88, 429] | [88, 429, **543**, **620**] | 12/25장, 폭 452px(정상 1383) |

    마지막 줄이 중요하다 — 가짜가 **마지막 진짜 컬럼보다 오른쪽**에 생기면 오른단 문항이
    통째로 잘린다. 가짜가 늘 컬럼 사이에 낀다고 가정한 규칙은 이 모양을 못 잡는다.

    **텍스트 추출은 멀쩡하다.** 띠 안에도 그 문항의 글자가 들어 있어 qa 의 '머리에 번호가
    없다'·'선지 ⑤ 가 없다' 가 울리지 않고 validate 도 통과한다. 대지를 눈으로 봐야 보인다.
    그래서 개수 판정을 토큰 하나의 존재가 아니라 **두 가지 반복성 근거** 위에 다시 세웠다.

    ### ① 쪽 반복성 (주 근거) — 왜 가짜 토큰에 안 속나
    컬럼은 **판면 격자의 성질**이라 번호가 찍히는 모든 쪽에 다시 나타난다. 가짜 번호는
    특정 문항의 자료 상자 **한 곳**에서 생기는 사고라 그 쪽에만 있다. 실측이 정확히
    그 모양이었다 — 진짜 컬럼은 4/4·6/6쪽, 가짜는 1/4·1/6쪽. 그래서 '번호가 있는 쪽의
    절반 이상에 나타날 것'을 요구한다. 이 기준은 상자 모양·들여쓰기 깊이·토큰 글자
    ('1930년대'인지 '1.'인지)에 전혀 의존하지 않는다 — 새 과목이 새로운 모양의 가짜
    토큰을 들고 와도 그것이 모든 쪽에 반복되지 않는 한 걸린다.

    ### ② 간격의 규칙성 (보조 근거) — ①이 못 보는 경우를 받는다
    쪽이 하나뿐인 문제지나, 매 쪽 같은 자리에 안내 상자가 있어 가짜가 함께 반복되면
    ①은 침묵한다. 그때는 기하로 받는다 — k단 조판의 컬럼 간격은 서로 같으므로,
    간격이 10% 넘게 어긋나면 그 집합은 격자가 아니다. 다만 **근거가 약한 후보(토큰 수가
    중앙값의 절반 미만)만** 뺀다. 실제로 3단 판형의 간격이 조금 불규칙할 수 있는데,
    그때 튼튼한 컬럼을 지워 문항을 통째로 잃는 쪽이 훨씬 나쁘기 때문이다.
    간격 하한을 '페이지 폭의 몇 %' 로 잡지 않은 이유도 같다 — 그 방식은 진짜 3·4단
    판형과 가짜를 가르는 선이 겹친다(실측: 위 9월 회차의 가짜 간격 110pt 는 판면 폭의
    23%로, 4단 조판의 25%와 사실상 구분되지 않는다).

    ### 남는 것: 앵커 x
    무리가 살아남아도 그 안에 섞인 가짜 토큰은 여전히 앵커를 훔칠 수 있다. 실측 —
    2025 고1 9월 21번은 진짜 번호가 5쪽 오른단인데 3쪽 상자 안 '21.' 이 읽기 순서상
    먼저라 그쪽이 앵커가 됐다. 그래서 무리의 **최빈 x**(=진짜 번호가 서는 자리)를
    함께 돌려주고 `_anchors` 가 거기서 ANCHOR_X_TOL 밖의 토큰을 앵커에서 뺀다.

    ### 판정을 데이터(subject.json)로 미루지 않은 이유
    컬럼 수 힌트를 과목 파일에 두면 새 과목마다 사람이 적어야 하고, 같은 슬러그 안에서
    판형이 바뀌는 회차(통합과목 20문항↔25문항)를 스칼라 하나로 담지 못한다. 위 두
    근거는 문제지 자신에게서 나오므로 그 두 대가를 치르지 않는다.
    """
    groups = _x_clusters(tokens)
    if not groups:
        return ColumnLayout([], [], [])

    dropped: list[dict] = []
    pages_with_tokens = {t[0] for t in tokens}

    # ① 쪽 반복성. 쪽이 하나뿐이면 이 근거 자체가 존재하지 않으므로 건너뛴다.
    if len(pages_with_tokens) >= 2:
        need = math.ceil(len(pages_with_tokens) * COL_PAGE_SHARE)
        strong = [g for g in groups if len(g["pages"]) >= need]
        if strong:                       # 전부 약하면 판정을 포기한다(0개를 돌려주면 회차가 죽는다)
            for g in groups:
                if len(g["pages"]) < need:
                    dropped.append(_drop_note(
                        g, f"번호가 찍힌 {len(pages_with_tokens)}쪽 중 {len(g['pages'])}쪽에만 "
                           f"있다(컬럼이면 {need}쪽 이상)"))
            groups = strong

    # ② 간격의 규칙성. 한 번에 하나씩만 빼고 매번 다시 잰다 — 여럿을 한꺼번에 빼면
    #    '어느 하나만 빼도 규칙적이 되는' 집합에서 멀쩡한 컬럼까지 날아간다.
    while len(groups) >= 3:
        pitches = [b["start"] - a["start"] for a, b in zip(groups, groups[1:])]
        if max(pitches) <= min(pitches) * (1.0 + COL_PITCH_TOL):
            break
        counts = sorted(len(g["tokens"]) for g in groups)
        mid = counts[len(counts) // 2]
        weak = [g for g in groups if len(g["tokens"]) < mid * COL_WEAK_SHARE]
        if not weak:
            break
        # 토큰이 가장 적은 것 → 쪽이 가장 적은 것 → 더 오른쪽. 마지막은 동점일 때만 쓰는
        # 꼬리표다(실측된 가짜는 전부 진짜 컬럼보다 오른쪽에 생겼다). 판정의 무게는
        # 앞의 두 개, 곧 '근거의 양'에 실려 있다.
        victim = min(weak, key=lambda g: (len(g["tokens"]), len(g["pages"]), -g["start"]))
        groups = [g for g in groups if g is not victim]
        dropped.append(_drop_note(
            victim, f"컬럼 간격이 고르지 않고(간격 {[round(p) for p in pitches]}) "
                    f"토큰 {len(victim['tokens'])}개로 근거가 가장 약하다"))

    return ColumnLayout([g["start"] for g in groups],
                        [g["anchor_x"] for g in groups], dropped)


def nearest_col(cols: list[float], x: float) -> int:
    return min(range(len(cols)), key=lambda i: abs(cols[i] - x))


def detect_column_rules(doc: Doc) -> list[tuple[float, float]]:
    """페이지 과반에 반복 등장하는 얇고 긴 세로 실선(=컬럼 구분선)의 x 구간."""
    per_page: list[set[int]] = []
    spans: dict[int, list[tuple[float, float]]] = {}
    for pidx in range(len(doc)):
        page = doc.page(pidx)
        min_h = page.rect.height * RULE_MIN_H_FRAC
        found = set()
        for d in page.get_drawings():
            r = d["rect"]
            if r.width <= RULE_MAX_W_PT and r.height >= min_h:
                k = int(round((r.x0 + r.x1) / 2.0))
                found.add(k)
                spans.setdefault(k, []).append((r.x0, r.x1))
        per_page.append(found)
    if not per_page:
        return []
    need = max(1, (len(per_page) + 1) // 2)
    rules = []
    for k, occ in spans.items():
        if sum(1 for f in per_page if k in f) >= need:
            rules.append((min(a for a, _ in occ), max(b for _, b in occ)))
    return sorted(rules)


def detect_margin_tabs(doc: Doc) -> dict[int, list[tuple[float, float]]]:
    """바깥 여백의 과목 표시 세로 탭(회색 박스) x 구간. {page_idx: [(x0,x1)]}.

    판면 밖 여백에만 존재하는 요소이므로 '페이지 폭의 바깥 10% 안에 완전히 들어온
    무채색 채움 박스'만 인정한다. 본문 안의 회색 음영 도형은 이 조건을 통과할 수
    없다(A3 판면 오른쪽 끝 754.5pt < 0.9×842pt). 탭은 특정 장에만 있어 장별로 적용한다.
    """
    out: dict[int, list[tuple[float, float]]] = {}
    for pidx in range(len(doc)):
        page = doc.page(pidx)
        w = page.rect.width
        lo, hi = w * TAB_MARGIN_FRAC, w * (1.0 - TAB_MARGIN_FRAC)
        for d in page.get_drawings():
            fill = d.get("fill")
            r = d["rect"]
            if not fill or r.height < TAB_MIN_H_PT or r.width > TAB_MAX_W_PT:
                continue
            if max(fill) - min(fill) > 0.03:
                continue
            if not (TAB_GRAY_LO <= fill[0] <= TAB_GRAY_HI):
                continue
            if not (r.x1 <= lo or r.x0 >= hi):
                continue
            out.setdefault(pidx, []).append((r.x0, r.x1))
    return {k: sorted(set(v)) for k, v in out.items()}


# ══════════════════════════════════════════════════════════
# 컬럼 x 범위 / 세그먼트 상·하단
# ══════════════════════════════════════════════════════════
def col_x_range(doc: Doc, pidx: int, cols: list[float], col_idx: int,
                rules: list[tuple[float, float]]) -> tuple[float, float]:
    page = doc.page(pidx)
    body_x0, body_x1 = doc.body_x(pidx)
    x0 = cols[col_idx] - COL_LEFT_PAD
    if col_idx + 1 < len(cols):
        x1 = cols[col_idx + 1] - GUTTER_PAD
    else:
        # 마지막 컬럼의 오른쪽은 판면 끝까지. 페이지 끝까지 열어 두면 바깥 여백의
        # 세로쓰기 과목명이 딸려 들어온다(pdfdoc.Doc.body_x 주석 참고).
        x1 = body_x1 + BODY_EDGE_PAD
    x0 = max(x0, body_x0 - BODY_EDGE_PAD, page.rect.x0)
    x1 = min(x1, body_x1 + BODY_EDGE_PAD, page.rect.x1)

    left_bound = cols[col_idx - 1] if col_idx > 0 else 0.0
    right_bound = cols[col_idx + 1] if col_idx + 1 < len(cols) else page.rect.width
    for rx0, rx1 in rules:
        cx = (rx0 + rx1) / 2.0
        if cols[col_idx] < cx <= right_bound:      # 오른쪽 거터의 실선 → 안쪽까지만
            x1 = min(x1, rx0 - RULE_CLEAR_PT)
        if left_bound <= cx < cols[col_idx]:       # 왼쪽 거터의 실선 → 바깥부터
            x0 = max(x0, rx1 + RULE_CLEAR_PT)
    return x0, x1


def raised_top(doc: Doc, pidx: int, x0: float, x1: float, anchor_y: float) -> float:
    """번호 토큰 첫 줄에서 위로 솟은 글자(분수 분자·위첨자)까지 포함한 y."""
    top = anchor_y
    for w in doc.words(pidx):
        if w[2] <= x0 or w[0] >= x1:
            continue
        if anchor_y - NEXT_RAISE_PT <= w[1] < top and w[3] >= anchor_y + NEXT_OVERLAP_PT:
            top = w[1]
    return top


def _rule_above(doc: Doc, pidx: int, line: dict, x0: float, x1: float,
                max_w: float | None = None) -> float | None:
    best = None
    for rx0, rx1, ry in doc.hrules(pidx):
        if ry >= line["y0"] or line["y0"] - ry > TAIL_RULE_LOOKUP:
            continue
        if rx1 <= x0 or rx0 >= x1:
            continue
        if max_w is not None and (rx1 - rx0) > max_w:
            continue
        if best is None or ry > best:
            best = ry
    return best


def tail_top(doc: Doc, pidx: int, x0: float, x1: float) -> float | None:
    """컬럼 [x0,x1] 하단 꼬리 쓰레기의 시작 y. 없으면 None.

    문구를 감싸는 박스의 상단 괘선까지 거슬러 올라가 그 위에서 자른다. 컬럼 범위로
    라인을 뽑는 것이 핵심 — 전폭으로 뽑으면 좌단 본문과 우단 '확인 사항'이 한 줄로
    합쳐져 본문 한가운데를 꼬리로 오인한다.
    """
    h = doc.page(pidx).rect.height
    zone = h * TAIL_ZONE_FRAC
    best = None
    for ln in doc.lines(pidx, x0, x1):
        if ln["y0"] < zone:
            continue
        txt = ln["text"].strip()
        y = None
        if TAIL_TEXT_RE.search(txt):
            y = ln["y0"]
            top = _rule_above(doc, pidx, ln, x0, x1)
            if top is not None:
                y = min(y, top)
        elif PAGENUM_RE.match(txt.replace(" ", "")) and ln["y0"] > h - PAGENUM_BOTTOM_PT:
            # 쪽번호는 좁은 박스 안에 홀로 놓인다(본문 표의 숫자 셀과 구분)
            top = _rule_above(doc, pidx, ln, x0, x1, max_w=PAGENUM_RULE_MAX_W)
            if top is not None:
                y = min(ln["y0"], top)
        if y is None:
            continue
        best = y if best is None else min(best, y)
    return None if best is None else best - TAIL_PAD


def seg_bottom(doc: Doc, pidx: int, next_in_same: float | None,
               x0: float, x1: float) -> float:
    page = doc.page(pidx)
    if next_in_same is not None:
        bottom = max(0.0, next_in_same - 3)
    else:
        bottom = page.rect.height - BOTTOM_MARGIN_PT
    tail = tail_top(doc, pidx, x0, x1)
    if tail is not None:
        bottom = min(bottom, tail)
    return bottom


def segments_for(doc: Doc, cols: list[float], rules: list[tuple[float, float]],
                 anchor: tuple, nxt: tuple | None,
                 tabs: dict[int, list[tuple[float, float]]] | None = None) -> list[Segment]:
    """앵커(page,col,y,…)부터 다음 앵커 직전까지의 세그먼트 목록.

    앵커 튜플의 4번째 자리(번호 토큰 bbox)는 여기서 쓰지 않는다 — 잘라내지 말고
    앞 세 개만 받아 둔다.
    """
    apage, acol, ay = anchor[0], anchor[1], anchor[2]
    tabs = tabs or {}
    out: list[Segment] = []
    pidx, col = apage, acol
    while True:
        is_first = (pidx, col) == (apage, acol)
        page = doc.page(pidx)
        x0, x1 = col_x_range(doc, pidx, cols, col, rules + tabs.get(pidx, []))
        same_seg_next = None
        if nxt is not None and (nxt[0], nxt[1]) == (pidx, col):
            same_seg_next = raised_top(doc, pidx, x0, x1, nxt[2])
        top = (raised_top(doc, pidx, x0, x1, ay) - ANCHOR_LIFT_PT
               if is_first else doc.header_bottom(pidx))
        bottom = seg_bottom(doc, pidx, same_seg_next, x0, x1)
        if bottom - top > (MIN_FIRST_SEG_PT if is_first else MIN_SEG_PT):
            out.append(Segment(pidx, col,
                               fitz.Rect(x0, top, x1, min(bottom, page.rect.height))))
        if nxt is None or (nxt[0], nxt[1]) == (pidx, col):
            break
        if col + 1 < len(cols):
            col += 1
        else:
            pidx += 1
            col = 0
            if pidx >= len(doc):
                break
    return out


# ══════════════════════════════════════════════════════════
# 회차 전체 계획
# ══════════════════════════════════════════════════════════
def plan_exam(doc: Doc, expected: int) -> ExamPlan:
    """회차 하나의 문항 크롭 계획. expected 는 subject.question_count."""
    max_number = max(expected, 30)
    tokens = number_tokens(doc, max_number)
    layout = column_layout(tokens)
    found = _anchors(tokens, layout)
    missing = [n for n in range(1, expected + 1) if n not in found]

    if missing:
        # 번호가 발문과 한 워드로 붙는 판형을 위한 느슨한 재시도. 이미 찾은 번호는
        # 유지하고 빠진 것만 채운다 — loose 는 본문 속 '3.'(소수점 등) 오탐이 있어
        # 컬럼 안에 서는 토큰만 인정한다(거르는 것은 _anchors 가 앵커 x 로 한다).
        loose = number_tokens(doc, max_number, loose=True)
        lay = layout if layout.cols else column_layout(loose)
        extra = _anchors(loose, lay)
        for n in missing:
            if n in extra:
                found[n] = extra[n]
        if not layout.cols:
            layout = lay
        missing = [n for n in range(1, expected + 1) if n not in found]

    cols = layout.cols
    duplicated = _duplicated(tokens, expected, layout)
    rules = detect_column_rules(doc)
    tabs = detect_margin_tabs(doc)

    ordered = sorted(found.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[1][2]))
    questions = []
    for i, (n, key) in enumerate(ordered):
        nxt = ordered[i + 1][1] if i + 1 < len(ordered) else None
        questions.append(QuestionPlan(n, segments_for(doc, cols, rules, key, nxt, tabs),
                                      num_page=key[0], num_rect=key[3]))
    questions.sort(key=lambda q: q.number)
    return ExamPlan(cols, rules, tabs, questions, missing, duplicated, layout.dropped)


def attach_number_boxes(doc: Doc, questions: list[QuestionPlan], max_number: int) -> int:
    """밖에서 만든 계획(crop_rects.json 경로)에도 번호 토큰 자리를 채운다. 채운 개수.

    사각형을 사람이 지정한 회차라도 텍스트 레이어가 살아 있으면 번호 토큰은 찾을 수
    있다. 텍스트가 없는 회차(2025 수능 지구과학Ⅱ처럼 글자가 전부 벡터)는 0을 돌려주고
    아무것도 채우지 않는다 — 없는 것을 지어내지 않는다.
    """
    if not doc.has_text_layer():
        return 0
    tokens = number_tokens(doc, max_number)
    found = _anchors(tokens, column_layout(tokens))
    filled = 0
    for qp in questions:
        a = found.get(qp.number)
        if a is None or a[3] is None or qp.num_rect is not None:
            continue
        qp.num_page, qp.num_rect = a[0], a[3]
        filled += 1
    return filled


def _on_column(tokens, layout: ColumnLayout) -> list:
    """컬럼 시작 자리에 선 토큰만 남긴다 — 자료 상자 안에 들여쓰인 'n.' 을 앵커에서 뺀다.

    이 걸름이 없으면 컬럼 개수를 바로잡아도 사고가 남는다. 실측(통합사회 2025 고1 9월):
    21번의 진짜 번호는 5쪽 오른단(x=289.1)인데 3쪽 자료 상자 안에도 '21.'(x=165.6)이 있고,
    읽기 순서가 쪽 → 컬럼 → y 라 **3쪽 것이 먼저 잡혀 앵커가 됐다.** 그 크롭에도 글자가
    들어 있어 '머리에 번호가 없다' 검사조차 울리지 않는다.

    기준을 컬럼 대표값(최솟값)이 아니라 **최빈 x** 로 잡은 이유는 ColumnLayout 주석에 있다.
    """
    if not layout.anchors_x:
        return []
    ax = layout.anchors_x
    return [t for t in tokens if abs(t[1] - ax[nearest_col(ax, t[1])]) <= ANCHOR_X_TOL]


def _anchors(tokens, layout: ColumnLayout) -> dict[int, tuple[int, int, float, tuple | None]]:
    """번호 → (page, col, y, bbox). 같은 번호가 여러 번 나오면 읽기 순서상 첫 것을 쓴다.

    bbox 는 그 앵커가 된 번호 토큰의 PDF 좌표 사각형이다(loose 토큰이면 None).
    앵커 y 만 남기고 x·폭·높이를 버리면 크롭 안에서 번호가 어디 있었는지 되짚을 수
    없어, 학습지 번호 다시 매기기가 원리적으로 불가능해진다.
    """
    if not layout.cols:
        return {}
    ax = layout.anchors_x
    best: dict[int, tuple[int, int, float, tuple | None]] = {}
    for t in sorted(_on_column(tokens, layout), key=lambda t: (t[0], nearest_col(ax, t[1]), t[2])):
        pidx, x, y, n, box = t
        if n not in best:
            best[n] = (pidx, nearest_col(ax, x), y, box)
    return best


def _duplicated(tokens, expected: int, layout: ColumnLayout) -> list[int]:
    """같은 번호가 컬럼 자리에 두 번 이상 서 있는 경우.

    자료 상자 안의 가짜 번호는 세지 않는다 — 그것까지 세면 회차마다 '중복' 경고가
    쏟아져(실측 2025 고1 9월 6건) 정작 진짜 중복 인쇄가 그 안에 묻힌다.
    """
    seen: dict[int, int] = {}
    for t in _on_column(tokens, layout):
        n = t[3]
        seen[n] = seen.get(n, 0) + 1
    return sorted(n for n, c in seen.items() if c > 1 and n <= expected)
