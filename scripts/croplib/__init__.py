# -*- coding: utf-8 -*-
"""크롭 구현 묶음. 명령 모듈(scripts/crop.py)은 여기서만 조립한다.

  pdfdoc    PDF 조회 캐시 · 판면 기하
  tamgu     판형 tamgu-1q1block 의 문항 영역 계산(검증된 알고리즘)
  materials 문항 안의 그림/표만 골라내는 판별기
  imaging   렌더 · 이미지 정리 · 대지
  numbox    크롭 안의 문항 번호 자리(items.number_box) 계산
  qa        크롭 결과 자동 점검
  vision    텍스트 레이어가 없는 회차의 컬럼 렌더 경로
"""
from .pdfdoc import Doc

__all__ = ["Doc"]
