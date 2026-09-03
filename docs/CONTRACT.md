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
- 없는 종류는 키 자체를 뺀다. `null` 을 넣지 않는다.
- 읽고 쓰는 것은 `scripts/manifest.py` 한 곳에서만 한다. 각 모듈이 직접 파싱하지 않는다.

## 2. 식별자

- `slug` : 과목 슬러그. 소문자 하이픈. 예 `earth-science-ii`, `life-science-i`, `korean-geography`.
- `exam_id` : `{학년도}_{시험}` . 시험은 `수능` `6월모평` `9월모평` `3월학평` `4월학평` `7월학평` `10월학평`.
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
- `question_count` / `points_total` 은 **검증 불변식**이다. 탐구는 20문항 50점.
- `providers` 가 비어 있으면 다운로드 단계에서 사용자에게 물어야 한다.
- `standard_prefixes` 는 **교육과정 원본에서 실측한 값**이어야 한다. 추측해서 적으면 조용히 틀린다.
  실제 사고: 2015개정 지구과학Ⅱ 코드를 `12지구` 로 적었는데 진짜 값은 `12지과Ⅱ` 였다.
  게다가 `12지구` 는 2022개정 '지구과학'(다른 과목)의 실재하는 코드라 대조를 통과해버릴 뻔했다.
  `curriculum/standards/<개정>.json` 의 `code_prefix` 를 보고 채운다.

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
    "2015": {"standard": "12지구03-02", "unit": "…", "confidence": 0.82, "by": "keyword"},
    "2022": {"standard": "12지시02-01", "unit": "…", "confidence": null, "by": "llm"}
  },
  "status": "verified",
  "notes": []
}
```

- `extraction_mode` : `direct` | `ocr` | `vision`. 텍스트 레이어가 없는 회차는 `vision`.
- `status` : `scaffold` (자동 생성만) | `verified` (검수 통과).
- `classification.*.by` : `keyword` | `llm` | `manual`.
- `text` 가 비어 있어도(`vision`) 파이프라인은 끝까지 돌아야 한다. 크롭 이미지가 본체다.
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
| `classify` | 키워드 1차 분류 + LLM 큐 산출 | `items/`, `reports/classify.json` |
| `map`      | 2015↔2022 성취기준 매핑 적용 | `items/` |
| `build`    | 문항집 제작기 HTML 생성 | `output/` |
| `validate` | 구조·정답·불변식 검증 | `reports/validate.json` |

공통 옵션: `--dry-run`, `--force`, `--only <qid|exam_id, ...>`, `--quiet`, `--workspace <경로>`.

- `--only` 는 **qid 와 exam_id 를 모두** 받는다. 모듈마다 문법이 다르면 안 된다.
- `--workspace` 는 기본값이 `workspace/<slug>` 다. 격리 실행에 쓴다.
  이게 없으면 병렬 작업이 서로의 산출물을 밟는다 — 실제로 겪었다.
- `--quiet` 는 요약을 삼키고 리포트 경로 한 줄만 남긴다.

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

큐에 들어가는 조건: 최고점수 < 0.35, 또는 1·2위 점수 차 < 0.15, 또는 매칭 0건.

## 8. 검증 불변식

`validate` 가 확인하는 것:

1. `sum(points) == subject.points_total` (회차별)
2. `len(items) == subject.question_count` (회차별)
3. 정답 3중 대조 일치 — 정답지 표 파싱 / 해설지 파싱 / pdfplumber 표 파싱
4. `[3점]` 표기 ↔ `points == 3`
5. `materials` 선언 ↔ 실제 파일 ↔ 본문 링크 3자 일치
6. 선택지 5개
7. 크롭 이미지 존재 및 최소 크기

3중 대조 중 **두 축만 일치해도 통과시키되 `attention` 에 남긴다.** 세 축이 모두 갈리면 `error`.
