"""Drive the ACK helper and measure it. Prototype benchmark only.

Two clocks are reported and they answer different questions:

  * The helper's own numbers (trigger -> play() call -> play() return -> end)
    are monotonic and internal, and exclude the pipe.
  * The driver's numbers (write "PLAY" -> read "STARTED"/"ENDED") include the
    pipe round trip, which is what an integrating runtime would actually see.

`play()` returning is the closest observable proxy for first audio that
AVAudioPlayer offers; the remaining latency is the output device's IO buffer,
which the API does not expose. Reported as a proxy, not as a first-sample
timestamp.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics as st
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, ".build", "arm64-apple-macosx", "release",
                   "JarvisAckHelper")

NUM = re.compile(r"(\w+)=([-\d.]+)")


def fields(line: str) -> dict:
    return {k: float(v) for k, v in NUM.findall(line)}


def pct(v: list[float]) -> dict:
    s = sorted(v)
    n = len(s)
    p = lambda q: s[min(n - 1, int(n * q))]      # noqa: E731
    return {"min": round(s[0], 2), "p10": round(p(0.10), 2),
            "p50": round(s[n // 2], 2), "p90": round(p(0.90), 2),
            "max": round(s[-1], 2), "mean": round(st.mean(s), 2), "n": n}


class Helper:
    def __init__(self):
        self.p = subprocess.Popen([BIN], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        self.boot = self._read_until("READY")

    def _read_until(self, prefix: str, timeout: float = 15.0) -> list[str]:
        out = []
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("helper exited: " + "\n".join(out))
            out.append(line.strip())
            if line.startswith(prefix):
                return out
        raise TimeoutError(f"no {prefix} within {timeout}s: " + "\n".join(out))

    def send(self, cmd: str) -> None:
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()

    def play_once(self) -> dict:
        t0 = time.monotonic()
        self.send("PLAY")
        started = None
        row = {}
        # STARTED then ENDED, in that order.
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("helper died mid-play")
            line = line.strip()
            if line.startswith("STARTED"):
                started = time.monotonic()
                row["driver_to_started_ms"] = round((started - t0) * 1000, 2)
                row.update({f"helper_{k}": v for k, v in fields(line).items()})
            elif line.startswith("ENDED"):
                row["driver_to_ended_ms"] = round(
                    (time.monotonic() - t0) * 1000, 2)
                row.update({f"helper_{k}": v for k, v in fields(line).items()})
                return row
            elif line.startswith(("PLAY_IGNORED", "PLAY_FAILED",
                                  "PLAY_REJECTED", "ERROR")):
                row["failure"] = line
                return row

    def quit(self) -> None:
        try:
            self.send("QUIT")
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    return round(int(out) / 1024, 1) if out else 0.0


def cpu_seconds(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "time=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    if not out:
        return 0.0
    m, s = out.split(":")[-2:]
    return int(m) * 60 + float(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plays", type=int, default=100)
    ap.add_argument("--gap", type=float, default=0.15,
                    help="seconds between plays, after the previous one ended")
    ap.add_argument("--idle-minutes", type=float, default=0.0)
    ap.add_argument("--out", default=os.path.join(ROOT, "logs", "bench.json"))
    a = ap.parse_args()

    if not os.path.exists(BIN):
        print("build first: swift build -c release", file=sys.stderr)
        return 1

    h = Helper()
    print("\n".join(h.boot))
    pid = h.p.pid
    print(f"helper pid={pid}")

    rss0, cpu0 = rss_mb(pid), cpu_seconds(pid)
    time.sleep(0.4)

    rows, failures = [], 0
    t_start = time.monotonic()
    for i in range(1, a.plays + 1):
        r = h.play_once()
        if "failure" in r:
            failures += 1
            print(f"  #{i} {r['failure']}")
        rows.append(r)
        if i % 25 == 0:
            print(f"  {i}/{a.plays} plays, {failures} failures")
        time.sleep(a.gap)
    elapsed = time.monotonic() - t_start

    ok = [r for r in rows if "failure" not in r]
    summary = {
        "plays": a.plays,
        "success": len(ok),
        "failures": failures,
        "elapsed_s": round(elapsed, 1),
        "asset_duration_ms": fields(h.boot[-1]).get("duration_ms"),
        "helper_trigger_to_play_call_ms": pct([r["helper_trigger_to_call"] for r in ok]),
        "helper_trigger_to_play_return_ms": pct([r["helper_trigger_to_return"] for r in ok]),
        "helper_trigger_to_end_ms": pct([r["helper_trigger_to_end"] for r in ok]),
        "driver_to_started_ms": pct([r["driver_to_started_ms"] for r in ok]),
        "driver_to_ended_ms": pct([r["driver_to_ended_ms"] for r in ok]),
        "rss_mb_start": rss0,
        "rss_mb_after_plays": rss_mb(pid),
        "cpu_s_over_plays": round(cpu_seconds(pid) - cpu0, 2),
    }

    # Overlap policy: a PLAY sent while one is running must be ignored, not
    # queued. Verified rather than assumed.
    h.send("PLAY")
    time.sleep(0.05)
    h.send("PLAY")
    overlap = []
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        line = h.p.stdout.readline().strip()
        overlap.append(line)
        if line.startswith("ENDED"):
            break
    summary["overlap_behaviour"] = overlap
    summary["overlap_ignored"] = any(l.startswith("PLAY_IGNORED")
                                     for l in overlap)

    if a.idle_minutes > 0:
        print(f"\nidle for {a.idle_minutes} min ...")
        r0, c0 = rss_mb(pid), cpu_seconds(pid)
        time.sleep(a.idle_minutes * 60)
        r1, c1 = rss_mb(pid), cpu_seconds(pid)
        summary["idle"] = {
            "minutes": a.idle_minutes,
            "rss_start_mb": r0, "rss_end_mb": r1,
            "cpu_s": round(c1 - c0, 2),
            "cpu_percent": round((c1 - c0) / (a.idle_minutes * 60) * 100, 4),
        }
        # Still alive and responsive after sitting idle?
        t = time.monotonic()
        h.send("PING")
        line = h.p.stdout.readline().strip()
        summary["idle"]["ping_after_idle_ms"] = round(
            (time.monotonic() - t) * 1000, 2)
        summary["idle"]["ping_reply"] = line

    h.quit()
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    json.dump({"summary": summary, "rows": rows}, open(a.out, "w"),
              ensure_ascii=False, indent=1)
    print(f"written: {a.out}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
