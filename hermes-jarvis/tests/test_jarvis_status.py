"""Tests for the read-only health check (tools/jarvis_status.py).

Two halves:

  * Fixture tests for jarvis_status_checks -- every judgement the report makes,
    driven by synthetic data. No live JARVIS, nothing on disk is touched.
  * One live side-effect test that runs the real tool against the real system
    and asserts nothing moved: same PIDs, same published state, same turn id,
    same stream counters, no wake and no ACTIVATE. That test is the reason the
    IO and the judgement live in separate modules -- it is cheap to assert the
    tool is inert only if the tool really is.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import jarvis_status as st  # noqa: E402
import jarvis_status_checks as c  # noqa: E402


# ----------------------------------------------------- process line matching
_RUNTIME_SPEC = {"script": "/Users/Rascal/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py"}
_HUD_SPEC = {"exec_prefix":
             "/Users/Rascal/Applications/JARVIS HUD.app/Contents/MacOS/JarvisHUD"}
_OLLAMA_SPEC = {"exec_suffix": "/ollama", "first_arg": "serve"}
_GATEWAY_SPEC = {"argv": ["hermes_cli.main", "gateway", "run"]}


def test_real_runtime_matches():
    assert c.match_process(
        "/Users/Rascal/.hermes-venv/bin/python "
        "/Users/Rascal/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py", _RUNTIME_SPEC)


@pytest.mark.parametrize("cmd", [
    # The exact false positive this tool produced on its own first run: the
    # shell that invoked it had the path in its command line.
    "/bin/bash -c shasum -a 256 bin/jarvis_runtime.py && ./bin/jarvis-status",
    "vim /Users/Rascal/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py",
    "grep -n warm /Users/Rascal/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py",
    "tail -f /Users/Rascal/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py",
])
def test_processes_that_merely_name_the_runtime_do_not_match(cmd):
    assert not c.match_process(cmd, _RUNTIME_SPEC)


def test_relative_path_invocation_does_not_match():
    """Production always launches from the plist with an absolute path."""
    assert not c.match_process("python bin/jarvis_runtime.py", _RUNTIME_SPEC)


def test_app_bundle_path_with_spaces_matches():
    assert c.match_process(
        "/Users/Rascal/Applications/JARVIS HUD.app/Contents/MacOS/JarvisHUD",
        _HUD_SPEC)


def test_app_bundle_mentioned_by_another_process_does_not_match():
    assert not c.match_process(
        "codesign -v /Users/Rascal/Applications/JARVIS HUD.app/Contents/MacOS/JarvisHUD",
        _HUD_SPEC)


def test_ollama_serve_matches_but_other_ollama_subcommands_do_not():
    assert c.match_process("/opt/homebrew/bin/ollama serve", _OLLAMA_SPEC)
    assert not c.match_process("/opt/homebrew/bin/ollama ps", _OLLAMA_SPEC)
    assert not c.match_process("/opt/homebrew/bin/ollama run gemma4:e2b", _OLLAMA_SPEC)


def test_gateway_argv_match():
    assert c.match_process(
        "/Users/Rascal/.hermes-venv/bin/python -m hermes_cli.main gateway run --replace",
        _GATEWAY_SPEC)
    assert not c.match_process(
        "/Users/Rascal/.hermes-venv/bin/python -m hermes_cli.main chat", _GATEWAY_SPEC)


def test_empty_command_line_is_not_a_match():
    assert not c.match_process("", _RUNTIME_SPEC)
    assert not c.match_process("   ", _HUD_SPEC)


# ------------------------------------------------------------------ process
def test_healthy_single_process():
    chk = c.evaluate_process("Runtime", [123])
    assert chk.status == c.OK
    assert chk.data == {"running": True, "pid": 123, "count": 1}


def test_missing_required_process_is_fail():
    chk = c.evaluate_process("Runtime", [], required=True)
    assert chk.status == c.FAIL
    assert chk.data["running"] is False


def test_missing_optional_process_is_only_warn():
    assert c.evaluate_process("HUD", [], required=False).status == c.WARN


def test_duplicate_process_is_warn_and_lists_pids():
    chk = c.evaluate_process("Runtime", [123, 456])
    assert chk.status == c.WARN
    assert chk.data["count"] == 2
    assert chk.data["pids"] == [123, 456]


# -------------------------------------------------------------------- state
def _state(**over) -> str:
    base = {"version": 2, "state": "IDLE", "pid": 42, "turn_id": None,
            "route": None, "detail": "waiting for wake word", "latency_ms": {}}
    return json.dumps({**base, **over})


def test_healthy_idle_state():
    chk = c.evaluate_state(_state())
    assert chk.status == c.OK
    assert chk.data["version"] == 2
    assert chk.data["turn_id"] is None
    assert chk.data["route"] is None


def test_invalid_state_json_does_not_crash():
    chk = c.evaluate_state("{not json at all")
    assert chk.status == c.WARN
    assert chk.data["parsed"] is False


def test_state_file_missing_is_warn_not_crash():
    assert c.evaluate_state(None).status == c.WARN


def test_state_error_is_fail():
    chk = c.evaluate_state(_state(state="ERROR", detail="mic gone"))
    assert chk.status == c.FAIL
    assert "mic gone" in chk.detail


def test_unexpected_schema_version_is_warn():
    assert c.evaluate_state(_state(version=1)).status == c.WARN


def test_state_in_a_turn_is_not_flagged():
    """A turn in flight while we look is normal, not a fault."""
    assert c.evaluate_state(_state(state="LOCAL_FAST", turn_id=3)).status == c.OK


# --------------------------------------------------------------- wake lease
def test_wake_lease_held_by_runtime():
    assert c.evaluate_wake_lease(100, 100).status == c.OK


def test_wake_lease_mismatch_is_fail():
    chk = c.evaluate_wake_lease(999, 100)
    assert chk.status == c.FAIL
    assert "999" in chk.detail and "100" in chk.detail


def test_wake_lease_unheld_is_warn():
    assert c.evaluate_wake_lease(None, 100).status == c.WARN


# ---------------------------------------------------------------- privacy
def test_correct_permission_passes():
    chk = c.evaluate_permission("State file", 0o600, 0o600, uid=501,
                                current_uid=501)
    assert chk.status == c.OK
    assert chk.detail == "0600"


def test_loose_permission_is_fail():
    chk = c.evaluate_permission("State file", 0o644, 0o600)
    assert chk.status == c.FAIL
    assert "0644" in chk.detail and "0600" in chk.detail


def test_missing_socket_is_fail():
    assert c.evaluate_permission("Socket", None, 0o600).status == c.FAIL


def test_socket_path_that_is_not_a_socket_is_fail():
    import stat as _stat
    chk = c.evaluate_permission("Socket", 0o600, 0o600, must_be_socket=True,
                                file_type=_stat.S_IFREG)
    assert chk.status == c.FAIL
    assert "not a socket" in chk.detail


def test_wrong_owner_is_fail():
    chk = c.evaluate_permission("State file", 0o600, 0o600, uid=0, current_uid=501)
    assert chk.status == c.FAIL


# ------------------------------------------------- shared stream generations
_OLD_GEN = (
    "2026-09-02 12:10:48 INFO jarvis: state=OFFLINE starting",
    "2026-09-02 12:10:51 INFO jarvis: wake listener up: "
    "{'PA_OPEN_COUNT': 4, 'PA_START_COUNT': 9, 'replacements': 3, 'feed_errors': 7}",
)
_NEW_GEN = (
    "2026-09-02 12:31:18 INFO jarvis: state=OFFLINE starting",
    "2026-09-02 12:31:20 INFO jarvis: wake listener up: "
    "{'PA_OPEN_COUNT': 1, 'PA_START_COUNT': 1, 'replacements': 0, 'feed_errors': 0}",
)


def test_old_generation_counters_are_excluded():
    """The headline regression risk: reporting a dead process's numbers."""
    gen = c.current_generation(list(_OLD_GEN + _NEW_GEN))
    counters = c.extract_counters(gen)
    assert counters["PA_OPEN_COUNT"] == 1
    assert counters["replacements"] == 0
    assert counters["feed_errors"] == 0
    assert c.evaluate_shared_stream(counters).status == c.OK


