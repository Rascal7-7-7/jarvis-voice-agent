"""ストリーム置換の数え方（所見#2）。

問題（2026-09-08/09 実測）:
  ログは `controlled replacement 1/3` と `shared stream replaced` を出しているのに
  counters の `replacements` は 0 のままだった。バグではなく、**置換経路が3つ**
  あり片方しか数えていなかったため。

    1. shared_audio.open_once                  初回オープン（数えない）
    2. shared_audio.ensure_healthy_for_turn    self.replacements += 1（数えていた）
    3. jarvis_runtime.py:1230 の無音 watchdog   shared.open_once() を呼ぶだけ（数えていない）

  3 は `detector.audio_silent` を契機に走り、最大 3 回/プロセス・1 回/分に制限されている。
  数えられないと jarvis-status の `repl` を根拠にした判定が置換を検知できない。

対策:
  数える場所を `_note_stream()` に集約する。ストリームオブジェクトが変わったことを
  見ているのはここだけなので、どの経路から来ても必ず通る。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))

from shared_audio import SharedAudioInput  # noqa: E402


class _Rec:
    def __init__(self):
        self._stream = None
        self._silence_threshold = 200
        self._sample_rate = 48000


def _bare():
    s = SharedAudioInput.__new__(SharedAudioInput)
    s._rec = _Rec()
    s._stream_obj = None
    s.pa_open_count = 0
    s.pa_start_count = 0
    s.replacements = 0
    return s


def test_first_open_is_not_counted_as_a_replacement():
    s = _bare()
    s._rec._stream = object()
    s._note_stream()
    assert s.pa_open_count == 1
    assert s.pa_start_count == 1
    assert s.replacements == 0


def test_a_new_stream_object_counts_as_one_replacement():
    s = _bare()
    s._rec._stream = object()
    s._note_stream()
    s._rec._stream = object()          # どの経路であれオブジェクトが変わった
    s._note_stream()
    assert s.pa_open_count == 2
    assert s.replacements == 1


def test_repeated_calls_with_the_same_object_do_not_count():
    s = _bare()
    obj = object()
    s._rec._stream = obj
    s._note_stream()
    s._note_stream()
    s._note_stream()
    assert s.pa_open_count == 1
    assert s.replacements == 0


def test_three_replacements_are_all_counted():
    s = _bare()
    for _ in range(4):                  # 初回 + 3 回の置換
        s._rec._stream = object()
        s._note_stream()
    assert s.pa_open_count == 4
    assert s.replacements == 3


def test_a_missing_stream_is_ignored():
    s = _bare()
    s._rec._stream = None
    s._note_stream()
    assert s.pa_open_count == 0
    assert s.replacements == 0
