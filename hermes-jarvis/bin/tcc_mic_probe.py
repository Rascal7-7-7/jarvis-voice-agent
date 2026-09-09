"""Decisive TCC probe: can a launchd-started process actually capture audio?

macOS does NOT return an error to a process without Microphone permission — it
returns digital silence. That is the same signature as a dead device, so the
only honest test is to capture and inspect the samples.

Writes a single JSON line so the launchd log can be parsed without ambiguity.
Read-only with respect to every JARVIS component.
"""
import json, os, sys, time

OUT = os.environ.get("TCC_PROBE_OUT", "/tmp/jarvis_tcc_probe.jsonl")


def main():
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "launched_by": os.environ.get("TCC_PROBE_CONTEXT", "unknown"),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "exe": sys.executable,
        "session_type": os.environ.get("XPC_SERVICE_NAME", "(none)"),
    }
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:
        rec.update(error=f"import:{type(e).__name__}:{e}")
        _write(rec)
        return

    try:
        di = sd.default.device[0]
        info = sd.query_devices(di)
        rec.update(device_index=int(di), device_name=info["name"],
                   rate=int(info["default_samplerate"]))
    except Exception as e:
        rec.update(error=f"query:{type(e).__name__}:{e}")
        _write(rec)
        return

    frames = []
    try:
        def cb(indata, n, t, s):
            frames.append(indata.copy())
        with sd.InputStream(samplerate=rec["rate"], channels=1,
                            dtype="int16", callback=cb):
            time.sleep(4.0)
    except Exception as e:
        rec.update(error=f"stream:{type(e).__name__}:{e}")
        _write(rec)
        return

    if not frames:
        rec.update(error="no_frames")
        _write(rec)
        return

    a = np.concatenate(frames, axis=0).astype(np.float64)
    distinct = int(len(np.unique(a.astype(np.int16))))
    rec.update(
        samples=int(len(a)),
        peak=float(np.max(np.abs(a))),
        rms=round(float(np.sqrt(np.mean(a ** 2))), 3),
        distinct_values=distinct,
        # distinct_values == 1 means every sample is identical: digital silence,
        # which is exactly what macOS hands an unpermitted process.
        verdict="DIGITAL_SILENCE" if distinct <= 1 else "REAL_AUDIO",
    )
    _write(rec)


def _write(rec):
    line = json.dumps(rec, ensure_ascii=False)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line)


if __name__ == "__main__":
    main()
