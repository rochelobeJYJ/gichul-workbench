# 내부 계약 (CONTRACT)

이 문서는 `scripts/` 안의 모든 모듈이 공유하는 규약이다. **여기서 벗어나면 파이프라인이 끊긴다.**
사람이 읽는 사용 설명은 `SKILL.md`, 방법론은 `docs/METHOD.md`에 있다.

## 0. 절대 원칙

1. **과목 하드코딩 금지.** 코드 어디에도 `지구과학`, `earth-science` 문자열이 분기 조건으로 들어가서는 안 된다.
   과목별 차이는 전부 `subjects/<slug>/subject.json` 에 데이터로 존재한다.
2. **LLM은 리포트만 읽는다.** 모든 명령은 `reports/<step>.json` 하나를 남기고, 그 파일만으로 다음 행동이 결정되어야 한다.
   PDF 본문, 380개 파일 목록 따위를 stdout 으로 쏟지 않는다.
3. **원본은 불변.** `workspace/<slug>/sources/` 아래 내려받은 PDF 는 어떤 단계도 수정하지 않는다.
4. **저작물은 커밋되지 않는다.** `workspace/` 는 통째로 `.gitignore` 대상이다.
5. **조용한 기본값을 두지 마라.** 값이 없으면 그럴듯한 값을 만들지 말고 멈춰라.
   `subject.question_count or 20` 은 45문항 과목에서 25문항을 소리 없이 버리고 성공 보고를 한다.
   무엇이 없는지 말하고 종료하는 편이 언제나 낫다.

## 1. 경로 규약

```
<repo>/
  curriculum/pdf/            교육과정 원본 PDF (gitignore, 사용자가 넣음)
  curriculum/standards/      파싱 결과 JSON (커밋됨)
  subjects/<slug>/           과목 정의 (커밋됨)
  workspace/<slug>/          사용자 작업 산출물 (gitignore)
```

`workspace/<slug>/` 내부:

```
sources/<exam_id>/problem.pdf | answer.pdf | answer.png | solution.pdf | manifest.json
pages/<exam_id>/p003.png        페이지 렌더 캐시 (재실행 비용 절감, 선택)
crops/questions/<qid>.png       문항 전체 크롭
crops/materials/<qid>_m1.png    자료(그림/표) 크롭
items/<qid>.json                문항 데이터 단일 원천
reports/<step>.json             단계별 리포트
output/                         최종 산출물 (HTML 문항집 등)
```

### `sources/<exam_id>/manifest.json`

`detect` 가 쓰고 `crop`·`extract` 가 읽는다. **스키마가 없어서 세 모듈이 각자 추측해 읽던 자리다.**

```json
{
  "schema_version": 2,
  "exam_id": "2024_수능",
  "slug": "earth-science-ii",
  "label": "지구과학Ⅱ",
  "files": {"problem": "problem.pdf", "answer": "answer.pdf", "solution": "solution.pdf"},
  "detected": {"year": 2024, "exam": "수능", "grade": 3, "by": "cover-text"},
  "pages": {"problem": 12, "solution": 24},
  "provider": "ebsi",
  "verified": true
}
```

- `files` 의 값은 **파일명만** 담는다(경로 아님). 같은 폴더에 있다.
- 프로바이더가 남기는 증거 필드(`sha256`, `source_url`, `target_cd`, `paper_id`)는 지우지 마라.
  `paper_id` 는 EBSi 목록 행마다 붙는 시험지 ID 로, **오답률 표를 찾는 유일한 열쇠**다.
  파일 URL 에는 들어 있지 않아서 이때 안 남기면 다시 목록을 훑어야 한다.
  같은 과목이라도 학년 목록마다 `subject_id` 가 달라서, `target_cd` 가 없으면
  '이 파일이 어느 목록에서 왔나' 를 되짚을 수 없다.
- 없는 종류는 키 자체를 뺀다. `null` 을 넣지 않는다.
- 읽고 쓰는 것은 `scripts/manifest.py` 한 곳에서만 한다. 각 모듈이 직접 파싱하지 않는다.

