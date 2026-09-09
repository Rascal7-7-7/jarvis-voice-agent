"""Time faster-whisper from inside a launchd job, and report the QoS band it ran in.

Terminal timings say nothing about a launchd agent -- that lesson is from the
microphone phase, where macOS handed an unpermitted process digital silence
rather than an error. The same discipline applies to CPU: a background-QoS job
is scheduled onto efficiency cores, and a CPU-bound int8 model feels that.

Writes JSON to the path given as argv[1].
"""
import json
import os
import subprocess
import sys
import time

SRC = os.path.expanduser("~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27")
sys.path.insert(0, SRC)
os.chdir(SRC)

OUT = sys.argv[1]
WAV = sys.argv[2]


def priority() -> dict:
    out = subprocess.run(["ps", "-o", "pid,nice,pri,comm", "-p", str(os.getpid())],
                         capture_output=True, text=True).stdout.strip().splitlines()
    row = out[-1].split() if len(out) > 1 else []
    return {"ps_line": out[-1] if len(out) > 1 else "", "pri": row[2] if len(row) > 2 else "",
            "nice": row[1] if len(row) > 1 else ""}


rec = {"pid": os.getpid(), "ppid": os.getppid(),
       "xpc_service": os.environ.get("XPC_SERVICE_NAME", ""),
       "before": priority()}

try:
    from tools import voice_mode as vm

    t = time.monotonic()
    vm.transcribe_recording(WAV)          # load + first inference
    rec["load_and_first"] = round(time.monotonic() - t, 3)

    runs = []
    for _ in range(3):
        t = time.monotonic()
        r = vm.transcribe_recording(WAV)
        runs.append(round(time.monotonic() - t, 3))
    rec["warm_runs"] = runs
    rec["transcript"] = (r.get("transcript") or "").strip() if isinstance(r, dict) else ""
    rec["during"] = priority()
    rec["ok"] = True
except Exception as e:
    rec["ok"] = False
    rec["error"] = f"{type(e).__name__}: {e}"

with open(OUT, "w") as fh:
    json.dump(rec, fh, ensure_ascii=False, indent=1)
