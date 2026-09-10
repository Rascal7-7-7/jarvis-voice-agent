"""音声の往復テスト — 合成 → STT → ルーティング。

**なぜ必要か（2026-09-10）:**
  音声アシスタントなのに、音声で検証したテストが1本も無かった。
  宛先は 22 種類あり、検証はすべて**テキストを直接** classify/route に
  渡すものだった。

  その結果 `(秘書|ひしょ)` が **音声で一度も当たっていなかった**。
  Whisper が「秘書」を「非処」、「ひしょ」を「一緒」と書き起こすため。
  テキストでは何度も「動く」と確認していた。

  このスイートは、その確認方法の穴を塞ぐ。

**実態とずらさないための決め事:**
  書き起こしは **runtime が実際に呼ぶ `vm.transcribe_recording`** を使う。
  Whisper のパラメータを複製すると、まさにここで直そうとしている
  「検証方法が実態と違う」誤りを再生産する。

**CI では動かせない。** `say`（macOS）と Whisper モデルが要る。
手元で実行する:

    ~/.hermes-venv/bin/python tests/test_voice_roundtrip.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "src", "hermes-agent-v2026.8.27"))

SAY = "/usr/bin/say"
VOICE = "Kyoko"
# .aiff は transcribe_recording が受け付けない。.m4a が最速（実測 0.7s）
EXT = "m4a"

_cache = {}


def _available():
    if not os.access(SAY, os.X_OK):
        return False, "say がありません（macOS 以外）"
    try:
        from tools import voice_mode  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, "hermes の voice_mode を import できません: %s" % exc
    return True, ""


AVAILABLE, WHY = _available()


def speak_and_transcribe(text):
    """発話を合成して、runtime と同じ経路で書き起こす。"""
    if text in _cache:
        return _cache[text]
    from tools import voice_mode as vm

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "u." + EXT)
        done = subprocess.run([SAY, "-v", VOICE, "-o", path, text],
                              capture_output=True)
        if done.returncode != 0 or not os.path.isfile(path):
            raise RuntimeError("合成に失敗: %s" % text)
        res = vm.transcribe_recording(path)
    if not res.get("success"):
        raise RuntimeError("書き起こしに失敗: %s / %s" % (text, res.get("error")))
    got = (res.get("transcript") or "").strip()
    _cache[text] = got
    return got


@unittest.skipUnless(AVAILABLE, WHY)
class VoiceCase(unittest.TestCase):
    """発話 -> 書き起こし -> route の宛先、を1つの検証にする。"""

    def assert_route(self, spoken, expected, msg=""):
        import jarvis_router

        heard = speak_and_transcribe(spoken)
        got = jarvis_router.route(heard)
        self.assertEqual(
            got["route"], expected,
            "話した=%r 聞こえた=%r 宛先=%s（期待 %s）%s"
            % (spoken, heard, got["route"], expected, msg))
        return heard

    def assert_not_route(self, spoken, forbidden):
        import jarvis_router

        heard = speak_and_transcribe(spoken)
        got = jarvis_router.route(heard)
        self.assertNotEqual(
            got["route"], forbidden,
            "話した=%r 聞こえた=%r が %s に流れた" % (spoken, heard, forbidden))
        return heard


class TestSecretaryTrigger(VoiceCase):
    """呼びかけ語が**音声で**届くか。ここが今日抜けていた穴。"""

    def test_the_nickname_reaches_the_secretary(self):
        self.assert_route("アルフ、状況を教えて", "SECRETARY")

    def test_the_nickname_with_san(self):
        self.assert_route("アルフさん、状況を教えて", "SECRETARY")

    def test_the_old_trigger_still_reaches_it(self):
        # 「秘書」は「非処」と書き起こされる。それでも届くこと
        heard = self.assert_route("秘書、状況を教えて", "SECRETARY")
        self.assertNotEqual(heard, "", "書き起こしが空")

    def test_hisho_in_kana(self):
        self.assert_route("ひしょ、状況を教えて", "SECRETARY")


class TestSecretaryIntents(VoiceCase):
    """秘書に届いた後、意図まで正しく落ちるか。"""

    def intent_of(self, spoken):
        import jarvis_secretary as js

        heard = speak_and_transcribe(spoken)
        intent, payload = js.classify(heard, projects=js.known_projects(),
                                      excluded=list(js.excluded_projects()))
        return heard, intent, payload

    def test_brief(self):
        heard, intent, _ = self.intent_of("アルフ、状況を教えて")
        self.assertEqual(intent, "BRIEF", heard)

    def test_attention(self):
        heard, intent, _ = self.intent_of("アルフ、対応が必要なものある")
        self.assertEqual(intent, "ATTENTION", heard)

    def test_history(self):
        heard, intent, _ = self.intent_of("アルフ、指示の履歴を見せて")
        self.assertEqual(intent, "HISTORY", heard)

    def test_search(self):
        heard, intent, payload = self.intent_of("アルフ、認証を探して")
        self.assertEqual(intent, "SEARCH", heard)
        self.assertTrue(payload.get("query"), heard)

    def test_sweep(self):
        heard, intent, _ = self.intent_of("アルフ、全プロジェクトのテストを走らせて")
        self.assertEqual(intent, "SWEEP", heard)


class TestOpenTargets(VoiceCase):
    """アプリ・URL・プロジェクト。呼びかけ語を要求しない経路。"""

    def test_a_url(self):
        self.assert_route("グーグルを開いて", "OPEN")

    def test_launching_a_project(self):
        heard = self.assert_route("アップを起動して", "OPEN")
        import jarvis_open as jo
        self.assertEqual(jo.plan(heard)["value"], "app", heard)

    def test_launching_by_spelling_it_out(self):
        # 実測: 「エーピーピー」は BPP と書き起こされる
        heard = self.assert_route("エーピーピーを起動して", "OPEN")
        import jarvis_open as jo
        self.assertEqual(jo.plan(heard)["value"], "app", heard)

    def test_closing_a_project(self):
        heard = self.assert_route("トレードを終了して", "OPEN")
        import jarvis_open as jo
        got = jo.plan(heard)
        self.assertEqual(got["action"], jo.ACTION_PROJECT_CLOSE, heard)
        self.assertEqual(got["value"], "trade", heard)

    def test_the_excluded_project_is_refused(self):
        heard = speak_and_transcribe("ワムを起動して")
        import jarvis_open as jo
        self.assertEqual(jo.plan(heard)["action"], jo.ACTION_REFUSE, heard)


class TestGate(VoiceCase):
    """危険発話は gate で止まるか。

    このスイートの初回実行で **gate が音声経路で機能していない**ことが分かった。
    STT が英単語をカタカナにするため:

        sudoで再起動して      -> スドーで再起動して      -> 素通り
        delete して          -> デリーとして           -> 素通り
        brew install して    -> ブルーインスタルして     -> 素通り
        npm uninstall して   -> npm アンインスタルして  -> 素通り

    素通り分は LOCAL_TOOL（toolsets=file,terminal,clarify）へ届いていた。
    実測した書き起こしを gate に足して塞いだ。ここはその退行テスト。
    """

    def test_sudo_reboot_is_gated(self):
        self.assert_route("sudoで再起動して", "CONFIRMATION_REQUIRED")

    def test_delete_is_gated(self):
        self.assert_route("データベースを delete して", "CONFIRMATION_REQUIRED")

    def test_brew_install_is_gated(self):
        self.assert_route("brew install してください", "CONFIRMATION_REQUIRED")

    def test_npm_uninstall_is_gated(self):
        self.assert_route("npm uninstall して", "CONFIRMATION_REQUIRED")

    def test_an_install_question_is_not_gated(self):
        # 「迷ったら確認」でも、普通の質問まで止めては使えない
        self.assert_not_route("インストール手順を教えて", "CONFIRMATION_REQUIRED")


class TestGateKnownGaps(VoiceCase):
    """塞げなかったもの。**測った事実として残す。**

    gate は keyword matcher で、意図的な難読化は覆えないと設計上明記されている。
    ここは「今どこに穴があるか」を忘れないための記録。
    """

    def test_chmod_is_mangled_beyond_matching(self):
        # 「chmod 777 にして」-> 「ともっと777にして」
        # 「ともっと」を足すと日常語で誤発火するので**追加していない**
        heard = speak_and_transcribe("chmod 777 にして")
        import jarvis_gate
        self.assertFalse(bool(jarvis_gate.check(heard)["route"]),
                         "塞げたなら known gap の記述を消すこと: %r" % heard)

    def test_a_question_about_sudo_gets_a_needless_prompt(self):
        # 「スドーとは何ですか」-> 「スドートは何ですか」で `とは` が消え、
        # _INQUIRY の免除が効かない。gate の方針（迷ったら確認）に沿うので許容
        heard = speak_and_transcribe("スドーとは何ですか")
        import jarvis_gate
        self.assertTrue(bool(jarvis_gate.check(heard)["route"]),
                        "免除が効くようになったなら記述を更新: %r" % heard)


class TestNoFalseFire(VoiceCase):
    """誤発火しないこと。実測でこの表記に書き起こされたものを使う。"""

    def test_alphabet_does_not_reach_the_secretary(self):
        self.assert_not_route("アルファベットで書いてください", "SECRETARY")

    def test_alfred_does_not_reach_the_secretary(self):
        self.assert_not_route("アルフレッドという名前", "SECRETARY")

    def test_rebooting_the_mac_is_not_an_open(self):
        self.assert_not_route("Macを再起動して", "OPEN")

    def test_update_is_not_the_app_project(self):
        heard = speak_and_transcribe("アップデートを起動して")
        import jarvis_open as jo
        self.assertEqual(jo.plan(heard)["action"], jo.ACTION_REFUSE, heard)


class TestTranscriptRecord(VoiceCase):
    """書き起こしそのものを記録する。

    落ちたときに「何と聞こえたか」が分からないと直せない。
    """

    def test_print_the_table(self):
        phrases = ["アルフ、状況を教えて", "秘書、状況を教えて",
                   "アップを起動して", "エーピーピーを起動して",
                   "トレードを終了して", "ワムを起動して"]
        print("\n  --- 話した / 聞こえた ---")
        for p in phrases:
            print("  %-30s %s" % (p, speak_and_transcribe(p)))


if __name__ == "__main__":
    if not AVAILABLE:
        print("実行できません:", WHY)
        sys.exit(77)
    unittest.main(verbosity=2)
