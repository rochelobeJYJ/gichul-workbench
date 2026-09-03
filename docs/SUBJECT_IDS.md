# SUBJECT_IDS — 탐구 영역 17과목 값의 출처와 공백

이 문서는 `subjects/<slug>/subject.json` 에 채운 값이 **어디서 왔는지**와 **아직 비어 있는 것**을
정리한다. 다음 사람이 `gw download --probe` 로 메울 목록이 마지막 절에 있다.

지구과학Ⅰ·Ⅱ(`earth-science-i`, `earth-science-ii`)는 다른 에이전트가 만든 레퍼런스 구현이라
이 문서가 값을 바꾸지 않는다 — 표에는 비교용으로만 같이 싣는다.

## 1. EBSi `subject_id` — KICE(수능·모평) 계열 vs 학평(전국연합) 계열

EBSi 다운로드는 두 계열이 완전히 다른 `subject_id` 를 쓴다(같은 과목이라도 값이 다르다).
`scripts/gw.py download` 는 이 저장소 밖의 스크립트이므로, 이 표의 출처는 전부
`C:/Users/user/.codex/skills/exam-source-downloader/scripts/download_exam_sources.py` 안의
세 표 — `KICE_SUBJECTS`(수능·모평, 과탐 8개뿐), `NATIONAL_2015_SUBJECTS`(학평, 2015개정 17개 중 13개),
`NATIONAL_HIGH1_SUBJECTS`(고1 공통과목, 탐구 아님 — 이 표에는 없음) — 이다.

**KICE(수능·모평) 계열 사회탐구 9과목의 `subject_id` 는 이 스크립트를 포함해 어디에도 없었다.**
추측해서 채우지 않았다 — `subject.json` 의 `providers.ebsi.subject_id` 는 빈 문자열로 남겨두고,
학평 계열 값은 `providers.ebsi.national` 아래에 따로 보관했다(학평도 그 자체로 유용한 자원이라
손실 없이 살려둠).

| slug | label | 영역 | KICE(수능·모평) subject_id | 학평 subject_id | form_field | area_order |
|---|---|---|---|---|---|---|
| physics-i | 물리학Ⅰ | 과학탐구 | `140115` | `140113` | sFormPartSci | 6 |
| physics-ii | 물리학Ⅱ | 과학탐구 | `140116` | *(없음 — 학평 미시행)* | sFormPartSci | 6 |
| chemistry-i | 화학Ⅰ | 과학탐구 | `156` | `17042` | sFormPartSci | 6 |
| chemistry-ii | 화학Ⅱ | 과학탐구 | `157` | *(없음 — 학평 미시행)* | sFormPartSci | 6 |
| life-science-i | 생명과학Ⅰ | 과학탐구 | `158` | `17043` | sFormPartSci | 6 |
| life-science-ii | 생명과학Ⅱ | 과학탐구 | `159` | *(없음 — 학평 미시행)* | sFormPartSci | 6 |
| *(참고) earth-science-i* | 지구과학Ⅰ | 과학탐구 | `154` | `17041` | sFormPartSci | 6 |
| *(참고) earth-science-ii* | 지구과학Ⅱ | 과학탐구 | `155` | *(없음 — 학평 미시행)* | sFormPartSci | 6 |
| life-ethics | 생활과 윤리 | 사회탐구 | **미확인** ⚠ | `50615` | sFormPartSoc | 5 |
| ethics-thought | 윤리와 사상 | 사회탐구 | **미확인** ⚠ | `50616` | sFormPartSoc | 5 |
| korean-geography | 한국지리 | 사회탐구 | **미확인** ⚠ | `17029` | sFormPartSoc | 5 |
| world-geography | 세계지리 | 사회탐구 | **미확인** ⚠ | `50618` | sFormPartSoc | 5 |
| east-asian-history | 동아시아사 | 사회탐구 | **미확인** ⚠ | `50613` | sFormPartSoc | 5 |
| world-history | 세계사 | 사회탐구 | **미확인** ⚠ | `50617` | sFormPartSoc | 5 |
| economics | 경제 | 사회탐구 | **미확인** ⚠ | `17037` | sFormPartSoc | 5 |
| politics-law | 정치와 법 | 사회탐구 | **미확인** ⚠ | `140109` | sFormPartSoc | 5 |
| society-culture | 사회·문화 | 사회탐구 | **미확인** ⚠ | `17038` | sFormPartSoc | 5 |

