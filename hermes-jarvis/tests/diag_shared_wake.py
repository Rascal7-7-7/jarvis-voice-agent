"""Diagnose the shared-stream wake path end to end, with numbers at each stage.

Runs the production objects (SharedAudioInput + AudioRecorder + external-mode
detector) and reports, for a played wake phrase: how many chunks the callback
saw, how many whole engine frames were fed, what the queue did, and the raw
scores the engine produced. Enough to tell "no audio", "wrong audio", and
"audio fine, model unconvinced" apart.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src", "hermes-agent-v2026.8.27")
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, SRC)
os.chdir(SRC)

import numpy as np  # noqa: E402

from shared_audio import SharedAudioInput   # noqa: E402
from tools import voice_mode as vm          # noqa: E402
from tools import wake_word as ww           # noqa: E402

WAKE_WAV = sys.argv[1] if len(sys.argv) > 1 else "/tmp/heyjarvis_sam.wav"

owner = object()
rec = vm.AudioRecorder()
shared = SharedAudioInput(vm, ww, rec, owner)
assert shared.open_once(), "stream did not start"
shared.attach_wake()

fired = threading.Event()
cfg = ww.load_wake_word_config()
det = ww.start_listening(fired.set, owner=owner, config=cfg, external_audio=True)

# Tap the engine so raw scores are visible; the detector calls process() from
# its own thread, so this observes exactly what it decided on.
scores: list[float] = []
real_process = det.engine.process


def traced(frame):
    try:
        s = det.engine._model.predict(frame)
        if s:
            scores.append(max(s.values()))
    except Exception as e:
        scores.append(-1.0)
        print("predict raised:", e)
    return real_process(frame)


det.engine.process = traced

time.sleep(1.0)
print(f"before : {shared.counters()}")
print(f"chunk size (48k) = "
      f"{shared.idle_frames // max(1, shared.idle_chunks)} samples")

subprocess.run(["/usr/bin/afplay", WAKE_WAV], capture_output=True, timeout=30)
detected = fired.wait(4.0)

print(f"after  : {shared.counters()}")
print(f"queue size now  = {det._audio_q.qsize()}")
print(f"detector thread = {det.running}")
print(f"audio_silent    = {det.audio_silent}")
print(f"engine threshold= {det.engine._threshold} "
      f"confirm={det.engine._confirm_needed}")
print(f"scored frames   = {len(scores)}")
if scores:
    arr = np.array(scores)
    print(f"score max={arr.max():.4f} mean={arr.mean():.4f} "
          f"over-threshold={(arr >= det.engine._threshold).sum()}")
print(f"WAKE_DETECTED   = {detected}")

ww.stop_listening(owner=owner)
shared.detach()
rec.shutdown()