## 2. 식별자

- `slug` : 과목 슬러그. 소문자 하이픈. 예 `earth-science-ii`, `life-science-i`, `korean-geography`.
- `exam_id` : `{학년도}_{시험}` . 시험 종류는 11종이다 —
  `수능` `6월모평` `9월모평` 과 학평 `3월` `4월` `5월` `6월` `7월` `9월` `10월` `11월`.
  학평이 8개인 이유: **학년마다 치르는 회차가 다르고, 경기 주관 회차는 이름이 해마다 옮겨다닌다.**
  고3 경기 회차는 2024년까지 4월학평, 2025·2026년은 5월학평이다.
  고2 경기 회차는 2023년 11월학평(12-19 시행), 2024·2025년은 10월학평이다.
  둘 다 갖고 있지 않으면 그 해 회차가 조회 단계에서 통째로 사라진다.
  `11월학평`이 정렬에서 `수능` 뒤인 것은 오타가 아니다 — 실제 시행일이 수능 뒤다.
  예 `2024_수능`, `2026_9월모평`.
- `qid` : `{exam_id}_{번호 2자리}` . 예 `2024_수능_07`.
  **학평은 학년이 붙는다**: `2025_고2_3월학평_07`. 규칙은 `subject.json.exam_id_style` 이 아니라
  `common/ids.py` 의 `make_exam_id()` 한 곳에서만 만든다.

## 3. `subjects/<slug>/subject.json`

```json
{
  "slug": "earth-science-ii",
  "label": "지구과학Ⅱ",
  "area": "과학탐구",
  "curriculum": {"2015": "지구과학Ⅱ", "2022": null},
  "layout": "tamgu-1q1block",
  "question_count": 20,
  "points_total": 50,
  "providers": {
    "ebsi": {"subject_id": "155", "form_field": "sFormPartSci", "area_order": "6"},
    "kice": {"area": "과학탐구", "aliases": ["지구과학II", "지구과학Ⅱ", "지구 과학 II", "지구과학2"]}
  },
  "standard_prefixes": {"2015": ["12지과Ⅱ"], "2022": ["12지시", "12행우"]},
  "notes": ""
}
```

- `layout` 은 크롭·추출 전략 선택자다. 현재 값: `tamgu-1q1block` (검증됨),
  `passage-group` (국어·영어, 실험적), `math-mixed` (수학, 실험적).
- `question_count` / `points_total` 은 **검증 불변식**이다. 탐구 선택과목은 20문항 50점.
- `overrides`(선택): 회차마다 불변식이 갈리는 과목을 위한 자리.
  ```json
  "overrides": [{"when": {"exam_id": "^202[34]_|^2025_고1_3월"}, "question_count": 20}]
  ```
  통합과목이 실제로 그렇다 — 2025년 6월 회차부터 20문항에서 **25문항**으로 바뀌었고
  같은 슬러그 안에 두 판형이 공존한다. **경계가 달이 아니다**(2026년은 3월부터 25문항).
  이 자리가 없으면 옛 회차에서 `crop` 이 21~25번 앵커를 못 찾아 실패하고,
  그 실패가 `extract` 로 번져 정답이 통째로 `null` 이 된다(실측).
- `curriculum.<개정>` 은 **문자열 또는 문자열 목록**이다. 한 과목이 다음 개정에서 둘로 갈릴 수 있다 —
  2022개정에서 통합과학은 통합과학1·2, 통합사회는 통합사회1·2 두 과목이다.
