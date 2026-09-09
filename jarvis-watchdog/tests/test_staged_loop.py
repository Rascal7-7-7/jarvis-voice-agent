"""The whole watchdog loop against staged state files, with no real restart.

The decision engine is tested directly elsewhere. This exercises the loop that
wraps it -- read, decide, log, act -- because that is where the identity has to
survive being read off disk, and because §12 asks for a staged real-stuck
fixture rather than only a unit-level one.

`restart_runtime` is replaced. The real one runs `launchctl kickstart -k` on the
production service, so a staged fixture that used it would restart the running
JARVIS to prove a point about a temp file.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))

import jarvis_watchdog as wd  # noqa: E402
from wlog import WatchdogLog  # noqa: E402


@dataclass
class FakeResult:
    ok: bool = True
    old_pid: int = 1
    new_pid: int = 2
    restart_return: int = 0
    recovery_state: str = "IDLE"
    recovery_ms: int = 1000
    note: str = "staged"


class Stage:
    """Rewrites the state file between polls, so the loop reads a script."""

    def __init__(self, path, script):
        self.path = path
        self.script = list(script)
        self.i = 0

    def next_payload(self):
        if self.i < len(self.script):
            p = self.script[self.i]
            self.i += 1
            if p is not None:
                with open(self.path, "w") as fh:
                    json.dump(p, fh)


@pytest.fixture
def staged(tmp_path, monkeypatch):
    calls: list[int] = []

    def fake_restart(pid, reader, path):
        calls.append(pid)
        return FakeResult(old_pid=pid)

    monkeypatch.setattr(wd, "restart_runtime", fake_restart)
    state_path = str(tmp_path / "jarvis_state.json")
    log = WatchdogLog(str(tmp_path / "watchdog.log"))
    return state_path, log, calls, tmp_path


def payload(state="LISTENING", pid=None, turn_id=1, since=1000.0, version=2):
    d = {"state": state, "pid": pid or os.getpid(), "since": since,
         "detail": "staged", "version": version}
    if turn_id is not None:
        d["turn_id"] = turn_id
    return d


def drive(state_path, log, script, calls, poll=0.0):
    """Run the loop once per scripted payload, with time advancing 5s a poll."""
    stage = Stage(state_path, script)
    fake_now = [0.0]

    real_sleep = wd.time.sleep

    def fake_sleep(_):
        fake_now[0] += 5.0
        stage.next_payload()

    def fake_monotonic():
        return fake_now[0]

    stage.next_payload()                       # write the first payload
    orig_sleep, orig_mono = wd.time.sleep, wd.time.monotonic
    wd.time.sleep = fake_sleep
    wd.time.monotonic = fake_monotonic
    try:
        return wd.run(state_path, log, poll, max_iterations=len(script))
    finally:
        wd.time.sleep = orig_sleep
        wd.time.monotonic = orig_mono
        real_sleep(0)


# --- the deadlock must still be recovered --------------------------------

def test_staged_stuck_listening_restarts(staged):
    """One turn, one identity, LISTENING for well past the threshold."""
    state_path, log, calls, _ = staged
    script = [payload(turn_id=42) for _ in range(14)]   # 14 polls = 65 s
    restarts = drive(state_path, log, script, calls)
    assert restarts >= 1, "a genuinely stuck turn was not recovered"
    assert calls, "restart_runtime was never called"


def test_staged_stuck_listening_v1_restarts(staged):
    """Same, for a runtime that publishes no version and no turn_id."""
    state_path, log, calls, _ = staged
    script = [{"state": "LISTENING", "pid": os.getpid(), "since": 1000.0,
               "detail": "staged"} for _ in range(14)]
    restarts = drive(state_path, log, script, calls)
    assert restarts >= 1


# --- consecutive turns must not ------------------------------------------

def test_staged_rapid_turns_never_restart(staged):
    """Ten turns of LISTENING with the IDLE between them never observed."""
    state_path, log, calls, _ = staged
    script = []
    for turn in range(1, 11):
        script += [payload(turn_id=turn, since=1000.0 + turn * 9)] * 2
    restarts = drive(state_path, log, script, calls)
    assert restarts == 0, f"{restarts} false restart(s)"
    assert calls == []


def test_staged_rapid_turns_v1_never_restart(staged):
    """The same shape from a v1 runtime, distinguished by `since` alone."""
    state_path, log, calls, _ = staged
    script = []
    for turn in range(1, 11):
        script += [{"state": "LISTENING", "pid": os.getpid(),
                    "since": 1000.0 + turn * 9, "detail": "staged"}] * 2
    restarts = drive(state_path, log, script, calls)
    assert restarts == 0, f"{restarts} false restart(s)"


def test_staged_v2_without_turn_id_never_restarts(staged):
    state_path, log, calls, _ = staged
    script = [payload(turn_id=None) for _ in range(20)]
    restarts = drive(state_path, log, script, calls)
    assert restarts == 0
    assert calls == []


# --- the log has to make this diagnosable -------------------------------

def test_each_turn_is_logged_as_its_own_observation(staged):
    """The log is what made the original bug invisible: nine turns, no lines."""
    state_path, log, calls, tmp_path = staged
    script = []
    for turn in range(1, 6):
        script += [payload(turn_id=turn, since=1000.0 + turn * 9)] * 2
    drive(state_path, log, script, calls)

    lines = [json.loads(x) for x in
             open(str(tmp_path / "watchdog.log")).read().splitlines() if x.strip()]
    observed = [e for e in lines if e.get("event") == "observed"]
    turn_ids = [e.get("turn_id") for e in observed]
    assert turn_ids == [1, 2, 3, 4, 5], turn_ids


def test_a_restart_log_names_the_turn_it_acted_on(staged):
    state_path, log, calls, tmp_path = staged
    script = [payload(turn_id=77) for _ in range(14)]
    drive(state_path, log, script, calls)
    lines = [json.loads(x) for x in
             open(str(tmp_path / "watchdog.log")).read().splitlines() if x.strip()]
    decision = next(e for e in lines if e.get("event") == "restart_decision")
    assert "77" in decision["reason"], decision["reason"]


# --- nothing here may touch the production state -------------------------

def test_the_loop_never_writes_the_state_file(staged):
    state_path, log, calls, _ = staged
    script = [payload(turn_id=1) for _ in range(6)]
    drive(state_path, log, script, calls)
    with open(state_path) as fh:
        after = json.load(fh)
    assert after == payload(turn_id=1), "the watchdog modified the state file"


# --- persistent ERROR, through the whole loop ---------------------------

def test_staged_persistent_error_restarts_and_recovers(staged):
    """IDLE -> persistent ERROR -> restart -> new pid -> IDLE.

    The production runtime is never deliberately broken to test this; the state
    file is staged instead, and `restart_runtime` is the stub. §10.
    """
    state_path, log, calls, tmp_path = staged
    old, new = os.getpid(), os.getpid() + 1
    script = [{"state": "IDLE", "pid": old, "since": 1.0, "detail": "ok",
               "version": 2}] * 2
    script += [{"state": "ERROR", "pid": old, "since": 100.0,
                "detail": "mic silent; replacements exhausted",
                "version": 2}] * 6
    script += [{"state": "IDLE", "pid": new, "since": 200.0,
                "detail": "listening", "version": 2}] * 4
    restarts = drive(state_path, log, script, calls)
    assert restarts == 1, restarts
    assert calls == [old], calls

    lines = [json.loads(x) for x in
             open(str(tmp_path / "watchdog.log")).read().splitlines() if x.strip()]
    decision = next(e for e in lines if e.get("event") == "restart_decision")
    assert decision["cause"] == "PERSISTENT_ERROR", decision
    result = next(e for e in lines if e.get("event") == "restart_result")
    assert result["cause"] == "PERSISTENT_ERROR"


def test_staged_transient_error_is_left_alone(staged):
    """The three failed replacements from the real incident, then recovery."""
    state_path, log, calls, _ = staged
    pid = os.getpid()
    script = []
    for i in range(3):
        script += [{"state": "ERROR", "pid": pid, "since": 100.0 + i,
                    "detail": "mic silent; replacing input stream",
                    "version": 2}] * 2
    script += [{"state": "IDLE", "pid": pid, "since": 200.0,
                "detail": "listening", "version": 2}] * 2
    restarts = drive(state_path, log, script, calls)
    assert restarts == 0
    assert calls == []


def test_staged_listening_stuck_is_labelled_separately(staged):
    state_path, log, calls, tmp_path = staged
    script = [payload(turn_id=91) for _ in range(14)]
    drive(state_path, log, script, calls)
    lines = [json.loads(x) for x in
             open(str(tmp_path / "watchdog.log")).read().splitlines() if x.strip()]
    decision = next(e for e in lines if e.get("event") == "restart_decision")
    assert decision["cause"] == "LISTENING_STUCK", decision
