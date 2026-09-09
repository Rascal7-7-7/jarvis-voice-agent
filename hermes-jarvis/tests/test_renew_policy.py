"""Tests for the usage-conditional Ollama residency renew (bin/jarvis_runtime.py).

The bug: `_renew_residency` ran in the turn's `finally` unconditionally, so an
ambient false wake -- no speech, no STT, no router, no backend -- reloaded the
expired 7.2 GB gemma4:e2b and pinned it for another hour. Observed in production
2026-09-02 13:32.

The fix asks Ollama, not the callers. The three inference sites share no request
layer and two of them live inside the jarvis-dispatch subprocess, so usage is
read off /api/ps: a landed inference always moves expires_at (measured
14:32:47 -> 14:49:08) or makes a non-resident model resident.

These tests run entirely against fixtures and a local fake /api/ps. No live
JARVIS, no real Ollama, no turn.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "bin"))

import jarvis_runtime as jr  # noqa: E402

MODEL = jr.ROUTER_MODEL
T1 = "2026-09-02T14:32:47.317167+09:00"
T2 = "2026-09-02T14:49:08.550778+09:00"

RESIDENT_T1 = {"resident": True, "expires_at": T1}
RESIDENT_T2 = {"resident": True, "expires_at": T2}
ABSENT = {"resident": False, "expires_at": None}


# ------------------------------------------------- A-C, F: the usage decision
def test_a_resident_with_changed_expiry_is_used():
    """A. The ordinary real turn: window moved, so an inference landed."""
    assert jr._ollama_was_used(RESIDENT_T1, RESIDENT_T2) is True


def test_b_nonresident_then_resident_is_used():
    """B. Cold model brought up by the turn itself."""
    assert jr._ollama_was_used(ABSENT, RESIDENT_T1) is True


def test_c_identical_snapshots_are_not_used():
    """C. Nothing moved -> nothing landed. The false-wake case."""
    assert jr._ollama_was_used(RESIDENT_T1, dict(RESIDENT_T1)) is False


def test_c2_absent_before_and_after_is_not_used():
    assert jr._ollama_was_used(ABSENT, dict(ABSENT)) is False


def test_c3_model_expired_mid_turn_without_use_is_not_used():
    """A window that lapsed during a long capture must not read as usage."""
    assert jr._ollama_was_used(RESIDENT_T1, ABSENT) is False


def test_f_another_models_window_moving_is_not_our_usage(monkeypatch):
    """F. Only ROUTER_MODEL counts. /api/ps carrying a different model only."""
    server, rec = _fake_ps(monkeypatch, {"models": [
        {"name": "some-other-model:7b", "expires_at": T2}]})
    with server:
        snap = jr._model_residency()
    assert snap == {"resident": False, "expires_at": None}
    assert jr._ollama_was_used(ABSENT, snap) is False


# --------------------------------------------------- D, E: unknown snapshots
def test_d_missing_before_snapshot_is_unknown():
    assert jr._ollama_was_used(None, RESIDENT_T2) is None


def test_e_missing_after_snapshot_is_unknown():
    assert jr._ollama_was_used(RESIDENT_T1, None) is None


def test_both_unknown_is_unknown():
    assert jr._ollama_was_used(None, None) is None


# --------------------------------------------------- /api/ps reading itself
class _FakePS:
    """A local server that answers /api/ps with a canned payload."""

    def __init__(self, payload, status=200, body=None):
        self.payload, self.status, self.body = payload, status, body
        self.hits = 0
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits += 1
                raw = (outer.body if outer.body is not None
                       else json.dumps(outer.payload).encode())
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), H)

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _fake_ps(monkeypatch, payload, status=200, body=None):
    server = _FakePS(payload, status, body)
    monkeypatch.setattr(jr, "OLLAMA_URL", f"http://127.0.0.1:{server.port}")
    return server, server


def test_residency_reads_the_router_model(monkeypatch):
    server, _ = _fake_ps(monkeypatch, {"models": [
        {"name": MODEL, "expires_at": T1},
        {"name": "other:1b", "expires_at": T2}]})
    with server:
        assert jr._model_residency() == {"resident": True, "expires_at": T1}


def test_residency_unreachable_is_unknown_not_absent(monkeypatch):
    """Unreachable must be None, so the caller can skip rather than guess."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    monkeypatch.setattr(jr, "OLLAMA_URL", f"http://127.0.0.1:{dead}")
    assert jr._model_residency() is None


def test_residency_bad_json_is_unknown(monkeypatch):
    server, _ = _fake_ps(monkeypatch, None, body=b"{not json")
    with server:
        assert jr._model_residency() is None


def test_residency_models_not_a_list_is_unknown(monkeypatch):
    server, _ = _fake_ps(monkeypatch, {"models": "nope"})
    with server:
        assert jr._model_residency() is None


# ----------------------------------------------- G, H: the renew decision
class _Tl:
    """Minimal stand-in for the turn's Timeline."""

    def __init__(self, before=None):
        self.ollama_before = before


@pytest.fixture
def spy(monkeypatch):
    """Record every _warm_ollama call instead of making one."""
    calls: list[str] = []
    monkeypatch.setattr(jr, "_warm_ollama",
                        lambda reason, **kw: calls.append(reason) or True)
    return calls