- `providers` 가 비어 있으면 다운로드 단계에서 사용자에게 물어야 한다.
- `providers.ebsi.national` — 전국연합학력평가(학평)용. **키는 학년(`"1"` `"2"`)이다.**

  ```json
  "national": {"2": {"subject_id": "17042"},
               "1": {"absent": true, "why": "고1 목록에는 통합과학만 있다(실측)"}}
  ```

  `"3"` 은 두지 않는다 — 고3은 최상위 `subject_id` 하나로 수능·모평·고3 학평이 전부 조회된다.
  같은 값을 두 곳에 적으면 언젠가 갈라진다.
  키를 EBSi 내부 코드(`targetCd`)가 아니라 학년으로 잡은 이유: 사용자가 입력하고 `exam_id` 에 남는 축이
  학년이다. 변환은 코드 한 곳에만 둔다.
  `{"absent": true, "why": "..."}` 는 **'아직 안 채웠다'와 '실측했고 없다'를 가른다.**
  빈 칸으로 두면 도구가 '실측해서 채워라'라고 안내하는데 채울 값이 없다. 0절의 반대편이라 계약 값이 맞다.
- `standard_prefixes` 는 **교육과정 원본에서 실측한 값**이어야 한다. 추측해서 적으면 조용히 틀린다.
  실제 사고: 2015개정 지구과학Ⅱ 코드를 `12지구` 로 적었는데 진짜 값은 `12지과Ⅱ` 였다.
  게다가 `12지구` 는 2022개정 '지구과학'(다른 과목)의 실재하는 코드라 대조를 통과해버릴 뻔했다.
  `curriculum/standards/<개정>.json` 의 `code_prefix` 를 보고 채운다.
- ⚠ **접두사로 개정을 가를 수 없는 과목이 있다.** 성취기준 코드는 개정마다 새로 매겨지지 않아서
  2015·2022 가 같은 접두사를 쓰는 과목이 원본 기준 9개다
  (`12경제 12과사 12사문 12사탐 12생활 12세사 12세지 12여지 12윤사`).
  등록된 것만 5개 — economics · society-culture · world-history · world-geography · ethics-thought.
  **`code.startswith(prefix)` 로 개정을 판정하면 이 과목들에서 조용히 틀린다.**
  개정은 데이터 구조에서 읽어라 — `keywords.json` 의 개정 층, `items` 의 `classification.<개정>`.
- ⚠⚠ **접두사가 다른 개정의 코드를 삼키기도 한다.** 2015 통합과학은 `10통과01-01`,
  2022 는 `10통과1-01-01` 이라 `startswith("10통과")` 가 **2022 코드까지 잡는다.**
  실측: 등록된 19과목 전 접두사로 걸린 코드 1,021개 중 **104개가 남의 개정 것**이었다
  (통합과목 61 · world-geography 15 · ethics-thought 9 · world-history 7 · economics 7 · society-culture 5).
  **접두사 문자열 비교로 개정을 판정하지 마라.** `curriculum/standards/<개정>.json` 이
  어느 코드가 어느 개정인지 알고 있다. 판정은 한 곳(`Subject.code_scope`)에서만 한다.
- `glyph_smells`(선택): 이 과목에만 적용할 손상 탐지 규칙.
  `[{"id": "...", "pattern": "...", "why": "...", "context": "...", "off": true}]`
  검증기의 기본 목록과 **`id` 로 병합**하고 같은 `id` 면 과목 것이 이긴다. `off: true` 는 기본 규칙을 끈다.
  끄는 수단이 필요한 이유: 오탐이 나는 검사는 결국 아무도 안 본다.
  예 — 화학·물리는 `angle_mark`(위도 `90N` 탐지)를 끈다. `12N`(몰 수)·`10N·s`(충격량)가 걸린다.

### `subjects/<slug>/keywords.json`

```json
{
  "_meta": {"schema": "...", "learned_at": "...", "learned_from": {"2015": ["qid", ...]}},
  "2015": {"12윤사01-01": {"curriculum": ["용어", ...],
                          "learned": [{"term": "...", "weight": 0.6, "df": 5, "lor": 2.1}]}},
  "2022": {"12윤사01-01": {"curriculum": [...], "learned": []}}
}
```

