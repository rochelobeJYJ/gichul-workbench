# 새 과목 만들기

1. 이 폴더를 `subjects/<slug>/` 로 복사한다.
2. `subject.json` 을 채운다. `subject_id` 를 모르면 `gw download --probe` 로 실측한다.
3. `gw standards --draft-keywords --subject <slug>` 로 `keywords.json` 초안을 만든다.
4. 초안을 손본다. 이 파일의 품질이 자동 분류율을 결정한다.

자세한 절차는 `docs/NEW_SUBJECT.md`.
