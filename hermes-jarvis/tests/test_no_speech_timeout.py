"""Does a no-speech turn end at _max_wait, or does it run to the hard ceiling?

Ambient noise makes "just stay quiet" an unreliable way to test this -- a real
attempt produced PEAK_RMS 2445-3591 against a threshold of 200, so every turn
took the `has_spoken` path instead. This raises the recorder's RMS threshold
above anything the room can produce, IN THIS TEST PROCESS ONLY, so nothing can
count as speech and the `not has_spoken` branch is the only one left.

Nothing in production is touched: the threshold is set on a throwaway recorder
instance, ~/.hermes/config.yaml is not written, and voice sensitivity for every
real surface is unchanged.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.expanduser("~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27"))
sys.path.insert(0, os.path.expanduser("~/AI-Lab/hermes-jarvis/bin"))

import logging  # noqa: E402
logging.basicConfig(level=logging.INFO, format="%(message)s")

from tools import voice_mode as vm  # noqa: E402
import jarvis_runtime as jr  # noqa: E402

CEILING = 30.0                      # the runtime's hard ceiling
UNREACHABLE_THRESHOLD = 1_000_000   # int16 tops out at 32767


def main() -> int:
    box = {"rec": None}
    rec = jr._prepare_recorder_for_turn(box, vm, 1)
    rec._silence_threshold = UNREACHABLE_THRESHOLD

    print(f"max_wait={rec._max_wait}s  silence_duration={rec._silence_duration}s  "
          f"threshold={rec._silence_threshold} (test-only)  ceiling={CEILING}s")

    done = threading.Event()
    t0 = time.monotonic()
    rec.start(on_silence_stop=done.set)
    fired = done.wait(timeout=CEILING)
    dt = time.monotonic() - t0
    frames = len(getattr(rec, "_frames", []) or [])
    peak = getattr(rec, "_peak_rms", None)
    wav = rec.stop()
    if wav and os.path.exists(wav):
        os.unlink(wav)
    try:
        rec.shutdown()
    except Exception:
        pass

    print(f"\ncallback_fired = {fired}")
    print(f"elapsed        = {dt:.2f}s")
    print(f"FRAMES         = {frames}   (non-zero proves the stream was alive)")
    print(f"PEAK_RMS       = {peak}   (below the test threshold, so no speech)")

    ok = True
    if frames == 0:
        print("FAIL: zero frames -- the stream was dead, this tested nothing")
        ok = False
    if not fired:
        print(f"FAIL: no callback within {CEILING}s -- ran to the hard ceiling")
        ok = False
    elif dt > rec._max_wait + 2.0:
        print(f"FAIL: ended at {dt:.2f}s, expected ~{rec._max_wait}s")
        ok = False
    else:
        print(f"PASS: ended at {dt:.2f}s via max_wait, not the {CEILING}s ceiling")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