- **개정이 코드보다 바깥 층이다.** 위의 접두사 충돌 때문이다.
  실측: 코드만으로 키를 잡았더니 윤리와 사상 22+15=37개가 23개로 줄었고,
  5개 과목 합계 173개가 108개가 됐다. classify 는 두 개정에 **글자 그대로 같은 사전**을 쓰고 있었다.
- 개정 층인지 코드 층인지는 **키의 모양**(`^\d{4}$`)으로 판정한다.
  `_meta.schema` 같은 자기 신고 문자열을 믿지 마라.
- 읽고 쓰는 것은 `scripts/keywordsio.py` 한 곳에서만 한다.
- 옛 두 형태(평면형 `{"코드": [용어]}`, 구조형 `{"코드": {"curriculum","learned"}}`)는 읽기에서 계속 지원한다.
  개정은 `standard_prefixes` 로 역추정하고, 못 가르면 리포트에 warn 을 남긴다.

## 4. `items/<qid>.json`

```json
{
  "qid": "2024_수능_07",
  "slug": "earth-science-ii",
  "exam_id": "2024_수능",
  "number": 7,
  "points": 3,
  "answer": 4,
  "answer_symbol": "④",
  "source": {"pdf": "sources/2024_수능/problem.pdf", "page": 4,
             "rect": [58.2, 402.1, 300.4, 690.8]},
  "crop": "crops/questions/2024_수능_07.png",
  "materials": ["crops/materials/2024_수능_07_m1.png"],
  "text": {"stem": "...", "boxed": "...", "choices": ["...", "...", "...", "...", "..."]},
  "extraction_mode": "direct",
  "classification": {
    "2015": {"standard": "12지과Ⅱ03-02", "unit": "…", "confidence": 0.867, "by": "keyword",
             "ext": {"score": 0.94, "calibrated_at": "2026-09-03T18:40:00"}},
    "2022": {"standard": "12지시02-01", "unit": "…", "confidence": null, "by": "llm"}
  },
  "status": "verified",
  "notes": []
}
```

- `extraction_mode` : `direct` | `ocr` | `vision`. **회차 단위** 값이다 —
  문제지 한 권에 쓸 만한 텍스트 레이어가 있는가를 말한다.
  같은 회차 안에서 문항마다 갈리는 부분 손실은 담지 못한다. 그 자리는 아래 `ext.*_source` 다.
- `ext.<칸>_source: "image"` : **그 칸의 본체가 텍스트가 아니라 크롭 이미지다.**
  칸 이름은 `text` 의 것을 따른다 — `ext.choices_source`, `ext.boxed_source`
  (발문이 그림이면 `stem_source` 로 확장된다).
  **정상이면 키 자체가 없다.** 멀쩡한 문항까지 `"text"` 로 채우면 380문항이 정보 없는 필드를 갖는다.
  실측 근거: 화학·물리는 선택지가 분수·도형인 문항이 회차당 2~4건이고,
  NFKC 가 ①을 `1` 로 눕히므로 분모 `2` 와 라벨 `②` 를 원리적으로 못 가른다.
  세 경우를 포함한다 — **선지가 조각만 남은 경우**, **선지가 표라 열을 잃지 않고는 한 줄로 못 펴는 경우**,
  **선지 자리가 텍스트에 통째로 없는 경우**.
- `points` 는 **정수가 아닐 수 있다.** 통합과목 25문항 판형은 1.5 / 2 / 2.5 세 계단이다.
  다만 **정수로 떨어지는 값은 int 로 적는다**(`2.0` 이 아니라 `2`) —
  안 그러면 이미 확정된 코퍼스의 파일 해시가 값이 같은데도 전부 달라진다.
- `status` : `scaffold` (자동 생성만) | `verified` (검수 통과).
- `classification.*.by` : `keyword` | `llm` | `manual`.
- `classification.*.confidence` : **원점수가 아니라 실측 정확도의 95% 하한**(Wilson).
  보정(`classify --calibrate`) 곡선에서 읽어온다. 보정 전이면 `null` 이다.
  원점수는 `ext.score` 에 따로 둔다.
  이유: 초안 사전이 **틀린 답에 0.96 을 붙이는** 사고가 실제로 있었다. 점수를 신뢰도로
  쓰면 LLM 이 검토를 건너뛴다. 하한은 정의상 1.0 이 될 수 없고 표본이 늘면 점추정에 수렴한다.
