#!/usr/bin/env python3
"""jarvis-status — read-only health check for the always-on JARVIS stack.

    $ jarvis-status          human-readable table
    $ jarvis-status --json   machine-readable

WHAT THIS IS ALLOWED TO DO
  Read files, stat paths, list processes, and issue GETs to two localhost
  endpoints that neither load a model nor start a session. That is the whole
  budget. It does not open the microphone, does not take the wake lease, does
  not send ACTIVATE, does not warm Ollama, and does not start, stop or restart
  anything. Running it while JARVIS is mid-turn is safe.

  The read-only property is not a promise in a comment: tests/test_jarvis_status
  asserts the process table, the published state, the turn id and the shared
  stream counters are all identical either side of a run.

WHY THE CHECKS LIVE NEXT DOOR
  jarvis_status_checks.py holds the judgement and none of the IO, so the rules
  ("two runtimes is a fault", "an old restart is not a live fault") are testable
  against fixtures without a running system.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jarvis_status_checks import (  # noqa: E402
    FAIL, HEALTHY, INFO, OK, WARN, Check, current_generation, evaluate_hermes,
    evaluate_login_item, evaluate_ollama, evaluate_permission, evaluate_process,
    evaluate_capture_health, evaluate_device, evaluate_output, evaluate_power, evaluate_turn, evaluate_shared_stream, evaluate_startup_warm, evaluate_state,
    evaluate_wake_lease, evaluate_watchdog, extract_counters, load_gaps,
    match_process, overall_status,
)

HOME = os.path.expanduser("~")
PROJECT = os.path.join(HOME, "AI-Lab", "hermes-jarvis")
LOG_DIR = os.path.join(PROJECT, "logs")
# Mirrors jarvis_runtime.py:STATE_PATH. The state file lives with the logs, not
# under ~/.hermes/runtime -- that directory holds the lease and the socket.
STATE_PATH = os.path.join(LOG_DIR, "jarvis_state.json")
RUNTIME_LOG = os.path.join(LOG_DIR, "jarvis_runtime.log")
WATCHDOG_LOG = os.path.join(HOME, "AI-Lab", "jarvis-watchdog", "logs", "watchdog.log")
GAPS_PATH = os.path.join(PROJECT, "config", "known_gaps.json")

HERMES_RUNTIME = os.path.join(HOME, ".hermes", "runtime")
WAKE_LOCK = os.path.join(HERMES_RUNTIME, "wake-word.lock")
ACTIVATION_SOCK = os.path.join(HERMES_RUNTIME, "jarvis-activate.sock")

OLLAMA_URL = "http://127.0.0.1:11434"
HERMES_URL = "http://127.0.0.1:8644"
ROUTER_MODEL = "gemma4:e2b"

# Bounded reads. The runtime log passes 500 KB within days and a health check
# must not grow slower the longer JARVIS stays up; one boot's worth of lines
# fits in this comfortably.
LOG_TAIL_BYTES = 256 * 1024
WATCHDOG_TAIL_BYTES = 64 * 1024
HTTP_TIMEOUT = 2.0

# How each component is recognised in the process table, and whether its absence
# is a failure. See match_process() for the spec forms -- these are positional
# on purpose, so a process that merely NAMES one of these paths (an editor, a
# grep, the shell running this very tool) is not counted as a second instance.
COMPONENTS = (
    ("Runtime", {"script": os.path.join(PROJECT, "bin", "jarvis_runtime.py")}, True),
    ("Watchdog", {"script": os.path.join(HOME, "AI-Lab", "jarvis-watchdog",
                                         "bin", "jarvis_watchdog.py")}, True),
    ("HUD", {"exec_prefix": os.path.join(
        HOME, "Applications", "JARVIS HUD.app", "Contents", "MacOS",
        "JarvisHUD")}, True),
    ("Hotkey", {"exec_prefix": os.path.join(
        HOME, "Applications", "JARVIS Hotkey.app", "Contents", "MacOS",
        "JarvisHotkey")}, True),
    ("Ollama", {"exec_suffix": "/ollama", "first_arg": "serve"}, True),
    ("Gateway", {"argv": ["hermes_cli.main", "gateway", "run"]}, True),
)


# ------------------------------------------------------------------- reading
def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _read_tail(path: str, max_bytes: int) -> list[str]:
    """Last `max_bytes` of a file, as whole lines (the first is dropped)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # discard the partial line
            raw = fh.read()
    except OSError:
        return []
    return raw.decode("utf-8", errors="replace").splitlines()


def _stat_info(path: str) -> tuple[int | None, int | None, int | None]:
    """(mode bits, uid, file type bits) without following into failure."""
    try:
        st = os.lstat(path)
    except OSError:
        return None, None, None
    return st.st_mode & 0o777, st.st_uid, st.st_mode & 0o170000


