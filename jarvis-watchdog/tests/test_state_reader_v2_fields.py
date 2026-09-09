"""Validation of the two v2 fields the watchdog now reads.

`turn_id` decides whether successive observations are the same turn, so a value
that is not a positive integer must become None rather than something that
happens to compare equal. `bool` is the one that bites: it is an int subclass in
Python, so `true` would otherwise read as turn 1 and version 1 -- the same trap
`pid` already guards against, and the same class of bug as the HUD's `raw is
Bool` NSNumber mistake.

Reading only. Every case writes a file into a temp directory and reads it back
through the production function.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from state_reader import read_state  # noqa: E402


def write(tmp_path, payload) -> str:
    p = tmp_path / "jarvis_state.json"
    p.write_text(json.dumps(payload))
    return str(p)


def base(**over):
    d = {"state": "LISTENING", "since": 1000.0, "detail": "capturing utterance",
         "pid": 4242, "version": 2, "turn_id": 7}
    d.update(over)
    return d


def test_a_well_formed_v2_payload_reads_both_fields(tmp_path):
    obs, note = read_state(write(tmp_path, base()))
    assert note == "ok"
    assert obs is not None
    assert obs.version == 2
    assert obs.turn_id == 7
    assert obs.identity() == ("v2", 4242, 7)


@pytest.mark.parametrize("bad", [
    0, -1, True, False, "7", 7.0, 7.5, None, [7], {"n": 7}, "",
])
def test_an_invalid_turn_id_becomes_none(tmp_path, bad):
    obs, note = read_state(write(tmp_path, base(turn_id=bad)))
    assert note == "ok", "a bad turn_id must not make the whole file unusable"
    assert obs is not None
    assert obs.turn_id is None, f"{bad!r} was accepted as {obs.turn_id!r}"


@pytest.mark.parametrize("bad", [0, -1, True, False, "2", 2.0, None, [2], {}])
def test_an_invalid_version_becomes_none(tmp_path, bad):
    obs, note = read_state(write(tmp_path, base(version=bad)))
    assert note == "ok"
    assert obs is not None
    assert obs.version is None


def test_true_does_not_become_turn_one(tmp_path):
    """bool is an int subclass; `true` must not compare equal to turn 1."""
    obs, _ = read_state(write(tmp_path, base(turn_id=True, version=True)))
    assert obs is not None
    assert obs.turn_id is None
    assert obs.version is None


def test_a_missing_turn_id_on_a_v2_payload_yields_no_identity(tmp_path):
    """This is the shape that makes the decision engine hold."""
    d = base()
    del d["turn_id"]
    obs, _ = read_state(write(tmp_path, d))
    assert obs is not None
    assert obs.version == 2
    assert obs.turn_id is None
    assert obs.identity() is None


def test_a_v1_payload_identifies_by_pid_state_and_since(tmp_path):
    d = {"state": "LISTENING", "since": 1234.5, "detail": "x", "pid": 99}
    obs, _ = read_state(write(tmp_path, d))
    assert obs is not None
    assert obs.version is None
    assert obs.turn_id is None
    assert obs.identity() == ("v1", 99, "LISTENING", 1234.5)


def test_a_future_version_is_treated_as_v2(tmp_path):
    """A newer runtime still publishes turn_id; use it."""
    obs, _ = read_state(write(tmp_path, base(version=99)))
    assert obs is not None
    assert obs.identity() == ("v2", 4242, 7)


def test_the_v2_fields_cannot_rescue_an_invalid_pid(tmp_path):
    """pid remains mandatory: no pid, no observation."""
    obs, note = read_state(write(tmp_path, base(pid=0)))
    assert obs is None
    assert "pid" in note


def test_malformed_json_is_still_unusable(tmp_path):
    p = tmp_path / "jarvis_state.json"
    p.write_text('{"state": "LISTENING", "turn_id": 1,')
    obs, note = read_state(str(p))
    assert obs is None
    assert "invalid JSON" in note


def test_nothing_here_writes_to_the_file(tmp_path):
    """The reader is read-only; reading must not change the bytes on disk."""
    path = write(tmp_path, base())
    before = open(path, "rb").read()
    stat_before = os.stat(path)
    read_state(path)
    assert open(path, "rb").read() == before
    assert os.stat(path).st_mtime == stat_before.st_mtime