- `text` 가 비어 있어도(`vision`) 파이프라인은 끝까지 돌아야 한다. 크롭 이미지가 본체다.
- `number_box` : 크롭 이미지 안에서 **원래 문항 번호가 차지하는 자리**.
  `[left, top, width, height]`, 전부 **이미지 크기 대비 0~1 비율**.
  비율인 이유는 이미지가 어떤 폭으로 표시되든 CSS 로 그대로 겹칠 수 있어야 하기 때문이다.
  문항집에서 번호를 1,2,3… 으로 다시 매길 때 이 자리를 덮는다.
  번호를 못 찾았거나 값이 이상하면(음수·1 초과·폭 0) **키를 넣지 않는다.**
  텍스트 레이어가 없는 회차(`vision`)에는 번호 토큰이 없어 원리적으로 만들 수 없다.
- `ext.error_rate` : EBSi 문항별 오답률. `0~100` 실수.
  `ext.error_rate_source` 에 출처(프로바이더·URL·수집 시각·paper_id 등),
  `ext.error_rate_choices` 에 선택지별 응답 비율 5개.
  후자는 **`정답 칸 비율 == 100 − 오답률`** 자기검증을 통과했을 때만 싣는다.
- ⚠ **오답률은 전 문항을 덮지 않는다.** EBSi 는 회차·과목당 **상위 15문항만** 공개한다
  (15위가 동점이면 16문항이 되기도 한다). 20문항 중 다섯에 키가 없는 것이 **정상 상태**다.
  이걸 결측으로 보고 채우려 들면 안 된다.
- **계약에 없는 필드는 `ext` 아래에 넣는다.** 최상위에 마음대로 키를 늘리면 검증기가 무엇을
  검사해야 하는지 알 수 없게 된다. 예: `ext.answer_check`(정답 3중 대조의 축별 원본값),
  `ext.text_raw`, `ext.mapping`(매핑 근거). `ext` 안의 것을 소비하는 모듈이 생기면
  그때 이 문서로 승격한다.

## 5. 리포트 계약 — `reports/<step>.json`

**모든 명령이 반드시 이 형태로 남긴다.** LLM 이 읽는 유일한 출력이다.

```json
{
  "step": "crop",
  "slug": "earth-science-ii",
  "ok": true,
  "counts": {"expected": 380, "done": 380, "failed": 0, "skipped": 0},
  "attention": [
    {"id": "2025_수능_11", "why": "텍스트 레이어 없음 — vision 모드로 처리함", "severity": "info"}
  ],
  "artifacts": ["crops/questions", "crops/questions/_contact_sheet.png"],
  "next": "python scripts/gw.py extract --subject earth-science-ii",
  "elapsed_sec": 41.2
}
```

- `attention` 은 **최대 30건**까지만 담는다. 초과하면 `attention_truncated: N` 을 함께 넣는다.
  LLM 컨텍스트를 지키기 위한 상한이다.
- `severity` : `info` | `warn` | `error`.
- `ok:false` 면 `next` 는 복구 명령이어야 한다.
- stdout 에는 리포트 경로와 한 줄 요약만 출력한다.
- **진행률은 stderr 로만 나간다.** stdout 은 리포트 경로와 한 줄 요약이 전부다.

## 6. CLI 계약

단일 진입점 `scripts/gw.py`.

```
python scripts/gw.py <command> --subject <slug> [옵션]
```

