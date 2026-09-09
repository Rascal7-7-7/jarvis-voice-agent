"""Prove the shared-stream audio path does not damage audio. No microphone.

Needs no device, no wake-word lease and no quiet room, so it can run any time
and is the regression test that actually pins the two bugs found during
integration:

  * frames handed on as VIEWS of PortAudio's reused buffer (score collapsed to
    0.0009 on a phrase that scores 0.4944);
  * partial engine frames, which wake_word.feed() zero-PADS rather than
    buffers, splicing silence through the phrase.

The acoustic tests cannot pin these: speaker-to-microphone playback on this
machine is marginal for the model -- the same phrase scored 0.0099-0.0560
through EVERY capture path, including the original wake listener's -- so a low
score there says nothing about the code. This test removes the room.
"""
from __future__ import annotations

import os
import sys
import unittest
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src", "hermes-agent-v2026.8.27")
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, SRC)
os.chdir(SRC)

import numpy as np  # noqa: E402

from tools import wake_word as ww  # noqa: E402

PHRASE_WAV = "/tmp/heyjarvis_sam.wav"
CAPTURE_RATE = 48000
RECORDER_BLOCK = 512          # PortAudio's default here, measured


def _load_phrase():
    with wave.open(PHRASE_WAV, "rb") as w:
        assert w.getframerate() == ww.SAMPLE_RATE
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


class _FakeRecorder:
    """Just enough AudioRecorder surface for SharedAudioInput to drive."""

    def __init__(self, rate):
        self._sample_rate = rate
        self._stream = None
        self._stream_identity = None
        self.follow_default_device = True
        self._idle_frame_listener = None

    def set_idle_frame_listener(self, cb):
        self._idle_frame_listener = cb


class FidelityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(PHRASE_WAV):
            raise unittest.SkipTest(f"{PHRASE_WAV} not present")
        cls.phrase = _load_phrase()
        cls.engine = ww._OpenWakeWordEngine(ww.load_wake_word_config())

    def _score(self, pcm16k) -> float:
        self.engine.reset()
        fl = self.engine.frame_length
        pcm = np.concatenate([np.zeros(fl * 10, dtype=np.int16),
                              np.asarray(pcm16k, dtype=np.int16),
                              np.zeros(fl * 10, dtype=np.int16)])
        best = 0.0
        for i in range(0, len(pcm) - fl + 1, fl):
            s = self.engine._model.predict(pcm[i:i + fl])
            if s:
                best = max(best, max(s.values()))
        return best

    def _through_adapter(self, pcm48k) -> np.ndarray:
        """Drive SharedAudioInput's real routing with synthetic callbacks."""
        from shared_audio import SharedAudioInput

        captured: list = []

        class _CapturingWW:
            SAMPLE_RATE = ww.SAMPLE_RATE
            _resample_audio_frame = staticmethod(ww._resample_audio_frame)

            @staticmethod
            def feed_audio(*, owner, pcm_int16):
                captured.append(np.asarray(pcm_int16, dtype=np.int16).copy())
                return True

        class _FakeVM:
            @staticmethod
            def _import_audio():
                return None, np

        rec = _FakeRecorder(CAPTURE_RATE)
        shared = SharedAudioInput(_FakeVM, _CapturingWW, rec, object())
        shared.attach_wake()

        # A reused buffer, exactly like PortAudio's: the SAME array object is
        # refilled for every callback. Code that keeps a view of it loses.
        scratch = np.zeros((RECORDER_BLOCK, 1), dtype=np.int16)
        for i in range(0, len(pcm48k), RECORDER_BLOCK):
            block = pcm48k[i:i + RECORDER_BLOCK]
            scratch[:] = 0
            scratch[:len(block), 0] = block
            rec._idle_frame_listener(scratch)
        return (np.concatenate(captured) if captured
                else np.zeros(0, dtype=np.int16))

    def test_phrase_survives_the_adapter_unchanged(self):
        direct = self._score(self.phrase)
        self.assertGreaterEqual(direct, self.engine._threshold,
                                "the fixture no longer fires; test is void")

        up = np.repeat(self.phrase, CAPTURE_RATE // ww.SAMPLE_RATE)
        through = self._score(self._through_adapter(up.astype(np.int16)))

        print(f"\n  direct={direct:.4f} through_adapter={through:.4f} "
              f"threshold={self.engine._threshold}")
        self.assertGreaterEqual(
            through, self.engine._threshold,
            f"the adapter destroyed the phrase: {through:.4f} vs {direct:.4f}")
        # Not merely "still fires" -- the score must not meaningfully degrade.
        self.assertGreater(through, direct * 0.9)

    def test_only_whole_engine_frames_are_emitted(self):
        up = np.repeat(self.phrase, CAPTURE_RATE // ww.SAMPLE_RATE)
        out = self._through_adapter(up.astype(np.int16))
        self.assertGreater(len(out), 0)
        self.assertEqual(len(out) % self.engine.frame_length, 0,
                         "a partial frame was emitted; feed() would zero-pad it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