def test_generation_with_no_boot_marker_falls_back_to_all_lines():
    gen = c.current_generation([_NEW_GEN[1]])
    assert c.extract_counters(gen)["PA_OPEN_COUNT"] == 1


def test_replacements_and_feed_errors_are_warned():
    counters = c.extract_counters(list(_OLD_GEN))
    chk = c.evaluate_shared_stream(counters)
    assert chk.status == c.WARN
    assert "replacement" in chk.detail and "feed error" in chk.detail


def test_abnormal_pa_lifecycle_is_warned():
    chk = c.evaluate_shared_stream({"PA_OPEN_COUNT": 2, "PA_START_COUNT": 2,
                                    "replacements": 0, "feed_errors": 0})
    assert chk.status == c.WARN
    assert "lifecycle" in chk.detail


def test_no_counters_available_is_warn():
    assert c.evaluate_shared_stream(None).status == c.WARN


def test_malformed_counter_dict_is_skipped_not_crashed():
    lines = ["junk {'PA_OPEN_COUNT': <<broken>>} junk"]
    assert c.extract_counters(lines) is None


# ------------------------------------------------------------- startup warm
def test_startup_warm_success():
    lines = [
        "ollama startup readiness: waiting attempt=1",
        "ollama startup readiness: ready after=6.0s attempts=4",
        "ollama warm (startup): gemma4:e2b ready in 17.17s, keep_alive=60m",
    ]
    chk = c.evaluate_startup_warm(lines)
    assert chk.status == c.OK
    assert chk.data["result"] == "PASS"
    assert chk.data["readiness_attempts"] == 4
    assert chk.data["warm_duration_s"] == 17.17