과학탐구Ⅱ 세 과목(물리학Ⅱ·화학Ⅱ·생명과학Ⅱ, 지구과학Ⅱ 포함)은 학평(전국연합학력평가)이
애초에 시행하지 않는 과목이라 `NATIONAL_2015_SUBJECTS` 에 항목이 없다 — 공백이 아니라
**실측 대상 자체가 없는 것**이니 probe 할 필요 없다.

## 2. `kice.aliases` — 평가원 파일명 오기 방어표

전부 `D:/codex_work/programs/kice_down/main.py` 의 `OLD_SUBJECT_FILTERS` 에서 손실 없이 옮겼다
(2014~2020년 시기 "과탐(과목 I)" 형식이나 옛 CP949 파일명 표기를 방어하기 위한 값들). 과탐 6과목은
지구과학과 같은 패턴 — 영문 I/II, 소문자, 띄어쓰기, 로마숫자, 아라비아숫자 변형까지 전부 포함.
사탐 9과목은 원본 표에 있는 값 그대로(대개 붙여쓰기·띄어쓰기 두 변형, 정치와 법만 옛 과목명
"법과 정치" 변형이 추가로 있음).

## 3. `standard_prefixes` — 성취기준 코드 접두사 (교육과정 PDF 원문 확인)

전부 `curriculum/pdf/` 안의 PDF에서 `[코드번호-번호]` 형태의 실제 성취기준을 grep 해서 확인했다
(정규식으로 전수 추출 후 각 접두사의 첫 등장 위치가 목차 순서와 일치하는지도 대조함).
**추측한 값은 하나도 없다** — 확인 못 한 건 아래 "공백"에 명시하고 비워뒀다.

| slug | 2015 과목명 | 2015 접두사 | 출처 PDF | 2022 과목명(대응) | 2022 접두사 | 출처 PDF |
|---|---|---|---|---|---|---|
| physics-i | 물리학Ⅰ | `12물리Ⅰ` | [별책9] 과학과 교육과정(2015).pdf | *(null — Ⅰ/Ⅱ 구분 소멸)* | `12역학`, `12전자` | [별책9] 과학과 교육과정(2022).pdf |
| physics-ii | 물리학Ⅱ | `12물리Ⅱ` | 〃 | *(null)* | `12역학`, `12전자` | 〃 |
| chemistry-i | 화학Ⅰ | `12화학Ⅰ` | 〃 | *(null)* | `12물에`, `12반응` | 〃 |
| chemistry-ii | 화학Ⅱ | `12화학Ⅱ` | 〃 | *(null)* | `12물에`, `12반응` | 〃 |
| life-science-i | 생명과학Ⅰ | `12생과Ⅰ` | 〃 | *(null)* | `12세포`, `12유전` | 〃 |
| life-science-ii | 생명과학Ⅱ | `12생과Ⅱ` | 〃 | *(null)* | `12세포`, `12유전` | 〃 |
| *(참고) earth-science-i/ii* | 지구과학Ⅰ/Ⅱ | `12지구`(레퍼런스 값) | — | *(null)* | `12지시`, `12행우` | — |
| life-ethics | 생활과 윤리 | **미확인** ⚠ | *(2015 도덕과 PDF 없음)* | *(null, "현대사회와 윤리"로 개편)* | `12현윤` | [별책6] 도덕과 교육과정.pdf(2022) |
| ethics-thought | 윤리와 사상 | **미확인** ⚠ | *(2015 도덕과 PDF 없음)* | 윤리와 사상(이름 유지) | `12윤사` | 〃 |
| korean-geography | 한국지리 | `12한지` | 별책7_사회과 교육과정(제2018-162호).pdf | *(null, "한국지리 탐구"로 개편)* | `12한탐` | [별책7] 사회과 교육과정.pdf(2022) |
| world-geography | 세계지리 | `12세지` | 〃 | *(null, "세계시민과 지리"로 개편 — 접두사만 우연히 동일)* | `12세지` | 〃 |
| east-asian-history | 동아시아사 | `12동사` | 〃 | *(null, "동아시아 역사 기행"로 개편)* | `12동역` | 〃 |
| world-history | 세계사 | `12세사` | 〃 | 세계사(이름 유지) | `12세사` | 〃 |
| economics | 경제 | `12경제` | 〃 | 경제(이름 유지, 일반→진로선택) | `12경제` | 〃 |
| politics-law | 정치와 법 | `12정법` | 〃 | *(null, "정치"+"법과 사회"로 분리)* | `12정치`, `12법사` | 〃 |
| society-culture | 사회·문화 | `12사문` | 〃 | *(null, "사회와 문화"로 표기 변경)* | `12사문` | 〃 |

