"""Reproduce the second-turn capture failure without needing a human to speak.

The decisive question is not "was there speech" but "did the stream deliver
frames at all". The callback appends a chunk for every buffer while recording,
so an ambient-silent room still produces frames > 0 on a healthy stream. A turn
with frames == 0 is a dead stream, and that distinction needs no microphone
input at all.

Drives exactly the runtime's sequence:

    start_listening
      turn N:  pause_listening -> rec.start -> (watch) -> rec.stop -> resume_listening

Requires the machine-wide wake lease, so the jarvis runtime must be stopped
first. The caller does that; this script refuses to guess.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.expanduser("~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27"))
sys.path.insert(0, os.path.expanduser("~/AI-Lab/hermes-jarvis/bin"))

import logging  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("repro")

from tools import voice_mode as vm  # noqa: E402
from tools import wake_word as ww  # noqa: E402
import jarvis_runtime as jr  # noqa: E402

TURNS = int(os.environ.get("REPRO_TURNS", "3"))
CAPTURE_SECONDS = float(os.environ.get("REPRO_CAPTURE", "6"))
GAP_SECONDS = float(os.environ.get("REPRO_GAP", "4"))   # stands in for TTS playback
APPLY_FIX = os.environ.get("REPRO_APPLY_FIX", "0") == "1"


def describe(rec, turn: int, when: str) -> dict:
    st = getattr(rec, "_stream", None)
    d = {
        "turn": turn, "when": when,
        "RECORDER_OBJECT": hex(id(rec)),
        "STREAM_OBJECT": hex(id(st)) if st is not None else "None",
        "stream_active": bool(getattr(st, "active", False)) if st is not None else False,
        "DEVICE": vm._identity_label(getattr(rec, "_stream_identity", None)),
        "RATE": getattr(rec, "_sample_rate", None),
        "MAX_WAIT": getattr(rec, "_max_wait", None),
        "SILENCE_DURATION": getattr(rec, "_silence_duration", None),
        "THRESHOLD": getattr(rec, "_silence_threshold", None),
    }
    log.info("  %-6s RECORDER=%s STREAM=%s active=%s DEVICE=%s RATE=%s "
             "MAX_WAIT=%s SILENCE_DURATION=%s THRESHOLD=%s",
             when, d["RECORDER_OBJECT"], d["STREAM_OBJECT"], d["stream_active"],
             d["DEVICE"], d["RATE"], d["MAX_WAIT"], d["SILENCE_DURATION"],
             d["THRESHOLD"])
    return d


def main() -> int:
    owner = object()
    cfg = ww.load_wake_word_config()
    try:
        detector = ww.start_listening(lambda: None, owner=owner, config=cfg)
    except ww.WakeWordInUse:
        log.error("wake lease is held by another surface -- stop jarvis_runtime first")
        return 75
    log.info("wake listener up on %s",
             (detector.input_device_details or {}).get("name"))

    box = {"rec": None}
    rec = None
    results = []
    try:
        for turn in range(1, TURNS + 1):
            log.info("=== TURN %d ===", turn)
            if not ww.pause_listening(owner=owner):
                log.error("turn %d: could not pause the wake listener", turn)
                return 1
            time.sleep(0.1)

            if APPLY_FIX:
                # Exercise the SHIPPED function, not a copy of the fix. If
                # jarvis_runtime regresses, this test regresses with it.
                rec = jr._prepare_recorder_for_turn(box, vm, turn)
            else:
                # The original behaviour: construct once, tune once, reuse
                # blindly. Kept so the failure stays reproducible on demand.
                if rec is None:
                    rec = vm.AudioRecorder()
                    box["rec"] = rec
                    log.info("  created AudioRecorder (unfixed path)")
            rec = box["rec"]

            before = describe(rec, turn, "before")

            done = threading.Event()
            frames_seen = []
            stop_watch = threading.Event()

            def watch():
                while not stop_watch.is_set():
                    frames_seen.append(len(getattr(rec, "_frames", []) or []))
                    time.sleep(0.25)

            w = threading.Thread(target=watch, daemon=True)
            w.start()

            t0 = time.monotonic()
            rec.start(on_silence_stop=done.set)
            fired = done.wait(timeout=CAPTURE_SECONDS)
            frames_at_stop = len(getattr(rec, "_frames", []) or [])
            peak = getattr(rec, "_peak_rms", None)
            cur = getattr(rec, "_current_rms", None)
            wav = rec.stop()
            dt = time.monotonic() - t0
            stop_watch.set()

            during = describe(rec, turn, "after")
            size = os.path.getsize(wav) if wav and os.path.exists(wav) else 0
            log.info("  CAPTURE=%.2fs silence_cb_fired=%s FRAMES=%d PEAK_RMS=%s "
                     "CUR_RMS=%s wav=%s bytes",
                     dt, fired, frames_at_stop, peak, cur, size)
            results.append({"turn": turn, "frames": frames_at_stop, "peak": peak,
                            "capture": dt, "fired": fired, "wav_bytes": size,
                            "before": before, "after": during})
            if wav and os.path.exists(wav):
                try:
                    os.unlink(wav)
                except OSError:
                    pass

            ww.resume_listening(owner=owner)
            log.info("  wake listener resumed; sleeping %.1fs (stands in for TTS)",
                     GAP_SECONDS)
            time.sleep(GAP_SECONDS)
    finally:
        try:
            ww.stop_listening(owner=owner)
        except Exception:
            pass
        if rec is not None:
            try:
                rec.shutdown()
            except Exception:
                pass

    print("\n" + "=" * 78)
    print(f"{'turn':>5}{'frames':>9}{'peak_rms':>10}{'capture s':>11}"
          f"{'wav B':>9}  stream")
    for r in results:
        print(f"{r['turn']:>5}{r['frames']:>9}{str(r['peak']):>10}"
              f"{r['capture']:>11.2f}{r['wav_bytes']:>9}  {r['after']['STREAM_OBJECT']}")
    dead = [r["turn"] for r in results if r["frames"] == 0]
    print(f"\nturns with ZERO frames: {dead or 'none'}")
    print("APPLY_FIX =", APPLY_FIX)
    return 0 if not dead else 1


if __name__ == "__main__":
    raise SystemExit(main())
