"""「〜を開いて」の判定。

境界は許可リストであって LLM ではない。router の override は
「開いて と言われた」ことしか判定せず、何を開くかは必ずここで決める。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import jarvis_open as jo


class TestTrigger(unittest.TestCase):
    def test_matches_open_forms(self):
        for t in ["グーグルを開いて", "ブレイブをひらいて", "Xを起動して", "ユーチューブ開いて"]:
            self.assertTrue(jo.looks_like_open(t), t)

    def test_does_not_match_unrelated(self):
        for t in ["今何時", "秘書、状況を教えて", "このファイルを読んで", "開発を続けて"]:
            self.assertFalse(jo.looks_like_open(t), t)

    def test_reboot_is_not_an_open_request(self):
        # gate が捕まえるのは「sudoで再起動して」だけ。「Macを再起動して」は
        # 素通りするので、trigger 側で除外しないと OPEN へ流れる
        for t in ["Macを再起動して", "ジャービスを再起動して", "再起動して"]:
            self.assertFalse(jo.looks_like_open(t), t)


class TestResolve(unittest.TestCase):
    def test_known_targets(self):
        cases = {
            "グーグルを開いて": "google",
            "googleを開いて": "google",
            "ブレイブを開いて": "brave",
            "Braveを起動して": "brave",
            "Xを開いて": "x",
            "エックスを開いて": "x",
            "ツイッターを開いて": "x",
            "ユーチューブを開いて": "youtube",
            "GitHubを開いて": "github",
            "ギットハブを開いて": "github",
        }
        for text, key in cases.items():
            got = jo.resolve_target(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got["key"], key, text)

    def test_every_project_target_maps_to_a_session(self):
        # 終了はセッションを落とすので、対応表に穴があると閉じられない
        for tgt in jo.TARGETS:
            if tgt["kind"] == jo.ACTION_PROJECT:
                self.assertIn(tgt["value"], jo.PROJECT_SESSIONS, tgt["key"])

    def test_unknown_target_is_none(self):
        for t in ["システム設定を開いて", "ノーションを開いて", "何かを開いて"]:
            self.assertIsNone(jo.resolve_target(t), t)

    def test_bare_x_needs_boundaries(self):
        # 単独の x は他の語の中で誤爆しやすい
        self.assertIsNone(jo.resolve_target("xcodeを開いて"))
        self.assertIsNone(jo.resolve_target("maxを開いて"))


class TestPlan(unittest.TestCase):
    def test_url_target(self):
        p = jo.plan("グーグルを開いて")
        self.assertEqual(p["action"], jo.ACTION_URL)
        self.assertTrue(p["value"].startswith("https://"))

    def test_app_target(self):
        p = jo.plan("ブレイブを開いて")
        self.assertEqual(p["action"], jo.ACTION_APP)
        self.assertEqual(p["value"], "Brave Browser")

    def test_refuses_unknown(self):
        p = jo.plan("システム設定を開いて")
        self.assertEqual(p["action"], jo.ACTION_REFUSE)
        # 何が開けるのかを言う。黙って拒否しない
        self.assertIn("Google", p["speech"])

    def test_refuses_when_not_an_open_request(self):
        p = jo.plan("今何時")
        self.assertEqual(p["action"], jo.ACTION_REFUSE)

    def test_speech_is_short(self):
        for t in ["グーグルを開いて", "システム設定を開いて"]:
            self.assertLessEqual(len(jo.plan(t)["speech"]), 120, t)

    def test_no_shell_metacharacters_reach_value(self):
        # 値は許可リスト由来のみ。発話の中身が混ざらないことを固定する
        p = jo.plan("グーグルを開いて; rm -rf /")
        self.assertEqual(p["value"], "https://www.google.com")


class TestTargets(unittest.TestCase):
    def test_every_target_has_a_known_kind(self):
        for tgt in jo.TARGETS:
            self.assertIn(tgt["kind"],
                          (jo.ACTION_APP, jo.ACTION_URL, jo.ACTION_PROJECT))
            self.assertTrue(tgt["value"])
            if tgt["kind"] == jo.ACTION_URL:
                self.assertTrue(tgt["value"].startswith("https://"), tgt["key"])


class TestRouting(unittest.TestCase):
    """router が OPEN へ流すこと、および LLM が OPEN を発明できないこと。"""

    def setUp(self):
        import jarvis_router
        self.r = jarvis_router

    def test_open_is_not_an_llm_label(self):
        # LABELS は LLM 出力の検証に使われる。ここに無ければモデルは選べない
        self.assertNotIn("OPEN", self.r.LABELS)

    def test_routes_to_open(self):
        for t in ["グーグルを開いて", "Xを開いて", "ユーチューブを起動して"]:
            got = self.r.route(t)
            self.assertEqual(got["route"], "OPEN", t)
            self.assertEqual(got["decided_by"], "explicit_override", t)
            self.assertFalse(got["llm_used"], t)

    def test_agent_names_still_win(self):
        # 既存挙動を壊さない。「クロードを開いて」は CLAUDE のまま
        self.assertEqual(self.r.route("クロードを開いて")["route"], "CLAUDE")
        self.assertEqual(self.r.route("コーデックスを起動して")["route"], "CODEX")

    def test_reboot_does_not_match_the_open_override(self):
        # route() を呼ぶと override に当たらない発話は LLM 経路まで落ちる。
        # CI に ollama は無く、待たされるだけで何も確かめられないので
        # override の一覧を直接見る
        pat = dict(self.r._OVERRIDES)["OPEN"]
        for t in ["Macを再起動して", "ジャービスを再起動して", "再起動して"]:
            self.assertIsNone(pat.search(t), t)

    def test_gate_still_outranks_open(self):
        got = self.r.route("sudoで再起動して")
        self.assertEqual(got["route"], "CONFIRMATION_REQUIRED")
        self.assertEqual(got["decided_by"], "security_gate")

    def test_secretary_still_wins(self):
        self.assertEqual(self.r.route("秘書、状況を開いて")["route"], "SECRETARY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
