"""NoiseFloorTracker — 適応的な無音しきい値。

背景（2026-09-09 実測）:
  SILENCE_RMS_THRESHOLD = 200 は固定値だが、外部マイク（ジャック）のノイズフロアは
  無発話時で mean RMS ≈ 2,663 = しきい値の 13 倍あった。よって rms <= 200 が
  3 秒連続する条件は原理的に成立せず、silence callback が一度も発火せず、
  capture が毎回 30 秒上限まで走っていた（1 ターン +26 秒）。

  内蔵マイクでは PEAK_RMS 1,446〜1,764 で発火していたため、固定しきい値は
  「静かなデバイス」を暗黙の前提にしていたことになる。

  共有ストリームはターン間も idle フレームを見ているので、ノイズフロアは
  追加コストなしで測れる。それを基準にしきい値を決める。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))

from shared_audio import NoiseFloorTracker  # noqa: E402


def test_no_observation_falls_back_to_minimum():
    t = NoiseFloorTracker()
    assert t.floor is None
    assert t.threshold() == 200


def test_quiet_device_keeps_the_upstream_minimum():
    # 内蔵マイク相当。フロアが低いので固定値 200 のままでよい
    t = NoiseFloorTracker()
    for _ in range(50):
        t.observe(30)
    assert t.floor == 30
    assert t.threshold() == 200


def test_noisy_device_raises_the_threshold_above_its_floor():
    # 外部マイク相当。実測フロア 2663
    t = NoiseFloorTracker()
    for _ in range(50):
        t.observe(2663)
    assert t.floor == 2663
    # 2663 * 2.5 = 6657.5 → 6657
    assert t.threshold() == 6657


def test_median_is_robust_against_occasional_speech():
    # idle 中に wake word 発話が混ざってもフロアは静かな側に留まる
    t = NoiseFloorTracker()
    for _ in range(90):
        t.observe(500)
    for _ in range(10):
        t.observe(25000)
    assert t.floor == 500
    assert t.threshold() == 1250


def test_threshold_is_capped_so_speech_is_not_swallowed():
    # フロアが極端に高い場合、しきい値を上げ続けると発話ごと無音扱いになる
    t = NoiseFloorTracker()
    for _ in range(50):
        t.observe(20000)
    assert t.threshold() == 8000


def test_window_is_bounded_and_forgets_old_values():
    t = NoiseFloorTracker(window=10)
    for _ in range(10):
        t.observe(9000)
    assert t.floor == 9000
    for _ in range(10):
        t.observe(100)
    assert t.floor == 100


def test_observe_ignores_negative_and_non_finite():
    t = NoiseFloorTracker()
    t.observe(-1)
    t.observe(float("nan"))
    assert t.floor is None
    t.observe(300)
    assert t.floor == 300


# ------------------------------------------------- apply 側（ハードウェア不要）

class _FakeRecorder:
    """AudioRecorder の必要最小限。_silence_threshold は実物と同じ初期値。"""
    def __init__(self):
        self._silence_threshold = 200
        self._sample_rate = 48000


class _NoAttrRecorder:
    """属性設定が失敗する recorder（__slots__ で拒否）。"""
    __slots__ = ()


def _stream_with(floor_samples, rec=None):
    """SharedAudioInput を最小構成で作る（stream は開かない）。"""
    from shared_audio import SharedAudioInput
    s = SharedAudioInput.__new__(SharedAudioInput)
    from shared_audio import NoiseFloorTracker
    s._floor = NoiseFloorTracker()
    s._rec = rec if rec is not None else _FakeRecorder()
    s.silence_threshold = 200
    for v in floor_samples:
        s._floor.observe(v)
    return s


def test_apply_sets_recorder_threshold_from_measured_floor():
    s = _stream_with([2663] * 50)
    assert s.apply_silence_threshold() == 6657
    assert s._rec._silence_threshold == 6657
    assert s.silence_threshold == 6657


def test_apply_keeps_upstream_default_on_a_quiet_device():
    s = _stream_with([30] * 50)
    assert s.apply_silence_threshold() == 200
    assert s._rec._silence_threshold == 200


def test_apply_without_observations_uses_the_minimum():
    s = _stream_with([])
    assert s.apply_silence_threshold() == 200


def test_apply_survives_a_recorder_that_rejects_the_attribute():
    # 設定できなくても例外を出さず、決めた値を返す
    s = _stream_with([2663] * 50, rec=_NoAttrRecorder())
    assert s.apply_silence_threshold() == 6657
