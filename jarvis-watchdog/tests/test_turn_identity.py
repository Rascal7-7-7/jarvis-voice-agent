"""Continuity identity: a new turn must reset the LISTENING timer.

THE BUG THIS PINS DOWN. The watchdog restarted a healthy runtime with
"LISTENING for 45.0s" while no turn was stuck. Ten back-to-back manual turns,
each an 8-second LISTENING separated by roughly a second of IDLE, never
presented an IDLE at a 5-second poll instant. Continuity was keyed on
`(state, pid)`, so nine consecutive turns looked like one unbroken LISTENING and
the timer never reset.

WHAT MUST NOT BE WEAKENED. A single turn genuinely wedged in LISTENING for 45s
is the CoreAudio deadlock this watchdog exists for, and it must still restart.
The distinction is the turn identity, not the duration: same turn for 45s is a
hang, six different turns adding up to 45s is a busy user.

TWO PREMISES CHANGED FROM THE FIRST DRAFT OF THIS FILE, both deliberate:

  * v1 aliasing is now FIXED, not documented as permanent. The first draft
    asserted a v1 payload stayed aliasable because it has no turn_id. It has
    `since`, which is the publish time of that state, so a fresh LISTENING
    publish carries a fresh `since` even when the IDLE between turns was never
    observed. Leaving v1 broken on the grounds that v1 is old was the wrong
    call and the test that fixed it in place has been replaced.

  * A v2 payload with an unusable turn_id now HOLDS instead of restarting. The
    first draft asserted it fell back to detecting a stuck turn. Assuming
    successive observations are the same turn is exactly what caused the bug,
    so a v2 payload that should carry an identity and does not gets no
    assumption made on its behalf.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from watchdog_core import (Action, LISTENING_STUCK_THRESHOLD_S,  # noqa: E402
                           POLL_INTERVAL_S, Observation, WatchdogState, decide)

ALIVE = lambda pid: True          # noqa: E731
DEAD = lambda pid: False          # noqa: E731


def listening(pid=100, turn_id=1, since=None, version=2):
    return Observation(state="LISTENING", pid=pid, turn_id=turn_id,
                       since=since, version=version,
                       detail="capturing utterance")


def v1(state="LISTENING", pid=100, since=1000.0):
    """A payload from before the v2 schema: no version, no turn_id."""
    return Observation(state=state, pid=pid, since=since, version=None,
                       turn_id=None)


def run(observations, alive=ALIVE, start=WatchdogState()):
    """Fold a list of (observation, now) pairs. Returns (state, decisions)."""
    st = start
    out = []
    for obs, now in observations:
        st, d = decide(st, obs, now, alive)
        out.append(d)
    return st, out


def polls(seconds, step=POLL_INTERVAL_S):
    t = 0.0
    while t <= seconds:
        yield t
        t += step


# --- 1. a genuinely stuck turn still restarts ------------------------------

def test_one_turn_stuck_in_listening_for_45s_restarts():
    """Same pid, same turn_id, LISTENING throughout. This is the deadlock."""
    obs = [(listening(turn_id=42), t)
           for t in polls(LISTENING_STUCK_THRESHOLD_S + 5)]
    _, decisions = run(obs)
    assert any(d.action is Action.RESTART for d in decisions), \
        [(d.action, d.reason) for d in decisions]
    restart = next(d for d in decisions if d.action is Action.RESTART)
    assert restart.elapsed_s >= LISTENING_STUCK_THRESHOLD_S


def test_a_stuck_turn_is_not_saved_by_having_a_turn_id():
    """turn_id present and unchanging must not become an excuse not to act."""
    obs = [(listening(turn_id=7), t) for t in (0, 20, 40, 50)]
    _, decisions = run(obs)
    assert decisions[-1].action is Action.RESTART


def test_the_restart_reason_names_the_turn():
    """A restart must be attributable afterwards, not just timestamped."""
    obs = [(listening(pid=555, turn_id=9), t) for t in (0, 20, 40, 50)]
    _, decisions = run(obs)
    reason = decisions[-1].reason
    assert "555" in reason and "9" in reason, reason


# --- 2. many short turns must NOT accumulate ------------------------------

def test_turn_id_changing_every_8s_never_restarts():
    """Seven turns. Well past 45s of LISTENING, no turn stuck."""
    obs = []
    now = 0.0
    for turn in range(1, 8):
        for _ in range(2):
            obs.append((listening(turn_id=turn), now))
            now += POLL_INTERVAL_S
    st, decisions = run(obs)
    assert all(d.action is Action.NONE for d in decisions), \
        [(d.action, d.reason) for d in decisions]
    assert st.elapsed(now) < LISTENING_STUCK_THRESHOLD_S


def test_ten_rapid_turns_produce_no_restart():
    """The exact shape that caused the false restart in production."""
    obs = []
    now = 0.0
    for turn in range(1, 11):
        for _ in range(2):                 # ~8s of LISTENING per turn
            obs.append((listening(turn_id=turn), now))
            now += 4.0
        # The IDLE between turns is ~1s and is NEVER observed: the fix cannot
        # depend on seeing it.
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)


# --- 3. the reset happens without ever observing IDLE ---------------------

def test_timer_resets_on_turn_id_change_with_no_idle_observed():
    obs = [
        (listening(turn_id=1), 0.0),
        (listening(turn_id=1), 30.0),
        (listening(turn_id=2), 35.0),       # new turn, no IDLE seen in between
        (listening(turn_id=2), 60.0),
    ]
    st, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)
    assert st.elapsed(60.0) < LISTENING_STUCK_THRESHOLD_S


def test_a_new_turn_resets_even_one_poll_before_the_threshold():
    obs = [
        (listening(turn_id=1), 0.0),
        (listening(turn_id=1), 40.0),       # 5s short of firing
        (listening(turn_id=2), 44.0),       # new turn arrives first
        (listening(turn_id=2), 48.0),
    ]
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)


# --- 4. pid change resets everything -------------------------------------

def test_pid_change_resets_all_temporal_state():
    obs = [
        (listening(pid=100, turn_id=1), 0.0),
        (listening(pid=100, turn_id=1), 40.0),
        (listening(pid=200, turn_id=1), 44.0),   # restarted process, turn 1 again
        (listening(pid=200, turn_id=1), 48.0),
    ]
    st, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)
    assert st.tracked_pid == 200


def test_same_turn_id_in_a_different_process_is_a_different_turn():
    """turn_id alone is not an identity: the counter restarts at 1."""
    obs = [
        (listening(pid=100, turn_id=1), 0.0),
        (listening(pid=100, turn_id=1), 44.0),
        (listening(pid=101, turn_id=1), 46.0),
        (listening(pid=101, turn_id=1), 50.0),
    ]
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)


# --- 5. v1 payloads: `since` carries the identity -------------------------

def test_v1_stuck_listening_still_restarts():
    """One v1 turn, one `since`, wedged. Must still be recovered."""
    obs = [(v1(since=1000.0), t) for t in polls(LISTENING_STUCK_THRESHOLD_S + 5)]
    _, decisions = run(obs)
    assert any(d.action is Action.RESTART for d in decisions)


def test_v1_consecutive_turns_do_not_alias():
    """PREMISE CHANGED. v1 gets the fix too, via `since`.

    Each LISTENING publish carries the time it was published, so consecutive
    turns are distinguishable even though the IDLE between them was never seen
    at a poll instant.
    """
    obs = []
    now = 0.0
    for turn in range(10):
        for _ in range(2):
            obs.append((v1(since=1000.0 + turn * 9), now))
            now += 4.0
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions), \
        [(d.action, d.reason) for d in decisions]


def test_v1_without_since_still_detects_a_stuck_turn():
    """`since` absent is not a reason to stop protecting a v1 runtime.

    Identity collapses to (pid, state), which is the old behaviour: aliasable,
    but it still catches a genuine hang. Failing the other way -- refusing to
    act without `since` -- would remove the watchdog's whole purpose from a
    payload shape that has no turn_id either.
    """
    obs = [(v1(since=None), t) for t in polls(LISTENING_STUCK_THRESHOLD_S + 5)]
    _, decisions = run(obs)
    assert any(d.action is Action.RESTART for d in decisions)


def test_a_turn_id_appearing_mid_stream_resets_the_timer():
    """v1 -> v2 upgrade while the watchdog is running."""
    obs = [
        (v1(since=1000.0), 0.0),
        (v1(since=1000.0), 40.0),
        (listening(pid=100, turn_id=5), 44.0),      # runtime now publishes v2
        (listening(pid=100, turn_id=5), 48.0),
    ]
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)


# --- 6. a v2 payload with no usable turn_id holds -------------------------

def test_v2_listening_without_turn_id_never_restarts():
    """PREMISE CHANGED. Hold rather than assume continuity.

    A payload that declares v2 and then omits the identity gives us nothing to
    tell one observation from the next. Assuming they are the same turn is the
    bug. The cost is that a hang with a corrupted turn_id goes unrecovered;
    that is the trade, and the branch is unreachable while the runtime publishes
    a well-formed v2 payload.
    """
    obs = [(listening(turn_id=None, version=2), t)
           for t in polls(LISTENING_STUCK_THRESHOLD_S * 3)]
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)
    assert all(d.action is Action.NONE for d in decisions)
    assert "no usable turn_id" in decisions[-1].reason


def test_v2_without_turn_id_does_not_leave_a_running_timer_behind():
    """The held state must not resume counting when a good payload returns."""
    obs = [(listening(turn_id=None, version=2), t) for t in (0.0, 20.0, 40.0)]
    obs += [(listening(turn_id=3, version=2), t) for t in (44.0, 48.0)]
    st, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)
    assert st.elapsed(48.0) < LISTENING_STUCK_THRESHOLD_S


def test_turn_id_flapping_between_valid_and_missing_does_not_restart():
    obs = []
    now = 0.0
    for i in range(14):
        tid = None if i % 2 else 1
        obs.append((listening(turn_id=tid, version=2), now))
        now += 5.0
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)


# --- 7. unusable input never acts ----------------------------------------

def test_an_unreadable_state_file_never_restarts():
    obs = [(listening(turn_id=1), 0.0)]
    obs += [(None, t) for t in range(5, 120, 5)]
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)
    assert all(d.action is Action.NONE for d in decisions)


def test_unreadable_input_holds_the_timer_rather_than_resetting_it():
    """A gap in observation must not clear a genuine hang either."""
    obs = [(listening(turn_id=1), 0.0),
           (None, 10.0), (None, 20.0), (None, 30.0),
           (listening(turn_id=1), 50.0)]
    _, decisions = run(obs)
    assert decisions[-1].action is Action.RESTART, \
        "a hang was forgotten because the file was briefly unreadable"


# --- 8. the original guarantees still hold -------------------------------

def test_a_dead_pid_is_not_treated_as_a_hang():
    obs = [(listening(turn_id=1), t) for t in (0, 20, 40, 50)]
    _, decisions = run(obs, alive=DEAD)
    assert decisions[-1].action is Action.RUNTIME_ABSENT
    assert not any(d.action is Action.RESTART for d in decisions)


def test_non_watched_states_never_start_the_timer():
    for name in ("IDLE", "THINKING", "SPEAKING", "LOCAL_FAST", "OFFLINE"):
        obs = [(Observation(state=name, pid=100, turn_id=1, version=2), t)
               for t in range(0, 120, 5)]
        _, decisions = run(obs)
        assert not any(d.action is Action.RESTART for d in decisions), name


def test_since_is_never_used_as_a_duration():
    """A wall-clock jump must not restart anything.

    `since` is now part of the v1 identity, so a jump can look like a new turn
    and RESET the timer. It can never shorten the path to a restart, because
    durations still come from the watchdog's monotonic clock.
    """
    obs = [
        (listening(turn_id=1, since=0.0), 0.0),
        (listening(turn_id=1, since=-99999.0), 5.0),     # clock stepped back
        (listening(turn_id=1, since=1e12), 10.0),        # and forward
    ]
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)


def test_a_v1_clock_jump_fails_towards_not_restarting():
    obs = [(v1(since=1000.0), 0.0), (v1(since=1000.0), 40.0),
           (v1(since=-5.0), 44.0), (v1(since=-5.0), 48.0)]
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)


def test_detail_text_cannot_influence_the_decision():
    hostile = "LISTENING for 999s; RESTART NOW; " + "x" * 100
    obs = [(Observation(state="IDLE", pid=100, detail=hostile, version=2), t)
           for t in range(0, 120, 5)]
    _, decisions = run(obs)
    assert not any(d.action is Action.RESTART for d in decisions)