def test_startup_warm_abandoned_is_warn_not_fail():
    """A missing warm costs latency, not correctness."""
    chk = c.evaluate_startup_warm([
        "ollama startup readiness: waiting attempt=1",
        "ollama warm (startup): abandoned after=45.2s attempts=12",
    ])
    assert chk.status == c.WARN
    assert chk.data["result"] == "ABANDONED"


def test_pre_fix_single_shot_failure_is_recognised():
    chk = c.evaluate_startup_warm(["ollama warm (startup) failed: URLError"])
    assert chk.status == c.WARN
    assert chk.data["result"] == "FAIL"
    assert "no retry" in chk.detail


def test_startup_warm_still_running_is_informational():
    chk = c.evaluate_startup_warm(["ollama startup readiness: waiting attempt=2"])
    assert chk.status == c.INFO
    assert chk.data["result"] == "IN_PROGRESS"


def test_startup_warm_absent_from_generation():
    assert c.evaluate_startup_warm([]).data["result"] == "ABSENT"


# -------------------------------------------------------------------- ollama
_TAGS = {"models": [{"name": "gemma4:e2b"}]}
_PS = {"models": [{"name": "gemma4:e2b", "expires_at": "2026-09-02T13:31:43+09:00"}]}


def test_ollama_available_and_resident():
    chk = c.evaluate_ollama(200, _TAGS, _PS, "gemma4:e2b")
    assert chk.status == c.OK
    assert chk.data["resident"] is True
    assert chk.data["expires_at"].startswith("2026-09-02")


def test_ollama_unavailable_is_fail():
    chk = c.evaluate_ollama(None, None, None, "gemma4:e2b")
    assert chk.status == c.FAIL
    assert chk.data["resident"] is None


def test_ollama_up_but_model_not_resident_is_degraded_not_failed():
    chk = c.evaluate_ollama(200, _TAGS, {"models": []}, "gemma4:e2b")
    assert chk.status == c.WARN
    assert chk.data["available"] is True
    assert chk.data["resident"] is False


def test_ollama_up_but_model_missing_is_fail():
    chk = c.evaluate_ollama(200, {"models": []}, {"models": []}, "gemma4:e2b")
    assert chk.status == c.FAIL


# -------------------------------------------------------------------- hermes
def test_hermes_health_ok():
    assert c.evaluate_hermes(200, True).status == c.OK


def test_hermes_listening_but_unhealthy_is_warn():
    assert c.evaluate_hermes(500, True).status == c.WARN


def test_hermes_absent_is_fail():
    assert c.evaluate_hermes(None, False).status == c.FAIL


