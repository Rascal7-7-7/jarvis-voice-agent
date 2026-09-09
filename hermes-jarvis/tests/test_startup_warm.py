"""Tests for the readiness-aware Ollama startup warm (bin/jarvis_runtime.py).

The bug these pin down: at login the runtime and `ollama serve` start as two
unordered LaunchAgents, the single startup warm POST hit a closed port, failed
with URLError, and never retried -- so the first real turn paid the 17.9 s cold
model load itself.

Everything here runs against a real local HTTP server on an ephemeral port, not
a mocked urllib, so the readiness probe is exercised through the same socket
path it uses in production. Test B deliberately leaves the port CLOSED and binds
it only part way through the retry budget: a fix built on a fixed sleep would
pass or fail by luck, one built on readiness passes because it waited.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "bin"))

import jarvis_runtime as jr  # noqa: E402


# ----------------------------------------------------------------- fake Ollama
class _Recorder:
    """Counts what the warm actually asked the server for."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tags = 0
        self.chat = 0
        self.chat_bodies: list[dict] = []
        # Number of leading /api/tags requests to answer with 503, and of
        # leading /api/chat requests to answer with 500.
        self.tags_fail_first = 0
        self.chat_fail_first = 0

    def snapshot(self) -> tuple[int, int]:
        with self.lock:
            return self.tags, self.chat


def _make_handler(rec: _Recorder):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # silence the default stderr spam
            pass

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != jr.OLLAMA_READY_PATH:
                self._send(404, {"error": "not found"})
                return
            with rec.lock:
                rec.tags += 1
                failing = rec.tags <= rec.tags_fail_first
            if failing:
                self._send(503, {"error": "loading"})
            else:
                self._send(200, {"models": [{"name": jr.ROUTER_MODEL}]})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            with rec.lock:
                rec.chat += 1
                rec.chat_bodies.append(json.loads(raw or b"{}"))
                failing = rec.chat <= rec.chat_fail_first
            if failing:
                self._send(500, {"error": "transient"})
            else:
                self._send(200, {"message": {"role": "assistant", "content": "ok"},
                                 "done": True})

    return Handler


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    """A fake Ollama that can be bound late, to reproduce the login race."""

    def __init__(self, port: int, rec: _Recorder) -> None:
        self.port = port
        self.rec = rec
        self.httpd: http.server.ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", self.port), _make_handler(self.rec))
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


