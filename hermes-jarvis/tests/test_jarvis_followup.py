"""確認待ちへの返事と、詳細の続き読み。

なぜ必要か:
  C8 は要約の最後に「詳細も読み上げますか？」と聞いていたが、
  **それに答える経路が無かった**。秘書の確認待ちも同じで、
  bare「はい」は LLM 経路で LOCAL_FAST に落ちていた。

安全側の設計:
  返事とみなすのは**発話全体が返事だけ**のときに限る。
  確認待ちは 180 秒有効なので、その間の普通の発話が
  「お願い」を含むだけで確認を承諾してしまってはいけない。
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import jarvis_followup as fu


class TestAnswerOnly(unittest.TestCase):
    def test_yes(self):
        for t in ["はい", "うん", "お願い", "お願いします", "はい、お願い",
                  "OK", "オーケー", "いいよ", "そう"]:
            self.assertEqual(fu.answer_only(t), fu.ANSWER_YES, t)

    def test_no(self):
        for t in ["いいえ", "いや", "やめて", "キャンセル", "違う", "不要", "no"]:
            self.assertEqual(fu.answer_only(t), fu.ANSWER_NO, t)

    def test_more(self):
        for t in ["続けて", "続き", "もっと", "詳細", "詳細をお願い", "全部読んで"]:
            self.assertEqual(fu.answer_only(t), fu.ANSWER_MORE, t)

    def test_negation_wins_when_mixed(self):
        # 「はい、やめて」は安全側（拒否）へ倒す
        self.assertEqual(fu.answer_only("はい、やめて"), fu.ANSWER_NO)

    def test_whole_utterance_must_be_an_answer(self):
        # ここが要点。確認待ちの 180 秒間に普通の発話をしても誤発火しない
        for t in ["お願いだから今何時か教えて", "そういえば天気は",
                  "はいと言われても困る話をして", "続けて開発の状況を教えて",
                  "秘書、状況を教えて", "グーグルを開いて"]:
            self.assertIsNone(fu.answer_only(t), t)

    def test_empty(self):
        for t in [None, "", "   "]:
            self.assertIsNone(fu.answer_only(t), repr(t))

    def test_trailing_punctuation_is_ignored(self):
        for t in ["はい。", "はい！", "うん、", "やめて。"]:
            self.assertIsNotNone(fu.answer_only(t), t)


class TestPending(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "_followup_test.json")
        fu.clear(self.path)

    tearDown = setUp

    def test_roundtrip(self):
        fu.remember("詳細な本文", "CODEX", path=self.path)
        got = fu.pending(path=self.path)
        self.assertIsNotNone(got)
        self.assertEqual(got["detail"], "詳細な本文")
        self.assertEqual(got["route"], "CODEX")
        self.assertEqual(got["offset"], 0)

    def test_missing_is_none(self):
        self.assertIsNone(fu.pending(path=self.path))

    def test_expired_is_none_and_removed(self):
        fu.remember("本文", "CODEX", path=self.path)
        self.assertIsNone(fu.pending(path=self.path, ttl=-1))
        self.assertFalse(os.path.exists(self.path))

    def test_file_is_owner_only(self):
        # 応答本文が入る。§17 PRIVACY と同じ 0600 を守る
        fu.remember("本文", "CODEX", path=self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_detail_is_capped(self):
        fu.remember("あ" * 99999, "CODEX", path=self.path)
        self.assertLessEqual(len(fu.pending(path=self.path)["detail"]),
                             fu.MAX_DETAIL_CHARS)


class TestChunking(unittest.TestCase):
    def test_reads_in_order_and_reports_more(self):
        detail = "".join("%d番目の文です。" % i for i in range(1, 40))
        first, off1, more1 = fu.next_chunk(detail, 0)
        self.assertTrue(more1)
        self.assertTrue(detail.startswith(first[:10]))
        second, off2, _ = fu.next_chunk(detail, off1)
        self.assertGreater(off2, off1)
        self.assertNotEqual(first, second)

    def test_short_detail_has_no_more(self):
        _, _, more = fu.next_chunk("短い本文です。", 0)
        self.assertFalse(more)

    def test_chunk_fits_the_spoken_cap(self):
        import jarvis_speech as sp
        detail = "あ" * 5000
        chunk, _, _ = fu.next_chunk(detail, 0)
        self.assertLessEqual(len(chunk), sp.MAX_SPOKEN_CHARS)

    def test_chunk_plus_continue_question_fits_the_cap(self):
        # 問いを後から足すと sanitize() の上限で切り落とされる。
        # 続きがあるのに聞かない状態になるので、上限から先に差し引く
        import jarvis_speech as sp
        detail = "あ" * 5000
        chunk, _, more = fu.next_chunk(detail, 0)
        self.assertTrue(more)
        self.assertLessEqual(len(chunk + fu.CONTINUE_SUFFIX), sp.MAX_SPOKEN_CHARS)

    def test_offset_past_end_yields_nothing(self):
        chunk, _, more = fu.next_chunk("本文", 999)
        self.assertEqual(chunk, "")
        self.assertFalse(more)


class TestHasMoreDetail(unittest.TestCase):
    """「詳細も読み上げますか？」を付けてよいかの判定。

    LLM に付けさせると、詳細が無いときにも聞いてしまう。
    """

    def test_no_promise_when_detail_is_not_longer(self):
        self.assertFalse(fu.has_more_detail("結論です。", "結論です。"))
        self.assertFalse(fu.has_more_detail("結論です。", ""))

    def test_promise_when_detail_is_substantially_longer(self):
        self.assertTrue(fu.has_more_detail("結論です。", "結論です。" + "あ" * 600))


class TestHandle(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "_followup_test.json")
        fu.clear(self.path)

    tearDown = setUp

    def test_no_pending_returns_none(self):
        self.assertIsNone(fu.handle("はい", path=self.path))

    def test_yes_reads_the_first_chunk(self):
        fu.remember("最初の文です。" + "あ" * 400, "CODEX", path=self.path)
        said = fu.handle("はい", path=self.path)
        self.assertIsNotNone(said)
        self.assertIn("最初の文", said)

    def test_no_clears_and_acknowledges(self):
        fu.remember("本文", "CODEX", path=self.path)
        said = fu.handle("いいえ", path=self.path)
        self.assertIsNotNone(said)
        self.assertIsNone(fu.pending(path=self.path))

    def test_continues_from_where_it_stopped(self):
        detail = "".join("%d番目の文です。" % i for i in range(1, 60))
        fu.remember(detail, "CODEX", path=self.path)
        first = fu.handle("はい", path=self.path)
        second = fu.handle("続けて", path=self.path)
        self.assertNotEqual(first, second)
        self.assertIsNotNone(second)

    def test_exhausted_clears_the_pending(self):
        fu.remember("短い本文です。", "CODEX", path=self.path)
        fu.handle("はい", path=self.path)
        self.assertIsNone(fu.pending(path=self.path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