# ------------------------------------------------------------------ watchdog
def test_watchdog_clean():
    events = [{"event": "start", "ts": "t0"},
              {"event": "observed", "ts": "t1", "pid": 100, "state": "IDLE"}]
    chk = c.evaluate_watchdog(events, 100)
    assert chk.status == c.OK
    assert chk.data["restarts_since_start"] == 0
    assert chk.data["observed_current_runtime"] is True


def test_old_restart_history_is_not_a_current_failure():
    """A restart before this watchdog generation is history, not a live fault."""
    events = [
        {"event": "start", "ts": "t0"},
        {"event": "restart", "ts": "t1", "reason": "stalled"},
        {"event": "start", "ts": "t2"},
        {"event": "observed", "ts": "t3", "pid": 100, "state": "IDLE"},
    ]
    chk = c.evaluate_watchdog(events, 100)
    assert chk.status == c.OK
    assert chk.data["restarts_since_start"] == 0


def test_restart_in_current_generation_is_warn():
    events = [{"event": "start", "ts": "t0"},
              {"event": "restart", "ts": "t1", "reason": "stalled"}]
    chk = c.evaluate_watchdog(events, 100)
    assert chk.status == c.WARN
    assert chk.data["last_restart_reason"] == "stalled"


def test_watchdog_has_not_seen_current_runtime():
    events = [{"event": "start", "ts": "t0"},
              {"event": "observed", "ts": "t1", "pid": 999, "state": "IDLE"}]
    assert c.evaluate_watchdog(events, 100).status == c.WARN


# --------------------------------------------------------------- login items
def test_login_item_registered():
    assert c.evaluate_login_item("HUD login item", True, 500).status == c.OK


def test_login_item_unknown_is_warn_not_fail():
    """Absence from launchd does not prove deregistration, only non-running."""
    assert c.evaluate_login_item("HUD login item", False, None).status == c.WARN


# ----------------------------------------------------------------- overall
def test_overall_healthy():
    assert c.overall_status([c.Check("a", c.OK, ""), c.Check("b", c.INFO, "")]) \
        == c.HEALTHY


def test_overall_degraded_on_warn():
    assert c.overall_status([c.Check("a", c.OK, ""), c.Check("b", c.WARN, "")]) \
        == c.DEGRADED


def test_overall_fail_beats_warn():
    assert c.overall_status([c.Check("a", c.WARN, ""), c.Check("b", c.FAIL, "")]) \
        == c.FAILED


def test_known_gaps_never_make_the_status_fail():
    """A documented limitation is not a fault. This is the rule that keeps
    jarvis-status usable while the wake word and clamshell gaps stay open."""
    gaps = c.load_gaps(json.dumps({"gaps": [
        {"id": "WAKE_WORD_RELIABILITY", "status": "KNOWN_GAP"},
        {"id": "CLAMSHELL_RECOVERY", "status": "UNVERIFIED"},
    ]}))
    assert len(gaps) == 2
    assert c.overall_status([c.Check("a", c.OK, "")]) == c.HEALTHY


def test_gaps_config_missing_or_broken_yields_no_gaps():
    assert c.load_gaps(None) == []
    assert c.load_gaps("{broken") == []
    assert c.load_gaps(json.dumps({"nogaps": 1})) == []


# ------------------------------------------------------------------ render
def test_json_output_is_machine_readable():
    checks = [c.Check("Runtime", c.OK, "PID 1", {"pid": 1})]
    payload = json.loads(st.render_json(checks, [], c.HEALTHY))
    assert payload["overall"] == "HEALTHY"
    assert payload["checks"][0]["data"]["pid"] == 1


# --------------------------------------------------------- LIVE side effects
def _live_snapshot() -> dict:
    """Everything that must be identical either side of a health check."""
    procs = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True,
                           text=True, timeout=10).stdout
    rows = []
    for line in procs.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit():
            rows.append((int(pid), cmd.strip()))
    pids = {name: sorted(pid for pid, cmd in rows if c.match_process(cmd, spec))
            for name, spec, _req in st.COMPONENTS}
    state = st._read_text(st.STATE_PATH)
    gen = c.current_generation(st._read_tail(st.RUNTIME_LOG, st.LOG_TAIL_BYTES))
    log = "\n".join(gen)
    return {
        "pids": pids,
        "state": json.loads(state) if state else None,
        "counters": c.extract_counters(gen),
        "wake_events": len(re.findall(r"state=LISTENING", log)),
        "activate_events": len(re.findall(r"activation: ACTIVATE", log)),
    }


