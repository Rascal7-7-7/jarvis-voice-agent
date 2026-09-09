"""音声から秘書を呼ぶ経路（deterministic のみ）。

設計の制約（DELEGATION_SECURITY.md に従う）:
  - **LLM Router ≠ security boundary。** router は gemma4:e2b で
    dangerous_action_accuracy 0.75 と実測されている。したがって SECRETARY は
    LABELS に入れない = **LLM が出力できない宛先**にする。
    到達経路は決定的なキーワード override だけ。
  - gate は override より前に走る（jarvis_router.route の順序）。
    よって CONFIRMATION_REQUIRED の発話は SECRETARY に到達しない。
  - **音声からの書き込みは行わない。** 読み取り系は即実行するが、
    dispatch は**キュー登録のみ**で、実行はテキスト側の明示操作に委ねる。
    JARVIS の委譲は plan mode / read-only sandbox がピン留めされており
    「Write delegation. Not enabled.」が明記されている。その境界を崩さない。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))

import jarvis_secretary as js  # noqa: E402

_PROJECTS = ["it-study", "tradingview-mcp", "automation", "AI_Trade",
             "brock_project", "Ai_Create", "portfolio-mvps"]


# ---------------------------------------------------------------- trigger

def test_trigger_requires_the_secretary_keyword():
    assert js.matches("秘書、状況を教えて") is True
    assert js.matches("ひしょ、停滞してるプロジェクトある？") is True
    assert js.matches("今日の天気を教えて") is False
    assert js.matches("") is False


def test_trigger_is_not_confused_by_similar_words():
    # 「秘書」を含まない語で誤発火しない
    assert js.matches("秘密の話") is False
    assert js.matches("書類を整理して") is False


# ---------------------------------------------------------------- read intents

def test_brief_intent_from_several_phrasings():
    for u in ("秘書、状況を教えて", "秘書、停滞してるやつある？",
              "秘書、止まってるプロジェクトは？", "秘書、進捗どう？"):
        intent, _ = js.classify(u, projects=_PROJECTS)
        assert intent == js.INTENT_BRIEF, u


def test_list_intent():
    intent, _ = js.classify("秘書、プロジェクト一覧を出して", projects=_PROJECTS)
    assert intent == js.INTENT_LIST


def test_reports_intent():
    intent, _ = js.classify("秘書、レポートを見せて", projects=_PROJECTS)
    assert intent == js.INTENT_REPORTS


# ---------------------------------------------------------------- dispatch

def test_dispatch_extracts_project_and_instruction():
    intent, payload = js.classify(
        "秘書、it-study の未コミットを整理して", projects=_PROJECTS)
    assert intent == js.INTENT_DISPATCH
    assert payload["project"] == "it-study"
    assert "未コミット" in payload["instruction"]


def test_dispatch_matches_the_longest_project_name():
    # 部分一致で短い名前に食われないこと
    intent, payload = js.classify(
        "秘書、tradingview-mcp の状態を調べて", projects=_PROJECTS + ["trading"])
    assert payload["project"] == "tradingview-mcp"


def test_dispatch_without_a_known_project_is_unknown():
    intent, _ = js.classify("秘書、なんかいい感じにやっといて", projects=_PROJECTS)
    assert intent == js.INTENT_UNKNOWN


def test_dispatch_action_is_enqueue_never_execute():
    """音声から書き込みを起こさない、という v1 の境界。"""
    _intent, payload = js.classify(
        "秘書、automation のテストを直して", projects=_PROJECTS)
    assert payload["action"] == "enqueue"
    assert payload["action"] != "execute"


def test_read_intents_are_marked_as_safe_to_execute():
    for u in ("秘書、状況を教えて", "秘書、レポートを見せて"):
        _intent, payload = js.classify(u, projects=_PROJECTS)
        assert payload["action"] == "read"


# ---------------------------------------------------------------- spoken reply

def test_brief_reply_is_short_enough_to_speak():
    reply = js.summarize_brief({"projects": [
        {"name": "tradingview-mcp", "stalled": 1, "idle_days": 158, "dirty": 1},
        {"name": "it-study", "stalled": 1, "idle_days": 11, "dirty": 29},
        {"name": "automation", "stalled": 0, "idle_days": 0, "dirty": 8},
    ]})
    assert "2" in reply                     # 停滞2件
    assert "tradingview-mcp" in reply       # 最長のものを名指しする
    assert len(reply) <= 120                # TTS で読み上げられる長さ


def test_brief_reply_when_nothing_is_stalled():
    reply = js.summarize_brief({"projects": [
        {"name": "automation", "stalled": 0, "idle_days": 0, "dirty": 8}]})
    assert "停滞" in reply
    assert "なし" in reply or "ありません" in reply


def test_brief_reply_tolerates_empty_input():
    assert js.summarize_brief({"projects": []})
    assert js.summarize_brief({})
    assert js.summarize_brief(None)


# ------------------------------------------------- router 統合（到達経路の固定）

import jarvis_router as jr  # noqa: E402


def test_secretary_is_not_an_llm_emittable_label():
    """LLM が SECRETARY を出力できないことを固定する。

    LABELS は LLM 出力の検証に使われる。ここに SECRETARY が入ると、
    dangerous_action_accuracy 0.75 のモデルが書き込みに繋がり得る宛先を
    選べるようになる。
    """
    assert "SECRETARY" not in jr.LABELS


def test_secretary_is_reachable_only_by_explicit_override():
    labels = [lab for lab, _pat in jr._OVERRIDES]
    assert "SECRETARY" in labels


def test_the_gate_runs_before_the_secretary_override():
    """危険発話は秘書に到達しない（gate が先）。"""
    import jarvis_gate as jg
    gated = jg.check("秘書、このファイル全部消して")
    assert gated["route"] == jg.CONFIRMATION_REQUIRED


def test_a_plain_secretary_request_is_not_gated():
    import jarvis_gate as jg
    assert jg.check("秘書、状況を教えて")["route"] is None


def test_brief_reply_does_not_read_the_no_commit_sentinel_as_days():
    """survey.sh は「コミット0件」に idle_days=999 を入れる。

    これは日数ではなく番兵なので、そのまま読み上げると事実と違うことを言う。
    """
    reply = js.summarize_brief({"projects": [
        {"name": "game-security-academy", "stalled": 1, "idle_days": 999, "dirty": 1},
        {"name": "it-study", "stalled": 1, "idle_days": 11, "dirty": 29},
    ]})
    assert "999" not in reply
    assert "it-study" in reply          # 日数のあるものを最長として名指しする
    assert "コミット" in reply           # 未コミットの件数には触れる


def test_brief_reply_when_every_stalled_project_has_no_commit():
    reply = js.summarize_brief({"projects": [
        {"name": "a", "stalled": 1, "idle_days": 999, "dirty": 1}]})
    assert "999" not in reply
    assert "1件" in reply


# ------------------------------------------- 音声からの書き込み実行（確認つき）
#
# 設計判断（DELEGATION_SECURITY.md に基づく）:
#   gate は keyword matcher であり、意図的に難読化した発話は覆えないと明記されている
#   （「あの子を綺麗にしといて」）。gate は「事故のコストを上げる」ものであって、
#   書き込みの最終判断を委ねられる仕組みではない。
#   よって書き込みを伴う指示は**復唱して確認を1回取る**。読み取りは即実行のまま。
#
#   確認は決定的なパターンで判定する（モデルに委ねない）。
#   確認待ちの状態はファイルに持つ（プロセスをまたぐため）。

def test_a_write_instruction_asks_for_confirmation():
    reply, pending = js.plan_dispatch("it-study", "未コミットを整理して")
    assert pending is not None
    assert pending["project"] == "it-study"
    assert "it-study" in reply
    assert "よろしいですか" in reply or "実行しますか" in reply


def test_the_confirmation_repeats_the_instruction_back():
    """復唱がないと、聞き間違いをそのまま実行してしまう。"""
    reply, _ = js.plan_dispatch("automation", "テストを直して")
    assert "テストを直して" in reply


def test_yes_confirms():
    for u in ("はい", "はい、お願い", "実行して", "OK", "オーケー", "やって"):
        assert js.is_confirmation(u) is True, u


def test_no_cancels():
    for u in ("いいえ", "やめて", "キャンセル", "違う", "だめ"):
        assert js.is_confirmation(u) is False, u


def test_an_unrelated_utterance_is_neither():
    for u in ("今日の天気", "秘書、状況を教えて", ""):
        assert js.is_confirmation(u) is None, u


def test_confirmation_uses_deterministic_patterns_only():
    """モデルに判定させない。パターンは決定的であること。"""
    import re
    assert isinstance(js.CONFIRM_YES, re.Pattern)
    assert isinstance(js.CONFIRM_NO, re.Pattern)


def test_read_instructions_do_not_ask_for_confirmation():
    for u in ("秘書、状況を教えて", "秘書、レポートを見せて"):
        _intent, payload = js.classify(u, projects=["it-study"])
        assert payload["action"] == "read"


# ------------------------------------------------------- 作業対象外の強制
#
# 2026-09-09 ユーザー指示: client-a（業務データ（取扱いに配慮が必要な情報を含む））は
# 秘書に触らせない。
#
# **記憶ではなくコードで止める。** 意図は忘れられるが決定的な判定は忘れない。
# 音声経路にも同じ境界を入れる。CLI 側だけ守っても、発話から抜けられては意味がない。
#
# 読み取り（状況確認）は許す。状態は把握したいが手は出させたくない、という区別。

def test_excluded_project_is_refused_for_dispatch():
    intent, payload = js.classify("秘書、client-a の未コミットを整理して",
                                  projects=["client-a", "it-study"],
                                  excluded=["client-a"])
    assert intent == js.INTENT_REFUSED
    assert payload["project"] == "client-a"


def test_a_non_excluded_project_still_dispatches():
    intent, payload = js.classify("秘書、it-study の未コミットを整理して",
                                  projects=["client-a", "it-study"],
                                  excluded=["client-a"])
    assert intent == js.INTENT_DISPATCH
    assert payload["project"] == "it-study"


def test_exclusion_is_exact_match_not_substring():
    """部分一致にすると別プロジェクトを巻き込む。

    'client-a' の除外が 'client-a-nested-git-backup' まで止めてはいけない。
    """
    intent, payload = js.classify(
        "秘書、client-a-nested-git-backup の状態を調べて",
        projects=["client-a", "client-a-nested-git-backup"],
        excluded=["client-a"])
    assert intent == js.INTENT_DISPATCH
    assert payload["project"] == "client-a-nested-git-backup"


def test_read_intents_are_not_blocked_by_exclusion():
    """読み取りは止めない。状態は把握したい。"""
    intent, payload = js.classify("秘書、状況を教えて",
                                  projects=["client-a"], excluded=["client-a"])
    assert intent == js.INTENT_BRIEF
    assert payload["action"] == "read"


def test_the_refusal_says_why():
    reply = js.handle("秘書、client-a を整理して",
                      projects=["client-a"], excluded=["client-a"])
    assert "client-a" in reply
    assert "対象外" in reply


def test_excluded_list_is_read_from_the_file_by_default():
    names = js.excluded_projects()
    assert "client-a" in names
    # コメント行や空行を拾っていないこと
    assert all(n and not n.startswith("#") for n in names)