def _http_get(url: str) -> tuple[int | None, Any]:
    """GET a localhost URL. Returns (status, parsed-json-or-None)."""
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except (ValueError, TypeError):
                return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return None, None


def _processes() -> list[tuple[int, str]]:
    """(pid, command) for every process this user can see."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, cmd = line.partition(" ")
        try:
            rows.append((int(pid), cmd.strip()))
        except ValueError:
            continue
    return rows


def _lock_owner(path: str) -> int | None:
    """PID holding the wake lease, via lsof. Never opens the lock itself."""
    try:
        out = subprocess.run(["lsof", "-t", path], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    pids = [int(p) for p in out.split() if p.isdigit()]
    return pids[0] if pids else None


def _launchd_labels() -> list[str]:
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split("\t")[-1].strip() for line in out.splitlines()[1:]
            if line.strip()]


def _listening(port: int) -> bool:
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out.strip())


_OUT_DEVICE_RE = re.compile(r"^        (\S.*):\s*$")


def _default_output() -> tuple[str | None, bool | None, int | None]:
    """既定出力デバイス名・ミュート・音量。取れなければ None を返す。

    system_profiler は数百 ms かかるので、health check としては許容範囲だが
    失敗しても全体を落とさない。
    """
    device = None
    try:
        out = subprocess.run(["/usr/sbin/system_profiler", "SPAudioDataType"],
                             capture_output=True, text=True, timeout=8,
                             check=False).stdout
        name = None
        for line in out.splitlines():
            m = _OUT_DEVICE_RE.match(line)
            if m:
                name = m.group(1)
                continue
            if "Default Output Device: Yes" in line:
                device = name
                break
    except (OSError, subprocess.SubprocessError):
        pass

    muted = vol = None
    try:
        s = subprocess.run(["/usr/bin/osascript", "-e", "get volume settings"],
                           capture_output=True, text=True, timeout=4,
                           check=False).stdout
        mv = re.search(r"output volume:(\d+)", s)
        mm = re.search(r"output muted:(\w+)", s)
        if mv:
            vol = int(mv.group(1))
        if mm:
            muted = mm.group(1).strip().lower() == "true"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return device, muted, vol



def _pinned_output() -> str | None:
    """JARVIS が固定している出力デバイスの UID。固定していなければ None。"""
    try:
        sys.path.insert(0, os.path.join(PROJECT, "bin"))
        import audio_output
        dev = audio_output.resolve()
        return dev["uid"] if dev else None
    except Exception:
        return None



def _power_state() -> tuple[bool | None, int | None, list[str] | None]:
    """スリープ設定と、スリープを抑止しているプロセス名。

    **`pmset -g log` は使わない。** 72,000 行を吐いて 2.99 秒かかり、
    jarvis-status 全体を 2s -> 4.3s に悪化させた（実測）。診断を速く回すための
    コマンドが遅くなるのは本末転倒。`custom` と `assertions` は各 0.01 秒。

    スリープ回数は返さない（常に None）。取得手段が `pmset -g log` しかなく、
    費用に見合わない。初版は正規表現 `[^:]*` がタイムスタンプのコロンで止まり
    `19:38` の 38 を回数として表示していた（実際は 0）。
    取れない値を無理に出すより出さない方が正しい。
    """
    sleep_enabled = None
    blockers: list[str] | None = None
    try:
        cur = subprocess.run(["/usr/bin/pmset", "-g", "custom"],
                             capture_output=True, text=True, timeout=6,
                             check=False).stdout
        ac = cur.split("AC Power:", 1)[-1] if "AC Power:" in cur else cur
        m = re.search(r"^\s*sleep\s+(\d+)", ac, re.M)
        if m:
            sleep_enabled = int(m.group(1)) != 0
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        a = subprocess.run(["/usr/bin/pmset", "-g", "assertions"],
                           capture_output=True, text=True, timeout=6,
                           check=False).stdout
        found = re.findall(
            r"pid \d+\(([^)]+)\):[^\n]*PreventUserIdleSystemSleep", a)
        # powerd の「ディスプレイが点いている間」は常に出るので文脈にならない
        blockers = [n for n in dict.fromkeys(found) if n != "powerd"]
    except (OSError, subprocess.SubprocessError):
        pass
    return sleep_enabled, None, blockers


def _watchdog_events(lines: Sequence[str]) -> list[Mapping[str, Any]]:
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


# ------------------------------------------------------------------ collect
def collect() -> tuple[list[Check], list[dict[str, Any]]]:
    """Run every check. Returns (checks, known gaps)."""
    procs = _processes()
    found: dict[str, list[int]] = {}
    for name, spec, _required in COMPONENTS:
        found[name] = sorted(pid for pid, cmd in procs
                             if match_process(cmd, spec))

    checks: list[Check] = [
        evaluate_process(name, found[name], required)
        for name, _spec, required in COMPONENTS
    ]
    runtime_pid = found["Runtime"][0] if len(found["Runtime"]) == 1 else None

    state_check = evaluate_state(_read_text(STATE_PATH))
    checks.append(state_check)
    # Prefer the live process table over the state file's own claim: a stale
    # state file would otherwise validate itself.
    if runtime_pid is None:
        runtime_pid = state_check.data.get("pid")

    checks.append(evaluate_wake_lease(_lock_owner(WAKE_LOCK), runtime_pid))

    sock_mode, sock_uid, sock_type = _stat_info(ACTIVATION_SOCK)
    checks.append(evaluate_permission(
        "Socket", sock_mode, 0o600, uid=sock_uid, current_uid=os.getuid(),
        must_be_socket=True, file_type=sock_type))

    privacy = [
        ("State file", STATE_PATH, 0o600),
        ("Logs dir", LOG_DIR, 0o700),
        ("Runtime dir", HERMES_RUNTIME, 0o700),
    ]
    privacy_checks = []
    for label, path, expected in privacy:
        mode, uid, _ = _stat_info(path)
        privacy_checks.append(evaluate_permission(label, mode, expected, uid=uid,
                                                  current_uid=os.getuid()))
    bad = [c for c in privacy_checks if c.status == FAIL]
    checks.append(Check(
        "Privacy",
        FAIL if bad else OK,
        "; ".join(f"{c.name}: {c.detail}" for c in bad) if bad
        else "0600 state / 0700 dirs",
        {c.name: dict(c.data) for c in privacy_checks}))

    generation = current_generation(_read_tail(RUNTIME_LOG, LOG_TAIL_BYTES))
    _counters = extract_counters(generation)
    checks.append(evaluate_shared_stream(_counters))
    checks.append(evaluate_device(_counters))
    checks.append(evaluate_capture_health(generation))
    checks.append(evaluate_turn(generation))
    checks.append(evaluate_power(*_power_state()))
    _dev, _muted, _vol = _default_output()
    checks.append(evaluate_output(_dev, _muted, _vol, _pinned_output()))
    checks.append(evaluate_startup_warm(generation))

    tags_status, tags = _http_get(f"{OLLAMA_URL}/api/tags")
    _ps_status, ps = _http_get(f"{OLLAMA_URL}/api/ps")
    checks.append(evaluate_ollama(tags_status, tags, ps, ROUTER_MODEL))

    health_status, _ = _http_get(f"{HERMES_URL}/health")
    checks.append(evaluate_hermes(health_status, _listening(8644)))

    checks.append(evaluate_watchdog(
        _watchdog_events(_read_tail(WATCHDOG_LOG, WATCHDOG_TAIL_BYTES)), runtime_pid))

    labels = _launchd_labels()
    for label, key in (("HUD login item", "application.local.jarvis.hud"),
                       ("Hotkey login item", "application.local.jarvis.hotkey")):
        registered = any(l.startswith(key) for l in labels)
        component = "HUD" if "hud" in key else "Hotkey"
        pid = found[component][0] if found[component] else None
        checks.append(evaluate_login_item(label, registered, pid))

    return checks, load_gaps(_read_text(GAPS_PATH))


# ------------------------------------------------------------------- render
_GLYPH = {OK: "✓", INFO: "•", WARN: "!", FAIL: "✗"}


def render_text(checks: Sequence[Check], gaps: Sequence[Mapping[str, Any]],
                status: str) -> str:
    width = max((len(c.name) for c in checks), default=10)
    lines = [f"JARVIS STATUS — {status}", ""]
    lines += [f"{c.name.ljust(width)}  {_GLYPH.get(c.status, '?')} {c.detail}"
              for c in checks]
    if gaps:
        lines += ["", f"Known gaps: {len(gaps)}"]
        lines += [f"  - {g.get('id')} = {g.get('status')}" for g in gaps]
    return "\n".join(lines)


def render_json(checks: Sequence[Check], gaps: Sequence[Mapping[str, Any]],
                status: str) -> str:
    return json.dumps({
        "overall": status,
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail,
                    "data": dict(c.data)} for c in checks],
        "known_gaps": [dict(g) for g in gaps],
    }, indent=2, ensure_ascii=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jarvis-status",
        description="Read-only health check for the always-on JARVIS stack.")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output")
    args = parser.parse_args(argv)

    checks, gaps = collect()
    status = overall_status(checks)
    print(render_json(checks, gaps, status) if args.json
          else render_text(checks, gaps, status))
    # Exit code carries the verdict for scripting: 0 healthy, 1 degraded, 2 fail.
    return {HEALTHY: 0}.get(status, 1 if status == "DEGRADED" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