@pytest.mark.skipif(
    not os.path.exists(st.STATE_PATH),
    reason="no live JARVIS on this machine")
def test_health_check_has_zero_side_effects():
    """Section 16: running jarvis-status must move nothing.

    Deliberately runs the tool as a subprocess, the way a user does, rather
    than calling collect() in-process -- an in-process call could not catch a
    side effect that only the CLI path triggers.
    """
    before = _live_snapshot()

    proc = subprocess.run([sys.executable,
                           os.path.join(ROOT, "tools", "jarvis_status.py"),
                           "--json"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode in (0, 1, 2), proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["overall"] in ("HEALTHY", "DEGRADED", "FAIL")

    after = _live_snapshot()

    assert after["pids"] == before["pids"], "a component PID changed"
    if before["state"] and after["state"]:
        assert after["state"]["pid"] == before["state"]["pid"]
        assert after["state"]["turn_id"] == before["state"]["turn_id"]
        # A turn genuinely started by the user mid-test would move this; the
        # check is that WE did not start one.
        assert after["state"]["state"] == before["state"]["state"]
    assert after["counters"] == before["counters"], "stream counters moved"
    assert after["wake_events"] == before["wake_events"], "a wake fired"
    assert after["activate_events"] == before["activate_events"], "ACTIVATE fired"


@pytest.mark.skipif(
    not os.path.exists(st.STATE_PATH),
    reason="no live JARVIS on this machine")
def test_health_check_finishes_quickly():
    """Section 17: seconds, not tens of seconds, on a multi-MB log."""
    import time

    t = time.monotonic()
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "jarvis_status.py")],
                   capture_output=True, text=True, timeout=60)
    elapsed = time.monotonic() - t
    assert elapsed < 10.0, f"health check took {elapsed:.1f}s"


# ---------------------------------------------------------------- capture health
#
# 2026-09-08/09 の実測で判明した3つの失敗モードを status に出す。
# いずれも既にログには出ていたが可視化されておらず、利用者からは
# 「待つが返答がない」「少し長い」としか見えず切り分けに数時間を要した。
#
#   1. digital silence   PEAK_RMS=0            クラムシェルで内蔵マイクが無音
#   2. clipping          PEAK_RMS>=32000       入力音量が高すぎて無音検出が発火しない
#   3. capture cap       silence_cb_fired=False 上限まで走り毎ターン +26s

def _cap(turn, fired, frames, rms):
    return (f"2026-09-09 12:00:0{turn} INFO jarvis: capture TURN={turn} "
            f"silence_cb_fired={fired} FRAMES={frames} PEAK_RMS={rms} wav=True "
            "{'PA_OPEN_COUNT': 1, 'PA_START_COUNT': 1}")


def test_capture_health_no_capture_yet_is_ok():
    chk = c.evaluate_capture_health(["2026-09-09 12:00:00 INFO jarvis: state=IDLE"])
    assert chk.status == c.OK
    assert chk.data["available"] is False


def test_capture_health_normal_turn_is_ok():
    chk = c.evaluate_capture_health([_cap(1, "True", 345, 20502)])
    assert chk.status == c.OK
    assert chk.data["peak_rms"] == 20502


def test_capture_health_digital_silence_is_warned():
    chk = c.evaluate_capture_health([_cap(1, "True", 751, 0)])
    assert chk.status == c.WARN
    assert "無音" in chk.detail
    assert chk.data["peak_rms"] == 0


def test_capture_health_counts_consecutive_silent_captures():
    lines = [_cap(1, "True", 751, 0), _cap(2, "True", 751, 0), _cap(3, "True", 751, 0)]
    chk = c.evaluate_capture_health(lines)
    assert chk.status == c.WARN
    assert chk.data["silent_streak"] == 3


def test_capture_health_clipping_is_warned():
    chk = c.evaluate_capture_health([_cap(1, "False", 2813, 32768)])
    assert chk.status == c.WARN
    assert "クリッピング" in chk.detail


