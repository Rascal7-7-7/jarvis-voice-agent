"""End-to-end test of the restart action against a DUMMY service.

The production JARVIS runtime is never targeted here, and no deadlock is
deliberately induced in it. A throwaway LaunchAgent stands in, so the thing
being proven is exactly the mechanism -- fixed launchctl argv restarts the named
service, the pid changes, and recovery is verified from the state file -- with
none of the risk of breaking real audio.

`restarter.SERVICE_LABEL` is redirected at the module level for the duration of
this test process only. That is a test-process rebinding; the constant in the
source is unchanged, which the unit tests assert separately.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import restarter                                   # noqa: E402
from state_reader import read_state                # noqa: E402

LABEL = "local.jarvis.wdtest"
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
UID = os.getuid()


def launchctl(*args, check=False):
    return subprocess.run(["/bin/launchctl", *args],
                          capture_output=True, text=True, check=check)


class RestartActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state_path = tempfile.mktemp(suffix="_wdtest_state.json")
        plist = {
            "Label": LABEL,
            "ProgramArguments": [sys.executable,
                                 os.path.join(HERE, "dummy_service.py"),
                                 cls.state_path],
            "RunAtLoad": True,
            "KeepAlive": True,
        }
        with open(PLIST, "wb") as fh:
            plistlib.dump(plist, fh)
        launchctl("bootout", f"gui/{UID}/{LABEL}")
        launchctl("bootstrap", f"gui/{UID}", PLIST)
        # Wait for the first state publication.
        for _ in range(40):
            if os.path.exists(cls.state_path):
                break
            time.sleep(0.25)
        cls._saved_label = restarter.SERVICE_LABEL
        restarter.SERVICE_LABEL = LABEL

    @classmethod
    def tearDownClass(cls):
        restarter.SERVICE_LABEL = cls._saved_label
        launchctl("bootout", f"gui/{UID}/{LABEL}")
        for p in (PLIST, cls.state_path, cls.state_path + ".tmp"):
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_dummy_service_is_up(self):
        obs, note = read_state(self.state_path)
        self.assertIsNotNone(obs, note)
        self.assertEqual(obs.state, "IDLE")

    def test_restart_changes_pid_and_verifies_recovery(self):
        before, _ = read_state(self.state_path)
        self.assertIsNotNone(before)

        result = restarter.restart_runtime(before.pid, read_state,
                                           self.state_path)
        print(f"\n  OLD_PID={result.old_pid} NEW_PID={result.new_pid} "
              f"RESTART_RETURN={result.restart_return} "
              f"RECOVERY_STATE={result.recovery_state} "
              f"RECOVERY_MS={result.recovery_ms}")
        self.assertTrue(result.ok, result.note)
        self.assertEqual(result.restart_return, 0)
        self.assertNotEqual(result.new_pid, before.pid,
                            "pid did not change -- nothing was restarted")
        self.assertEqual(result.recovery_state, "IDLE")

    def test_only_the_target_service_is_restarted(self):
        # The real runtime's pid must be untouched by a restart aimed at the
        # dummy. This is the containment property that matters most.
        real_state = os.path.expanduser(
            "~/AI-Lab/hermes-jarvis/logs/jarvis_state.json")
        real_before, _ = read_state(real_state)
        before, _ = read_state(self.state_path)
        result = restarter.restart_runtime(before.pid, read_state,
                                           self.state_path)
        self.assertTrue(result.ok, result.note)
        real_after, _ = read_state(real_state)
        if real_before is not None and real_after is not None:
            self.assertEqual(real_before.pid, real_after.pid,
                             "restarting the dummy disturbed the real runtime")

    def test_recovery_failure_is_reported_not_retried(self):
        # Point verification at a state file that never appears: the call must
        # come back RECOVERY_FAILED once, not loop kickstarting forever.
        missing = self.state_path + ".nonexistent"
        started = time.monotonic()
        result = restarter.restart_runtime(999_999, read_state, missing)
        elapsed = time.monotonic() - started
        self.assertFalse(result.ok)
        self.assertIn("RECOVERY_FAILED", result.note)
        self.assertLess(elapsed, restarter.RECOVERY_TIMEOUT_S + 15)

    def test_injection_via_state_file_is_impossible(self):
        # A hostile state file cannot reach argv: the command has no inputs.
        with open(self.state_path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"state": "LISTENING", "pid": 1,
                       "detail": "; /bin/rm -rf ~ #"}, fh)
        os.replace(self.state_path + ".tmp", self.state_path)
        obs, _ = read_state(self.state_path)
        self.assertNotIn(obs.detail, restarter._argv())
        self.assertEqual(len(restarter._argv()), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
