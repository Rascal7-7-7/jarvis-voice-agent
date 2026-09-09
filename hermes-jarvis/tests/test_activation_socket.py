"""Security and behaviour tests for the manual activation socket.

Every test runs against an isolated socket under a temporary directory, so
nothing here touches the production path or weakens the production socket's
ownership or mode. That is the point of §10's "wrong uid test ... isolated test
socket": the interesting cases are refusals, and a refusal test that needs the
real socket loosened is not worth running.
"""

from __future__ import annotations

import ast
import os
import pathlib
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.expanduser("~/AI-Lab/hermes-jarvis/bin"))

import activation_socket as act  # noqa: E402


@pytest.fixture
def shortdir():
    """A short-lived directory with a SHORT path.

    pytest's tmp_path is far too long for an AF_UNIX address: macOS caps
    sun_path at 104 bytes and a tmp_path is routinely past that, which fails as
    "AF_UNIX path too long" rather than as anything to do with the code.
    """
    d = tempfile.mkdtemp(prefix="jact", dir="/tmp")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def server(shortdir):
    """A bound server on an isolated path, plus a list of activations seen."""
    fired: list[float] = []
    stop = threading.Event()
    srv = act.ActivationServer(os.path.join(shortdir, "s", "a.sock"),
                              lambda: fired.append(time.monotonic()), stop)
    srv.bind()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, fired
    stop.set()
    t.join(timeout=3)


def send(path: str, payload: bytes) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        s.connect(path)
        s.sendall(payload)
        # Give the server a moment to read before the socket closes under it.
        time.sleep(0.15)


