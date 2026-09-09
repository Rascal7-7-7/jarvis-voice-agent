"""The one privileged-ish action this watchdog may take, and nothing else.

The command is a constant. There is no parameter anywhere in this module that
can reach argv: no shell, no `sh -c`, no string interpolation, no value taken
from the state file, no PATH lookup. `launchctl` is addressed by absolute path
and the service label is a literal. If this file ever needs a variable in the
command, that is a redesign, not an edit.

`kickstart -k` is used rather than bootout+bootstrap. It is a single operation
that keeps the service loaded, so there is no window where JARVIS is unloaded
and a second failing call leaves nothing running -- and it was observed to
terminate the genuinely wedged runtime (pid 12948 -> 22386) during the hang
investigation, so it works against the failure it is meant to fix.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

LAUNCHCTL = "/bin/launchctl"
SERVICE_LABEL = "local.jarvis.runtime"

KICKSTART_TIMEOUT_S = 30.0
RECOVERY_TIMEOUT_S = 25.0
RECOVERY_POLL_S = 0.5

# What a healthy runtime settles into after a restart.
HEALTHY_STATES = frozenset({"IDLE"})


@dataclass(frozen=True)
class RestartResult:
    ok: bool
    old_pid: int | None
    new_pid: int | None
    restart_return: int | None
    recovery_state: str | None
    recovery_ms: int | None
    note: str


def _argv() -> list[str]:
    return [LAUNCHCTL, "kickstart", "-k",
            f"gui/{os.getuid()}/{SERVICE_LABEL}"]


def restart_runtime(old_pid: int | None, read_state, state_path: str,
                    now=time.monotonic) -> RestartResult:
    """Kickstart the runtime, then prove it actually came back.

    A zero exit from launchctl is not success: the point is a NEW pid that
    reaches a healthy state. `read_state` is injected so this is testable
    against a fake state file.
    """
    argv = _argv()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=KICKSTART_TIMEOUT_S, shell=False)
    except subprocess.TimeoutExpired:
        return RestartResult(False, old_pid, None, None, None, None,
                             "launchctl kickstart timed out")
    except OSError as e:
        return RestartResult(False, old_pid, None, None, None, None,
                             f"launchctl failed to run: {e}")

    rc = proc.returncode
    started = now()
    last_state: str | None = None
    last_pid: int | None = None

    while now() - started < RECOVERY_TIMEOUT_S:
        obs, _ = read_state(state_path)
        if obs is not None:
            last_state, last_pid = obs.state, obs.pid
            fresh = old_pid is None or obs.pid != old_pid
            if fresh and obs.state in HEALTHY_STATES:
                return RestartResult(
                    True, old_pid, obs.pid, rc, obs.state,
                    int((now() - started) * 1000),
                    "runtime restarted and reached a healthy state")
        time.sleep(RECOVERY_POLL_S)

    return RestartResult(False, old_pid, last_pid, rc, last_state,
                         int((now() - started) * 1000),
                         "RECOVERY_FAILED: no healthy new pid within "
                         f"{RECOVERY_TIMEOUT_S:.0f}s")
