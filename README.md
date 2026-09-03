# 기출 문항 작업대 (gichul-workbench)

**수능·모의평가 기출을 내려받아 문항별로 잘라내고, 교육과정 단원에 자동 분류해서, 인쇄용 문항집을 만든다.**

사회탐구 9과목 · 과학탐구 8과목. 지구과학Ⅰ·Ⅱ는 760문항 전사와 2015→2022 교육과정 매핑이 끝나 있다.

```
기출 PDF  →  문항별 크롭  →  단원 분류  →  A4 문항집
```

---

## 쓰는 법 — LLM에게 저장소 주소만 주면 된다

이 저장소는 **Claude Code / Codex 가 읽고 알아서 쓰도록** 만들어졌다.
설명서를 읽을 필요가 없다. 클론하고 이렇게 말하면 된다.

```
이 폴더의 도구로 지구과학2 기출 문항집 만들어줘
```

```
한국지리 2020~2026 수능·모평 기출 받아서 단원별로 정리해줘
```

LLM이 `SKILL.md`(Claude) 또는 `AGENTS.md`(Codex)를 읽고 나머지를 처리한다.

### 설치

```bash
git clone https://github.com/rochelobeJYJ/gichul-workbench
cd gichul-workbench
python scripts/bootstrap.py
```

`bootstrap.py` 가 빠진 것을 알려준다. 파이썬 3.9 이상이면 된다.

### 직접 쓰기

```bash
S=earth-science-ii
python scripts/gw.py subjects                                    # 과목 목록
python scripts/gw.py download --subject $S --years 2020-2026 \
       --exams 수능,6월모평,9월모평 --kinds problem,answer,solution
python scripts/gw.py detect   --subject $S
python scripts/gw.py crop     --subject $S
python scripts/gw.py extract  --subject $S
python scripts/gw.py classify --subject $S
python scripts/gw.py validate --subject $S
python scripts/gw.py build    --subject $S
```

마지막 명령이 만든 HTML을 브라우저로 열면 문항집 제작기가 뜬다.
성취기준을 고르고, 문항을 체크하고, 인쇄하면 A4 2단 학습지가 나온다.

이미 PDF를 갖고 있으면 `download` 를 건너뛰고 `detect --input <폴더>` 로 시작한다.

---

## 무엇이 들어 있나

| | |
|---|---|
| **다운로더** | 평가원 공식(2005~) + EBSi(해설·학평 포함) 두 경로 |
| **크롭** | 문항 하나씩 PNG로. 발문·그림·`<보기>`·선지 5개를 온전히 |
| **추출** | 본문·선택지·정답·배점. 정답은 **3중 대조**로 확인 |
| **분류** | 키워드 사전으로 자동, 애매한 것만 LLM. 토큰이 적게 든다 |
| **매핑** | 2015개정 문항 ↔ 2022개정 성취기준. 지구과학 760문항분 동봉 |
| **문항집** | 브라우저에서 고르고 A4로 인쇄. 정답표·단원 설명 토글 |

새 과목은 30분이면 추가된다. `docs/NEW_SUBJECT.md`

---

## 문서

| 파일 | 무엇 |
|---|---|
| [`docs/PITFALLS.md`](docs/PITFALLS.md) | **실제로 겪은 사고 30여 건.** 결과가 이상하면 여기부터 |
| [`docs/NEW_SUBJECT.md`](docs/NEW_SUBJECT.md) | 새 과목 추가 |
| [`docs/METHOD.md`](docs/METHOD.md) | 공정과 그 근거 |
| [`docs/LAYOUTS.md`](docs/LAYOUTS.md) | 판형별 대응. 국어·수학·영어가 왜 다른가 |
| [`docs/CONTRACT.md`](docs/CONTRACT.md) | 코드를 고칠 때 지킬 규약 |

---

## 국어·수학·영어는?

**아직 안 된다.** 검증된 것은 탐구 영역 판형뿐이다.

국어·영어는 지문 하나에 문항이 여러 개 묶여 문항 단위로 자를 수가 없고,
수학은 수식이 벡터라 텍스트 추출이 깨지며 단답형에는 선택지가 없다.
무엇을 만들어야 하는지는 [`docs/LAYOUTS.md`](docs/LAYOUTS.md) 에 분석해 두었다.

만드셨다면 이슈나 PR로 알려주시면 좋겠다.

---

## 업데이트

판형 예외 대응이 계속 추가된다. 가끔 확인해 주시길.

```bash
git pull
```

LLM에게 시키면 시작할 때 알아서 확인하고 알려준다.

---

## 저작권

이 도구는 저작물을 **배포하지 않는다.** 사용자 PC에서만 처리하고,
작업 폴더(`workspace/`)는 통째로 git 추적에서 제외되어 있다.

기출문제·정답의 저작권은 **한국교육과정평가원**, 해설은 **EBS** 등에 있다.
만든 문항집은 저작권법 제25조가 허용하는 **수업 목적 범위**에서 사용한다.
자세한 것은 [`NOTICE`](NOTICE).

## 라이선스

[CC BY-NC-SA 4.0](LICENSE) — 자유롭게 쓰되,

- **출처를 밝히고**
- **상업적으로 쓰지 않으며**
- **고쳐서 배포할 때는 소스를 같은 조건으로 공개한다.**

---

## ⭐

쓸 만하셨다면 별 하나 눌러주시면 큰 힘이 됩니다.
현장에서 만난 판형 문제나 개선점은 이슈로 남겨주시면 다음 사람이 덜 고생합니다.