@pytest.fixture
def env(monkeypatch):
    """Point the runtime at a fake Ollama and shrink the retry budget."""
    rec = _Recorder()
    port = _free_port()
    server = _Server(port, rec)
    monkeypatch.setattr(jr, "OLLAMA_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(jr, "READY_BACKOFF", (0.2, 0.3, 0.5))
    monkeypatch.setattr(jr, "READY_BUDGET", 6.0)
    monkeypatch.setattr(jr, "READY_PROBE_TIMEOUT", 1.0)
    # _stop is module state shared with the real runtime loop; each test gets a
    # clean one so a shutdown test cannot leak into the next.
    monkeypatch.setattr(jr, "_stop", threading.Event())
    monkeypatch.setattr(jr, "_warm_inflight", threading.Lock())
    try:
        yield rec, server
    finally:
        server.stop()


def _run_startup_warm(timeout: float = 20.0) -> float:
    """Run the worker to completion on its own thread; return elapsed seconds."""
    t = time.monotonic()
    th = threading.Thread(target=jr._startup_warm, daemon=True)
    th.start()
    th.join(timeout)
    assert not th.is_alive(), "startup warm worker did not finish"
    return time.monotonic() - t


# ------------------------------------------------------------------------ A
def test_a_ready_immediately_warms_exactly_once(env):
    """A. Ollama is already up -> warm fires exactly once."""
    rec, server = env
    server.start()

    _run_startup_warm()

    tags, chat = rec.snapshot()
    assert chat == 1, f"expected exactly one warm, got {chat}"
    assert tags == 1, f"expected one readiness probe, got {tags}"
    assert rec.chat_bodies[0]["model"] == jr.ROUTER_MODEL
    assert rec.chat_bodies[0]["keep_alive"] == jr.KEEP_ALIVE


# ------------------------------------------------------------------------ B
def test_b_port_closed_then_opens_warms_after_waiting(env):
    """B. The login race itself: refused, refused, then the server appears.

    The port is genuinely unbound for the first ~0.6 s, so the connection is
    refused exactly as it was at 12:10:51 in production.
    """
    rec, server = env

    def bind_late():
        time.sleep(0.6)
        server.start()

    threading.Thread(target=bind_late, daemon=True).start()
    elapsed = _run_startup_warm()

    tags, chat = rec.snapshot()
    assert chat == 1, f"warm did not happen after the server came up (chat={chat})"
    assert elapsed >= 0.6, "warm completed before the server was bound"
    # Readiness-driven, not a fixed sleep: it retried and then succeeded.
    assert tags >= 1


# ------------------------------------------------------------------------ C
def test_c_never_available_gives_up_and_exits_cleanly(env):
    """C. Ollama never comes up -> the worker gives up inside its budget."""
    rec, _server = env  # server deliberately never started

    elapsed = _run_startup_warm()

    _tags, chat = rec.snapshot()
    assert chat == 0
    assert elapsed <= jr.READY_BUDGET + 3.0, (
        f"worker ran {elapsed:.1f}s against a {jr.READY_BUDGET}s budget")


# ------------------------------------------------------------------------ D
def test_d_ready_but_warm_fails_transiently_retries_within_budget(env):
    """D. Readiness succeeds, the warm 500s twice, then works."""
    rec, server = env
    rec.chat_fail_first = 2
    server.start()

    _run_startup_warm()

    _tags, chat = rec.snapshot()
    assert chat == 3, f"expected 2 failures then a success, got chat={chat}"


# ------------------------------------------------------------------------ E
def test_e_success_stops_retrying(env):
    """E. Once warm, the worker returns -- no further probes or warms."""
    rec, server = env
    server.start()

    _run_startup_warm()
    after_finish = rec.snapshot()
    time.sleep(1.0)

    assert rec.snapshot() == after_finish, "worker kept running after success"


# ------------------------------------------------------------------------ F
def test_f_renew_path_unchanged(env, monkeypatch):
    """F. The post-turn renew still warms once, with keep_alive, via /api/chat.

    The renew became conditional on the turn having used Ollama (see
    tests/test_renew_policy.py); once that condition is met, the request it
    sends is unchanged. Residency is stubbed here so this test stays about the
    warm request rather than about the usage decision.
    """
    rec, server = env
    server.start()
    monkeypatch.setattr(jr, "_model_residency",
                        lambda: {"resident": True, "expires_at": "after"})

    class _Tl:
        ollama_before = {"resident": True, "expires_at": "before"}

    jr._renew_residency(_Tl())

    tags, chat = rec.snapshot()
    assert chat == 1
    assert tags == 0, "renew must not probe readiness -- it is not the startup path"
    assert rec.chat_bodies[0]["keep_alive"] == jr.KEEP_ALIVE
    assert rec.chat_bodies[0]["model"] == jr.ROUTER_MODEL


# ------------------------------------------------------------------------ G
def test_g_startup_warm_does_not_block_the_caller(env):
    """G. Starting the worker returns immediately even with Ollama absent.

    This is the property that keeps the wake listener's startup unaffected: in
    main() the worker is launched the same way, on a daemon thread, after the
    listener is armed.
    """
    rec, _server = env  # never started -> the worker will retry for its budget

    t = time.monotonic()
    th = threading.Thread(target=jr._startup_warm, daemon=True)
    th.start()
    launch_cost = time.monotonic() - t

    assert launch_cost < 0.5, f"launching the warm cost {launch_cost:.2f}s"
    assert th.is_alive(), "worker should still be retrying, not already done"
    jr._stop.set()
    th.join(5.0)
    assert not th.is_alive()


def test_g2_main_launches_warm_off_thread():
    """G. …and main() really does launch it that way, not inline."""
    import inspect

    src = inspect.getsource(jr.main)
    assert "threading.Thread(target=_startup_warm, daemon=True).start()" in src
    # An inline call would block the line that follows the listener coming up.
    assert "\n    _startup_warm()" not in src


# ------------------------------------------------------------------------ H
def test_h_concurrent_warms_do_not_duplicate(env):
    """H. Two warms racing -> one in flight, the other skips."""
    rec, server = env
    rec.chat_fail_first = 0
    server.start()

    results: list[bool] = []
    lock = threading.Lock()

    def call(reason: str):
        ok = jr._warm_ollama(reason)
        with lock:
            results.append(ok)

    # Hold the lock so both threads are forced to contend for it.
    jr._warm_inflight.acquire()
    threads = [threading.Thread(target=call, args=(f"t{i}",)) for i in range(2)]
    for th in threads:
        th.start()
    time.sleep(0.3)
    jr._warm_inflight.release()
    for th in threads:
        th.join(10.0)

    _tags, chat = rec.snapshot()
    assert chat <= 1, f"duplicate warm reached the server (chat={chat})"
    assert results.count(False) >= 1, "no caller skipped -- the guard did nothing"


# ------------------------------------------------------------------------ I
def test_i_shutdown_stops_the_worker_promptly(env):
    """I. SIGTERM during a retry -> the worker unwinds instead of hanging."""
    rec, _server = env  # never started, so the worker is mid-retry

    th = threading.Thread(target=jr._startup_warm, daemon=True)
    th.start()
    time.sleep(0.4)
    t = time.monotonic()
    jr._stop.set()
    th.join(5.0)
    unwind = time.monotonic() - t

    assert not th.is_alive(), "worker survived shutdown"
    assert unwind < 3.0, f"worker took {unwind:.1f}s to notice shutdown"
    _tags, chat = rec.snapshot()
    assert chat == 0


# ------------------------------------------------------- readiness semantics
def test_readiness_requires_a_valid_response_not_just_a_socket(env):
    """A bound port that answers 503 is not ready.

    Ollama can accept connections before it can serve, so "the port answers" is
    too weak a signal to warm on.
    """
    rec, server = env
    rec.tags_fail_first = 2
    server.start()

    assert jr._ollama_ready() is False
    assert jr._ollama_ready() is False
    assert jr._ollama_ready() is True
