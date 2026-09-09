"""Manual activation over an AF_UNIX socket: one verb, no parameters.

WHY THIS EXISTS. A 150-second observation with the runtime IDLE and the wake
listener armed produced no activation at all, matching the device A/B result of
0/5 fires at a median score of 0.0011. Until the wake model is fixed there has
to be a second way to start a turn, or JARVIS is not usable day to day.

WHAT CROSSES THE SOCKET. The eight bytes ``ACTIVATE``. Nothing else is
understood and nothing else is read: no command text, no arguments, no paths, no
JSON. The spoken command is still captured by the microphone exactly as it is
after a wake, so the trigger carries no content and widens no surface. This is
the same decision the ACK helper made -- fixed verbs, no parameterised command --
and for the same reason: a trigger with a payload is a trigger that has to be
validated forever.

WHERE IT JOINS. The caller passes the runtime's existing ``on_wake`` callable.
An activation therefore enters the identical turn: capture -> STT -> dispatch ->
security gate -> backend -> TTS. Nothing is skipped, no new code path is
reachable, and the router and gate cannot be bypassed because this module knows
nothing about them.

WHAT IT DOES NOT TOUCH. There is no import of sounddevice, voice_mode or
shared_audio here, and no way to reach one. The code that opens a microphone
stream is unreachable from this thread, which is why PA_OPEN_COUNT and
PA_START_COUNT stay at 1 -- not because a counter is guarded, but because no
second open exists.
"""

from __future__ import annotations

import logging
import os
import socket
import stat
import struct
import threading
from typing import Callable

logger = logging.getLogger("jarvis")

# The only accepted payload. Compared with ==, never parsed.
ACTIVATE = b"ACTIVATE"

# Enough for the verb and a newline, and small enough that a client which opens
# the socket and writes forever cannot grow this process.
MAX_READ = 16

SOCKET_MODE = 0o600
DIR_MODE = 0o700

# macOS peer credentials. Linux's SO_PEERCRED does not exist here; the BSD
# equivalent is LOCAL_PEERCRED on SOL_LOCAL, which returns a struct xucred:
#
#   u_int cr_version;  uid_t cr_uid;  short cr_ngroups;  gid_t cr_groups[16];
#
# Only cr_version and cr_uid are needed, and the version is checked so that a
# layout change is noticed rather than silently reinterpreted as a uid.
SOL_LOCAL = 0
LOCAL_PEERCRED = 0x001
XUCRED_VERSION = 0
_XUCRED_HEAD = struct.Struct("=II")          # cr_version, cr_uid


def peer_uid(conn: socket.socket) -> int | None:
    """The connecting process's effective uid, or None if it cannot be read.

    None is a refusal, never a pass: a connection whose owner cannot be
    established is dropped by the caller.
    """
    try:
        raw = conn.getsockopt(SOL_LOCAL, LOCAL_PEERCRED, 128)
    except OSError:
        return None
    if len(raw) < _XUCRED_HEAD.size:
        return None
    version, uid = _XUCRED_HEAD.unpack_from(raw, 0)
    if version != XUCRED_VERSION:
        logger.warning("activation: unexpected xucred version %d; refusing",
                       version)
        return None
    return int(uid)


def _clear_stale(path: str) -> None:
    """Remove a leftover socket, and only ever a socket this user owns.

    lstat, not stat: a symlink at this path must be seen as a symlink and
    refused rather than followed to whatever it points at.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode):
        raise RuntimeError(
            f"activation: {path} exists and is not a socket; refusing to remove it")
    if st.st_uid != os.getuid():
        raise RuntimeError(
            f"activation: {path} is owned by uid {st.st_uid}, not {os.getuid()}")
    os.unlink(path)


def _prepare_dir(path: str) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    st = os.stat(d)
    if st.st_uid != os.getuid():
        raise RuntimeError(f"activation: {d} is owned by uid {st.st_uid}")
    os.chmod(d, DIR_MODE)


class ActivationServer:
    """Serves ``ACTIVATE`` on an AF_UNIX socket and calls one callback.

    Counters are the whole diagnostic surface. A rejected payload is counted and
    dropped, never echoed and never logged verbatim at info level, because the
    bytes came from outside and a log is a place things get read back.
    """

    def __init__(self, path: str, on_activate: Callable[[], None],
                 stop: threading.Event | None = None):
        self.path = path
        self._on_activate = on_activate
        self._stop = stop or threading.Event()
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self.accepted = 0
        self.rejected_payload = 0
        self.rejected_uid = 0
        self.errors = 0

    def counters(self) -> dict:
        with self._lock:
            return {"accepted": self.accepted,
                    "rejected_payload": self.rejected_payload,
                    "rejected_uid": self.rejected_uid,
                    "errors": self.errors}

    def bind(self) -> None:
        _prepare_dir(self.path)
        _clear_stale(self.path)
        # AF_UNIX / SOCK_STREAM only. No AF_INET socket is created anywhere in
        # this module, so there is nothing for an off-host client to reach.
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # bind() honours the umask, which is not ours to assume, so the mode is
        # set explicitly straight afterwards. The window between the two is why
        # the directory is 0700: nothing else can see into it in the meantime.
        s.bind(self.path)
        os.chmod(self.path, SOCKET_MODE)
        s.listen(4)
        s.settimeout(0.5)
        self._sock = s
        logger.info("activation socket up: %s mode=%o dir=%o",
                    self.path, SOCKET_MODE, DIR_MODE)

    def serve_forever(self) -> None:
        if self._sock is None:
            self.bind()
        assert self._sock is not None
        try:
            while not self._stop.is_set():
                try:
                    conn, _ = self._sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    with self._lock:
                        self.errors += 1
                    continue
                with conn:
                    try:
                        self._handle(conn)
                    except Exception:
                        with self._lock:
                            self.errors += 1
                        logger.exception("activation: connection failed")
        finally:
            self.close()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(2.0)

        uid = peer_uid(conn)
        if uid is None or uid != os.getuid():
            with self._lock:
                self.rejected_uid += 1
            logger.warning("activation: refused a connection from uid %s", uid)
            return

        try:
            data = conn.recv(MAX_READ)
        except socket.timeout:
            with self._lock:
                self.rejected_payload += 1
            return

        # strip() only, then an equality test. No parsing, no decoding to text,
        # no length-prefixed framing to get wrong.
        if data.strip() != ACTIVATE:
            with self._lock:
                self.rejected_payload += 1
            logger.warning("activation: refused an unknown payload (%d bytes)",
                           len(data))
            return

        with self._lock:
            self.accepted += 1
        logger.info("activation: ACTIVATE accepted (manual trigger)")
        self._on_activate()

    def close(self) -> None:
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        try:
            _clear_stale(self.path)
        except Exception:
            pass


def default_socket_path() -> str:
    return os.path.expanduser("~/.hermes/runtime/jarvis-activate.sock")


def serve(on_activate: Callable[[], None], stop: threading.Event,
          path: str | None = None) -> ActivationServer | None:
    """Bind and serve on a daemon thread. Returns None if binding failed.

    A failure here must never take the runtime down: manual activation is the
    fallback, and losing the fallback is not a reason to lose the wake word too.
    """
    srv = ActivationServer(path or default_socket_path(), on_activate, stop)
    try:
        srv.bind()
    except Exception:
        logger.exception("activation: could not bind %s; manual activation "
                         "is unavailable this run", srv.path)
        return None
    threading.Thread(target=srv.serve_forever, name="activation",
                     daemon=True).start()
    return srv
