# -*- coding: utf-8 -*-
"""배점. PITFALLS 4-3 — 소수 배점을 못 읽으면 역산이 합계를 정확히 맞춰 준다."""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extractlib.points import (POINT_MARK_RE, read_point_mark, read_point_cell,
                               normalize_points, points_equal, on_point_grid)


class TestMark(unittest.TestCase):
    def test_정수_표기(self):
        self.assertEqual(read_point_mark("… 고른 것은? [3점]"), 3)

    def test_소수_표기(self):
        """통합과목 25문항 판형은 1.5 / 2 / 2.5 세 계단이다.
        한 자리 숫자만 잡던 옛 정규식이 이것을 통째로 놓쳤다."""
        self.assertEqual(read_point_mark("… [1.5점]"), 1.5)
        self.assertEqual(read_point_mark("… [2.5점]"), 2.5)

    def test_소수점_붙은_정수도_읽는다(self):
        """같은 회차 안에서 [2점] 과 [2.0점] 이 섞여 나온다(실측)."""
        self.assertEqual(read_point_mark("… [2.0점]"), 2)

    def test_공백_흔들림(self):
        self.assertEqual(read_point_mark("… [ 3 점 ]"), 3)

    def test_표기가_없으면_None(self):
        self.assertIsNone(read_point_mark("… 고른 것은?"))

    def test_정규식이_소수를_포함한다(self):
        self.assertTrue(POINT_MARK_RE.search("[2.5점]"))


class TestNormalize(unittest.TestCase):
    def test_정수로_떨어지면_정수로_적는다(self):
        """PITFALLS 7-6: 값이 같아도 타입이 바뀌면 코퍼스 파일 해시가 전부 달라진다."""
        self.assertIsInstance(normalize_points(2.0), int)
        self.assertEqual(normalize_points(2.0), 2)

    def test_소수는_소수로_남는다(self):
        self.assertEqual(normalize_points(1.5), 1.5)
        self.assertIsInstance(normalize_points(1.5), float)


class TestCompare(unittest.TestCase):
    def test_같은_값(self):
        self.assertTrue(points_equal(2, 2.0))
        self.assertTrue(points_equal(1.5, 1.5))

    def test_다른_값을_int_절단으로_놓치지_않는다(self):
        """int() 로 자르면 2.0 vs 2.5 가 2 == 2 로 통과해 대조축이 무력해진다."""
        self.assertFalse(points_equal(2.0, 2.5))

    def test_배점_계단(self):
        for v in (1.5, 2, 2.5, 3):
            self.assertTrue(on_point_grid(v), v)
        self.assertFalse(on_point_grid(2.3))


class TestSum(unittest.TestCase):
    def test_세_계단_판형의_합이_정확히_50이다(self):
        """1.5×8 + 2×9 + 2.5×8 = 50. 실수 합이 등호로 떨어지는지 확인한다."""
        pts = [1.5] * 8 + [2] * 9 + [2.5] * 8
        self.assertEqual(len(pts), 25)
        self.assertTrue(points_equal(sum(pts), 50))


class TestCell(unittest.TestCase):
    def test_오답률_표의_배점_칸(self):
        """EBSi 오답률 표는 배점을 1.5 / 2.0 / 2.5 로 준다."""
        self.assertEqual(read_point_cell("2"), 2)
        self.assertEqual(read_point_cell("1.5"), 1.5)
        self.assertEqual(read_point_cell("2.0"), 2)
        self.assertIsNone(read_point_cell(""))


if __name__ == "__main__":
    unittest.main()
