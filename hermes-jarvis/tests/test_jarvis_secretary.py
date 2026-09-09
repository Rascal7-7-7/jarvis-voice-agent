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
