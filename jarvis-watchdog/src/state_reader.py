"""Read and validate jarvis_state.json. READ ONLY -- nothing here ever writes.

The file is produced by another process and can be truncated mid-write, replaced
under us, or simply absent. Every one of those must come back as "unusable",
which the decision engine treats as "do nothing" -- never as evidence of a hang.
"""
from __future__ import annotations

import json
import os

from watchdog_core import Observation

PRODUCTION_STATE_PATH = os.path.expanduser(
    "~/AI-Lab/hermes-jarvis/logs/jarvis_state.json")

MAX_FILE_BYTES = 64 * 1024
MAX_STATE_CHARS = 64
MAX_DETAIL_CHARS = 200


class ReadProblem(Exception):
    """The file could not be turned into a trustworthy Observation."""


def read_state(path: str) -> tuple[Observation | None, str]:
    """Return (observation, note). A `None` observation is always safe to ignore.

    Errors are returned rather than raised so the caller's loop cannot be taken
    down by a malformed file.
    """
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        return None, "state file missing"
    except OSError as e:
        return None, f"stat failed: {e}"

    if size > MAX_FILE_BYTES:
        return None, f"state file too large ({size} bytes)"

    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        return None, f"read failed: {e}"

    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        # The runtime publishes atomically (tmp + rename), so a torn read should
        # not happen -- but a corrupt file must not be able to trigger anything.
        return None, f"invalid JSON: {type(e).__name__}"

    if not isinstance(obj, dict):
        return None, "state file is not an object"

    state = obj.get("state")
    if not isinstance(state, str) or not state or len(state) > MAX_STATE_CHARS:
        return None, "missing or invalid state"

    pid = obj.get("pid")
    # bool is an int subclass; a JSON `true` must not become pid 1.
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None, "missing or invalid pid"

    since = obj.get("since")
    if not isinstance(since, (int, float)) or isinstance(since, bool):
        since = None

    detail = obj.get("detail")
    detail = detail[:MAX_DETAIL_CHARS] if isinstance(detail, str) else ""

    # bool is an int subclass in Python, so `true` would otherwise read as
    # version 1 / turn 1. Both are excluded explicitly, the same way pid is.
    version = obj.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        version = None

    # Positive integers only. 0, negatives, floats, strings, objects and arrays
    # all become None -- and None on a v2 payload is what makes
    # Observation.identity() refuse to guess rather than assume continuity.
    turn_id = obj.get("turn_id")
    if isinstance(turn_id, bool) or not isinstance(turn_id, int) or turn_id <= 0:
        turn_id = None

    return Observation(state=state, pid=pid, since=since, detail=detail,
                       turn_id=turn_id, version=version), "ok"


def process_exists(pid: int) -> bool:
    """kill(pid, 0): EPERM means it exists but is not ours; ESRCH means gone."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:       # ESRCH
        return False
    except PermissionError:          # EPERM -- alive, different owner
        return True
    except OSError:
        return False
    return True