| command | 하는 일 | 주 산출 |
|---|---|---|
| `subjects` | 등록된 과목 목록·상태 | stdout 표 |
| `download` | 문제지·정답·해설 내려받기 | `sources/` |
| `detect`   | 메타데이터 인식 + 문제지↔정답 짝짓기 | `sources/*/manifest.json` |
| `crop`     | 문항·자료 크롭 + 대지(contact sheet) | `crops/` |
| `extract`  | 본문·정답·배점 추출 (3중 대조) | `items/` |
| `rates`    | EBSi 문항별 오답률 수집 | `items/`, `reports/rates.json` |
| `classify` | 키워드 1차 분류 + LLM 큐 산출 | `items/`, `reports/classify.json` |
| `map`      | 2015↔2022 성취기준 매핑 적용 | `items/` |
| `build`    | 문항집 제작기 HTML 생성 | `output/` |
| `validate` | 구조·정답·불변식 검증 | `reports/validate.json` |

공통 옵션: `--dry-run`, `--force`, `--only <qid|exam_id, ...>`, `--quiet`, `--workspace <경로>`.

- `--only` 는 **qid 와 exam_id 를 모두** 받는다. 모듈마다 문법이 다르면 안 된다.
- `--workspace` 는 기본값이 `workspace/<slug>` 다. 격리 실행에 쓴다.
  이게 없으면 병렬 작업이 서로의 산출물을 밟는다 — 실제로 겪었다.
- `--quiet` 는 요약을 삼키고 리포트 경로 한 줄만 남긴다. **진행률도 함께 꺼진다.**
- 진행률은 `--quiet` 이거나 **stdout·stderr 중 하나라도 tty 가 아니면** 그리지 않는다.
  stderr 만 보면 `| cat` 으로 넘겨도 계속 그려지기 때문에 둘 다 본다.
  그리는 코드는 `scripts/common/progress.py` 한 곳뿐이고, 세는 단위는 사용자 단위다
  (문항·회차·쪽·파일·문서·이미지·과목).
- `GW_PROGRESS=1` 이면 tty 가 아니어도 그리고 `0` 이면 무조건 끈다.
  tty 를 만들 수 없는 자동 검증용이며 **`--quiet` 이 항상 이긴다.**

## 7. 하이브리드 분류 계약

`classify` 는 LLM 을 호출하지 않는다. **LLM 이 처리할 목록만 만든다.**

```json
{
  "step": "classify",
  "counts": {"total": 380, "auto": 271, "queued": 109},
  "queue_file": "reports/classify_queue.json"
}
```

`classify_queue.json` 은 LLM 이 한 번에 읽을 수 있도록 문항당 **본문 400자 + 후보 성취기준 3개**만 담는다.
LLM 은 판정 결과를 `classify_result.json` 으로 되돌려주고, `gw.py classify --apply` 가 그것을 `items/` 에 반영한다.

큐에 들어가는 조건: 최고점수 < 임계값, 또는 1·2위 점수 차 < 0.15, 또는 매칭 0건.

**임계값은 고정값이 아니라 보정에서 나온다.**

- `subjects/<slug>/calibration.json` 이 없으면 **자동확정을 하지 않는다.** 전부 큐로 보내고
  `attention` 에 warn 을 남긴다. 보정되지 않은 사전의 점수는 신뢰할 근거가 없다.
- 있으면 그 권장 임계값을 쓴다. 단 **0.35 아래로는 내려가지 않는다**(계약 하한).
- 권장 임계값은 "목표 정확도(0.85)를 표본 10건 이상으로 지키는 가장 낮은 임계값"이다.
  그런 임계값이 없으면 보정 결과는 `auto_confirm: false` 이고 자동확정은 계속 꺼진다.
- 표본 하한 10은 타협 대상이 아니다. 5건으로 두었더니 `5/5 = 정확도 1.00` 이 나와
  임계값 0.90 을 권했는데, 5/5 의 95% 하한은 0.57 이다. 보정기가 보정하려던 바로 그
  사고를 되풀이할 뻔했다.

### 사전은 라벨된 문항에서 학습한다

`classify --learn` 이 `by` 가 `manual`·`llm` 인 문항에서 변별력 있는 용어를 뽑아
`keywords.json` 의 `learned` 칸에 넣는다(`curriculum` 칸은 교육과정 초안 몫).

