# -*- coding: utf-8 -*-
"""키워드 사전. PITFALLS 5-6 — 성취기준 코드는 개정마다 새로 매겨지지 않는다."""
import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import keywordsio


class TestRevisionLayer(unittest.TestCase):
    """2015 `12윤사01-01` 과 2022 `12윤사01-01` 은 다른 성취기준인데 코드가 같다.
    코드만으로 키를 잡으면 나중 개정이 앞 개정을 말없이 덮어쓴다."""

    def _rt(self, payload):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "keywords.json"
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return keywordsio.load(p)

    def test_두_개정이_같은_코드를_가져도_안_섞인다(self):
        book = self._rt({
            "2015": {"12윤사01-01": {"curriculum": ["사회사상"], "learned": []}},
            "2022": {"12윤사01-01": {"curriculum": ["공자사상"], "learned": []}},
        })
        self.assertEqual(keywordsio.entry_terms(book.entry("2015", "12윤사01-01")), ["사회사상"])
        self.assertEqual(keywordsio.entry_terms(book.entry("2022", "12윤사01-01")), ["공자사상"])
        # 두 개정 모두 코드를 하나씩 갖는다 — 한쪽이 다른 쪽을 덮어쓰지 않았다.
        self.assertEqual(book.codes("2015"), ["12윤사01-01"])
        self.assertEqual(book.codes("2022"), ["12윤사01-01"])

    def test_평면형_옛_파일도_읽힌다(self):
        """{"코드": ["용어"]} — 초안만 있던 시절의 형태."""
        book = self._rt({"12지과Ⅱ01-01": ["집적", "미행성체"]})
        self.assertIsNotNone(book)

    def test_구조형_옛_파일도_읽힌다(self):
        """{"코드": {"curriculum": [...], "learned": [...]}} — 학습 이후 형태."""
        book = self._rt({"12지과Ⅱ01-01": {"curriculum": ["집적"],
                                          "learned": [{"term": "열류량", "weight": 0.6}]}})
        self.assertIsNotNone(book)


class TestInferRevision(unittest.TestCase):
    def test_긴_접두사가_이긴다(self):
        """`10통과` 는 `10통과1-01-01` 의 접두사이기도 하다.
        짧은 쪽이 이기면 2022 코드가 2015 로 배정된다."""
        rev, warn = keywordsio.infer_revision(
            "10통과1-01-01", {"2015": ["10통과"], "2022": ["10통과1-", "10통과2-"]})
        self.assertEqual(rev, "2022")

    def test_짧은_쪽_코드는_짧은_쪽으로(self):
        rev, warn = keywordsio.infer_revision(
            "10통과01-01", {"2015": ["10통과"], "2022": ["10통과1-", "10통과2-"]})
        self.assertEqual(rev, "2015")


if __name__ == "__main__":
    unittest.main()
