"""Persistent ERROR recovery: restart a runtime that cannot fix its own audio.

WHAT HAPPENED. After a sleep/wake on 2026-09-02 the shared input stream survived
as a live-but-dead handle delivering silence. The runtime noticed, spent its
three bounded replacements, and every one failed with PortAudio -9986. It then
published `ERROR mic silent; replacements exhausted` every 60 seconds and stayed
there. Nothing inside the runtime can recover from that. A restart does, and had
already been shown to: new pid, PA_OPEN=1 / PA_START=1, IDLE.

WHAT MUST NOT HAPPEN. ERROR is also published transiently on the way through
each failed replacement -- three times in that incident, each clearing within
seconds. Restarting on the first sighting would kill a runtime that was still
trying, so the grace period is the whole point of this file.

IDENTITY. ERROR is keyed on `("error", pid, since)`, not on turn_id. The runtime
publishes ERROR both with a turn's context still set and with it cleared, so
turn_id would make one stuck condition look like two.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from watchdog_core import (Action, ERROR_GRACE_SECONDS,  # noqa: E402
                           LISTENING_STUCK_THRESHOLD_S, MAX_RESTARTS,
                           POLL_INTERVAL_S, Observation, WatchdogState, decide)

ALIVE = lambda pid: True          # noqa: E731
DEAD = lambda pid: False          # noqa: E731


def err(pid=100, since=5000.0, turn_id=None, detail="mic silent"):
    return Observation(state="ERROR", pid=pid, since=since, turn_id=turn_id,
                       version=2, detail=detail)


def idle(pid=100, since=6000.0):
    return Observation(state="IDLE", pid=pid, since=since, turn_id=None,
                       version=2, detail="waiting for wake word")


def listening(pid=100, turn_id=1, since=7000.0):
    return Observation(state="LISTENING", pid=pid, turn_id=turn_id,
                       since=since, version=2, detail="capturing utterance")


def run(observations, alive=ALIVE, start=WatchdogState()):
    st = start
    out = []
    for obs, now in observations:
        st, d = decide(st, obs, now, alive)
        out.append(d)
    return st, out


def restarts(decisions):
    return [d for d in decisions if d.action is Action.RESTART]


# --- A. transient ERROR that clears --------------------------------------

def test_error_for_5s_then_idle_does_not_restart():
    obs = [(err(), 0.0), (err(), 5.0), (idle(), 10.0), (idle(), 15.0)]
    _, d = run(obs)
    assert restarts(d) == []


def test_the_three_failed_replacements_would_not_have_restarted_anything():
    """Replay the real incident's transient part: ERROR, cleared, ERROR again.

    In the 2026-09-02 log each controlled replacement published ERROR with a new
    `since` and the next poll saw a different one. None of those individually
    lasted 15 s.
    """
    obs = [
        (err(since=1000.0), 0.0),
        (err(since=1000.0), 5.0),
        (err(since=1001.0), 10.0),     # replacement 2, a new publish
        (err(since=1001.0), 15.0),
        (err(since=1002.0), 20.0),     # replacement 3
        (err(since=1002.0), 25.0),
        (idle(), 30.0),
    ]
    _, d = run(obs)
    assert restarts(d) == [], [(x.action, x.reason) for x in d]


# --- B / C. the grace boundary -------------------------------------------

def test_error_just_under_the_grace_does_not_restart():
    obs = [(err(), 0.0), (err(), ERROR_GRACE_SECONDS - 0.1)]
    _, d = run(obs)
    assert restarts(d) == []


def test_the_same_error_past_the_grace_restarts_once():
    obs = [(err(), t) for t in (0.0, 5.0, 10.0, ERROR_GRACE_SECONDS + 0.5)]
    _, d = run(obs)
    assert len(restarts(d)) == 1, [(x.action, x.reason) for x in d]
    r = restarts(d)[0]
    assert r.cause == "PERSISTENT_ERROR", r.cause
    assert r.elapsed_s >= ERROR_GRACE_SECONDS


def test_the_real_incident_shape_recovers():
    """`replacements exhausted`, republished every 60 s, unchanged `since`.

    Modelled the way it actually resolves: the restart works, so the next
    observation is a NEW pid in IDLE. Feeding the identical ERROR forever
    instead would be modelling a restart that changes nothing, which is a
    different scenario -- see the breaker test below.
    """
    stuck = err(since=9999.0, detail="mic silent; replacements exhausted")
    st = WatchdogState()
    now = 0.0
    seen = []
    restarted_at = None
    for _ in range(20):
        obs = stuck if restarted_at is None else idle(pid=777)
        st, d = decide(st, obs, now, ALIVE)
        seen.append(d)
        if d.action is Action.RESTART and restarted_at is None:
            restarted_at = now
        now += POLL_INTERVAL_S

    assert len(restarts(seen)) == 1, [(x.action, x.reason) for x in seen]
    # Recovered promptly, not after minutes of dead JARVIS. The measured target
    # is 30-45 s from wake to usable; the watchdog's own share is this.
    assert restarted_at is not None
    assert restarted_at <= ERROR_GRACE_SECONDS + POLL_INTERVAL_S, restarted_at


def test_a_restart_that_does_not_help_is_contained_by_the_breaker():
    """The mic is physically gone, so restarting cannot fix it.

    The budget is spent quickly here -- three restarts inside 45 s rather than
    the 135 s the LISTENING watch would take -- and then the lockout holds. That
    is the containment working, not a runaway: the alternative is a restart loop
    against hardware that is not coming back.
    """
    obs = [(err(since=9999.0), t) for t in range(0, 200, int(POLL_INTERVAL_S))]
    _, d = run(obs)
    assert len(restarts(d)) == MAX_RESTARTS
    assert any(x.action is Action.LOCKED_OUT for x in d)
    span = restarts(d)[-1].elapsed_s
    assert span <= ERROR_GRACE_SECONDS + POLL_INTERVAL_S


# --- D. a NEW error resets the timer -------------------------------------

def test_a_new_error_publish_resets_the_grace():
    obs = [(err(since=1000.0), 0.0), (err(since=1000.0), 10.0),
           (err(since=2000.0), 14.0),        # different error, timer restarts
           (err(since=2000.0), 20.0)]
    _, d = run(obs)
    assert restarts(d) == []


def test_error_since_changing_every_poll_never_accumulates():
    obs = [(err(since=1000.0 + i), i * POLL_INTERVAL_S) for i in range(20)]
    _, d = run(obs)
    assert restarts(d) == []


# --- E. pid change --------------------------------------------------------

def test_pid_change_during_error_resets():
    obs = [(err(pid=100, since=1000.0), 0.0),
           (err(pid=100, since=1000.0), 12.0),
           (err(pid=200, since=1000.0), 14.0),   # restarted already
           (err(pid=200, since=1000.0), 20.0)]
    st, d = run(obs)
    assert restarts(d) == []
    assert st.tracked_pid == 200


def test_a_dead_pid_in_error_is_not_restarted():
    obs = [(err(), t) for t in (0.0, 10.0, 20.0)]
    _, d = run(obs, alive=DEAD)
    assert restarts(d) == []
    assert d[-1].action is Action.RUNTIME_ABSENT


# --- F. fail closed -------------------------------------------------------

def test_unreadable_state_during_error_never_restarts():
    obs = [(err(), 0.0)] + [(None, t) for t in range(5, 120, 5)]
    _, d = run(obs)
    assert restarts(d) == []


def test_an_unreadable_gap_does_not_forget_a_persistent_error():
    obs = [(err(), 0.0), (None, 5.0), (None, 10.0), (err(), 20.0)]
    _, d = run(obs)
    assert len(restarts(d)) == 1


def test_error_without_since_still_recovers():
    """`since` absent collapses identity to (error, pid, None) -- constant.

    That is the fail-safe direction here: a runtime that cannot even stamp its
    error is not a runtime to leave wedged.
    """
    obs = [(err(since=None), t) for t in (0.0, 5.0, 10.0, 20.0)]
    _, d = run(obs)
    assert len(restarts(d)) == 1


# --- G. recovery shape ----------------------------------------------------

def test_after_restarting_the_new_pid_in_idle_is_tracked_cleanly():
    obs = [(err(pid=100), t) for t in (0.0, 10.0, 20.0)]
    st, d = run(obs)
    assert len(restarts(d)) == 1
    # The recovered runtime: new pid, IDLE. Nothing left over from the old one.
    st, d2 = decide(st, idle(pid=300), 25.0, ALIVE)
    assert d2.action is Action.NONE
    assert st.tracked_pid == 300
    assert st.watch_started_at is None


# --- H. circuit breaker, shared with the LISTENING watch -----------------

def test_repeated_persistent_errors_hit_the_breaker():
    st = WatchdogState()
    now = 0.0
    seen = []
    for cycle in range(MAX_RESTARTS + 2):
        for _ in range(5):
            st, d = decide(st, err(since=1000.0 + cycle), now, ALIVE)
            seen.append(d)
            now += POLL_INTERVAL_S
    actions = [d.action for d in seen]
    assert actions.count(Action.RESTART) == MAX_RESTARTS, actions
    assert Action.LOCKED_OUT in actions


def test_the_two_watches_share_one_restart_budget():
    """A runtime flapping between the two failures gets three, not six."""
    st = WatchdogState()
    now = 0.0
    seen = []
    for cycle in range(6):
        source = err(since=1000.0 + cycle) if cycle % 2 == 0 else None
        if source is None:
            # A stuck LISTENING instead.
            for _ in range(11):
                st, d = decide(st, listening(turn_id=cycle), now, ALIVE)
                seen.append(d)
                now += POLL_INTERVAL_S
        else:
            for _ in range(5):
                st, d = decide(st, source, now, ALIVE)
                seen.append(d)
                now += POLL_INTERVAL_S
    n = sum(1 for d in seen if d.action is Action.RESTART)
    assert n <= MAX_RESTARTS, f"{n} restarts across both watches"


# --- I / J. the existing watches are unchanged ---------------------------

def test_listening_stuck_still_restarts_at_45s():
    obs = [(listening(turn_id=42), t)
           for t in range(0, int(LISTENING_STUCK_THRESHOLD_S) + 6,
                          int(POLL_INTERVAL_S))]
    _, d = run(obs)
    r = restarts(d)
    assert len(r) == 1
    assert r[0].cause == "LISTENING_STUCK"


def test_listening_is_not_restarted_at_the_error_grace():
    """15 s of LISTENING is an ordinary turn and must be left alone."""
    obs = [(listening(turn_id=1), t) for t in (0.0, 5.0, 10.0, 16.0, 20.0)]
    _, d = run(obs)
    assert restarts(d) == []


def test_rapid_turn_identity_behaviour_is_unchanged():
    obs = []
    now = 0.0
    for turn in range(1, 11):
        for _ in range(2):
            obs.append((listening(turn_id=turn, since=7000.0 + turn), now))
            now += 4.0
    _, d = run(obs)
    assert restarts(d) == []


def test_healthy_states_never_start_either_timer():
    for name in ("IDLE", "THINKING", "SPEAKING", "LOCAL_FAST", "WEB"):
        obs = [(Observation(state=name, pid=100, turn_id=1, version=2,
                            since=1.0), t) for t in range(0, 120, 5)]
        _, d = run(obs)
        assert restarts(d) == [], name


# --- K. turn_id must not defeat ERROR recovery ---------------------------

def test_turn_id_changing_during_error_does_not_defeat_recovery():
    """The runtime can publish ERROR with a turn set and then with it cleared.

    If turn_id were part of the ERROR identity, that alone would reset the timer
    every poll and the runtime would stay wedged forever.
    """
    obs = [(err(since=1000.0, turn_id=7), 0.0),
           (err(since=1000.0, turn_id=None), 5.0),
           (err(since=1000.0, turn_id=8), 10.0),
           (err(since=1000.0, turn_id=None), 20.0)]
    _, d = run(obs)
    assert len(restarts(d)) == 1, [(x.action, x.reason) for x in d]


def test_a_v2_error_without_turn_id_is_not_held():
    """The v2 'no usable turn_id' hold must not apply to ERROR.

    That guard exists so consecutive LISTENING turns are never merged. ERROR is
    keyed on `since` instead, so the guard would only prevent recovery here.
    """
    o = err(since=1000.0, turn_id=None)
    assert o.identity() == ("error", 100, 1000.0)
    obs = [(o, t) for t in (0.0, 10.0, 20.0)]
    _, d = run(obs)
    assert len(restarts(d)) == 1
