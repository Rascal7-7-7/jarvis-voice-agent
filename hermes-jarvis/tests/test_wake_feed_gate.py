"""The wake feed gate: withhold frames from the detector, keep the stream alive.

§6 is specific about what must and must not stop. The physical callback, the
copy, the aggregation and the resample all keep running; only
``ww.feed_audio(...)`` is skipped. These tests hold that line, because a gate
implemented by pausing the stream would look identical from the outside until
the day it deadlocks -- which is the failure this whole subsystem exists to
avoid.

SharedAudioInput is driven with stand-in `vm` and `ww` objects rather than real
audio, so the routing decisions are visible and no device is touched.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.expanduser("~/AI-Lab/hermes-jarvis/bin"))

from shared_audio import SharedAudioInput  # noqa: E402

CAPTURE_RATE = 48_000
CAPTURE_FRAME = 3840
ENGINE_FRAME = 1280


class FakeVM:
    def _import_audio(self):
        return None, np


class FakeWW:
    """Records every frame the detector would have been given."""

    def __init__(self):
        self.fed: list[np.ndarray] = []

    def _resample_audio_frame(self, np_mod, frame, out_len):
        source = np_mod.asarray(frame, dtype=np_mod.float64).reshape(-1)
        edges = np_mod.linspace(0, source.size, out_len + 1, dtype=np_mod.int64)
        vals = np_mod.add.reduceat(source, edges[:-1]) / np_mod.diff(edges)
        return np_mod.rint(vals).clip(-32768, 32767).astype(np_mod.int16)

    def feed_audio(self, *, owner, pcm_int16):
        self.fed.append(np.array(pcm_int16, copy=True))
        return True


class FakeRecorder:
    def __init__(self):
        self._sample_rate = CAPTURE_RATE
        self.follow_default_device = True
        self.idle_listener = None
        self.stream_stops = 0
        self.stream_closes = 0

    def set_idle_frame_listener(self, fn):
        self.idle_listener = fn


@pytest.fixture
def shared():
    ww = FakeWW()
    rec = FakeRecorder()
    s = SharedAudioInput(FakeVM(), ww, rec, object())
    return s, ww, rec


def block(n=CAPTURE_FRAME, value=1000) -> np.ndarray:
    """One capture block of a constant, non-zero signal."""
    return np.full(n, value, dtype=np.int16)


# --- open by default ------------------------------------------------------

def test_the_gate_is_open_by_default(shared):
    s, ww, _ = shared
    assert s.wake_feed_enabled is True
    s._on_idle_frame(block())
    assert len(ww.fed) == 1
    assert s.engine_frames_fed == 1
    assert s.wake_feed_gated_frames == 0


# --- gated ----------------------------------------------------------------

def test_gating_withholds_frames_from_the_detector(shared):
    s, ww, _ = shared
    s.gate_wake_feed()
    for _ in range(5):
        s._on_idle_frame(block())
    assert ww.fed == [], "frames reached the detector while gated"
    assert s.wake_feed_gated_frames == 5
    assert s.engine_frames_fed == 0


def test_the_callback_still_does_all_its_work_while_gated(shared):
    """§6: callback, copy, aggregation and resample continue. Only feed stops."""
    s, _ww, _ = shared
    s.gate_wake_feed()
    s._on_idle_frame(block())
    # The chunk was counted, so the callback ran and the copy happened.
    assert s.idle_chunks == 1
    assert s.idle_frames == CAPTURE_FRAME
    # A whole engine frame was assembled and resampled, then withheld.
    assert s.wake_feed_gated_frames == 1


def test_gating_never_touches_the_stream(shared):
    s, _ww, rec = shared
    s.gate_wake_feed()
    for _ in range(10):
        s._on_idle_frame(block())
    s.ungate_wake_feed()
    assert rec.stream_stops == 0, "the stream was stopped"
    assert rec.stream_closes == 0, "the stream was closed"
    assert s.pa_open_count == 0
    assert s.pa_start_count == 0
    assert s.replacements == 0


def test_gate_engagements_counts_transitions_not_calls(shared):
    s, _ww, _ = shared
    s.gate_wake_feed()
    s.gate_wake_feed()
    s.gate_wake_feed()
    assert s.gate_engagements == 1, "a re-gate was counted as a new engagement"
    s.ungate_wake_feed()
    s.gate_wake_feed()
    assert s.gate_engagements == 2


# --- ungated --------------------------------------------------------------

def test_ungating_restores_the_feed(shared):
    s, ww, _ = shared
    s.gate_wake_feed()
    s._on_idle_frame(block())
    assert ww.fed == []
    s.ungate_wake_feed()
    s._on_idle_frame(block())
    assert len(ww.fed) == 1


def test_no_audio_from_the_gated_period_leaks_across_the_reopen(shared):
    """The pending remainder is dropped, not spliced onto the next frame.

    While gated, up to one capture frame short of a full block can be sitting in
    `_pending`. That audio is JARVIS's own reply. Carrying it over would put the
    tail of the reply at the head of the first thing the detector scores next.
    """
    s, ww, _ = shared
    s.gate_wake_feed()
    # A partial block: not enough to make an engine frame, so it stays pending.
    s._on_idle_frame(block(n=CAPTURE_FRAME // 2, value=-30000))
    assert s._pending is not None and len(s._pending) == CAPTURE_FRAME // 2

    s.ungate_wake_feed()
    assert s._pending is None, "gated-period audio survived the reopen"

    s._on_idle_frame(block(n=CAPTURE_FRAME, value=1000))
    assert len(ww.fed) == 1
    # Nothing from the -30000 signal is in what the detector received.
    assert ww.fed[0].min() > 0, "audio from the gated period leaked through"


def test_a_full_turn_shaped_sequence(shared):
    """gate -> speak -> ungate, with the frame accounting checked throughout."""
    s, ww, _ = shared
    for _ in range(3):                      # idle, armed
        s._on_idle_frame(block())
    assert len(ww.fed) == 3

    s.gate_wake_feed()                      # turn starts
    for _ in range(25):                     # capture, STT, backend, TTS
        s._on_idle_frame(block())
    assert len(ww.fed) == 3, "frames reached the detector during the turn"
    assert s.wake_feed_gated_frames == 25

    s.ungate_wake_feed()                    # after the settle
    for _ in range(3):
        s._on_idle_frame(block())
    assert len(ww.fed) == 6
    assert s.engine_frames_fed == 6
    counters = s.counters()
    assert counters["wake_feed_enabled"] is True
    assert counters["wake_feed_gated_frames"] == 25
    assert counters["gate_engagements"] == 1
    assert counters["PA_OPEN_COUNT"] == 0
    assert counters["PA_START_COUNT"] == 0
    assert counters["replacements"] == 0


def test_gating_does_not_raise_on_the_audio_thread(shared):
    """A gate that can throw would land in feed_errors and be nearly silent."""
    s, _ww, _ = shared
    s.gate_wake_feed()
    for n in (0, 1, 17, CAPTURE_FRAME, CAPTURE_FRAME * 3 + 5):
        s._on_idle_frame(block(n=max(n, 1)))
    assert s.feed_errors == 0


# --- what the runtime relies on ------------------------------------------

def test_the_runtime_gates_before_capture_and_ungates_after_resume():
    """Source-order check: the gate must close before the recorder starts.

    Asserted against the source because the ordering is what matters and a
    helper-level test cannot see it -- the same reason the turn_id ordering bug
    needed a source-order test to catch it.
    """
    src = open(os.path.expanduser(
        "~/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py")).read()
    gate = src.index("shared.gate_wake_feed()")
    capture_start = src.index('tl.mark("capture_start")')
    settle = src.index("_stop.wait(SETTLE_AFTER_SPEAKING_S)")
    resume = src.index("ww.resume_listening(owner=recorder_box")
    ungate = src.index("shared.ungate_wake_feed()")
    assert gate < capture_start, "capture starts before the feed is gated"
    assert settle < resume, "the listener re-arms before the settle"
    assert resume < ungate, "the feed reopens before the detector re-arms"


def test_the_settle_is_400ms():
    src = open(os.path.expanduser(
        "~/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py")).read()
    assert "SETTLE_AFTER_SPEAKING_S = 0.400" in src
