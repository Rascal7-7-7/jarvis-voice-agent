"""File-permission regression tests for the state file and the runtime log.

These exist because the correct mode is not something you can set once. The
state file is replaced inode-and-all on every publish, so a `chmod 600` on the
live file is undone by the next `set_state()`; the mode has to come from the
temp file the writer creates. The log is recreated by `FileHandler` if it is
deleted or rotated, at whatever the umask happens to be.

So the assertions are deliberately "after N replacements" and "after a restart",
not "right now".

Runs against a COPY of the runtime with LOG_DIR pointed at a temp directory.
The live runtime's own state file is never written by these tests.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

RUNTIME = os.path.expanduser("~/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py")
LIVE_LOG_DIR = os.path.expanduser("~/AI-Lab/hermes-jarvis/logs")


def load_runtime_with_log_dir(log_dir: str):
    """Import the real runtime module with LOG_DIR redirected.

    A copy on disk rather than monkeypatching, because LOG_DIR is used at import
    time (makedirs + chmod) and the point of the test is to exercise exactly
    that code.
    """
    src = open(RUNTIME, encoding="utf-8").read()
    needle = 'LOG_DIR = os.path.expanduser("~/AI-Lab/hermes-jarvis/logs")'
    assert needle in src, "LOG_DIR definition moved; update this test"
    patched = src.replace(needle, f"LOG_DIR = {log_dir!r}")

    path = os.path.join(log_dir, "_runtime_under_test.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(patched)

    spec = importlib.util.spec_from_file_location("jarvis_runtime_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # constants + makedirs only; no main()
    return module


def mode_of(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class StatePermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="jarvis-perm-")
        # Start deliberately wrong, so a passing test means the runtime set it
        # rather than that it was already right.
        os.chmod(cls.dir, 0o755)
        cls.mod = load_runtime_with_log_dir(cls.dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_a_log_directory_is_owner_only(self):
        self.assertEqual(mode_of(self.mod.LOG_DIR), 0o700,
                         "import did not tighten a 0755 log directory")

    def test_b_first_state_file_is_owner_only(self):
        self.mod.set_state("IDLE", "initial")
        self.assertTrue(os.path.exists(self.mod.STATE_PATH))
        self.assertEqual(mode_of(self.mod.STATE_PATH), 0o600)

    def test_c_mode_survives_100_atomic_replacements(self):
        self.mod.set_state("IDLE", "before")
        inodes = set()
        for i in range(100):
            self.mod.set_state("SPEAKING", f"reply {i}")
            inodes.add(os.stat(self.mod.STATE_PATH).st_ino)
            self.assertEqual(mode_of(self.mod.STATE_PATH), 0o600,
                             f"mode lost at replacement {i}")
        # If the inode never changed, the file was not being replaced and this
        # test proved nothing about the writer.
        self.assertGreater(len(inodes), 1,
                           "no inode change: publication was not atomic")

    def test_d_chmod_on_the_live_file_would_not_have_worked(self):
        """The reason the mode is set on the TEMP file, demonstrated."""
        self.mod.set_state("IDLE", "x")
        os.chmod(self.mod.STATE_PATH, 0o644)          # simulate a manual fix
        self.mod.set_state("SPEAKING", "y")           # one publish later
        self.assertEqual(mode_of(self.mod.STATE_PATH), 0o600,
                         "a later publish restored a permissive mode")

    def test_e_content_and_atomicity_unchanged(self):
        """The v1 fields still behave; the key set is v2.

        This assertion was written against the four-key v1 schema and was
        updated when Minimal V2 added version/turn_id/route/latency_ms. The
        change is deliberate, so the expectation moves with it -- but the
        original point stands and is still checked below: the four fields this
        test has always cared about must still round-trip, non-ASCII included,
        and no temp file may be left behind.
        """
        self.mod.set_state("SPEAKING", "こんにちは")
        with open(self.mod.STATE_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        self.assertEqual(sorted(d.keys()),
                         ["detail", "latency_ms", "pid", "route", "since",
                          "state", "turn_id", "version"])
        self.assertEqual(d["state"], "SPEAKING")
        self.assertEqual(d["detail"], "こんにちは")     # non-ASCII survives
        self.assertGreater(d["since"], 0)
        self.assertEqual(d["pid"], os.getpid())
        self.assertFalse(os.path.exists(self.mod.STATE_PATH + ".tmp"),
                         "a temp file was left behind")

    def test_f_runtime_log_is_created_owner_only(self):
        log = os.path.join(self.mod.LOG_DIR, "jarvis_runtime.log")
        if os.path.exists(log):
            os.unlink(log)
        self.mod._setup_logging()
        self.assertTrue(os.path.exists(log), "the log was not created")
        self.assertEqual(mode_of(log), 0o600)

    def test_g_a_permissive_existing_log_is_tightened(self):
        """A log created before this change must be fixed, not left as it is."""
        log = os.path.join(self.mod.LOG_DIR, "jarvis_runtime.log")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("pre-existing line\n")
        os.chmod(log, 0o644)
        self.mod._setup_logging()
        self.assertEqual(mode_of(log), 0o600)
        # And the existing content is still there: O_CREAT|O_APPEND, not O_TRUNC.
        with open(log, encoding="utf-8") as fh:
            self.assertIn("pre-existing line", fh.read())


class LiveDeploymentTests(unittest.TestCase):
    """Assertions about the real installation. Read-only."""

    def test_live_log_directory_is_owner_only(self):
        self.assertEqual(mode_of(LIVE_LOG_DIR), 0o700)

    def test_live_state_and_log_are_owner_only(self):
        for name in ("jarvis_state.json", "jarvis_runtime.log"):
            path = os.path.join(LIVE_LOG_DIR, name)
            if os.path.exists(path):
                self.assertEqual(mode_of(path), 0o600, f"{name} is not 0600")

    def test_live_files_are_owned_by_this_user(self):
        st = os.stat(LIVE_LOG_DIR)
        self.assertEqual(st.st_uid, os.getuid())


if __name__ == "__main__":
    unittest.main(verbosity=2)
