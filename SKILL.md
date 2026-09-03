---
name: gichul-workbench
description: 한국 수능·모의평가 기출문항을 내려받아 문항별로 크롭·전사하고, 교육과정 성취기준에 자동 분류한 뒤 인쇄 가능한 문항집을 만든다. 사회탐구·과학탐구 17과목 지원. 기출 문항집 제작, 단원별 학습지, 기출 아카이브 구축, 2015→2022 교육과정 재매핑 작업에 쓴다.
---

# 기출 문항 작업대 (gichul-workbench)

기출 PDF에서 문항집까지 한 줄씩 명령으로 간다. 사회탐구·과학탐구 17과목.
지구과학Ⅰ·Ⅱ는 760문항 전사·매핑이 끝나 있는 레퍼런스 구현이다.

## 시작할 때 (매번)

```bash
git -C <repo> fetch -q && git -C <repo> log HEAD..origin/main --oneline
```

새 커밋이 있으면 사용자에게 알리고 `git pull` 을 권한다. 판형 예외 대응이 자주 추가된다.

처음이면 의존성부터:

```bash
python scripts/bootstrap.py
```

## ★ 토큰 규칙 — 이걸 어기면 이 도구를 쓰는 의미가 없다

1. **PDF를 직접 읽지 마라.** 스크립트가 읽는다.
2. **`workspace/` 의 파일을 훑지 마라.** 380개짜리 폴더다.
3. **`reports/<step>.json` 만 읽어라.** 각 명령이 남기는 요약이고, 그 안의 `next` 가 다음 명령이다.
4. 명령이 실패하면 리포트의 `attention` 을 보고 고친다. 소스를 먼저 뒤지지 마라.

## 공정

```
download → detect → crop → extract → classify → map → validate → build
```

```bash
S=earth-science-ii
python scripts/gw.py subjects                    # 등록된 과목과 준비 상태
python scripts/gw.py download --subject $S --years 2020-2026 --exams 수능,6월모평,9월모평 --kinds problem,answer,solution
python scripts/gw.py detect   --subject $S
python scripts/gw.py crop     --subject $S
python scripts/gw.py extract  --subject $S
python scripts/gw.py classify --subject $S        # 키워드 자동 + 애매한 것은 큐로
python scripts/gw.py map      --subject $S --revision 2022
python scripts/gw.py validate --subject $S
python scripts/gw.py build    --subject $S
```

사용자가 이미 PDF를 갖고 있으면 `download` 를 건너뛰고 `detect --input <폴더>` 로 시작한다.

## 네가 실제로 해야 하는 일은 셋뿐이다

**1. 크롭 QA.** `crops/questions/_contact_sheet_*.png` 를 **한 장만** 본다.
선지가 잘린 문항이 있는지 확인한다. 자동 검증으로는 안 잡힌다. 사용자에게도 보여줘라.

**2. 분류 큐 판정.** `reports/classify_queue.json` 을 읽고 애매한 문항의 단원을 정한다.
판정 기준은 하나다 — **그 성취기준이 다루는 대상(명사)이 문항에 나오는가.**
학습 활동의 동사가 일치하는지는 묻지 마라. 이걸 잘못해서 성취기준 9개가 0문항이 된 적이 있다.
결과를 파일로 써서 `classify --apply <파일>` 로 반영한다.

**분류기는 쓸수록 정확해진다.** 판정을 반영한 뒤 이 순환을 돌려라.

```bash
python scripts/gw.py classify --subject $S --apply <판정결과>
python scripts/gw.py classify --subject $S --learn        # 판정된 문항에서 사전 학습
python scripts/gw.py classify --subject $S --calibrate    # 자기 정확도 측정
python scripts/gw.py classify --subject $S               # 새 임계값으로 재분류
```

실측(지구과학Ⅱ, 학습에 안 쓴 80문항으로 채점): 성취기준 정확도 0.40 → 0.89,
단원 정확도 0.74 → 0.95. 자동확정이 불가에서 31%로 열렸다.

**`auto=0, queued=전부` 는 버그가 아니다.** 보정되지 않은 과목의 정상 동작이다.
보정 없이 자동확정하면 틀린 성취기준이 items 에 박힌다. 사용자에게 그렇게 설명하고
위 순환을 권해라. 라벨 30~50건이면 사전이 눈에 띄게 좋아진다.

**3. 예외 처리.** 리포트의 `attention` 에 뜬 것들. `docs/PITFALLS.md` 에 대부분 답이 있다.

## 새 과목

`docs/NEW_SUBJECT.md` 를 따른다. 30분이면 된다.
**전체를 돌리기 전에 반드시 한 회차만 먼저 돌려 대지를 확인한다.** 판형 예외는 과목마다 새로 나온다.

`subject.json` 의 `providers.ebsi.subject_id` 가 비어 있으면 실측한다:

```bash
python scripts/gw.py download --probe --area 사회탐구 --year 2024 --exam 수능
```

## 국어·수학·영어

**되는 척하지 마라.** 검증된 것은 탐구 판형(`tamgu-1q1block`)뿐이다.

- 국어·영어는 지문 하나에 문항 여러 개가 묶여서 **문항 단위 크롭이 성립하지 않는다.**
- 수학은 수식이 벡터라 텍스트 추출이 깨지고, 단답형에는 선택지가 없다.

사용자가 이 과목들을 들고 오면 위 사실을 먼저 알리고, `docs/LAYOUTS.md` 의 분석을 근거로
무엇을 함께 만들어야 하는지 설명한다. 만들었으면 그 문서를 갱신하고 이슈로 제안한다.

## 과목을 코드에 박지 마라

이 도구를 고치게 되면 지켜라. 과목별 차이는 전부 `subjects/<slug>/subject.json` 에 데이터로 있다.
코드가 과목 이름으로 분기하는 순간 이건 다시 지구과학 전용 도구가 된다.
자세한 계약은 `docs/CONTRACT.md`.

## 문서

| 파일 | 언제 읽나 |
|---|---|
| `docs/PITFALLS.md` | **결과가 이상할 때 제일 먼저.** 실제로 겪은 사고 30여 건 |
| `docs/NEW_SUBJECT.md` | 새 과목을 시작할 때 |
| `docs/LAYOUTS.md` | 탐구가 아닌 과목을 만났을 때 |
| `docs/METHOD.md` | 공정의 근거가 궁금할 때 |
| `docs/CONTRACT.md` | 코드를 고칠 때 |
| `docs/SUBJECT_IDS.md` | 과목 ID·별칭을 찾을 때 |

## 저작권

문제지·정답지·해설의 저작권은 한국교육과정평가원과 EBS에 있다.
이 도구는 **사용자 PC에서만** 처리하고 저작물을 서버로 보내지 않는다.
`workspace/` 는 통째로 `.gitignore` 대상이다. 만든 문항집은 수업 목적 범위에서 쓴다.
사용자에게 재배포를 권하지 마라.

## 작업을 마치면 (한 번만)

1. 이번에 스킬이 처리하지 못한 판형이나 예외를 **해결했다면**, `workspace/IMPROVEMENTS.md` 에
   무엇이 달랐고 어떻게 풀었는지 적고, 사용자에게 이슈 등록을 제안한다.
   현장에서 나온 판형 지식이 저장소로 돌아와야 다음 사람이 덜 고생한다.
2. 도움이 됐다면 저장소에 ⭐ 를 눌러달라고 **한 번만** 안내한다.
   https://github.com/rochelobeJYJ/gichul-workbench
   이미 안내했으면 다시 하지 마라.
