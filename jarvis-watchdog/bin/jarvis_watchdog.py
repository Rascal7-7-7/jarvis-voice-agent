"""JARVIS external watchdog — recovery fail-safe, not a fix.

Why this exists as a separate process: the failure it recovers from wedges the
runtime inside CoreAudio's HAL client, holding a mutex that nothing in that
process can take back. Closing the stream needs the same guard; abandoning the
blocked thread leaks it. In-process recovery is not merely hard, it is not
available. So recovery has to come from outside, and the only thing outside can
usefully do is restart the process.

Scope is deliberately one state. LISTENING is the only state with a proven
deadlock, so it is the only state that can cause a restart here. Adding more
states means first having evidence for them.

Writes only to this project's own logs/ directory. Reads jarvis_state.json and
never writes it. No network, no microphone, no audio APIs, no config changes.
The single control action is a fixed launchctl argv in restarter.py.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from restarter import restart_runtime                     # noqa: E402
from state_reader import (PRODUCTION_STATE_PATH,          # noqa: E402
                          process_exists, read_state)
from watchdog_core import (ERROR_GRACE_SECONDS,           # noqa: E402
                           LISTENING_STUCK_THRESHOLD_S,
                           POLL_INTERVAL_S, Action, WatchdogState, decide)
from wlog import WatchdogLog                              # noqa: E402

DEFAULT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "logs", "watchdog.log")

# A quiet runtime is the normal case, so a heartbeat is emitted rarely rather
# than every poll. Everything interesting is logged on transition instead.
HEARTBEAT_S = 1800.0


def run(state_path: str, log: WatchdogLog, poll: float,
        max_iterations: int | None = None) -> int:
    state = WatchdogState()
    last_note = ""
    last_heartbeat = 0.0
    restarts = 0
    iterations = 0

    log.event("start",
              pid=os.getpid(), state_path=state_path,
              threshold_s=LISTENING_STUCK_THRESHOLD_S,
              error_grace_s=ERROR_GRACE_SECONDS, poll_s=poll)

    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        now = time.monotonic()
        obs, note = read_state(state_path)
        # The identity belongs in here, not just the pid and the state name.
        # Without it, ten consecutive LISTENING turns are one "transition" and
        # the log stays silent for the whole run -- which is why the false
        # restart had no visible run-up: nine turns had gone by unlogged.
        prev_tracked = (state.tracked_pid, state.tracked_state,
                        state.tracked_identity)

        state, decision = decide(state, obs, now, process_exists)

        # Log on transition and on anything actionable; stay silent while a
        # healthy runtime sits in IDLE for hours.
        transitioned = (state.tracked_pid, state.tracked_state,
                        state.tracked_identity) != prev_tracked
        if obs is None and note != last_note:
            log.event("state_unreadable", note=note)
        elif transitioned and obs is not None:
            log.event("observed", state=obs.state, pid=obs.pid,
                      turn_id=obs.turn_id, since=obs.since, detail=obs.detail)
        last_note = note

        if decision.action is Action.RUNTIME_ABSENT:
            log.event("runtime_absent", pid=decision.pid,
                      listening_s=round(decision.elapsed_s, 1),
                      note="pid gone; leaving this to launchd")

        elif decision.action is Action.LOCKED_OUT:
            log.event("locked_out", pid=decision.pid, cause=decision.cause,
                      listening_s=round(decision.elapsed_s, 1),
                      reason=decision.reason)

        elif decision.action is Action.RESTART:
            restarts += 1
            # `cause` separates the two failures without anyone having to parse
            # the prose: LISTENING_STUCK is the CoreAudio deadlock,
            # PERSISTENT_ERROR is a runtime that cannot repair its own stream.
            log.event("restart_decision", pid=decision.pid,
                      cause=decision.cause,
                      watched_s=round(decision.elapsed_s, 1),
                      listening_s=round(decision.elapsed_s, 1),
                      reason=decision.reason)
            result = restart_runtime(decision.pid, read_state, state_path)
            log.event("restart_result", cause=decision.cause,
                      ok=result.ok, old_pid=result.old_pid,
                      new_pid=result.new_pid,
                      restart_return=result.restart_return,
                      recovery_state=result.recovery_state,
                      recovery_ms=result.recovery_ms, note=result.note)

        if now - last_heartbeat > HEARTBEAT_S:
            last_heartbeat = now
            log.event("heartbeat",
                      state=(obs.state if obs else None),
                      pid=(obs.pid if obs else None),
                      turn_id=(obs.turn_id if obs else None),
                      listening_s=round(state.elapsed(now), 1),
                      restarts=restarts)

        time.sleep(poll)

    return restarts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-path", default=PRODUCTION_STATE_PATH)
    ap.add_argument("--log", default=os.path.abspath(DEFAULT_LOG))
    ap.add_argument("--poll", type=float, default=POLL_INTERVAL_S)
    ap.add_argument("--iterations", type=int, default=None,
                    help="stop after N polls (testing only)")
    a = ap.parse_args()

    log = WatchdogLog(a.log)
    try:
        run(a.state_path, log, a.poll, a.iterations)
    except KeyboardInterrupt:
        log.event("stop", reason="interrupt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
