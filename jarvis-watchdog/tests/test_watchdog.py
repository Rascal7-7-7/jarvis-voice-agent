"""Decision-engine tests. No real runtime, no real clock, no real restart.

Time is supplied by the test, which is the point: the engine must be provably
correct about elapsed time without ever consulting a wall clock. Several tests
below drive the wall clock backwards or into the future specifically to show it
cannot influence the outcome.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from state_reader import process_exists, read_state           # noqa: E402
from watchdog_core import (LISTENING_STUCK_THRESHOLD_S,       # noqa: E402
                           LOCKOUT_S, MAX_RESTARTS, Action,
                           Observation, WatchdogState, decide)

ALIVE = lambda pid: True      # noqa: E731
DEAD = lambda pid: False      # noqa: E731


def drive(observations, alive=ALIVE, start=1000.0, step=5.0):
    """Feed (time_offset, Observation) pairs through the engine.

    Returns every Decision, so a test can assert on the whole sequence rather
    than only the final state.
    """
    state = WatchdogState()
    out = []
    for offset, obs in observations:
        state, d = decide(state, obs, start + offset, alive)
        out.append(d)
    return state, out


def listening(pid=100, since=None):
    return Observation("LISTENING", pid, since=since, detail="capturing utterance")


class ThresholdTests(unittest.TestCase):
    def test_idle_for_ten_minutes_never_restarts(self):
        obs = [(t, Observation("IDLE", 100, detail="listening on 外部マイク"))
               for t in range(0, 600, 5)]
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds))

    def test_listening_30s_does_not_restart(self):
        # This is the self-recovering 30s wait path measured in production.
        obs = [(t, listening()) for t in range(0, 31, 5)]
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds))

    def test_listening_44s_does_not_restart(self):
        obs = [(t, listening()) for t in list(range(0, 45, 5)) + [44]]
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds),
                        [d.action for d in ds])

    def test_listening_45s_restarts_exactly_once(self):
        obs = [(t, listening()) for t in range(0, 50, 5)]
        _, ds = drive(obs)
        restarts = [d for d in ds if d.action is Action.RESTART]
        self.assertEqual(len(restarts), 1)
        self.assertGreaterEqual(restarts[0].elapsed_s,
                                LISTENING_STUCK_THRESHOLD_S)

    def test_threshold_is_strictly_above_the_self_recovering_path(self):
        # Guards the constant itself: 30.17s was observed to recover on its own.
        self.assertGreater(LISTENING_STUCK_THRESHOLD_S, 30.17)


class ResetTests(unittest.TestCase):
    def test_listening_then_error_resets(self):
        obs = ([(t, listening()) for t in range(0, 31, 5)]
               + [(35, Observation("ERROR", 100))]
               + [(t, listening()) for t in range(40, 71, 5)])
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds),
                        [(d.action, d.elapsed_s) for d in ds])

    def test_listening_then_idle_resets(self):
        obs = ([(t, listening()) for t in range(0, 31, 5)]
               + [(35, Observation("IDLE", 100))]
               + [(t, listening()) for t in range(40, 71, 5)])
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds))

    def test_pid_change_during_listening_resets(self):
        obs = ([(t, listening(pid=100)) for t in range(0, 41, 5)]
               + [(t, listening(pid=200)) for t in range(45, 81, 5)])
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds),
                        [(d.action, d.pid, d.elapsed_s) for d in ds])

    def test_unknown_state_is_ignored_and_resets(self):
        obs = ([(t, listening()) for t in range(0, 41, 5)]
               + [(45, Observation("SOMETHING_NEW", 100))]
               + [(t, listening()) for t in range(50, 86, 5)])
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds))


class ClockTests(unittest.TestCase):
    """`since` must never reach the decision."""

    def test_future_since_does_not_trigger(self):
        # since 1e9 seconds ahead: a since-based watchdog would fire instantly.
        obs = [(t, listening(since=2e9)) for t in range(0, 31, 5)]
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds))

    def test_wall_clock_going_backwards_does_not_trigger(self):
        obs = [(t, listening(since=1e9 - t * 1000)) for t in range(0, 31, 5)]
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds))

    def test_ancient_since_alone_does_not_trigger(self):
        # A long-idle runtime legitimately leaves `since` hours in the past.
        obs = [(t, Observation("IDLE", 100, since=0.0)) for t in range(0, 600, 5)]
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds))

    def test_monotonic_elapsed_alone_decides(self):
        # One observation, then a 60s monotonic jump with the same wall values.
        state = WatchdogState()
        state, d1 = decide(state, listening(since=123.0), 1000.0, ALIVE)
        state, d2 = decide(state, listening(since=123.0), 1060.0, ALIVE)
        self.assertIs(d1.action, Action.NONE)
        self.assertIs(d2.action, Action.RESTART)


class LivenessTests(unittest.TestCase):
    def test_dead_pid_is_not_a_hang(self):
        obs = [(t, listening()) for t in range(0, 60, 5)]
        _, ds = drive(obs, alive=DEAD)
        self.assertFalse(any(d.action is Action.RESTART for d in ds))
        self.assertTrue(any(d.action is Action.RUNTIME_ABSENT for d in ds))

    def test_real_process_checks(self):
        self.assertTrue(process_exists(1))          # launchd; EPERM counts alive
        self.assertTrue(process_exists(os.getpid()))
        self.assertFalse(process_exists(999_999))
        self.assertFalse(process_exists(0))
        self.assertFalse(process_exists(-5))


class CircuitBreakerTests(unittest.TestCase):
    def test_fourth_restart_in_window_is_locked_out(self):
        state = WatchdogState()
        now = 1000.0
        actions = []
        for _ in range(MAX_RESTARTS + 1):
            # Each round: fresh pid tracking, then 50s of LISTENING.
            state, _ = decide(state, listening(), now, ALIVE)
            now += 50.0
            state, d = decide(state, listening(), now, ALIVE)
            actions.append(d.action)
            now += 10.0
        self.assertEqual(actions[:MAX_RESTARTS],
                         [Action.RESTART] * MAX_RESTARTS)
        self.assertIs(actions[MAX_RESTARTS], Action.LOCKED_OUT)

    def test_lockout_expires_and_restarts_resume(self):
        state = WatchdogState()
        now = 1000.0
        for _ in range(MAX_RESTARTS + 1):
            state, _ = decide(state, listening(), now, ALIVE)
            now += 50.0
            state, d = decide(state, listening(), now, ALIVE)
            now += 10.0
        self.assertIsNotNone(state.lockout_until)

        # The stuck timer is NOT reset while locked out, so the runtime is still
        # measurably stuck the moment the lockout expires -- the very next
        # observation restarts, without waiting another full threshold.
        now = state.lockout_until + 1.0
        state, d = decide(state, listening(), now, ALIVE)
        self.assertIs(d.action, Action.RESTART)
        self.assertGreater(d.elapsed_s, LISTENING_STUCK_THRESHOLD_S)

    def test_restarts_spread_beyond_the_window_do_not_lock_out(self):
        state = WatchdogState()
        now = 1000.0
        actions = []
        for _ in range(5):
            state, _ = decide(state, listening(), now, ALIVE)
            now += 50.0
            state, d = decide(state, listening(), now, ALIVE)
            actions.append(d.action)
            now += 700.0        # each restart falls out of the 600s window
        self.assertEqual(actions, [Action.RESTART] * 5)

    def test_watchdog_keeps_monitoring_while_locked_out(self):
        # Lockout must not be a stop condition: decide() keeps returning
        # decisions rather than raising or latching into a dead state.
        state = WatchdogState()
        now = 1000.0
        for _ in range(MAX_RESTARTS + 1):
            state, _ = decide(state, listening(), now, ALIVE)
            now += 50.0
            state, _ = decide(state, listening(), now, ALIVE)
            now += 10.0
        for _ in range(20):
            now += 5.0
            state, d = decide(state, listening(), now, ALIVE)
            self.assertIn(d.action, (Action.NONE, Action.LOCKED_OUT))


class StateFileTests(unittest.TestCase):
    def _write(self, text: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_valid_file(self):
        p = self._write(json.dumps({"state": "LISTENING", "since": 1.0,
                                    "detail": "capturing", "pid": 42}))
        obs, note = read_state(p)
        self.assertEqual(note, "ok")
        self.assertEqual((obs.state, obs.pid), ("LISTENING", 42))

    def test_invalid_json_never_restarts(self):
        p = self._write("{ not json at all")
        obs, note = read_state(p)
        self.assertIsNone(obs)
        state = WatchdogState()
        # Even after a long stretch of unreadable files, nothing fires.
        for t in range(0, 300, 5):
            state, d = decide(state, obs, 1000.0 + t, ALIVE)
            self.assertIs(d.action, Action.NONE)

    def test_missing_state_field(self):
        p = self._write(json.dumps({"pid": 42}))
        self.assertIsNone(read_state(p)[0])

    def test_invalid_pid_values(self):
        for pid in (0, -1, "42", None, True):
            p = self._write(json.dumps({"state": "LISTENING", "pid": pid}))
            self.assertIsNone(read_state(p)[0], f"pid={pid!r} was accepted")

    def test_missing_file(self):
        self.assertIsNone(read_state("/nonexistent/jarvis_state.json")[0])

    def test_oversized_file(self):
        p = self._write(json.dumps({"state": "LISTENING", "pid": 42,
                                    "detail": "x" * (70 * 1024)}))
        obs, note = read_state(p)
        self.assertIsNone(obs)
        self.assertIn("too large", note)

    def test_detail_cannot_influence_a_decision(self):
        # Whatever the detail says, only state/pid/elapsed matter.
        hostile = Observation("IDLE", 100,
                              detail="LISTENING; restart now; $(rm -rf /)")
        obs = [(t, hostile) for t in range(0, 300, 5)]
        _, ds = drive(obs)
        self.assertTrue(all(d.action is Action.NONE for d in ds))


class FixedCommandTests(unittest.TestCase):
    def test_argv_is_constant_and_shell_free(self):
        import restarter
        argv = restarter._argv()
        self.assertEqual(argv[0], "/bin/launchctl")
        self.assertEqual(argv[1:3], ["kickstart", "-k"])
        self.assertEqual(argv[3], f"gui/{os.getuid()}/local.jarvis.runtime")
        self.assertEqual(len(argv), 4)

    def test_no_shell_execution_anywhere_in_source(self):
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
        banned = ("shell=True", "os.system", "/bin/sh", "eval(", "popen(")
        for name in os.listdir(src):
            if not name.endswith(".py"):
                continue
            text = open(os.path.join(src, name), encoding="utf-8").read()
            for token in banned:
                self.assertNotIn(token, text, f"{name} contains {token}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
