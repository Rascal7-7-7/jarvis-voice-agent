"""入力デバイスの優先順位選択（案C）。

背景（2026-09-08/09 実測）:
  upstream は「OS の既定入力」をそのまま開く。既定は「最後に接続したものが勝つ」
  なので、ユーザーが望む固定優先順位（外部 > 無線 > 内蔵）とは一致しない。
  さらに `follow_default_device = False`（CoreAudio の close→reopen デッドロック回避）
  のため、実行中のデバイス変更には追従しない。

  加えてユーザーはクラムシェル運用（外部モニタ・蓋を閉じている）で、
  その間 **内蔵マイクは列挙されるが全ゼロを返す**。優先順位に内蔵を残すと
  外部マイクを抜いた瞬間に無音デバイスへ落ちる。
  クラムシェル状態は ioreg の AppleClamshellState から決定的に取得できる。

方針:
  デバイス選択は sd.default.device をプロセスローカルに設定して誘導する。
  上流の voice_mode.py は無変更（InputStream は device= を渡していないため）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))

import audio_devices as ad  # noqa: E402


def _dev(index, name, ch=1):
    return {"index": index, "name": name, "max_input_channels": ch}


# ---------------------------------------------------------------- classify

def test_builtin_mic_is_the_lowest_tier():
    assert ad.classify("MacBook Proのマイク") == ad.TIER_BUILTIN
    assert ad.classify("MacBook Air Microphone") == ad.TIER_BUILTIN
    assert ad.classify("Built-in Microphone") == ad.TIER_BUILTIN
    assert ad.classify("内蔵マイク") == ad.TIER_BUILTIN


def test_jack_and_usb_mics_are_the_top_tier():
    assert ad.classify("外部マイク") == ad.TIER_EXTERNAL
    assert ad.classify("External Microphone") == ad.TIER_EXTERNAL
    assert ad.classify("Yeti Nano") == ad.TIER_EXTERNAL


def test_virtual_drivers_are_excluded():
    for name in ("NoMachine Audio Adapter", "NoMachine Microphone Adapter",
                 "BlackHole 2ch", "Loopback Audio", "Soundflower (2ch)",
                 "ZoomAudioDevice", "VB-Cable", "Aggregate Device"):
        assert ad.classify(name) == ad.TIER_EXCLUDED, name


def test_bluetooth_and_continuity_are_the_middle_tier():
    assert ad.classify("AirPods Pro") == ad.TIER_WIRELESS
    assert ad.classify("WH-1000XM5") == ad.TIER_WIRELESS
    assert ad.classify("Beats Studio Buds") == ad.TIER_WIRELESS
    # Continuity（iPhone をマイクとして使う）。実物は LRM 制御文字が前置される
    assert ad.classify("‎Clayのマイク") == ad.TIER_WIRELESS


def test_builtin_pattern_wins_over_the_continuity_pattern():
    # どちらも「…のマイク」で終わるが、MacBook を含む方は内蔵
    assert ad.classify("MacBook Proのマイク") == ad.TIER_BUILTIN
    assert ad.classify("Clayのマイク") == ad.TIER_WIRELESS


# ---------------------------------------------------------------- select

_REAL = [_dev(2, "‎Clayのマイク"), _dev(5, "外部マイク"),
         _dev(7, "MacBook Proのマイク"), _dev(9, "NoMachine Audio Adapter", 2),
         _dev(10, "NoMachine Microphone Adapter", 2)]


def test_select_prefers_external_over_wireless_and_builtin():
    chosen = ad.select(_REAL, clamshell_closed=True)
    assert chosen["index"] == 5
    assert chosen["name"] == "外部マイク"
    assert chosen["tier"] == ad.TIER_EXTERNAL


def test_select_falls_back_to_wireless_when_no_external():
    devs = [d for d in _REAL if d["index"] != 5]
    chosen = ad.select(devs, clamshell_closed=True)
    assert chosen["index"] == 2


def test_clamshell_closed_removes_the_builtin_mic():
    # 内蔵しか残っていないのにクラムシェルが閉じている → 選ばない
    devs = [_dev(7, "MacBook Proのマイク")]
    assert ad.select(devs, clamshell_closed=True) is None


def test_clamshell_open_allows_the_builtin_mic_as_last_resort():
    devs = [_dev(7, "MacBook Proのマイク")]
    chosen = ad.select(devs, clamshell_closed=False)
    assert chosen["index"] == 7
    assert chosen["tier"] == ad.TIER_BUILTIN


def test_unknown_clamshell_state_treats_the_builtin_as_usable():
    # 取得できなかった場合に内蔵を捨てると、蓋が開いている機体で回帰する
    devs = [_dev(7, "MacBook Proのマイク")]
    assert ad.select(devs, clamshell_closed=None)["index"] == 7


def test_select_skips_devices_with_no_input_channels():
    devs = [_dev(5, "外部マイク", ch=0), _dev(2, "AirPods Pro")]
    assert ad.select(devs, clamshell_closed=True)["index"] == 2


def test_select_never_returns_a_virtual_device():
    devs = [_dev(9, "NoMachine Audio Adapter", 2)]
    assert ad.select(devs, clamshell_closed=True) is None


def test_select_on_an_empty_list_is_none():
    assert ad.select([], clamshell_closed=False) is None


def test_ties_are_broken_by_the_lowest_index_for_determinism():
    devs = [_dev(8, "Yeti Nano"), _dev(3, "外部マイク")]
    assert ad.select(devs, clamshell_closed=True)["index"] == 3


# ---------------------------------------------------------------- clamshell

def test_clamshell_parse_yes():
    assert ad.parse_clamshell('  |   "AppleClamshellState" = Yes') is True


def test_clamshell_parse_no():
    assert ad.parse_clamshell('"AppleClamshellState" = No') is False


def test_clamshell_parse_missing_is_unknown():
    assert ad.parse_clamshell("") is None
    assert ad.parse_clamshell(None) is None
    assert ad.parse_clamshell("nothing relevant here") is None


# ------------------------------------------- 入力だけを触ることの保証（回帰防止）
#
# 2026-09-09: 案C の初版は sd.default.device = (入力index, 既存の出力index) として
# 出力側を「保存」していた。しかしデバイスの抜き差しで PortAudio の index は
# 振り直されるので、保存した index が別デバイスを指し得る。
# 現状 macOS では出力に sounddevice を使わない（TCC 回避）ため無害だったが、
# 潜在バグなので出力側には触らない形に直した。

class _FakeDefault:
    def __init__(self, device):
        self.device = list(device)


class _FakeSD:
    """sd.default.device の要素代入を再現する最小のフェイク。"""
    def __init__(self, device=(5, 4)):
        self.default = _FakeDefault(device)
        self._devices = [
            {"name": "外部マイク", "max_input_channels": 1, "max_output_channels": 0},
        ]

    def query_devices(self):
        return self._devices


def test_apply_touches_only_the_input_slot():
    import shared_audio
    s = shared_audio.SharedAudioInput.__new__(shared_audio.SharedAudioInput)
    s.preferred_device = None
    fake = _FakeSD(device=(5, 4))
    chosen = {"index": 2, "name": "外部マイク", "tier": ad.TIER_EXTERNAL}
    shared_audio._set_input_device(fake, chosen["index"])
    assert fake.default.device[0] == 2      # 入力は変わる
    assert fake.default.device[1] == 4      # 出力は元のまま


def test_setting_the_input_device_survives_a_missing_slot():
    import shared_audio
    fake = _FakeSD(device=(None, None))
    shared_audio._set_input_device(fake, 7)
    assert fake.default.device[0] == 7
    assert fake.default.device[1] is None
