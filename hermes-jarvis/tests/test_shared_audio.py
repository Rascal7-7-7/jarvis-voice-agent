"""Regression test for the production shared-stream implementation.

Drives the REAL objects -- ``tools.voice_mode.AudioRecorder``, the real
``wake_word`` detector in external_audio mode, and ``bin/shared_audio.py`` --
not the prototype. The prototype's numbers do not transfer: it had its own
command consumer, and the whole point of the production integration is that
capture runs upstream's untouched silence logic.

What is asserted:

  * the physical stream is opened and started exactly ONCE and stays that way
    across hundreds of WAKE <-> COMMAND transitions;
  * frames keep arriving throughout;
  * the wake detector never sees a frame while a capture is running;
  * upstream's silence semantics still hold -- a silent room ends a capture on
    the no-speech path at the configured max_wait, not on the 30 s ceiling.

Requires the wake-word machine lease, so the runtime must be stopped first.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src", "hermes-agent-v2026.8.27")
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, SRC)
os.chdir(SRC)

from shared_audio import SharedAudioInput          # noqa: E402
from tools import voice_mode as vm                 # noqa: E402
from tools import wake_word as ww                  # noqa: E402


class SharedStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = object()
        cls.rec = vm.AudioRecorder()
        cls.rec._silence_duration = 1.2
        cls.rec._max_wait = 8.0
        cls.shared = SharedAudioInput(vm, ww, cls.rec, cls.owner)
        assert cls.shared.open_once(), "the shared stream did not become active"
        cls.shared.attach_wake()
        cls.detector = ww.start_listening(lambda: None, owner=cls.owner,
                                          config=ww.load_wake_word_config(),
                                          external_audio=True)
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        try:
            ww.stop_listening(owner=cls.owner)
        finally:
            cls.shared.detach()
            cls.rec.shutdown()

    def test_startup_opened_exactly_one_stream(self):
        c = self.shared.counters()
        self.assertEqual(c["PA_OPEN_COUNT"], 1, c)
        self.assertEqual(c["PA_START_COUNT"], 1, c)
        self.assertTrue(c["stream_active"], c)

    def test_wake_detector_owns_no_device(self):
        # If it opened its own microphone, the whole design is void.
        self.assertTrue(self.detector.external_audio)
        d = self.detector.input_device_details or {}
        self.assertEqual(d.get("selector"), "client", d)

    def test_idle_frames_reach_the_wake_detector(self):
        before = self.shared.counters()["idle_chunks"]
        time.sleep(1.5)
        after = self.shared.counters()["idle_chunks"]
        self.assertGreater(after, before, "no idle frames were routed to wake")
        self.assertEqual(self.shared.counters()["feed_errors"], 0)

    def test_500_mode_transitions_never_touch_the_stream(self):
        start = self.shared.counters()
        stalls = 0
        last_chunks = start["idle_chunks"]

        for i in range(1, 501):
            # COMMAND: exactly what a turn does, minus STT and TTS.
            self.rec.start(on_silence_stop=lambda: None)
            self.assertTrue(self.rec._recording)
            time.sleep(0.004)
            self.rec.cancel()          # discard, no WAV
            time.sleep(0.004)

            if i % 100 == 0:
                c = self.shared.counters()
                self.assertEqual(c["PA_OPEN_COUNT"], 1,
                                 f"cycle {i}: a stream was opened")
                self.assertEqual(c["PA_START_COUNT"], 1,
                                 f"cycle {i}: a stream was started")
                self.assertTrue(c["stream_active"], f"cycle {i}: stream died")
                if c["idle_chunks"] == last_chunks:
                    stalls += 1
                last_chunks = c["idle_chunks"]

        end = self.shared.counters()
        print(f"\n  500 transitions: {end}")
        self.assertEqual(end["PA_OPEN_COUNT"], 1, end)
        self.assertEqual(end["PA_START_COUNT"], 1, end)
        self.assertEqual(end["replacements"], 0, end)
        self.assertEqual(end["feed_errors"], 0, end)
        self.assertEqual(stalls, 0, "frames stopped arriving between checkpoints")

    def test_wake_is_starved_while_recording(self):
        """Exclusive ownership must hold by construction, not by agreement."""
        before = self.shared.counters()["idle_chunks"]
        self.rec.start(on_silence_stop=lambda: None)
        time.sleep(0.8)                      # ~10 blocks of audio
        during = self.shared.counters()["idle_chunks"]
        self.rec.cancel()
        self.assertEqual(during, before,
                         "the wake detector was fed during a capture")
        time.sleep(0.5)
        self.assertGreater(self.shared.counters()["idle_chunks"], during,
                           "idle frames did not resume after the capture")

    def _ambient_rms(self, seconds: float = 2.0) -> int:
        """Median block RMS of the room, measured through the shared stream."""
        import numpy as np
        samples = []
        self.shared.detach()
        try:
            self.rec.set_idle_frame_listener(
                lambda indata: samples.append(
                    int(np.sqrt(np.mean(indata.astype(np.float64) ** 2)))))
            time.sleep(seconds)
        finally:
            self.rec.set_idle_frame_listener(None)
            self.shared.attach_wake()
        return sorted(samples)[len(samples) // 2] if samples else 0

    def test_capture_delivers_frames_and_opens_no_stream(self):
        """The signal that separates 'quiet room' from 'dead stream'.

        A live stream yields frames even in silence; the dead-stream bug
        produced exactly zero. This holds regardless of how noisy the room is,
        which is why it is asserted separately from the timing below.
        """
        done = threading.Event()
        self.rec.start(on_silence_stop=done.set)
        time.sleep(1.0)
        frames = len(self.rec._frames or [])
        self.rec.cancel()
        self.assertGreater(frames, 0, "the stream delivered no callbacks")
        self.assertEqual(self.shared.counters()["PA_OPEN_COUNT"], 1)
        self.assertEqual(self.shared.counters()["PA_START_COUNT"], 1)

    def test_silence_logic_still_ends_a_quiet_capture(self):
        """Upstream's no-speech path, unmodified, on the shared stream.

        Only meaningful in a room quieter than SILENCE_RMS_THRESHOLD. Above it
        every block counts as speech, so upstream deliberately keeps recording
        and this path cannot fire -- that is unchanged behaviour, not a
        regression, and failing the test there would be measuring the room.
        The recorder's blocksize is untouched by the shared stream (it IS the
        recorder's own stream), so the granularity that feeds this decision is
        exactly what it was before.
        """
        ambient = self._ambient_rms()
        print(f"\n  ambient median RMS = {ambient} "
              f"(threshold {vm.SILENCE_RMS_THRESHOLD})")
        if ambient > vm.SILENCE_RMS_THRESHOLD:
            self.skipTest(
                f"room too noisy to exercise the no-speech path: median RMS "
                f"{ambient} > {vm.SILENCE_RMS_THRESHOLD}. Upstream treats this "
                f"as continuous speech by design.")

        self.rec._max_wait = 2.0
        self.rec._silence_duration = 1.2
        done = threading.Event()
        t0 = time.monotonic()
        self.rec.start(on_silence_stop=done.set)
        fired = done.wait(timeout=10.0)
        elapsed = time.monotonic() - t0
        frames = len(self.rec._frames or [])
        self.rec.cancel()
        self.rec._max_wait = 8.0

        print(f"  quiet capture: fired={fired} elapsed={elapsed:.2f}s "
              f"frames={frames}")
        self.assertTrue(fired, "the no-speech path never fired")
        self.assertGreater(frames, 0)
        self.assertLess(elapsed, 6.0, "did not stop near max_wait")
        self.assertEqual(self.shared.counters()["PA_START_COUNT"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
