# -*- coding: utf-8 -*-
"""식별자 규약. exam_id·qid 가 한 곳에서만 만들어지는지 지킨다."""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common.ids import make_exam_id, make_qid, split_qid, normalize_exam, exam_sort_key


class TestExamId(unittest.TestCase):
    def test_수능은_학년을_받지_않는다(self):
        self.assertEqual(make_exam_id(2024, "수능"), "2024_수능")

    def test_학평은_학년이_들어간다(self):
        """같은 달에 고1·고2·고3 시험이 따로 있다. 학년이 없으면 회차가 섞인다."""
        self.assertEqual(make_exam_id(2025, "3월학평", 2), "2025_고2_3월학평")

    def test_학평에_학년을_안_주면_멈춘다(self):
        """조용한 기본값 금지(CONTRACT 0절 5번)."""
        with self.assertRaises(ValueError):
            make_exam_id(2025, "3월학평")

    def test_시험이름_표기흔들림을_흡수한다(self):
        for raw in ("6월 모의평가", "6월모평", "06", "6"):
            self.assertEqual(normalize_exam(raw), "6월모평")

    def test_모르는_시험은_조용히_넘어가지_않는다(self):
        with self.assertRaises(ValueError):
            normalize_exam("13월학평")

    def test_경기주관_회차가_전부_있다(self):
        """PITFALLS 1-5: 4·5월과 10·11월을 둘 다 갖고 있지 않으면
        그 해 회차가 조회 단계에서 통째로 사라진다."""
        for kind in ("4월학평", "5월학평", "10월학평", "11월학평"):
            self.assertEqual(normalize_exam(kind), kind)


class TestQid(unittest.TestCase):
    def test_왕복(self):
        qid = make_qid("2024_수능", 7)
        self.assertEqual(qid, "2024_수능_07")
        self.assertEqual(split_qid(qid), ("2024_수능", 7))

    def test_학년이_붙은_회차도_왕복한다(self):
        self.assertEqual(split_qid("2025_고2_3월학평_20"), ("2025_고2_3월학평", 20))


class TestSort(unittest.TestCase):
    def test_한_학년도_안의_시행순서(self):
        got = sorted(["2024_수능", "2024_6월모평", "2024_9월모평"], key=exam_sort_key)
        self.assertEqual(got, ["2024_6월모평", "2024_9월모평", "2024_수능"])

    def test_11월학평은_수능_뒤다(self):
        """오타가 아니다 — 고2 경기 회차는 실제 시행일이 수능 뒤(12월)다."""
        got = sorted(["2023_고2_11월학평", "2023_수능"], key=exam_sort_key)
        self.assertEqual(got[-1], "2023_고2_11월학평")


if __name__ == "__main__":
    unittest.main()
