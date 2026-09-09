"""Pure evaluation logic for jarvis-status.

Everything here is a function from already-collected data to a Check. Nothing in
this module opens a socket, reads a file, or runs a process -- that is
jarvis_status.py's job. The split exists so the judgement calls (is one runtime
healthy and two a fault? does an old restart count against us now?) can be
tested against fixtures without a live JARVIS, and so a health check can never
grow a side effect by accident.

Every function returns a new Check; none mutates its input.
"""
from __future__ import annotations

import ast
import json
import os
import re
import stat
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# Status vocabulary, worst last -- OVERALL_ORDER relies on this ordering.
OK = "OK"
INFO = "INFO"
WARN = "WARN"
FAIL = "FAIL"
_SEVERITY = {OK: 0, INFO: 0, WARN: 1, FAIL: 2}

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
FAILED = "FAIL"


def _mode_str(mode: int) -> str:
    """Render a permission the way chmod takes it: 600, not 0o600."""
    return format(mode, "04o")


@dataclass(frozen=True)
class Check:
    """One line of the report. `data` carries the machine-readable detail."""

    name: str
    status: str
    detail: str
    data: Mapping[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------- process
_INTERPRETER_HINTS = ("python", "Python")


def match_process(cmd: str, spec: Mapping[str, Any]) -> bool:
    """Does this command line REALLY run the component?

    Substring matching was the first attempt and it was wrong: any process that
    merely mentions the path matches it. Caught in practice by the tool's own
    first run, which reported two runtimes because the shell invoking it had
    `shasum -a 256 bin/jarvis_runtime.py` in its command line. `vim
    bin/jarvis_runtime.py` or a grep would have done the same, and a spurious
    "2 processes" reads as the one fault an operator must act on immediately.

    So a match must be positional:

      exec_prefix  the command STARTS with this binary path. Used for the .app
                   bundles, whose paths contain spaces and so cannot be split.
      script       an interpreter running exactly this absolute script path.
                   The path must be an argument, not just present somewhere.
      argv         every one of these tokens appears, and argv[0] is an
                   interpreter -- for `python -m hermes_cli.main gateway run`.
      exec_suffix  argv[0] ends with this, plus the given first argument.
    """
    cmd = cmd.strip()
    if "exec_prefix" in spec:
        return cmd.startswith(spec["exec_prefix"])

    tokens = cmd.split()
    if not tokens:
        return False
    interpreter = any(h in os.path.basename(tokens[0]) for h in _INTERPRETER_HINTS)

    if "script" in spec:
        return interpreter and spec["script"] in tokens[1:]
    if "argv" in spec:
        return interpreter and all(t in tokens for t in spec["argv"])
    if "exec_suffix" in spec:
        if not tokens[0].endswith(spec["exec_suffix"]):
            return False
        first_arg = spec.get("first_arg")
        return first_arg is None or (len(tokens) > 1 and tokens[1] == first_arg)
    return False


def evaluate_process(name: str, matches: Sequence[int],
                     required: bool = True) -> Check:
    """A component is healthy at exactly one process.

    Two is a fault, not a nicety: a second runtime would contend for the wake
    lease and the shared audio stream, and a second HUD would render a second
    overlay. One is expected; zero is FAIL for anything JARVIS needs to work.
    """
    count = len(matches)
    if count == 1:
        return Check(name, OK, f"PID {matches[0]}",
                     {"running": True, "pid": matches[0], "count": 1})
    if count == 0:
        status = FAIL if required else WARN
        return Check(name, status, "not running",
                     {"running": False, "pid": None, "count": 0})
    return Check(name, WARN, f"{count} processes: {', '.join(map(str, matches))}",
                 {"running": True, "pid": matches[0], "count": count,
                  "pids": list(matches)})


# ----------------------------------------------------------------------- state
def evaluate_state(raw: str | None) -> Check:
    """Parse and judge logs/jarvis_state.json.

    Unreadable or malformed state is WARN, not FAIL: the runtime can be running
    correctly while a publish is half-written, and this tool must never turn a
    torn read into a false alarm about the service itself. A published ERROR
    state, by contrast, is the runtime telling us something is wrong.
    """
    if raw is None:
        return Check("State", WARN, "state file missing or unreadable",
                     {"readable": False})
    try:
        state = json.loads(raw)
    except (ValueError, TypeError):
        return Check("State", WARN, "state file is not valid JSON",
                     {"readable": True, "parsed": False})
    if not isinstance(state, dict):
        return Check("State", WARN, "state file is not a JSON object",
                     {"readable": True, "parsed": False})

    version = state.get("version")
    name = state.get("state")
    data = {
        "readable": True, "parsed": True, "version": version, "state": name,
        "pid": state.get("pid"), "turn_id": state.get("turn_id"),
        "route": state.get("route"), "detail": state.get("detail"),
        "latency_ms": state.get("latency_ms"),
    }
    if name == "ERROR":
        return Check("State", FAIL, f"state=ERROR ({state.get('detail')})", data)
    if version != 2:
        return Check("State", WARN, f"unexpected schema version {version!r}", data)
    if name == "OFFLINE":
        return Check("State", WARN, f"state=OFFLINE ({state.get('detail')})", data)
    if name is None:
        return Check("State", WARN, "no state field", data)
    # IDLE is the resting state; anything else means a turn is genuinely in
    # flight while we look, which is normal and not worth flagging.
    return Check("State", OK, f"{name} / v{version}", data)


# ------------------------------------------------------------------ wake lease
def evaluate_wake_lease(owner_pid: int | None,
                        runtime_pid: int | None) -> Check:
    """The wake lease is machine-wide; the runtime must be the one holding it.

    A different owner is the failure that kept this project blocked for days --
    an interactive `hermes chat` holding the lock leaves the runtime listening
    to nothing while looking perfectly alive.
    """
    data = {"owner_pid": owner_pid, "runtime_pid": runtime_pid}
    if owner_pid is None:
        return Check("Wake lease", WARN, "unheld — no process owns the lock", data)
    if runtime_pid is None:
        return Check("Wake lease", WARN, f"held by PID {owner_pid}, runtime PID unknown",
                     data)
    if owner_pid == runtime_pid:
        return Check("Wake lease", OK, f"runtime (PID {owner_pid})", data)
    return Check("Wake lease", FAIL,
                 f"held by PID {owner_pid}, not the runtime (PID {runtime_pid})", data)


# -------------------------------------------------------------------- privacy
def evaluate_permission(name: str, mode: int | None, expected: int,
                        uid: int | None = None, current_uid: int | None = None,
                        must_be_socket: bool = False,
                        file_type: int | None = None) -> Check:
    """One filesystem permission expectation.

    Modes are compared exactly. These paths carry transcripts, state and the
    activation channel, so "0600 or tighter" is not the rule -- a surprise in
    either direction is a regression worth seeing.
    """
    data = {"mode": None if mode is None else _mode_str(mode),
            "expected": _mode_str(expected),
            "uid": uid}
    if mode is None:
        return Check(name, FAIL, "missing", {**data, "present": False})
    if must_be_socket and file_type is not None and not stat.S_ISSOCK(file_type):
        return Check(name, FAIL, "exists but is not a socket",
                     {**data, "present": True, "is_socket": False})
    if mode != expected:
        return Check(name, FAIL,
                     f"mode {_mode_str(mode)}, expected {_mode_str(expected)}",
                     {**data, "present": True})
    if uid is not None and current_uid is not None and uid != current_uid:
        return Check(name, FAIL, f"owned by uid {uid}, expected {current_uid}",
                     {**data, "present": True})
    return Check(name, OK, _mode_str(mode), {**data, "present": True})


# ------------------------------------------------------- shared stream counters
_COUNTER_RE = re.compile(r"(\{[^{}]*'PA_OPEN_COUNT'[^{}]*\})")
_GENERATION_MARKER = "state=OFFLINE starting"


def current_generation(lines: Sequence[str]) -> list[str]:
    """Drop everything before this runtime's own boot.

    The log is append-only across restarts, so a naive "last counters in the
    file" would happily report a dead process's numbers. Each boot logs
    "state=OFFLINE starting"; the current generation is everything after the
    last one.
    """
    last = -1
    for i, line in enumerate(lines):
        if _GENERATION_MARKER in line:
            last = i
    return list(lines[last + 1:]) if last >= 0 else list(lines)


def extract_counters(lines: Sequence[str]) -> dict[str, Any] | None:
    """Latest shared-stream counter dict in the given lines, or None."""
    for line in reversed(lines):
        m = _COUNTER_RE.search(line)
        if not m:
            continue
        try:
            parsed = ast.literal_eval(m.group(1))
        except (ValueError, SyntaxError):
            continue
        if isinstance(parsed, dict):
            return dict(parsed)
    return None


def evaluate_shared_stream(counters: Mapping[str, Any] | None) -> Check:
    """One stream opened once, no replacements, no feed errors.

    A replacement means the silence watchdog had to rebuild a deaf CoreAudio
    stream; feed errors mean frames are not reaching the detector. Either is
    real, so both are WARN even though JARVIS keeps running -- and more than one
    open means the single-stream invariant broke.
    """
    if counters is None:
        return Check("Audio", WARN, "no counters in the current log generation",
                     {"available": False})
    opened = counters.get("PA_OPEN_COUNT")
    started = counters.get("PA_START_COUNT")
    repl = counters.get("replacements")
    errs = counters.get("feed_errors")
    data = {"available": True, "PA_OPEN_COUNT": opened, "PA_START_COUNT": started,
            "replacements": repl, "feed_errors": errs}
    summary = f"PA {opened}/{started} / repl {repl} / err {errs}"

    problems = []
    if opened != 1 or started != 1:
        problems.append("stream lifecycle abnormal")
    if repl:
        problems.append(f"{repl} stream replacement(s)")
    if errs:
        problems.append(f"{errs} feed error(s)")
    if problems:
        return Check("Audio", WARN, f"{summary} — {'; '.join(problems)}", data)
    return Check("Audio", OK, summary, data)


# ------------------------------------------------------------------ output
#
# 2026-09-09 実測: TTS は mp3 を生成し PLAYBACK_DURATION も記録されるのに音が出なかった。
# 既定出力が DisplayLink ドック（Realtek USB2.0 Audio）で、そこに何も繋がって
# いなかったため。JARVIS は「再生した」しか知らず、出力先を知る手段が無かった。
# 出力先の名前を 1 行出すだけで、この切り分けは即答になる。

# 「繋がっていなければ無音」になりやすい出力先。断定はせず注記に留める
# （ドックにスピーカーを繋いでいる構成も普通にあるため）。
_DETACHABLE_OUTPUT = ("usb", "displaylink", "hdmi", "realtek", "dock",
                      "nomachine")


def evaluate_output(device: str | None, muted: bool | None,
                    volume: int | None, pinned: str | None = None) -> Check:
    """出力の健全性。

    ``pinned`` は JARVIS が固定している出力デバイスの UID。固定できているなら
    システム既定がドックに奪われても影響しないので、既定に対する警告は出さない。
    ミュートと音量 0 は固定の有無に関係なく効くので、そちらは常に警告する。
    """
    if not device and not pinned:
        return Check("Output", OK, "情報なし", {"available": False})

    data = {"available": True, "device": device, "muted": muted,
            "volume": volume, "pinned": pinned}

    if muted:
        return Check("Output", WARN, f"{device or pinned} / ミュート中です", data)

    if volume is not None and int(volume) == 0:
        return Check("Output", WARN, f"{device or pinned} / 音量が 0 です", data)

    vol = f" / 音量 {volume}" if volume is not None else ""

    if pinned:
        return Check("Output", OK, f"{pinned}（JARVIS が固定）{vol}", data)

    lowered = (device or "").lower()
    if any(p in lowered for p in _DETACHABLE_OUTPUT):
        return Check("Output", WARN,
                     f"{device} — 機器が接続されていないと無音になります。"
                     "音が聞こえない場合は出力先を確認してください", data)

    return Check("Output", OK, f"{device}{vol}", data)


# ------------------------------------------------------------------ device
#
# 案C: 優先順位（外部 > 無線 > 内蔵）で選んだデバイスと実際の束縛先が食い違ったら
# 知らせる。稼働中のストリームは差し替えない（CoreAudio の close→reopen が
# デッドロックするため）ので、切り替えには runtime 再起動が必要。
# 「何が起きていて、どうすれば直るか」まで出す。


def evaluate_device(counters: Mapping[str, Any] | None) -> Check:
    if not counters or "bound_device" not in counters:
        return Check("Device", OK, "情報なし", {"available": False})

    bound = counters.get("bound_device")
    preferred = counters.get("preferred_device")
    pending = bool(counters.get("device_change_pending"))
    data = {"available": True, "bound_device": bound,
            "preferred_device": preferred, "pending": pending}

    if preferred is None:
        return Check("Device", WARN,
                     f"束縛中 {bound!r} / 選択可能な候補がありません"
                     "（クラムシェル中に外部マイクを抜いた等）", data)

    if pending:
        return Check("Device", WARN,
                     f"優先デバイスが {preferred!r} に変わりました"
                     f"（束縛中は {bound!r}）。切り替えには runtime の再起動が必要です",
                     data)

    return Check("Device", OK, f"{bound}", data)


# ----------------------------------------------------------- capture health
#
# 2026-09-08/09 の実測で3つの失敗モードを踏んだ。いずれも既にログには出ていたが
# status/HUD に出ていなかったため、利用者からは「待つが返答がない」「少し長い」
# としか見えず、切り分けに数時間かかった。ここで可視化する。
#
#   digital silence  PEAK_RMS=0             クラムシェルで内蔵マイクが無音を返す
#   clipping         PEAK_RMS>=CLIP         入力音量が高すぎ、無音検出が発火しない
#   capture cap      silence_cb_fired=False 上限まで走り 1 ターン +26s
#
# int16 の上限は 32767。飽和判定はその手前に置く。
_CLIP_THRESHOLD = 32000

_CAPTURE_RE = re.compile(
    r"capture TURN=(\d+) silence_cb_fired=(True|False) "
    r"FRAMES=(\d+) PEAK_RMS=(\d+)")


def parse_captures(lines: Sequence[str]) -> list[dict[str, Any]]:
    """Capture 行を古い順に構造化する。壊れた行は黙って捨てる。"""
    out: list[dict[str, Any]] = []
    for line in lines:
        m = _CAPTURE_RE.search(line)
        if not m:
            continue
        out.append({"turn": int(m.group(1)),
                    "silence_cb_fired": m.group(2) == "True",
                    "frames": int(m.group(3)),
                    "peak_rms": int(m.group(4))})
    return out


def evaluate_capture_health(lines: Sequence[str]) -> Check:
    """直近の capture が実際に音を拾えているか。

    判定は**最後の capture** に対して行う。過去に無音があっても現在正常なら
    OK にしないと、一度きりの事故で永久に WARN が残る。ただし無音が連続して
    いる場合はその本数を出す（クラムシェルのように状態が持続する故障は
    1 本より N 本の方が状況を語る）。
    """
    caps = parse_captures(lines)
    if not caps:
        return Check("Capture", OK, "まだ capture がありません",
                     {"available": False})

    last = caps[-1]
    rms = last["peak_rms"]
    fired = last["silence_cb_fired"]

    streak = 0
    for cap in reversed(caps):
        if cap["peak_rms"] == 0:
            streak += 1
        else:
            break

    data = {"available": True, "turn": last["turn"], "peak_rms": rms,
            "frames": last["frames"], "silence_cb_fired": fired,
            "silent_streak": streak}

    if rms == 0:
        detail = f"PEAK_RMS=0 — マイクが無音です"
        if streak > 1:
            detail += f"（{streak}ターン連続）"
        detail += "。既定入力デバイスを確認し、runtime を再起動してください"
        return Check("Capture", WARN, detail, data)

    if rms >= _CLIP_THRESHOLD:
        return Check("Capture", WARN,
                     f"PEAK_RMS={rms} — 入力がクリッピングしています。"
                     "入力音量を下げてください（無音検出が発火せず capture が上限まで走ります）",
                     data)

    if not fired:
        return Check("Capture", WARN,
                     f"silence_cb_fired=False / FRAMES={last['frames']} — "
                     "無音検出が発火せず capture が上限まで走りました",
                     data)

    return Check("Capture", OK,
                 f"PEAK_RMS={rms} / FRAMES={last['frames']} / TURN={last['turn']}",
                 data)


# ------------------------------------------------------------- startup warm
_WARM_OK_RE = re.compile(r"ollama warm \(startup\): (\S+) ready in ([\d.]+)s")
_WARM_FAIL_RE = re.compile(r"ollama warm \(startup\) failed: (\w+)")
_WARM_ABANDON_RE = re.compile(r"ollama warm \(startup\): abandoned after=([\d.]+)s "
                              r"attempts=(\d+)")
_READY_RE = re.compile(r"ollama startup readiness: ready after=([\d.]+)s attempts=(\d+)")
_WAITING_RE = re.compile(r"ollama startup readiness: waiting attempt=(\d+)")


def evaluate_startup_warm(lines: Sequence[str]) -> Check:
    """What the readiness-aware warm did on this boot.

    A failed warm is WARN, not FAIL: JARVIS still answers, the first turn just
    pays the model load. That is exactly the pre-fix behaviour, so it is a
    degradation rather than a breakage.
    """
    waiting = [int(m.group(1)) for m in
               (_WAITING_RE.search(ln) for ln in lines) if m]
    ready = None
    for line in lines:
        m = _READY_RE.search(line)
        if m:
            ready = (float(m.group(1)), int(m.group(2)))
    attempts = ready[1] if ready else (max(waiting) if waiting else 0)
    data: dict[str, Any] = {
        "readiness_attempts": attempts,
        "ready_after_s": ready[0] if ready else None,
    }

    for line in reversed(lines):
        m = _WARM_OK_RE.search(line)
        if m:
            return Check("Startup warm", OK,
                         f"{m.group(1)} warm in {m.group(2)}s"
                         + (f", ready after {attempts} attempt(s)" if attempts else ""),
                         {**data, "result": "PASS", "model": m.group(1),
                          "warm_duration_s": float(m.group(2))})
        m = _WARM_ABANDON_RE.search(line)
        if m:
            return Check("Startup warm", WARN,
                         f"abandoned after {m.group(1)}s / {m.group(2)} attempts",
                         {**data, "result": "ABANDONED",
                          "abandoned_after_s": float(m.group(1))})
        m = _WARM_FAIL_RE.search(line)
        if m:
            # The pre-fix shape: one shot, no retry.
            return Check("Startup warm", WARN, f"failed: {m.group(1)} (no retry)",
                         {**data, "result": "FAIL", "error": m.group(1)})
    if waiting:
        return Check("Startup warm", INFO,
                     f"in progress, {attempts} readiness attempt(s) so far",
                     {**data, "result": "IN_PROGRESS"})
    return Check("Startup warm", WARN, "no startup warm in this log generation",
                 {**data, "result": "ABSENT"})


# --------------------------------------------------------------------- ollama
def evaluate_ollama(http_status: int | None, tags: Mapping[str, Any] | None,
                    ps: Mapping[str, Any] | None, wanted_model: str) -> Check:
    """Reachability, availability of the router model, and residency.

    Residency is the thing that decides whether the next turn is fast, but a
    non-resident model is WARN: it costs latency, not correctness, and Ollama
    is entitled to drop it once keep_alive expires.
    """
    data: dict[str, Any] = {"http": http_status, "model": wanted_model,
                            "available": None, "resident": None,
                            "expires_at": None}
    if http_status is None:
        return Check("Ollama API", FAIL, "unreachable on 127.0.0.1:11434", data)
    if not 200 <= http_status < 300:
        return Check("Ollama API", FAIL, f"HTTP {http_status}", data)

    names = []
    if isinstance(tags, dict):
        names = [m.get("name") for m in tags.get("models", [])
                 if isinstance(m, dict)]
    available = wanted_model in names
    data["available"] = available

    resident_entry = None
    if isinstance(ps, dict):
        for m in ps.get("models", []):
            if isinstance(m, dict) and m.get("name") == wanted_model:
                resident_entry = m
                break
    data["resident"] = resident_entry is not None
    if resident_entry is not None:
        data["expires_at"] = resident_entry.get("expires_at")

    if not available:
        return Check("Ollama API", FAIL, f"ready, but {wanted_model} is not installed",
                     data)
    if resident_entry is None:
        return Check("Ollama API", WARN,
                     f"ready / {wanted_model} installed but not resident "
                     "(next turn pays the load)", data)
    return Check("Ollama API", OK, f"ready / {wanted_model} resident", data)


# --------------------------------------------------------------------- hermes
def evaluate_hermes(http_status: int | None, listening: bool) -> Check:
    """Gateway health. The listener existing is the fallback signal."""
    data = {"http": http_status, "listening": listening}
    if http_status is not None and 200 <= http_status < 300:
        return Check("Hermes", OK, "ready (/health)", data)
    if listening:
        return Check("Hermes", WARN,
                     f"listening on :8644 but /health returned {http_status}", data)
    return Check("Hermes", FAIL, "not reachable on 127.0.0.1:8644", data)


# ------------------------------------------------------------------ watchdog
def evaluate_watchdog(events: Sequence[Mapping[str, Any]],
                      runtime_pid: int | None) -> Check:
    """Has the watchdog seen THIS runtime, and has it had to restart it?

    Restarts are counted only for the current runtime generation. An old
    restart from a previous boot is history, not a live fault, and reporting it
    as one would make the check cry wolf after every deploy.
    """
    start_idx = 0
    for i, ev in enumerate(events):
        if ev.get("event") == "start":
            start_idx = i
    current = list(events[start_idx:])
    observed = [ev for ev in current
                if ev.get("event") == "observed" and ev.get("pid") == runtime_pid]
    restarts = [ev for ev in current if ev.get("event") in ("restart", "restarted")]
    heartbeats = [ev for ev in current if ev.get("event") == "heartbeat"]
    last_seen = (observed or heartbeats or current or [{}])[-1]

    data = {
        "observed_current_runtime": bool(observed),
        "restarts_since_start": len(restarts),
        "last_restart_reason": restarts[-1].get("reason") if restarts else None,
        "last_event_ts": last_seen.get("ts"),
        "last_observed_state": last_seen.get("state"),
    }
    if not current:
        return Check("Watchdog log", WARN, "no events in the watchdog log", data)
    if restarts:
        return Check("Watchdog log", WARN,
                     f"{len(restarts)} restart(s) this generation "
                     f"(last: {data['last_restart_reason']})", data)
    if runtime_pid is not None and not observed:
        return Check("Watchdog log", WARN,
                     f"has not observed the current runtime (PID {runtime_pid})", data)
    return Check("Watchdog log", OK,
                 f"0 restarts, last seen {data['last_event_ts']}", data)


# --------------------------------------------------------------- login items
def evaluate_login_item(name: str, registered: bool, pid: int | None) -> Check:
    """Login-item registration, as far as it can be read without root.

    `sfltool dumpbtm` needs root, so this reports what launchd exposes to the
    user: an "application.<label>.*" job means the item is registered and
    running. Absence here does not prove it is unregistered -- only that it is
    not currently running under launchd -- so it is reported as UNKNOWN rather
    than as a failure.
    """
    data = {"registered": registered, "pid": pid}
    if registered:
        return Check(name, OK, f"registered, running (PID {pid})", data)
    return Check(name, WARN, "no launchd application job — registration unknown",
                 data)


# ---------------------------------------------------------------- known gaps
def load_gaps(raw: str | None) -> list[dict[str, Any]]:
    """Known gaps come from config, so closing one is not a code change."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    gaps = parsed.get("gaps") if isinstance(parsed, dict) else None
    return [g for g in gaps if isinstance(g, dict)] if isinstance(gaps, list) else []


# ------------------------------------------------------------------- overall
def overall_status(checks: Iterable[Check]) -> str:
    """FAIL beats WARN beats OK. Known gaps are not checks and never appear here."""
    worst = max((_SEVERITY.get(c.status, 0) for c in checks), default=0)
    return {0: HEALTHY, 1: DEGRADED, 2: FAILED}[worst]