실측(지구과학Ⅱ, 학습 8회차 / 채점 4회차 홀드아웃 80문항):
성취기준 정확도 0.400 → 0.888, 단원 정확도 0.738 → 0.950, top-3 0.500 → 0.913.

`standards --draft-keywords` 는 `--force` 로도 `learned` 를 지우지 않는다. 초안(`curriculum` 칸)만 덮어쓴다.
**이것은 계약이다** — 초안은 교육과정 문장에서 언제든 다시 뽑을 수 있지만,
학습분은 사람이 판정한 라벨이 있어야만 다시 만들 수 있다.

`Subject.keywords()` 는 개정을 **필수 인자**로 받는다. 기본값(양쪽 병합)을 두면
부르는 쪽이 다시 두 교육과정을 섞는다 — 0절 '조용한 기본값' 의 정확한 재발 지점이다.

**학습에 쓴 문항으로 채점하면 안 된다.** `--learn` 이 `_meta.learned_from` 에 학습 qid 를
남기고 `--calibrate` 가 그것을 빼고 채점한다. 홀드아웃이 20건 미만이면 임계값을 권하지 않는다.

## 8. 검증 불변식

`validate` 가 확인하는 것:

1. `sum(points) == subject.points_total` (회차별). **등호가 아니라 허용오차 비교다** —
   배점이 실수일 수 있다.
2. `len(items) == subject.question_count` (회차별). `overrides` 가 있으면 회차별 값을 쓴다.
3. 정답 3중 대조 일치 — 정답지 표 파싱 / 해설지 파싱 / pdfplumber 파싱.
   세 번째 축은 경로가 셋이다: `extract_tables`(괘선 표, 배점도 함께 준다)
   → `extract_words`(원문자 정답표) → layout 텍스트.
   가운데 경로가 학평처럼 **정답지가 PNG 인 회차**의 축을 되살린다 —
   `extract_words` 에는 원문자 ①~⑤ 가 NFKC 에 눕기 전 상태로 남아 있어서
   정답을 원문자로만 인정하면 **배점이 정답 자리에 들어앉는 사고가 원리적으로 불가능해진다.**
4. `[N점]` 표기 ↔ `points`. 표기는 `[1.5점]` 처럼 소수일 수 있다.
   ⚠ **'표기 없는 문항 = 기본 배점' 이 성립하지 않는 판형이 있다.**
   배점 계단이 셋인 판형은 25문항 **전부**에 표기가 붙는다(실측 2025·2026 6개 회차).
5. `materials` 선언 ↔ 실제 파일 ↔ 본문 링크 3자 일치
6. 선택지 5개.
   단 `ext.choices_source == "image"` 이고 `choices` 가 비면 **error 가 아니라 warn(전사 대기)**.
   코드로 고칠 수 없는 것을 error 로 올리면 리포트 전체가 안 읽힌다.
   표시가 있는데 `choices` 가 남아 있으면 표시와 데이터가 어긋난 것이므로 그대로 error.
7. 크롭 이미지 존재 및 최소 크기

3중 대조 중 **두 축만 일치해도 통과시키되 `attention` 에 남긴다.** 세 축이 모두 갈리면 `error`.

⚠ **학평에서는 2축이 구조적 상한이다.** 학평 정답지는 PNG 로만 제공되고 텍스트가 0자라
pdfplumber 의 표 추출 경로를 원리적으로 못 탄다(실측 5/5 회차).
따라서 '두 축만 일치' 는 학평에서 예외가 아니라 **상시 경로**이고, 회차마다 warn 이 하나씩 나온다.
그 대신 정답지 PNG 의 원문자 픽셀 대조가 5/5 회차에서 20/20 정확했다 — 이 축이 없으면 학평은 1축이다.
그리고 **학평 정답표에는 배점 칸이 없다**(평가원 3칸 vs 학평 2칸). 배점은 문제지의 `[3점]` 한 축에서만 온다.
