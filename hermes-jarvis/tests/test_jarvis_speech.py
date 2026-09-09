"""読み上げ前のサニタイズ（所見#10）。

背景（2026-09-09 17:26 実測）:
  LOCAL_TOOL 経路は C8 の読み上げ整形を通らない（C8 は esac の後にあるので
  CODEX / CLAUDE にしか適用されない）。そのため hermes の戻り値がそのまま
  TTS に渡り、**33 秒間ツール呼び出しの生 JSON を読み上げた**。

    state=SPEAKING clarify{questions:[{choices:[<|"|>症状の詳細を教えてください<|"|>,…
    PLAYBACK_DURATION=32992ms

  `<|"|>` は hermes の Python ソースに存在しない。つまり gemma4:e2b が
  **テキスト形式の壊れたツール呼び出しを出力した**もので、hermes は
  それを回答として返し、JARVIS がそのまま読み上げた。

  handoff §7 は「production で表示しないもの: heard_text / expanded response /
  raw tool args」と規定している。読み上げも同じ扱いにする。

方針:
  - モデル固有の特殊トークンや `name{...}` 形の構造を検出する（決定的・モデル不使用）
  - clarify の場合は人間可読な文字列を取り出して質問として読み上げる
  - 取り出せなければ短い定型文に落とす
  - 形に関係なく**読み上げ長の上限**を設ける。33 秒読み上げを二度と起こさないため
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))

import jarvis_speech as sp  # noqa: E402

# 実測された生出力（ログから復元）
_CLARIFY = ('clarify{questions:[{choices:[<|"|>症状の詳細を教えてください<|"|>,'
            '<|"|>特定の機能について<|"|>],question:<|"|>どちらについて聞きたいですか<|"|>}]}')
_CLARIFY_YESNO = ('clarify{questions:[{choices:[<|"|>はい<|"|>,<|"|>いいえ<|"|>],'
                  'question:<|"|>続けますか<|"|>}]}')


# ---------------------------------------------------------------- detection

def test_a_normal_sentence_is_not_a_tool_call():
    assert sp.looks_like_tool_call("こんにちは。何かお手伝いできることはありますか。") is False
    assert sp.looks_like_tool_call("停滞は7件です。") is False


def test_the_observed_clarify_payload_is_detected():
    assert sp.looks_like_tool_call(_CLARIFY) is True
    assert sp.looks_like_tool_call(_CLARIFY_YESNO) is True


def test_model_special_tokens_are_detected():
    assert sp.looks_like_tool_call('答えは<|"|>42<|"|>です') is True
    assert sp.looks_like_tool_call("<|im_start|>assistant") is True


def test_a_name_brace_structure_is_detected():
    assert sp.looks_like_tool_call('web_search{query:"天気"}') is True


def test_empty_input_is_not_a_tool_call():
    assert sp.looks_like_tool_call("") is False
    assert sp.looks_like_tool_call(None) is False


# ---------------------------------------------------------------- extraction

def test_clarify_is_spoken_as_its_question():
    said = sp.sanitize(_CLARIFY)
    assert "どちらについて聞きたいですか" in said
    assert "clarify" not in said
    assert "<|" not in said
    assert "choices" not in said


def test_clarify_without_a_question_field_falls_back_to_the_first_choice():
    payload = 'clarify{questions:[{choices:[<|"|>症状の詳細を教えてください<|"|>]}]}'
    said = sp.sanitize(payload)
    assert "症状の詳細を教えてください" in said
    assert "<|" not in said


def test_an_unparseable_tool_call_becomes_a_short_line():
    said = sp.sanitize("clarify{aaaa:[[[")
    assert said
    assert "clarify" not in said
    assert len(said) <= 80


# ---------------------------------------------------------------- passthrough

def test_a_normal_answer_passes_through_unchanged():
    text = "こんにちは。何かお手伝いできることはありますか。"
    assert sp.sanitize(text) == text


def test_whitespace_is_tidied_but_content_kept():
    assert sp.sanitize("  停滞は7件です。  ") == "停滞は7件です。"


# ---------------------------------------------------------------- length cap

def test_speech_is_capped_so_nothing_reads_for_30_seconds():
    """形に関係なく効く backstop。33 秒読み上げを二度と起こさない。"""
    long = "あ" * 5000
    said = sp.sanitize(long)
    assert len(said) <= sp.MAX_SPOKEN_CHARS


def test_the_cap_cuts_at_a_sentence_boundary_when_possible():
    text = "一文目です。" * 200
    said = sp.sanitize(text)
    assert said.endswith("。")
    assert len(said) <= sp.MAX_SPOKEN_CHARS


def test_none_and_empty_produce_a_speakable_fallback():
    assert sp.sanitize(None)
    assert sp.sanitize("")
    assert sp.sanitize("   ")