def test_g_false_wake_no_speech_turn_renews_nothing(spy, monkeypatch):
    """G. THE ACCEPTANCE TEST for the reported bug.

    A turn that never reached _dispatch leaves ollama_before as None. It must
    renew nothing -- and must not even ask Ollama, so a false wake costs no
    network at all.
    """
    probed = []
    monkeypatch.setattr(jr, "_model_residency",
                        lambda: probed.append(1) or RESIDENT_T1)

    jr._renew_residency(_Tl(before=None))

    assert spy == [], "a turn that used nothing triggered a renew"
    assert probed == [], "a false-wake turn queried /api/ps needlessly"


def test_h_real_ollama_turn_renews_exactly_once(spy, monkeypatch):
    """H. Window moved during the turn -> exactly one renew."""
    monkeypatch.setattr(jr, "_model_residency", lambda: RESIDENT_T2)

    jr._renew_residency(_Tl(before=RESIDENT_T1))

    assert spy == ["renew"]


def test_dispatch_ran_but_used_no_ollama_does_not_renew(spy, monkeypatch):
    """A turn that reached _dispatch but never hit the model (gate-only route)."""
    monkeypatch.setattr(jr, "_model_residency", lambda: dict(RESIDENT_T1))

    jr._renew_residency(_Tl(before=RESIDENT_T1))

    assert spy == []


def test_unknown_residency_skips_the_renew(spy, monkeypatch):
    """Fail-safe: unknown must not resurrect 7.2 GB on a guess."""
    monkeypatch.setattr(jr, "_model_residency", lambda: None)

    jr._renew_residency(_Tl(before=RESIDENT_T1))

    assert spy == []


def test_cold_model_loaded_by_the_turn_renews(spy, monkeypatch):
    """The post-idle real turn: cold load inside the turn, then renew."""
    monkeypatch.setattr(jr, "_model_residency", lambda: RESIDENT_T1)

    jr._renew_residency(_Tl(before=ABSENT))

    assert spy == ["renew"]


# ------------------------------------------------ I: no leakage across turns
def test_i_usage_state_is_per_turn_and_cannot_leak():
    """I. A fresh Timeline starts with no snapshot.

    The snapshot lives on the Timeline, and a Timeline is created per turn, so
    there is no module-level flag that a later turn could inherit.
    """
    assert jr.Timeline().ollama_before is None
    used = jr.Timeline()
    used.ollama_before = RESIDENT_T1
    assert jr.Timeline().ollama_before is None


def test_i2_two_turns_used_then_unused_renew_once(spy, monkeypatch):
    """H (two-turn form): only the turn that used Ollama renews."""
    monkeypatch.setattr(jr, "_model_residency", lambda: RESIDENT_T2)
    jr._renew_residency(_Tl(before=RESIDENT_T1))   # used
    jr._renew_residency(_Tl(before=None))          # false wake
    assert spy == ["renew"]


# ------------------------------- J, K: startup warm and the warm lock intact
def test_j_startup_warm_is_not_conditional_on_turn_usage():
    """J. _startup_warm still warms unconditionally; it predates any turn."""
    import inspect

    src = inspect.getsource(jr._startup_warm)
    assert '_warm_ollama("startup")' in src
    assert "ollama_before" not in src
    assert "_model_residency" not in src


def test_j2_startup_warm_constants_unchanged():
    assert jr.READY_BUDGET == 45.0
    assert jr.READY_BACKOFF == (1.0, 2.0, 3.0, 5.0)
    assert jr.OLLAMA_READY_PATH == "/api/tags"


def test_k_warm_lock_semantics_unchanged():
    """K. Duplicate-warm prevention still guards _warm_ollama itself."""
    import inspect

    src = inspect.getsource(jr._warm_ollama)
    assert "_warm_inflight.acquire(blocking=False)" in src
    assert "_warm_inflight.release()" in src


def test_keep_alive_and_scheduler_policy_unchanged():
    """No periodic renew was added, and keep_alive is still 60m."""
    import inspect

    assert jr.KEEP_ALIVE == "60m"
    src = inspect.getsource(jr)
    assert "threading.Timer" not in src
    # The only renew callsite is the per-turn one.
    assert src.count("_renew_residency(") == 1


# -------------------------------------- 17: controlled expired-model scenario
def test_controlled_expired_model_no_speech_turn_renews_zero(spy, monkeypatch):
    """Section 17, without waiting 60 minutes.

    Model already expired (absent from /api/ps), then a false wake with no
    speech. Before the fix this reloaded 7.2 GB; now it does nothing.
    """
    monkeypatch.setattr(jr, "_model_residency", lambda: ABSENT)
    jr._renew_residency(_Tl(before=None))
    assert spy == []


def test_controlled_expired_model_real_turn_renews_once(spy, monkeypatch):
    """…and a real turn against the same expired model still renews once."""
    monkeypatch.setattr(jr, "_model_residency", lambda: RESIDENT_T1)
    jr._renew_residency(_Tl(before=ABSENT))
    assert spy == ["renew"]
