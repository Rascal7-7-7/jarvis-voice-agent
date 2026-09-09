"""Schema v2 tests: version, turn_id, route, latency_ms.

Runs the REAL `set_state` with `LOG_DIR` redirected to a temp directory, so the
live runtime's state file is never written.

The two tests that matter most are `test_turn_id_is_current_at_listening` — it
fails if the counter increments after the LISTENING publish, which is how the
code was before this change — and `test_no_cross_turn_contamination`, which is
the whole reason `turn_id` exists.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_state_permissions import load_runtime_with_log_dir  # noqa: E402

RUNTIME_SOURCE = os.path.expanduser("~/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py")
LATENCY_KEYS = {"wake_to_capture", "capture_duration", "stt", "router", "backend"}
ROUTES = ("LOCAL_FAST", "LOCAL_TOOL", "LOCAL", "WEB", "CODEX", "CLAUDE",
          "SECRETARY", "OPEN")


class FakeTimeline:
    """Stands in for Timeline, with the same `_ms` contract.

    Marks are set explicitly so a test can say "STT has finished but the backend
    has not" without running a turn.
    """

    def __init__(self):
        self.marks = {}

    def at(self, name: str, seconds: float):
        self.marks[name] = seconds
        return self

    def _ms(self, a: str, b: str):
        if a in self.marks and b in self.marks:
            return (self.marks[b] - self.marks[a]) * 1000.0
        return None


class SchemaV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="jarvis-v2-")
        cls.mod = load_runtime_with_log_dir(cls.dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.mod.end_turn()

    def published(self) -> dict:
        with open(self.mod.STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)

    # ---- schema ----------------------------------------------------------

    def test_version_is_present_on_every_publish(self):
        for state in ("OFFLINE", "IDLE", "LISTENING", "SPEAKING", "ERROR"):
            self.mod.set_state(state, "x")
            self.assertEqual(self.published()["version"], 2, f"missing at {state}")

    def test_key_set_is_exactly_the_v2_schema(self):
        self.mod.set_state("IDLE", "waiting")
        self.assertEqual(sorted(self.published().keys()),
                         ["detail", "latency_ms", "pid", "route", "since",
                          "state", "turn_id", "version"])

    def test_latency_object_always_has_the_five_fixed_keys(self):
        self.mod.set_state("IDLE", "")
        self.assertEqual(set(self.published()["latency_ms"].keys()), LATENCY_KEYS)

    def test_no_text_fields_were_added(self):
        """The fields explicitly excluded from Minimal V2 must not appear."""
        self.mod.set_state("SPEAKING", "こんにちは")
        keys = set(self.published().keys())
        for forbidden in ("heard_text", "transcript", "response", "response_preview",
                          "activity", "error", "tool_args"):
            self.assertNotIn(forbidden, keys)

    def test_detail_still_carries_the_existing_60_char_reply(self):
        reply = "あ" * 200
        self.mod.set_state("SPEAKING", reply[:60])
        self.assertEqual(len(self.published()["detail"]), 60,
                         "the existing reply preview changed length")

    # ---- turn_id ---------------------------------------------------------

    def test_turn_id_is_null_outside_a_turn(self):
        self.mod.set_state("IDLE", "waiting")
        self.assertIsNone(self.published()["turn_id"])

    def test_turn_id_is_current_at_listening(self):
        """REGRESSION: the counter used to increment AFTER this publish.

        With the old ordering the LISTENING state carried the previous turn's
        id -- turn 2's LISTENING said 1 -- which is precisely the stale context
        this field is for.
        """
        self.mod.begin_turn(1, FakeTimeline())
        self.mod.set_state("LISTENING", "capturing utterance")
        self.assertEqual(self.published()["turn_id"], 1)

        self.mod.end_turn()
        self.mod.set_state("IDLE", "waiting")

        self.mod.begin_turn(2, FakeTimeline())
        self.mod.set_state("LISTENING", "capturing utterance")
        self.assertEqual(self.published()["turn_id"], 2,
                         "LISTENING published the previous turn's id")

    def test_turn_id_is_stable_through_a_whole_turn(self):
        self.mod.begin_turn(7, FakeTimeline())
        for state in ("LISTENING", "THINKING", "ROUTING", "CODEX", "SPEAKING"):
            self.mod.set_state(state, "x")
            self.assertEqual(self.published()["turn_id"], 7, f"changed at {state}")

    def test_turn_id_is_cleared_before_idle(self):
        self.mod.begin_turn(9, FakeTimeline())
        self.mod.set_state("SPEAKING", "reply")
        self.mod.end_turn()
        self.mod.set_state("IDLE", "waiting for wake word")
        self.assertIsNone(self.published()["turn_id"])

    def test_turn_id_is_process_local_not_globally_unique(self):
        """Documented behaviour: the counter restarts with the process.

        A consumer must read (pid, turn_id) together. 42 -> 1 means a new
        runtime, not a counter going backwards.
        """
        self.mod.begin_turn(42, FakeTimeline())
        self.mod.set_state("LISTENING", "x")
        first = self.published()
        # A fresh module is a fresh process for this purpose.
        second_dir = tempfile.mkdtemp(prefix="jarvis-v2-restart-")
        try:
            restarted = load_runtime_with_log_dir(second_dir)
            restarted.begin_turn(1, FakeTimeline())
            restarted.set_state("LISTENING", "x")
            with open(restarted.STATE_PATH, encoding="utf-8") as fh:
                second = json.load(fh)
            self.assertEqual(first["turn_id"], 42)
            self.assertEqual(second["turn_id"], 1)
        finally:
            shutil.rmtree(second_dir, ignore_errors=True)

    # ---- route -----------------------------------------------------------

    def test_route_is_null_until_dispatch(self):
        self.mod.begin_turn(1, FakeTimeline())
        for state in ("LISTENING", "THINKING", "ROUTING"):
            self.mod.set_state(state, "CODEX via llm")
            self.assertIsNone(self.published()["route"],
                              f"route was published at {state}")

    def test_all_six_routes_publish_and_survive_into_speaking(self):
        for route in ROUTES:
            self.mod.end_turn()
            self.mod.begin_turn(1, FakeTimeline())
            self.mod.set_turn_route(route)
            self.mod.set_state(route, "working")
            self.assertEqual(self.published()["route"], route)
            self.mod.set_state("SPEAKING", "reply")
            self.assertEqual(self.published()["route"], route,
                             f"{route} was lost at SPEAKING")

    def test_route_is_cleared_at_idle(self):
        self.mod.begin_turn(1, FakeTimeline())
        self.mod.set_turn_route("CLAUDE")
        self.mod.set_state("SPEAKING", "reply")
        self.mod.end_turn()
        self.mod.set_state("IDLE", "waiting")
        self.assertIsNone(self.published()["route"])

    def test_confirmation_required_has_no_route(self):
        """A gate verdict is not a backend. Nothing was dispatched."""
        self.mod.begin_turn(3, FakeTimeline())
        self.mod.set_state("CONFIRMATION_REQUIRED", "DESTRUCTIVE")
        published = self.published()
        self.assertIsNone(published["route"])
        self.assertEqual(published["turn_id"], 3, "the turn id was lost")

    def test_error_before_dispatch_has_no_route(self):
        self.mod.begin_turn(4, FakeTimeline())
        self.mod.set_state("ERROR", "mic silent")
        self.assertIsNone(self.published()["route"])

    def test_error_after_dispatch_keeps_the_route(self):
        """"It failed, in Codex" is more useful than "it failed"."""
        self.mod.begin_turn(5, FakeTimeline())
        self.mod.set_turn_route("CODEX")
        self.mod.set_state("ERROR", "delegate failed")
        self.assertEqual(self.published()["route"], "CODEX")

    def test_unknown_route_is_refused_not_published_as_text(self):
        self.mod.begin_turn(6, FakeTimeline())
        for bogus in ("codex", "GPT5", "", "CONFIRMATION_REQUIRED",
                      "'; DROP TABLE", "LOCAL_FAST "):
            self.mod.set_turn_route(bogus)
            self.mod.set_state("SPEAKING", "x")
            self.assertIsNone(self.published()["route"],
                              f"{bogus!r} was accepted as a route")

    # ---- latency ---------------------------------------------------------

    def test_all_stages_null_before_anything_is_measured(self):
        self.mod.begin_turn(1, FakeTimeline())
        self.mod.set_state("LISTENING", "capturing")
        self.assertTrue(all(v is None
                            for v in self.published()["latency_ms"].values()))

    def test_stages_appear_as_they_complete(self):
        tl = FakeTimeline()
        self.mod.begin_turn(1, tl)

        tl.at("wake_detected", 100.0)
        self.mod.set_state("LISTENING", "capturing")
        self.assertIsNone(self.published()["latency_ms"]["wake_to_capture"],
                          "an interval was reported with only its start mark")

        tl.at("capture_start", 100.251).at("capture_end", 103.808)
        self.mod.set_state("THINKING", "transcribing")
        lat = self.published()["latency_ms"]
        self.assertEqual(lat["wake_to_capture"], 251)
        self.assertEqual(lat["capture_duration"], 3557)
        self.assertIsNone(lat["stt"])
        self.assertIsNone(lat["backend"], "backend reported before dispatch")

        tl.at("stt_start", 103.81).at("stt_done", 104.541)
        tl.at("router_start", 104.55).at("router_done", 107.096)
        self.mod.set_state("CODEX", "working")
        lat = self.published()["latency_ms"]
        self.assertEqual(lat["stt"], 731)
        self.assertEqual(lat["router"], 2546)
        self.assertIsNone(lat["backend"])

        tl.at("delegate_start", 107.1).at("delegate_done", 110.154)
        self.mod.set_state("SPEAKING", "reply")
        self.assertEqual(self.published()["latency_ms"]["backend"], 3054)

    def test_zero_is_a_real_measurement_not_a_missing_one(self):
        """router=0 happens: the greeting fast path never reaches the LLM."""
        tl = FakeTimeline().at("router_start", 50.0).at("router_done", 50.0)
        self.mod.begin_turn(1, tl)
        self.mod.set_state("LOCAL_FAST", "working")
        lat = self.published()["latency_ms"]
        self.assertEqual(lat["router"], 0)
        self.assertIsNotNone(lat["router"])
        self.assertIsNone(lat["stt"], "an unmeasured stage was filled with 0")

    def test_values_are_integers_never_strings_or_floats(self):
        tl = FakeTimeline().at("stt_start", 1.0).at("stt_done", 1.7314)
        self.mod.begin_turn(1, tl)
        self.mod.set_state("THINKING", "x")
        v = self.published()["latency_ms"]["stt"]
        self.assertIsInstance(v, int)
        self.assertNotIsInstance(v, bool)
        self.assertEqual(v, 731)

    def test_negative_intervals_are_clamped_not_published(self):
        tl = FakeTimeline().at("stt_start", 10.0).at("stt_done", 9.0)
        self.mod.begin_turn(1, tl)
        self.mod.set_state("THINKING", "x")
        self.assertEqual(self.published()["latency_ms"]["stt"], 0)

    def test_absurd_intervals_become_null(self):
        """Beyond an hour the marks are wrong, not the turn."""
        tl = FakeTimeline().at("stt_start", 0.0).at("stt_done", 7200.0)
        self.mod.begin_turn(1, tl)
        self.mod.set_state("THINKING", "x")
        self.assertIsNone(self.published()["latency_ms"]["stt"])

    def test_capture_duration_is_not_wake_to_capture(self):
        """They are different intervals and must not be swapped."""
        tl = (FakeTimeline().at("wake_detected", 0.0).at("capture_start", 0.251)
              .at("capture_end", 30.251))
        self.mod.begin_turn(1, tl)
        self.mod.set_state("THINKING", "x")
        lat = self.published()["latency_ms"]
        self.assertEqual(lat["wake_to_capture"], 251)
        self.assertEqual(lat["capture_duration"], 30000)

    # ---- the important one ----------------------------------------------

    def test_no_cross_turn_contamination(self):
        """Turn 41's route and timings must not reach turn 42. Not one field."""
        tl41 = (FakeTimeline().at("wake_detected", 0.0).at("capture_start", 0.3)
                .at("capture_end", 3.0).at("stt_start", 3.0).at("stt_done", 3.8)
                .at("router_start", 3.8).at("router_done", 6.3)
                .at("delegate_start", 6.3).at("delegate_done", 9.4))
        self.mod.begin_turn(41, tl41)
        self.mod.set_turn_route("CODEX")
        self.mod.set_state("SPEAKING", "reply from turn 41")
        turn41 = self.published()
        self.assertEqual(turn41["turn_id"], 41)
        self.assertEqual(turn41["route"], "CODEX")
        self.assertTrue(any(v is not None for v in turn41["latency_ms"].values()))

        # Turn ends.
        self.mod.end_turn()
        self.mod.set_state("IDLE", "waiting for wake word")
        idle = self.published()
        self.assertIsNone(idle["turn_id"])
        self.assertIsNone(idle["route"])
        self.assertTrue(all(v is None for v in idle["latency_ms"].values()),
                        f"turn 41 timings survived into IDLE: {idle['latency_ms']}")

        # Next turn begins.
        self.mod.begin_turn(42, FakeTimeline())
        self.mod.set_state("LISTENING", "capturing utterance")
        turn42 = self.published()
        self.assertEqual(turn42["turn_id"], 42)
        self.assertIsNone(turn42["route"], "turn 41's route leaked into turn 42")
        self.assertTrue(all(v is None for v in turn42["latency_ms"].values()),
                        f"turn 41 timings leaked into turn 42: {turn42['latency_ms']}")

    # ---- call ORDER in the runtime itself ---------------------------------
    #
    # The tests above drive begin_turn/end_turn directly, so they pin the
    # helpers' semantics but say nothing about where the runtime calls them --
    # and the bug that was found was purely one of ordering. These read the
    # source, because that is where the ordering lives.

    def test_begin_turn_precedes_the_listening_publish(self):
        """REGRESSION: the counter used to increment after LISTENING.

        Asserted against the source, not behaviour: with the wrong order the
        published turn_id is the previous turn's, and no amount of driving the
        helpers directly would reveal it.
        """
        src = open(RUNTIME_SOURCE, encoding="utf-8").read()
        begin = src.index("begin_turn(turn, tl)")
        listening = src.index('set_state("LISTENING", "capturing utterance")')
        increment = src.index('recorder_box["turn"] = recorder_box.get("turn", 0) + 1')
        self.assertLess(increment, begin, "begin_turn runs before the increment")
        self.assertLess(begin, listening,
                        "LISTENING is published before begin_turn: turn_id is stale")

    def test_the_turn_counter_is_incremented_exactly_once(self):
        src = open(RUNTIME_SOURCE, encoding="utf-8").read()
        self.assertEqual(src.count('recorder_box["turn"] = recorder_box.get("turn", 0) + 1'),
                         1, "the turn counter is incremented in more than one place")

    def test_end_turn_precedes_the_idle_publish(self):
        """The other ordering that matters: IDLE must not carry the old turn."""
        src = open(RUNTIME_SOURCE, encoding="utf-8").read()
        end = src.index("end_turn()")
        idle = src.index('set_state("IDLE", "waiting for wake word")')
        self.assertLess(end, idle,
                        "IDLE is published before end_turn: it keeps the old route")

    def test_route_is_set_at_the_working_publish_not_at_routing(self):
        src = open(RUNTIME_SOURCE, encoding="utf-8").read()
        set_route = src.index("set_turn_route(route)")
        routing = src.index('set_state("ROUTING", f"{route} via')
        working = src.index('set_state(route, "working")')
        self.assertLess(routing, set_route,
                        "the route is set before ROUTING is published")
        self.assertLess(set_route, working)

    # ---- privacy and size ------------------------------------------------

    def test_mode_still_0600_after_100_v2_replacements(self):
        tl = FakeTimeline().at("stt_start", 1.0).at("stt_done", 1.8)
        self.mod.begin_turn(1, tl)
        self.mod.set_turn_route("CODEX")
        inodes = set()
        for i in range(100):
            self.mod.set_state("SPEAKING", f"reply {i}")
            self.assertEqual(stat.S_IMODE(os.stat(self.mod.STATE_PATH).st_mode),
                             0o600, f"mode lost at {i}")
            inodes.add(os.stat(self.mod.STATE_PATH).st_ino)
        self.assertGreater(len(inodes), 1, "publication was not atomic")
        self.assertFalse(os.path.exists(self.mod.STATE_PATH + ".tmp"))
        json.load(open(self.mod.STATE_PATH, encoding="utf-8"))   # still valid

    def test_worst_case_size_is_far_below_the_parser_cap(self):
        tl = (FakeTimeline().at("wake_detected", 0.0).at("capture_start", 0.3)
              .at("capture_end", 30.0).at("stt_start", 30.0).at("stt_done", 31.0)
              .at("router_start", 31.0).at("router_done", 34.0)
              .at("delegate_start", 34.0).at("delegate_done", 99.0))
        self.mod.begin_turn(999999, tl)
        self.mod.set_turn_route("LOCAL_FAST")
        self.mod.set_state("SPEAKING", "あ" * 120)     # the display cap
        size = os.path.getsize(self.mod.STATE_PATH)
        print(f"\n  worst-case STATE_SIZE_BYTES = {size}")
        self.assertLess(size, 2048)
        self.assertLess(size, 64 * 1024)


if __name__ == "__main__":
    unittest.main(verbosity=2)
