# -*- coding: utf-8 -*-
"""계약. 리포트가 LLM 이 읽는 유일한 출력이라 형태가 고정되어 있다(CONTRACT 5절)."""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common.report import Report, ATTENTION_LIMIT
from common.subjects import all_subjects, load_subject, KNOWN_LAYOUTS


class TestReport(unittest.TestCase):
    def test_필수_키(self):
        d = Report("crop", "x").to_dict()
        for k in ("step", "slug", "ok", "counts", "attention", "artifacts", "next", "elapsed_sec"):
            self.assertIn(k, d)

    def test_attention_은_상한에서_잘린다(self):
        """오류가 많은 회차 하나가 LLM 컨텍스트를 통째로 태우면 안 된다."""
        r = Report("validate", "x")
        for i in range(ATTENTION_LIMIT + 20):
            r.note(f"q{i}", "warn 사유", "warn")
        d = r.to_dict()
        self.assertEqual(len(d["attention"]), ATTENTION_LIMIT)
        self.assertEqual(d["attention_truncated"], 20)

    def test_error_는_잘림을_뚫고_살아남는다(self):
        """상한에 걸려도 심각한 것이 먼저 보여야 한다."""
        r = Report("validate", "x")
        for i in range(ATTENTION_LIMIT + 5):
            r.note(f"w{i}", "그냥 경고", "warn")
        r.note("치명", "여기가 진짜 문제다", "error")
        d = r.to_dict()
        self.assertIn("치명", [a["id"] for a in d["attention"]])
        self.assertFalse(d["ok"])

    def test_모르는_severity_는_거부한다(self):
        with self.assertRaises(ValueError):
            Report("crop", "x").note("q1", "사유", "심각")


class TestRegistry(unittest.TestCase):
    def test_등록된_과목이_전부_로드된다(self):
        subs = all_subjects()
        self.assertGreater(len(subs), 0)
        for s in subs:
            self.assertIn(s.layout, KNOWN_LAYOUTS, f"{s.slug}: 모르는 layout")
            self.assertTrue(s.label and s.area, s.slug)

    def test_없는_과목은_안내와_함께_실패한다(self):
        """조용한 기본값 금지 — 무엇이 없는지 말하고 끝나야 한다."""
        with self.assertRaises(FileNotFoundError) as cm:
            load_subject("존재하지-않는-과목")
        self.assertIn("과목 정의가 없다", str(cm.exception))

    def test_레퍼런스_과목의_불변식(self):
        s = load_subject("earth-science-ii")
        self.assertEqual(s.points_total, 50)
        self.assertEqual(s.layout, "tamgu-1q1block")

    def test_접두사가_추측이_아니다(self):
        """standard_prefixes 는 교육과정 원본에서 실측한 값이어야 한다.
        '12지구' 를 적었다가 진짜 값이 '12지과Ⅱ' 였던 사고가 있었다(PITFALLS 5-7)."""
        s = load_subject("earth-science-ii")
        self.assertIn("12지과Ⅱ", s.standard_prefixes.get("2015", []))


if __name__ == "__main__":
    unittest.main()
