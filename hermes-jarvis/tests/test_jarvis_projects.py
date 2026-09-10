"""プロジェクトの起動と終了（音声）。

なぜ jarvis_open に相乗りするか:
  「アルフ、エムブイピーを起動して」を実測したら **「α-MVP を起動して」**
  と書き起こされ、**呼びかけ語の「アルフ」が消えた**。秘書経由にすると
  トリガが当たらず届かない。`jarvis_open` は「開いて/起動して」の許可リストを
  既に持っていて呼びかけ語を要求しないので、そこへプロジェクトを足す。

別名はすべて実測から取った（2026-09-10、faster-whisper small / ja）:
  アップ          -> アップ
  エーピーピー      -> BPP
  ボット          -> ロット      （誤変換）
  ディスコード      -> リスコード   （誤変換）
  エルピー         -> lp        （英字化）
  エムブイピー      -> MVP
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import jarvis_open as jo


class TestProjectTargets(unittest.TestCase):
    def test_spoken_forms_resolve(self):
        cases = {
            "アップを起動して": "app",
            "BPPを起動して": "app",
            "エーピーピーを起動して": "app",
            "トレードを起動して": "trade",
            "オートを起動して": "auto",
            "オートメーションを起動して": "auto",
            "ボットを起動して": "bot",
            "ロットを起動して": "bot",
            "リスコードを起動して": "bot",
            "lpを起動して": "lp",
            "エルピーを起動して": "lp",
            "MVPを起動して": "mvp",
            "センコウを起動して": "senkou",
        }
        for text, alias in cases.items():
            got = jo.resolve_target(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got["kind"], jo.ACTION_PROJECT, text)
            self.assertEqual(got["value"], alias, text)

    def test_alpha_prefix_does_not_break_it(self):
        # 実測: 「アルフ、エムブイピーを起動して」-> 「α-MVPを起動して」
        got = jo.resolve_target("α-MVPを起動して")
        self.assertIsNotNone(got)
        self.assertEqual(got["value"], "mvp")

    def test_the_excluded_project_is_not_a_target(self):
        # wam / client-a は作業対象外。許可リストに載せない
        for text in ["wamを起動して", "タダカヨを起動して", "ワムを起動して"]:
            got = jo.resolve_target(text)
            if got is not None:
                self.assertNotEqual(got["kind"], jo.ACTION_PROJECT, text)


class TestFalsePositives(unittest.TestCase):
    """実測で誤発火した3件。動詞があるので OPEN 経路に到達してしまう。"""

    def test_update_is_not_the_app_project(self):
        self.assertIsNone(jo.resolve_target("アップデートを起動して"))

    def test_upload_is_not_the_app_project(self):
        self.assertIsNone(jo.resolve_target("アップロードを開いて"))

    def test_slot_is_not_the_bot_project(self):
        self.assertIsNone(jo.resolve_target("スロットを開いて"))

    def test_tradeoff_is_not_the_trade_project(self):
        self.assertIsNone(jo.resolve_target("トレードオフを開いて"))

    def test_lp_gas_is_not_the_lp_project(self):
        self.assertIsNone(jo.resolve_target("エルピーガスを開いて"))

    def test_autocomplete_is_not_the_auto_project(self):
        self.assertIsNone(jo.resolve_target("オートコンプリートを開いて"))


class TestVerbs(unittest.TestCase):
    def test_open_verbs(self):
        for t in ["アップを起動して", "アップを開いて", "アップを立ち上げて"]:
            self.assertEqual(jo.plan(t)["action"], jo.ACTION_PROJECT, t)

    def test_close_verbs(self):
        for t in ["アップを終了して", "アップを閉じて", "アップを落として"]:
            got = jo.plan(t)
            self.assertEqual(got["action"], jo.ACTION_PROJECT_CLOSE, t)
            self.assertEqual(got["value"], "app", t)

    def test_close_beats_open_when_both_appear(self):
        # 「起動しているアップを終了して」は終了
        self.assertEqual(jo.plan("起動しているアップを終了して")["action"],
                         jo.ACTION_PROJECT_CLOSE)

    def test_a_url_target_is_unaffected(self):
        self.assertEqual(jo.plan("グーグルを開いて")["action"], jo.ACTION_URL)

    def test_closing_a_url_is_refused(self):
        # ブラウザを閉じる機能は無い。黙って開かない
        got = jo.plan("グーグルを閉じて")
        self.assertEqual(got["action"], jo.ACTION_REFUSE)


class TestSpeech(unittest.TestCase):
    def test_launch_says_what_it_opens(self):
        self.assertIn("app", jo.plan("アップを起動して")["speech"])

    def test_close_says_what_it_closes(self):
        self.assertIn("app", jo.plan("アップを終了して")["speech"])

    def test_refusal_lists_projects_too(self):
        said = jo.plan("ノーションを開いて")["speech"]
        self.assertIn("app", said)

class TestRouting(unittest.TestCase):
    def setUp(self):
        import jarvis_router
        self.r = jarvis_router

    def test_launch_reaches_open_without_the_nickname(self):
        # 実測で「アルフ」が α に化けるので、呼びかけ語に依存させない
        for t in ["アップを起動して", "α-MVPを起動して", "トレードを立ち上げて"]:
            self.assertEqual(self.r.route(t)["route"], "OPEN", t)

    def test_close_reaches_open_only_for_known_targets(self):
        self.assertEqual(self.r.route("トレードを終了して")["route"], "OPEN")
        self.assertEqual(self.r.route("アップを閉じて")["route"], "OPEN")

    def test_a_generic_close_does_not_reach_open(self):
        # 「終了して」だけで OPEN に来ると拒否メッセージを返してしまう。
        # route() を呼ぶと override に当たらない発話は LLM 経路まで落ちるので
        # 判定条件だけを見る（CI に ollama は無い）
        for t in ["会議を終了して", "作業を止めて", "アプリを閉じて"]:
            self.assertTrue(jo.looks_like_close(t), t)
            self.assertIsNone(jo.resolve_target(t), t)

    def test_the_secretary_still_wins(self):
        self.assertEqual(self.r.route("秘書、アップを終了して")["route"],
                         "SECRETARY")

    def test_reboot_still_does_not_reach_open(self):
        pat = dict(self.r._OVERRIDES)["OPEN"]
        self.assertIsNone(pat.search("Macを再起動して"))
if __name__ == "__main__":
    unittest.main(verbosity=2)
