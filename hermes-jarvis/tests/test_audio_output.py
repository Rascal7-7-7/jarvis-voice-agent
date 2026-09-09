"""出力デバイスの固定（イヤホンへ流す）。

背景（2026-09-09 実測）:
  JARVIS の再生は afplay 経由で、afplay に**デバイス指定オプションが無い**
  （実測: 音量・時間・レートのみ）。よってシステム既定出力にしか出せなかった。
  そして DisplayLink ドックが既定を繰り返し奪い返すため、手動で戻しても維持できない
  （2 回発生）。

  ffmpeg の audiotoolbox 出力は `-audio_device_index` を持つ。実機検証で
  「既定=イヤホン」の状態で index 8（本体スピーカー）を指定したら**本体から鳴った**。
  つまりシステム既定を無視して指定先へ流せる。

  さらに JARVIS は自前の `_speak()`（jarvis_runtime.py:787）を持ち、
  そこから `play_audio_file(p)` を呼んでいる。**上流を触らずに差し替えられる。**

UID で照合する理由:
  ffmpeg が出す index はデバイスの抜き差しで振り直されるが、
  UID（`BuiltInHeadphoneOutputDevice` 等）は不変。毎回 UID から index を解決する。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))

import audio_output as ao  # noqa: E402

# 実機の ffmpeg -list_devices 出力（2026-09-09）
_REAL = """[AudioToolbox @ 0x96703d080] CoreAudio devices:
[AudioToolbox @ 0x96703d080] [0]                         (null), 430F0024-0000-0000-0123-010380351E78
[AudioToolbox @ 0x96703d080] [1]                         (null), 430F0024-0000-0000-0123-010380351E78_00000030
[AudioToolbox @ 0x96703d080] [2]                         (null), 5A98D091-FA89-4EAF-8A4F-7DCF00000003
[AudioToolbox @ 0x96703d080] [3]                         (null), AppleUSBAudioEngine:DisplayLink:USB Display:XF39360102849:3
[AudioToolbox @ 0x96703d080] [4]           Realtek USB2.0 Audio, AppleUSBAudioEngine:Generic:USB Audio:2143000:1
[AudioToolbox @ 0x96703d080] [5]                         (null), BuiltInHeadphoneInputDevice
[AudioToolbox @ 0x96703d080] [6]                         (null), BuiltInHeadphoneOutputDevice
[AudioToolbox @ 0x96703d080] [7]                         (null), BuiltInMicrophoneDevice
[AudioToolbox @ 0x96703d080] [8]                         (null), BuiltInSpeakerDevice
[AudioToolbox @ 0x96703d080] [9]        NoMachine Audio Adapter, NMAudioDevice_UID
[AudioToolbox @ 0x96703d080] [10]   NoMachine Microphone Adapter, NMAudioMicDevice_UID
"""


# ---------------------------------------------------------------- parsing

def test_parses_index_and_uid_from_the_real_output():
    devs = ao.parse_devices(_REAL)
    assert len(devs) == 11
    assert devs[6]["index"] == 6
    assert devs[6]["uid"] == "BuiltInHeadphoneOutputDevice"
    assert devs[4]["name"] == "Realtek USB2.0 Audio"


def test_parsing_ignores_unrelated_lines():
    assert ao.parse_devices("no devices here") == []
    assert ao.parse_devices("") == []
    assert ao.parse_devices(None) == []


def test_uid_with_colons_is_kept_whole():
    devs = ao.parse_devices(_REAL)
    assert devs[3]["uid"] == "AppleUSBAudioEngine:DisplayLink:USB Display:XF39360102849:3"


# ---------------------------------------------------------------- classify

def test_headphone_jack_is_the_top_tier():
    assert ao.classify_output("BuiltInHeadphoneOutputDevice") == ao.TIER_JACK


def test_builtin_speaker_is_the_fallback():
    # クラムシェルでも鳴ることを実機で確認済み
    assert ao.classify_output("BuiltInSpeakerDevice") == ao.TIER_SPEAKER


def test_input_only_devices_are_excluded():
    for uid in ("BuiltInHeadphoneInputDevice", "BuiltInMicrophoneDevice",
                "NMAudioMicDevice_UID"):
        assert ao.classify_output(uid) == ao.TIER_EXCLUDED, uid


def test_virtual_and_dock_devices_are_excluded():
    for uid in ("NMAudioDevice_UID",
                "AppleUSBAudioEngine:DisplayLink:USB Display:XF39360102849:3"):
        assert ao.classify_output(uid) == ao.TIER_EXCLUDED, uid


def test_unknown_devices_are_excluded_not_guessed():
    """未知の出力先は選ばない。

    入力側（audio_devices）では未知を実マイクとみなしたが、出力は逆。
    「開けるが音が出ない」機器（繋がっていないドック・HDMI）が実在し、
    それを選ぶと今日と同じ無音事故になる。確実なものだけ選ぶ。
    """
    assert ao.classify_output("430F0024-0000-0000-0123-010380351E78") == ao.TIER_EXCLUDED
    assert ao.classify_output("AppleUSBAudioEngine:Generic:USB Audio:2143000:1") == ao.TIER_EXCLUDED


# ---------------------------------------------------------------- select

def test_select_picks_the_jack_on_the_real_device_list():
    chosen = ao.select_output(ao.parse_devices(_REAL))
    assert chosen["index"] == 6
    assert chosen["uid"] == "BuiltInHeadphoneOutputDevice"


def test_select_falls_back_to_the_builtin_speaker():
    devs = [d for d in ao.parse_devices(_REAL)
            if d["uid"] != "BuiltInHeadphoneOutputDevice"]
    chosen = ao.select_output(devs)
    assert chosen["uid"] == "BuiltInSpeakerDevice"


def test_select_returns_none_when_nothing_is_usable():
    devs = [d for d in ao.parse_devices(_REAL)
            if d["uid"] not in ("BuiltInHeadphoneOutputDevice", "BuiltInSpeakerDevice")]
    assert ao.select_output(devs) is None


def test_an_explicit_uid_override_wins():
    devs = ao.parse_devices(_REAL)
    chosen = ao.select_output(devs, override_uid="BuiltInSpeakerDevice")
    assert chosen["index"] == 8


def test_an_override_that_is_absent_falls_back_to_the_priority():
    devs = ao.parse_devices(_REAL)
    chosen = ao.select_output(devs, override_uid="SomeMissingDevice")
    assert chosen["uid"] == "BuiltInHeadphoneOutputDevice"


def test_select_on_an_empty_list_is_none():
    assert ao.select_output([]) is None