**참고**: earth-science-i/ii 의 2015 접두사 `12지구` 는 실제로 [별책9] 과학과 교육과정(2015).pdf
원문을 확인해 보면 `12지과Ⅰ`/`12지과Ⅱ` 로 적혀 있다(2015 개정 지구과학Ⅰ·Ⅱ 성취기준을 grep 해서
직접 확인함 — 표 안 "12지과Ⅰ" 사례: `[12지과Ⅱ01-01] 원시 태양계 성운에서...`). 이 저장소 규칙상
`earth-science-i`/`earth-science-ii` 폴더는 다른 에이전트 소유라 건드리지 않았지만, 다음에 그
폴더를 만지는 사람은 이 불일치를 알고 있어야 한다.

## 4. 공백 — 다음 사람이 채워야 할 것

1. **KICE(수능·모평) 계열 사회탐구 9과목의 `ebsi.subject_id`.** `NATIONAL_2015_SUBJECTS` 는
   학평 계열이라 그대로 쓸 수 없다. 실측 방법:
   ```bash
   python scripts/gw.py download --probe --area 사회탐구 --year 2024 --exam 수능
   ```
   (`standards` 와 마찬가지로 `download --probe` 도 이 저장소 안에는 아직 구현이 없다 — 있으면 이
   명령, 없으면 `download_exam_sources.py` 를 사탐 9과목·family=kice 로 직접 돌려서 EBSi AJAX
   응답에서 `subjIdList` 값을 읽어내야 한다.)
2. **`life-ethics`, `ethics-thought` 의 2015 개정 성취기준 접두사.** `curriculum/pdf/` 에
   2015개정 도덕과 교육과정 원본이 없다 — 있는 `[별책6] 도덕과 교육과정.pdf` 는
   "교육부 고시 제2022-33호"로 2022개정판뿐이다. 세간에는 생활과 윤리가 `12생윤`,
   윤리와 사상이 `12윤사`(2015판에서도 동일할 것으로 보임)로 알려져 있지만, 이 저장소 안에서
   직접 확인하지 못했으므로 채우지 않았다. 2015개정 도덕과 교육과정 PDF를 `curriculum/pdf/` 에
   추가하면 채울 수 있다.
3. **`world-geography` 의 2022 접두사 `12세지` 는 2015 세계지리의 접두사와 우연히 같다.**
   실제로는 서로 다른 과목("세계지리" vs "세계시민과 지리")이니 `classify` 단계에서 2022 매핑을
   자동으로 신뢰하지 말고 한 번 더 확인할 것.
4. `earth-science-i`/`earth-science-ii` 의 2015 접두사(`12지구`)가 PDF 원문(`12지과Ⅰ`/`12지과Ⅱ`)과
   다르다 — 위 3절 참고 각주. 이 저장소가 아닌 다른 에이전트 소관이라 여기서는 기록만 남긴다.
