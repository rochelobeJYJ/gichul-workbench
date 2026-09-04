# -*- coding: utf-8 -*-
"""파일 이름이 제각각일 때 **내용으로** 문제지/정답지/해설지를 가르는 판별기.

README 가 "파일 이름이 제각각이어도 괜찮습니다" 라고 약속하는 자리다. 실측에서
'한국지리 기출 (1).pdf' / '답 모음.pdf' / '해설 파일.pdf' 묶음이 done=0 으로 끝났었다.

여기 있는 지문은 전부 **실제 PDF 에서 잰 모양을 최소로 줄인 것**이다. 기출 원본은
저장소에 들어가지 않으므로(CONTRACT 0절) 수치만 옮겨 왔다. 각 테스트 docstring 에
어느 실측이 근거인지 적어 둔다.
"""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from detect import (bundle_subject_page, detect_kind, kind_from_content,
                    resolve_kind, _choice_runs)


def 문제지(문항수=20) -> list[str]:
    """문항마다 선택지 ①②③④⑤ 가 순서대로 한 바퀴. 쪽당 글자 수는 실측 1,300~1,800자."""
    한문항 = "다음 자료에 대한 설명으로 옳은 것만을 고른 것은? [3점]\n" + "가" * 220 + "\n①ㄱ\n②ㄴ\n③ㄷ\n④ㄱ, ㄴ\n⑤ㄴ, ㄷ\n"
    본문 = 한문항 * 문항수
    return [본문[i:i + len(본문) // 4] for i in range(0, len(본문), max(len(본문) // 4, 1))][:4]


def 정답지() -> list[str]:
    """문장이 없는 격자. 실측 쪽당 228자, 원문자는 정답 낱개라 순서가 뒤섞여 있다."""
    격자 = "문항\n번호\n정 답배 점\n" * 4
    답 = "".join(f"{n}\n{'①②③④⑤'[(n * 3) % 5]}\n{2 + n % 2}\n" for n in range(1, 21))
    return ["2024학년도 대학수학능력시험\n과학탐구 영역 정답표\n( 지구과학Ⅰ ) 과목\n" + 격자 + 답]


def 해설지(쪽=7) -> list[str]:
    """문항마다 '[출제의도]' 가 붙고 서술이 길다. 원문자는 정답 표기로 낱개만 나온다."""
    한문항 = "1. [출제의도] 판 경계의 특징을 분석한다.\n정답 ③\n" + "설명" * 200 + "\n[오답 풀이] ④ 는 …\n"
    return ["2024학년도 대학수학능력시험\n지구과학Ⅰ 정답 및 해설\n" + 한문항] + [한문항] * (쪽 - 1)


class TestChoiceRuns(unittest.TestCase):
    def test_순서대로_한_바퀴만_센다(self):
        self.assertEqual(_choice_runs("①②③④⑤"), 1)
        self.assertEqual(_choice_runs("①②③④⑤①②③④⑤"), 2)

    def test_뒤섞인_낱개는_묶음이_아니다(self):
        """정답지·해설지의 원문자가 이 모양이다. 개수(10개)로는 문제지와 못 가른다."""
        self.assertEqual(_choice_runs("⑤③②④①⑤③②④①"), 0)

    def test_중간에_끊기면_처음부터(self):
        self.assertEqual(_choice_runs("①②③①②③④⑤"), 1)


class TestKindFromContent(unittest.TestCase):
    def test_문제지(self):
        self.assertEqual(kind_from_content(문제지())[0], "problem")

    def test_정답지(self):
        self.assertEqual(kind_from_content(정답지())[0], "answer")

    def test_해설지(self):
        self.assertEqual(kind_from_content(해설지())[0], "solution")

    def test_한글이_깨진_해설지도_해설지다(self):
        """PITFALLS 3-1 / 실측 2022학년도 수능 해설: 본문 한글이 전부 사설 글리프로 나와
        한글이 쪽당 7자뿐이다. '문장이 없다'로 가르면 이 파일이 정답지로 배정된다 —
        20문항 정답이 통째로 어긋나는 경로다. 표지의 '정답 및 해설' 은 살아남으므로
        그쪽을 본다."""
        깨진본문 = "㛫⇋⢴䙞ᴆ" * 300
        pages = ["지구과학Ⅰ 정답 및 해설\n정답 및 해설\n정답 및 해설\n" + 깨진본문] + [깨진본문] * 7
        self.assertEqual(kind_from_content(pages)[0], "solution")

    def test_텍스트가_없으면_기권한다(self):
        """스캔본. 쪽수로 찍을 수도 있지만 1쪽짜리 스캔 정답지와 4쪽짜리 스캔 문제지가
        둘 다 실재해서(실측) 쪽수는 신호가 아니다. 배정하는 대신 기권한다."""
        kind, why = kind_from_content(["", "", "", ""])
        self.assertIsNone(kind)
        self.assertIn("텍스트", why)

    def test_두_신호가_겹치면_기권한다(self):
        """문항 원문을 그대로 싣는 해설지 같은 경우. 선택지 묶음도 해설 머리글도 많아서
        어느 축도 단독으로 켜지지 않는다 — 확실하지 않으면 답하지 않는다."""
        kind, why = kind_from_content(문제지() + 해설지())
        self.assertIsNone(kind)
        self.assertIn("선택지묶음 20", why)
        self.assertNotIn("해설머리글 0", why)

    def test_교육과정_문서는_셋_중_어느_것도_아니다(self):
        """--input 폴더에는 기출이 아닌 PDF 도 섞여 들어온다(실측: 성취기준·평가기준 문서)."""
        self.assertIsNone(kind_from_content(["성취기준 해설\n" + "학습" * 400] * 16)[0])


class TestResolveKind(unittest.TestCase):
    def test_이름이_없으면_내용으로_정한다(self):
        kind, by = resolve_kind(Path("한국지리 기출 (1).pdf"), 문제지)
        self.assertEqual(kind, "problem")
        self.assertTrue(by.startswith("content("))

    def test_이름과_내용이_어긋나면_내용이_이긴다(self):
        """해설지를 '정답.pdf' 로 저장해 둔 더미가 실전에 있다. 이름을 믿으면 세 역할이
        통째로 뒤바뀌고 verified 까지 켜진 채 지나간다(실측)."""
        kind, by = resolve_kind(Path("정답.pdf"), 해설지)
        self.assertEqual(kind, "solution")
        self.assertIn("content-overrides-filename", by)

    def test_내용이_기권하면_이름이_이긴다(self):
        """텍스트 레이어 없는 정답 스캔 PDF. 여기서 이름을 버리면 멀쩡하던 회차가 깨진다."""
        self.assertEqual(resolve_kind(Path("2024_수능_정답.pdf"), lambda: ["", ""])[0], "answer")

    def test_이미지는_확장자_규칙에서_끝난다(self):
        self.assertEqual(detect_kind(Path("답 모음.png")), ("answer", "ext-fallback"))


class TestBundleSubjectPage(unittest.TestCase):
    """평가원 정답표는 한 교시 8~9과목을 한 파일에 담고 쪽마다 과목 머리글을 찍는다."""

    쪽 = [f"2021학년도 대학수학능력시험\n과학탐구 영역 정답표\n( {name} ) 과목\n"
         for name in ("물리학Ⅰ", "화학Ⅰ", "생명과학Ⅰ", "지구과학Ⅰ",
                      "물리학Ⅱ", "화학Ⅱ", "생명과학Ⅱ", "지구과학Ⅱ")]

    def test_우리_과목_쪽을_찾는다(self):
        self.assertEqual(bundle_subject_page(self.쪽, ["지구과학I", "지구과학Ⅰ"]), 4)
        self.assertEqual(bundle_subject_page(self.쪽, ["지구과학II", "지구과학Ⅱ"]), 8)

    def test_부분열로_매칭하지_않는다(self):
        """'지구과학Ⅰ' 이 '지구과학Ⅱ' 머리글에 걸리면 남의 과목 정답표를 우리 것으로
        가져온다 — 정답 20개가 통째로 틀리는 경로다."""
        self.assertIsNone(bundle_subject_page([self.쪽[7]], ["지구과학I", "지구과학Ⅰ"]))

    def test_없는_과목이면_None(self):
        self.assertIsNone(bundle_subject_page(self.쪽, ["한국지리"]))

    def test_별칭이_없으면_아무것도_고르지_않는다(self):
        self.assertIsNone(bundle_subject_page(self.쪽, []))


if __name__ == "__main__":
    unittest.main()