def test_capture_health_cap_without_clipping_is_warned():
    # 無音検出が発火せず上限まで走ったが、レベルは飽和していない場合
    chk = c.evaluate_capture_health([_cap(1, "False", 2813, 5000)])
    assert chk.status == c.WARN
    assert chk.data["silence_cb_fired"] is False


def test_capture_health_uses_the_most_recent_capture():
    lines = [_cap(1, "True", 751, 0), _cap(2, "True", 345, 18000)]
    chk = c.evaluate_capture_health(lines)
    assert chk.status == c.OK
    assert chk.data["peak_rms"] == 18000
    assert chk.data["silent_streak"] == 0


def test_capture_health_ignores_malformed_capture_lines():
    lines = ["2026-09-09 12:00:00 INFO jarvis: capture TURN=x PEAK_RMS=oops",
             _cap(2, "True", 345, 12000)]
    chk = c.evaluate_capture_health(lines)
    assert chk.status == c.OK
    assert chk.data["peak_rms"] == 12000


# ------------------------------------------------------------------ device
#
# 案C: 優先順位（外部 > 無線 > 内蔵）で選んだデバイスと、実際に束縛している
# デバイスが食い違ったら知らせる。切り替えは runtime 再起動が必要なので
# （follow_default_device=False / CoreAudio デッドロック回避）、
# 「再起動すれば直る」ことまで出す。

def test_device_check_is_ok_when_bound_matches_preferred():
    chk = c.evaluate_device({"bound_device": "外部マイク",
                             "preferred_device": "外部マイク",
                             "device_change_pending": False})
    assert chk.status == c.OK
    assert "外部マイク" in chk.detail


def test_device_check_warns_when_a_better_device_appeared():
    chk = c.evaluate_device({"bound_device": "MacBook Proのマイク",
                             "preferred_device": "外部マイク",
                             "device_change_pending": True})
    assert chk.status == c.WARN
    assert "再起動" in chk.detail


def test_device_check_warns_when_no_device_is_selectable():
    # クラムシェル中に外部マイクを抜くと候補が消える
    chk = c.evaluate_device({"bound_device": "外部マイク",
                             "preferred_device": None,
                             "device_change_pending": False})
    assert chk.status == c.WARN
    assert "候補" in chk.detail


def test_device_check_without_counters_is_not_a_failure():
    chk = c.evaluate_device(None)
    assert chk.status == c.OK
    assert chk.data["available"] is False


def test_device_check_tolerates_missing_keys():
    chk = c.evaluate_device({})
    assert chk.status == c.OK
    assert chk.data["available"] is False


# ------------------------------------------------------------------ output
#
# 2026-09-09: TTS は mp3 を生成し PLAYBACK_DURATION も記録されるのに音が出なかった。
# 既定出力が DisplayLink ドック（Realtek USB2.0 Audio）で、そこに何も繋がって
# いなかったため。JARVIS は「再生した」しか知らず、出力先を知る手段がなかった。
# 既定出力デバイス名を 1 行出すだけで、この切り分けは即答になる。

def test_output_check_names_the_default_device():
    chk = c.evaluate_output("MacBook Proのスピーカー", muted=False, volume=88)
    assert chk.status == c.OK
    assert "MacBook Proのスピーカー" in chk.detail


def test_output_check_warns_when_muted():
    chk = c.evaluate_output("MacBook Proのスピーカー", muted=True, volume=88)
    assert chk.status == c.WARN
    assert "ミュート" in chk.detail


def test_output_check_warns_on_zero_volume():
    chk = c.evaluate_output("MacBook Proのスピーカー", muted=False, volume=0)
    assert chk.status == c.WARN
    assert "音量" in chk.detail


def test_output_check_flags_a_device_that_often_has_nothing_attached():
    """ドック/HDMI は「繋がっていなければ無音」になりやすいので注記する。"""
    chk = c.evaluate_output("Realtek USB2.0 Audio", muted=False, volume=88)
    assert chk.status == c.WARN
    assert "接続" in chk.detail


def test_output_check_without_information_is_not_a_failure():
    chk = c.evaluate_output(None, muted=None, volume=None)
    assert chk.status == c.OK
    assert chk.data["available"] is False
