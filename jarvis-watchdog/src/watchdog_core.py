"""Decision engine for the JARVIS watchdog. Pure: no files, no clock, no spawn.

Everything that could make the watchdog misfire lives here, so all of it can be
tested without a real runtime, a real clock, or a real restart. The main loop
does I/O and calls `decide()`; it makes no judgements of its own.

Two properties this module exists to guarantee:

1. Elapsed time is measured on a MONOTONIC clock owned by the watchdog, never
   from the state file's `since`. `since` is wall-clock, so NTP steps, sleep or
   resume, and manual clock changes make it discontinuous -- and a watchdog that
   restarts a healthy runtime because the clock jumped is worse than no watchdog.
   `since` is carried through for logging only and is never compared against.

2. Nothing from the state file's free text can influence the decision. Only the
   state enum, the pid, and the watchdog's own elapsed measurement do. `detail`
   is untrusted text written by another process and is treated as a log field.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

# The deadlock proven in production wedges the runtime inside LISTENING.
WATCHED_STATE = "LISTENING"

# The second proven failure: after a sleep/wake the shared input stream can
# survive as a live-but-dead handle delivering silence. The runtime notices,
# tries its three bounded replacements, and every one fails with PortAudio
# -9986 -- so it parks in ERROR and republishes that every 60 seconds forever.
# Measured 2026-09-02: 08:59:20 to 09:24:46, ending only because a human
# restarted it. Nothing in the runtime can recover from this; a restart does,
# and produces a new pid with PA_OPEN=1 / PA_START=1 and IDLE.
ERROR_STATE = "ERROR"

# ERROR is not automatically a hang. The runtime publishes it transiently on its
# way through a failed replacement, and those clear on their own within a couple
# of seconds. 15 s is long enough that a transient ERROR is never acted on, and
# short enough that a persistent one is recovered inside the ~30-45 s target
# rather than the 25 minutes it took by hand.
ERROR_GRACE_SECONDS = 15.0

# 45s, from the measured dwell-time distribution of real turns:
#   normal completions            2.39 - 8.13 s
#   a normal turn with long silence      15.76 s
#   the 30s wait path, which RECOVERS BY ITSELF   30.01 / 30.01 / 30.17 s
#   the CoreAudio deadlock                        > 700 s
# Anything at or below 30.17s would restart a runtime that was about to recover
# on its own. 45s clears that path with margin and still fires ~15x sooner than
# the observed deadlock would have been noticed by a human.
LISTENING_STUCK_THRESHOLD_S = 45.0

POLL_INTERVAL_S = 5.0

# Restart storm containment. Three restarts inside ten minutes means restarting
# is not fixing anything, so automatic restarts pause -- but the watchdog itself
# keeps running and keeps logging, because a watchdog that kills itself leaves
# the next deadlock unattended.
#
# ONE BUDGET FOR BOTH WATCHES. A stuck LISTENING and a dead audio stream are two
# symptoms of the same subsystem, and a runtime alternating between them would
# otherwise get six restarts per window instead of three.
MAX_RESTARTS = 3
RESTART_WINDOW_S = 600.0
LOCKOUT_S = 900.0


class Action(Enum):
    NONE = "NONE"
    RESTART = "RESTART"
    LOCKED_OUT = "LOCKED_OUT"          # would restart, but the breaker is open
    RUNTIME_ABSENT = "RUNTIME_ABSENT"  # stuck-looking, but the pid is gone


@dataclass(frozen=True)
class Observation:
    """One validated reading of the state file. `None` means it was unusable."""
    state: str
    pid: int
    since: float | None = None   # wall-clock; an identity component, never a duration
    detail: str = ""             # untrusted text, never used in a decision
    turn_id: int | None = None   # v2; positive int or None
    version: int | None = None   # schema version, None for v1

    def identity(self) -> tuple | None:
        """What makes this observation the SAME turn as the last one.

        `(state, pid)` alone was the bug. Ten manual turns of eight seconds each,
        separated by about a second of IDLE, never showed an IDLE at a five-second
        poll instant -- so nine consecutive turns looked like one unbroken
        LISTENING and the timer ran to 45 s against a runtime that was working
        perfectly. One real restart was caused this way.

        v2  ->  (pid, turn_id).  The pid is essential: turn_id is process-local
                and restarts at 1, so 42 -> 1 is a new process, not a counter
                going backwards.

        v1  ->  (pid, state, since).  `since` is the publish time of that state,
                so a fresh LISTENING publish carries a fresh `since` even when
                the IDLE between turns was never observed. That fixes the same
                aliasing for a v1 runtime rather than leaving it broken on the
                grounds that v1 is old.

        None -> identity could not be established. The caller must not restart
                on it; see `decide`.

        `since` is compared for EQUALITY here and never subtracted. Durations
        stay on the watchdog's monotonic clock, so a wall-clock step can at worst
        look like a new turn and reset the timer -- which fails towards not
        restarting.
        """
        if self.state == ERROR_STATE:
            # ERROR is keyed on the publish, not the turn. `turn_id` is not
            # dependable here: the runtime can publish ERROR while a turn's
            # context is still set, or with it already cleared, and the same
            # stuck condition would then look like two different identities.
            # `since` changes on every publish of a NEW error and stays put
            # while one persists, which is exactly the distinction needed.
            return ("error", self.pid, self.since)
        if self.version is not None and self.version >= 2:
            if self.turn_id is None:
                # A v2 payload that should carry a turn_id and does not. Refuse
                # to guess: assuming successive observations are the same turn
                # is exactly how the aliasing bug restarted a healthy runtime.
                return None
            return ("v2", self.pid, self.turn_id)
        return ("v1", self.pid, self.state, self.since)


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    elapsed_s: float = 0.0
    pid: int | None = None
    # Which watch produced this, so the log can tell a deadlock apart from a
    # dead audio stream without parsing the prose in `reason`.
    cause: str | None = None      # "LISTENING_STUCK" | "PERSISTENT_ERROR"


@dataclass(frozen=True)
class WatchdogState:
    """Immutable. `decide()` returns a new one rather than mutating this."""
    tracked_pid: int | None = None
    tracked_state: str | None = None
    tracked_identity: tuple | None = None   # see Observation.identity()
    watch_started_at: float | None = None   # monotonic, set by the watchdog
    restart_times: tuple[float, ...] = ()   # monotonic
    lockout_until: float | None = None

    def elapsed(self, now: float) -> float:
        if self.watch_started_at is None:
            return 0.0
        return max(0.0, now - self.watch_started_at)


def _watch(obs: Observation) -> tuple[float, str] | None:
    """(threshold, cause) for a state worth timing, or None.

    Two watches, one timer. They cannot overlap -- the runtime is in exactly one
    state -- so a single `watch_started_at` serves both, and any change of state
    resets it through the identity comparison below.
    """
    if obs.state == WATCHED_STATE:
        return LISTENING_STUCK_THRESHOLD_S, "LISTENING_STUCK"
    if obs.state == ERROR_STATE:
        return ERROR_GRACE_SECONDS, "PERSISTENT_ERROR"
    return None


def _reset(state: WatchdogState, obs: Observation, now: float) -> WatchdogState:
    """Begin (or restart) tracking from this observation."""
    watching = _watch(obs) is not None
    return replace(state,
                   tracked_pid=obs.pid,
                   tracked_state=obs.state,
                   tracked_identity=obs.identity(),
                   watch_started_at=now if watching else None)


def decide(state: WatchdogState,
           obs: Observation | None,
           now: float,
           process_exists) -> tuple[WatchdogState, Decision]:
    """Fold one observation into the watchdog state.

    `now` is a monotonic timestamp supplied by the caller; `process_exists` is
    injected so liveness can be exercised in tests without real processes.
    """
    # An unusable file is not evidence of a hang. Hold the timer where it is and
    # never act -- the failure direction here must be "do nothing".
    if obs is None:
        return state, Decision(Action.NONE, "unreadable state, holding")

    identity = obs.identity()

    # A v2 payload whose turn_id is missing or unusable. Nothing here can tell
    # one observation from the next, so the timer must not accumulate across
    # them -- that assumption is precisely what restarted a healthy runtime.
    # The failure direction is "do not restart"; a real hang with a corrupted
    # turn_id goes unrecovered, which is the trade this branch is for, and it is
    # unreachable while the runtime publishes a well-formed v2 payload.
    if identity is None:
        return replace(state,
                       tracked_pid=obs.pid,
                       tracked_state=obs.state,
                       tracked_identity=None,
                       watch_started_at=None), Decision(
            Action.NONE,
            f"v{obs.version} payload with no usable turn_id; "
            f"holding rather than assuming continuity", pid=obs.pid)

    watch = _watch(obs)

    if (obs.state != state.tracked_state
            or obs.pid != state.tracked_pid
            or identity != state.tracked_identity):
        new = _reset(state, obs, now)
        if watch is None:
            return new, Decision(Action.NONE, f"state={obs.state}, timer idle")
        return new, Decision(Action.NONE,
                             f"began watching {obs.state} pid={obs.pid} "
                             f"turn={obs.turn_id}", pid=obs.pid,
                             cause=watch[1])

    if watch is None:
        return state, Decision(Action.NONE, f"state={obs.state}, timer idle")

    threshold, cause = watch
    elapsed = state.elapsed(now)
    if elapsed < threshold:
        return state, Decision(Action.NONE, "within threshold", elapsed,
                               obs.pid, cause=cause)

    # Stuck by the clock. Everything below decides whether acting is allowed.
    if not process_exists(obs.pid):
        # A dead pid is launchd's problem, not a CoreAudio hang. Restarting on
        # this would misattribute the failure and could fight launchd's own
        # relaunch.
        return _reset(state, obs, now), Decision(
            Action.RUNTIME_ABSENT, f"pid {obs.pid} is gone", elapsed, obs.pid,
            cause=cause)

    if state.lockout_until is not None and now < state.lockout_until:
        remaining = round(state.lockout_until - now, 1)
        return state, Decision(Action.LOCKED_OUT,
                               f"auto-restart locked out for {remaining}s",
                               elapsed, obs.pid, cause=cause)

    recent = tuple(t for t in state.restart_times if now - t < RESTART_WINDOW_S)
    if len(recent) >= MAX_RESTARTS:
        new = replace(state, restart_times=recent, lockout_until=now + LOCKOUT_S)
        return new, Decision(Action.LOCKED_OUT,
                             f"{len(recent)} restarts in "
                             f"{int(RESTART_WINDOW_S / 60)}min, locking out "
                             f"{int(LOCKOUT_S / 60)}min",
                             elapsed, obs.pid, cause=cause)

    # Restart. The timer clears so the new pid is tracked from scratch.
    new = replace(state,
                  restart_times=recent + (now,),
                  tracked_pid=None,
                  tracked_state=None,
                  tracked_identity=None,
                  watch_started_at=None,
                  lockout_until=None)
    return new, Decision(Action.RESTART,
                         f"{obs.state} for {elapsed:.1f}s unchanged "
                         f"(pid={obs.pid} turn={obs.turn_id}, "
                         f"threshold {threshold:.0f}s)",
                         elapsed, obs.pid, cause=cause)
