# 새 과목 시작하기

탐구 영역(사탐·과탐) 기준 30분. 판형이 같아서 새로 필요한 건 데이터 두 개뿐이다.

---

## 1. 과목 폴더 만들기

```bash
cp -r subjects/_template subjects/korean-geography
```

슬러그는 소문자 하이픈. 이미 등록된 과목은 `python scripts/gw.py subjects` 로 확인한다.

## 2. `subject.json` 채우기

| 필드 | 어디서 얻나 |
|---|---|
| `label`, `area` | 그냥 적는다 |
| `layout` | 탐구면 `tamgu-1q1block` |
| `question_count`, `points_total` | 탐구는 20 / 50 |
| `curriculum.2015`, `curriculum.2022` | 교육과정 PDF의 과목명. 2022에 없어졌으면 `null` |
| `standard_prefixes` | 교육과정 PDF에서 실제 코드를 확인한다. **추측 금지** |
| `providers.kice.aliases` | `docs/SUBJECT_IDS.md` 에 17과목 별칭표가 있다 |
| `providers.ebsi.subject_id` | 아래 3번 |

## 3. EBSi subject_id 실측

`docs/SUBJECT_IDS.md` 에 있으면 그걸 쓴다. 비어 있으면 실측한다.

```bash
python scripts/gw.py download --probe --area 사회탐구 --year 2024 --exam 수능
```

리포트에 (과목명, subject_id, form_field, area_order) 표가 나온다.
**ID는 계열·학년·연도·교육과정에 따라 다르다.** 오래된 ID를 그대로 재사용하지 마라.

## 4. 키워드 사전 초안 만들기

```bash
python scripts/gw.py standards --draft-keywords --subject korean-geography
```

`subjects/korean-geography/keywords.json` 이 생긴다. **이 파일의 품질이 자동 분류율을 결정한다.**
다듬을 때:

- 평가 동사(`이해한다`, `설명할 수 있다`, `탐구한다`)는 뺀다.
- 그 단원에만 나오는 말을 넣는다. 여러 단원에 걸치는 말은 점수를 흐린다.
- 교과서 용어와 문항 표현이 다르면 **문항 표현**을 넣는다.

엑셀이 편하면 왕복할 수 있다.

```bash
python scripts/gw.py classify --subject korean-geography --export-xlsx keywords.xlsx
# 엑셀에서 편집한 뒤
python scripts/gw.py classify --subject korean-geography --import-xlsx keywords.xlsx
```

## 5. **한 회차만** 먼저 돌린다

전체를 돌리기 전에 반드시. 판형 예외는 과목마다 새로 나온다.

```bash
S=korean-geography
python scripts/gw.py download --subject $S --years 2024 --exams 수능 --kinds problem,answer,solution
python scripts/gw.py detect   --subject $S
python scripts/gw.py crop     --subject $S
python scripts/gw.py extract  --subject $S
python scripts/gw.py validate --subject $S
```

확인할 것 두 가지:

1. **대지 이미지를 눈으로 본다.** `workspace/<slug>/crops/questions/_contact_sheet_*.png`
   선지가 잘린 문항이 하나라도 있으면 크롭부터 고친다. 자동 검증으로는 안 잡힌다.
2. **`validate` 의 배점 합·문항 수가 통과하는가.** 여기서 걸리면 추출이 틀린 것이다.

여기서 새로운 판형 예외를 만났다면 `docs/PITFALLS.md` 에 적고 이슈로 제안한다.
다음 사람이 같은 것을 다시 알아내지 않도록.

## 6. 전체 돌리기

```bash
python scripts/gw.py download --subject $S --years 2015-2026 --exams 수능,6월모평,9월모평 --kinds problem,answer,solution
python scripts/gw.py detect --subject $S
python scripts/gw.py crop --subject $S
python scripts/gw.py extract --subject $S
python scripts/gw.py classify --subject $S
python scripts/gw.py validate --subject $S
python scripts/gw.py build --subject $S
```

`classify` 는 애매한 문항을 `reports/classify_queue.json` 에 모아둔다.
LLM이 그것만 읽고 판정한 뒤 `classify --apply` 로 반영한다.

## 7. 문항집 만들기

`workspace/<slug>/output/` 의 HTML을 브라우저로 열면 끝이다.

---

## 국어·수학·영어는?

판형이 다르다. `docs/LAYOUTS.md` 를 읽어라.
어댑터가 아직 없어서 **함께 만들어야 한다.**