def wait_for(pred, timeout=3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# --- the happy path -------------------------------------------------------

def test_activate_fires_the_callback(server):
    srv, fired = server
    send(srv.path, b"ACTIVATE")
    assert wait_for(lambda: len(fired) == 1)
    assert srv.counters()["accepted"] == 1


def test_a_trailing_newline_is_accepted(server):
    srv, fired = server
    send(srv.path, b"ACTIVATE\n")
    assert wait_for(lambda: len(fired) == 1)


def test_each_activation_fires_exactly_once(server):
    srv, fired = server
    for _ in range(10):
        send(srv.path, b"ACTIVATE")
    assert wait_for(lambda: len(fired) == 10)
    time.sleep(0.3)
    assert len(fired) == 10, "an activation fired more than one turn"
    assert srv.counters() == {"accepted": 10, "rejected_payload": 0,
                              "rejected_uid": 0, "errors": 0}


# --- filesystem posture ---------------------------------------------------

def test_socket_is_0600_inside_a_0700_directory(server):
    srv, _ = server
    st = os.lstat(srv.path)
    assert stat.S_ISSOCK(st.st_mode)
    assert stat.S_IMODE(st.st_mode) == 0o600, oct(stat.S_IMODE(st.st_mode))
    d = os.stat(os.path.dirname(srv.path))
    assert stat.S_IMODE(d.st_mode) == 0o700, oct(stat.S_IMODE(d.st_mode))


def test_a_stale_socket_is_replaced(shortdir):
    path = os.path.join(shortdir, "a.sock")
    old = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old.bind(path)
    old.close()
    assert os.path.exists(path)
    srv = act.ActivationServer(path, lambda: None)
    srv.bind()                      # must not raise
    srv.close()


def test_a_regular_file_at_the_path_is_refused_not_deleted(shortdir):
    """A non-socket at the path is someone else's file. Do not remove it."""
    path = pathlib.Path(shortdir) / "a.sock"
    path.write_text("not a socket")
    srv = act.ActivationServer(str(path), lambda: None)
    with pytest.raises(RuntimeError, match="not a socket"):
        srv.bind()
    assert path.read_text() == "not a socket", "the file was destroyed"


def test_a_symlink_at_the_path_is_refused(shortdir):
    """lstat, not stat: the link must be seen as a link, not followed."""
    target = pathlib.Path(shortdir) / "t.txt"
    target.write_text("victim")
    link = pathlib.Path(shortdir) / "a.sock"
    os.symlink(target, link)
    srv = act.ActivationServer(str(link), lambda: None)
    with pytest.raises(RuntimeError, match="not a socket"):
        srv.bind()
    assert target.read_text() == "victim", "the symlink was followed"


def test_the_socket_is_removed_on_close(shortdir):
    path = os.path.join(shortdir, "a.sock")
    srv = act.ActivationServer(path, lambda: None)
    srv.bind()
    srv.close()
    assert not os.path.exists(path)


# --- payload refusals -----------------------------------------------------

BAD_PAYLOADS = [
    b"",
    b"activate",                       # case matters: == not casefold
    b"ACTIVATE ; rm -rf /",
    b"ACTIVATEX",
    b"ACTIVATE\x00EXTRA",
    b'{"cmd":"ACTIVATE"}',
    b'{"cmd":"shell","args":["/bin/sh"]}',
    b"PLAY",
    b"QUIT",
    b"ACTIVATE" * 4,                   # longer than MAX_READ
    b"\xff\xfe\xfd\xfc",
    b"/etc/passwd",
    b"ACTIVATE\r\nACTIVATE",           # two verbs in one write
]


@pytest.mark.parametrize("payload", BAD_PAYLOADS)
def test_unknown_payloads_start_no_turn(server, payload):
    srv, fired = server
    send(srv.path, payload)
    time.sleep(0.35)
    assert fired == [], f"{payload!r} started a turn"
    assert srv.counters()["rejected_payload"] >= 1
    assert srv.counters()["accepted"] == 0


def test_a_client_that_writes_nothing_starts_no_turn(server):
    srv, fired = server
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        s.connect(srv.path)
    time.sleep(0.35)
    assert fired == []


def test_only_max_read_bytes_are_ever_read():
    """A flood must not be buffered. MAX_READ is the whole defence."""
    assert act.MAX_READ <= 32
    assert len(act.ACTIVATE) < act.MAX_READ


def test_the_server_survives_a_flood_of_bad_payloads(server):
    srv, fired = server
    for i in range(60):
        try:
            send(srv.path, b"X" * (i % 20 + 1))
        except OSError:
            pass
    assert fired == []
    # Still serving afterwards -- a refusal must not be a denial of service.
    send(srv.path, b"ACTIVATE")
    assert wait_for(lambda: len(fired) == 1)


# --- peer credentials -----------------------------------------------------

def test_peer_uid_reads_our_own_uid(server):
    """The check has to actually work, or it is decoration."""
    srv, _ = server
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        s.connect(srv.path)
        assert act.peer_uid(s) == os.getuid()


def test_a_uid_that_is_not_ours_is_refused(shortdir, monkeypatch):
    """Simulated, because becoming another uid needs privileges we will not take.

    The refusal path is what matters and it is exercised for real: the server's
    own comparison runs against a uid it believes is different.
    """
    fired: list[int] = []
    stop = threading.Event()
    srv = act.ActivationServer(os.path.join(shortdir, "a.sock"),
                              lambda: fired.append(1), stop)
    srv.bind()
    monkeypatch.setattr(act, "peer_uid", lambda conn: os.getuid() + 1)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        send(srv.path, b"ACTIVATE")
        time.sleep(0.35)
        assert fired == [], "a foreign uid started a turn"
        assert srv.counters()["rejected_uid"] == 1
        assert srv.counters()["accepted"] == 0
    finally:
        stop.set()
        t.join(timeout=3)


def test_unreadable_peer_credentials_are_refused(shortdir, monkeypatch):
    """None from peer_uid is a refusal, not an unknown-so-allow."""
    fired: list[int] = []
    stop = threading.Event()
    srv = act.ActivationServer(os.path.join(shortdir, "a.sock"),
                              lambda: fired.append(1), stop)
    srv.bind()
    monkeypatch.setattr(act, "peer_uid", lambda conn: None)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        send(srv.path, b"ACTIVATE")
        time.sleep(0.35)
        assert fired == []
        assert srv.counters()["rejected_uid"] == 1
    finally:
        stop.set()
        t.join(timeout=3)


# --- what must NOT be in this module -------------------------------------

def test_the_module_imports_nothing_that_could_shell_out_or_record():
    """Checked with the AST, not with a text search.

    A text search over the source also matches the module docstring, which
    NAMES these modules precisely to say it does not import them -- so the
    grep-style version of this test failed on its own explanation. The parsed
    import list cannot be fooled that way.
    """
    tree = ast.parse(open(act.__file__).read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("subprocess", "sounddevice", "voice_mode",
                      "shared_audio", "numpy", "shlex", "pty"):
        assert forbidden not in imported, f"{forbidden} is imported"


def test_the_module_calls_no_shell_or_eval_primitive():
    """No call to eval/exec/system/popen anywhere, by AST rather than by grep."""
    tree = ast.parse(open(act.__file__).read())
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                called.add(f.id)
            elif isinstance(f, ast.Attribute):
                called.add(f.attr)
    for forbidden in ("eval", "exec", "compile", "system", "popen",
                      "spawn", "fork", "execv", "run"):
        assert forbidden not in called, f"{forbidden}() is called"


def test_only_one_socket_is_created_and_it_is_af_unix_stream():
    """Exactly one socket() call, with AF_UNIX and SOCK_STREAM."""
    tree = ast.parse(open(act.__file__).read())
    creations = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "socket"]
    assert len(creations) == 1, f"{len(creations)} socket() calls"
    args = [ast.unparse(a) for a in creations[0].args]
    assert args == ["socket.AF_UNIX", "socket.SOCK_STREAM"], args


def test_no_internet_address_family_appears_anywhere():
    tree = ast.parse(open(act.__file__).read())
    names = {ast.unparse(n) for n in ast.walk(tree)
             if isinstance(n, ast.Attribute)}
    for forbidden in ("socket.AF_INET", "socket.AF_INET6"):
        assert forbidden not in names, f"{forbidden} appears"
