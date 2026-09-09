# HUD PHASE 1 — STATE DISCOVERY REPORT

Read-only audit, 2026-08-30. **Nothing was modified.** No file under
`~/AI-Lab/hermes-jarvis/bin/`, no LaunchAgent, no Hermes config, no routing, no
gate, no voice or TTS setting was touched.

---

## CURRENT_STATE_SOURCE

```
~/AI-Lab/hermes-jarvis/logs/jarvis_state.json
```

Written by `set_state()` at `bin/jarvis_runtime.py:135`. **Already
HUD-shaped**, and deliberately so — the docstring reads:

> "Publish the HUD state. A file, not a socket: the HUD is read-only and must
> never be able to reach back into the runtime."

Three properties that matter for a reader:

| property | detail |
|---|---|
| **atomic** | written to `.tmp` then `os.replace()` — a reader never sees a partial JSON |
| **liveness** | carries `pid`, so a stale file left by a dead runtime is detectable |
| **failure mode** | write errors are swallowed (`except OSError: pass`) — the file can go stale silently while the runtime keeps running |

The last row is a real constraint: a HUD must not assume the file is fresh just
because it exists.

---

## CURRENT_STATE_FIELDS

Live sample:

```json
{"state": "IDLE", "since": 1788044091.775396, "detail": "waiting for wake word", "pid": 82497}
```

| field | type | notes |
|---|---|---|
| `state` | string | the vocabulary below |
| `since` | float | `time.time()`, i.e. **wall clock**, not monotonic |
| `detail` | string | short human text; content depends on state |
| `pid` | int | runtime PID |

**Four fields. No `version`, no `route`, no `heard_text`, no `response_preview`,
no `latency_ms`, no `error` object.**

`detail` is currently bounded in every code path — the longest is
`reply[:60]` — but nothing enforces that, so a HUD must still cap it itself.

---

## PROPOSED_HUD_STATES — what the runtime actually emits today

From all 18 `set_state()` call sites, plus `set_state(route, "working")` at
line 398, which puts **route labels into the `state` field itself**:

| state | emitted at | `detail` carries |
|---|---|---|
| `OFFLINE` | startup, lease wait, shutdown | reason |
| `IDLE` | armed, no-speech, empty transcript | `listening on <device>` |
| `LISTENING` | capture open | `capturing utterance` |
| `THINKING` | STT running | `transcribing` |
| `ROUTING` | router returned | `<ROUTE> via <decided_by>` |
| `LOCAL_FAST` | dispatch running | `working` |
| `LOCAL_TOOL` | dispatch running | `working` |
| `LOCAL` | legacy label, still reachable | `working` |
| `WEB` | dispatch running | `working` |
| `CODEX` | dispatch running | `working` |
| `CLAUDE` | dispatch running | `working` |
| `CONFIRMATION_REQUIRED` | gate blocked the utterance | matched categories |
| `SPEAKING` | TTS starting | `reply[:60]` |
| `ERROR` | listener start failed / mic silent | `<reason>` |

**Observed in the real log** (counted): OFFLINE 34, IDLE 23, LISTENING 14,
THINKING 12, SPEAKING 12, ROUTING 12, LOCAL_FAST 8, LOCAL_TOOL 2, WEB 1,
LOCAL 1.

**Reachable in code but not yet observed**: `CODEX`, `CLAUDE`, `ERROR`,
`CONFIRMATION_REQUIRED`. A HUD must handle them; they simply have not fired
since this logging began.

---

## MISSING_STATES

Against the list in the brief:

| requested | status |
|---|---|
| IDLE, LISTENING, THINKING, ROUTING, LOCAL_TOOL, WEB, CODEX, CLAUDE, SPEAKING, ERROR | **present** |
| `TRANSCRIBING` | **not a state.** Folded into `THINKING` + `detail="transcribing"` |
| `LOCAL` | present, but the runtime now emits `LOCAL_FAST` / `LOCAL_TOOL`; bare `LOCAL` is legacy |

Missing **data**, not states:

| wanted by the brief | where it lives today |
|---|---|
| `heard_text` | log only — `logger.info("transcript=%r", ...)` at line 663 |
| `route` as its own field | encoded inside `detail` during ROUTING, and as the `state` value afterwards |
| `latency_ms` | log only — the `timeline …ms` line at line 678 |
| `response_preview` | partially: `SPEAKING`'s `detail` is `reply[:60]` |
| `error` object | no dedicated field; `ERROR` state + free-text `detail` |
| `version` | absent |

Real log lines showing what is available but unpublished:

```
transcript='マイッキング・ジャーバス'
route=LOCAL_FAST by=llm cats=[] llm_latency=2.674
timeline WAKE=377ms CAPTURE=4302ms STT=869ms GATE=0ms ROUTER=2674ms
  TTS_GENERATION=888ms PLAYBACK_DURATION=12770ms TOTAL_TO_FIRST_AUDIO=12822ms
```

---

## BACKEND_CHANGES_REQUIRED = **NO for Phase 1**

Everything Phase 1 needs — orb, expand on wake, state, route, speaking, error —
is derivable from the existing four fields:

- **state** → orb appearance directly
- **route** → the `state` value itself once dispatch starts; and `detail`
  (`"LOCAL_FAST via llm"`) during ROUTING
- **response preview** → `SPEAKING`'s `detail` (60 chars)
- **error** → `ERROR` state + `detail`
- **elapsed** → `now - since`

What Phase 1 **cannot** show without a backend change: the recognised user text,
and per-stage latency. Both exist in the process but are only logged.

A proposed schema is below. **It is a proposal only — no backend file is
modified in this phase, and doing so needs its own approval.**

```json
{
  "version": 1,
  "state": "THINKING",
  "route": "LOCAL_FAST",
  "detail": "transcribing",
  "heard_text": "…",
  "response_preview": "…",
  "since": 1788044091.775,
  "pid": 82497,
  "latency_ms": {"wake": 377, "stt": 869, "router": 2674, "backend": 3703, "tts": 888},
  "error": null
}
```

Diff vs today: `version`, `route`, `heard_text`, `response_preview`,
`latency_ms`, `error` are **added**; `state`, `detail`, `since`, `pid` are
**unchanged**. Additive only, so an old reader keeps working.

**A caution about `heard_text`.** Publishing the transcript widens what a
viewer process can see from "the assistant is busy" to "here is what the user
said in their home". That is a privacy decision, not a technical one, and it
should be an explicit choice rather than a side effect of wanting a nicer HUD.

---

## RECOMMENDED_FRAMEWORK = SwiftUI (native)

Verified on this machine: **macOS 26.5.2, Xcode 26.6, Swift 6.3.3,
arm64-apple-macosx26.0**. Everything needed is installed.

| | SwiftUI menu bar | SwiftUI floating panel | Electron / webview |
|---|---|---|---|
| idle RAM | ~30–60 MB | ~30–60 MB | 150–300 MB+ |
| startup | <0.5 s | <0.5 s | 1–3 s |
| transparency / click-through | native (`NSWindow`, `ignoresMouseEvents`) | native | fights the platform |
| always-on-top | native (`.statusBar`, `.floating` level) | native | possible, clumsy |
| menu bar integration | **`MenuBarExtra`, first-class** | needs a companion item | needs a helper |
| animation | SwiftUI, GPU-composited | same | DOM/CSS, heavier |
| signing | ad-hoc for local use | same | plus notarising a runtime |
| sandbox | straightforward; read-only file access | same | far more surface |
| JSON watching | `DispatchSource` / `FSEvents` native | same | needs a bridge |

Electron would add a browser engine to display four fields of JSON. Rejected on
that basis alone; the brief's instinct is right.

**Recommendation: A + B together** — `MenuBarExtra` for the always-present
glyph, plus a borderless floating `NSPanel` that appears on wake and collapses
back to `IDLE`. That matches the Phase 1 design and needs no third-party
dependency.

---

## RECOMMENDED_ARCHITECTURE

```
jarvis_runtime.py  ──writes──▶  logs/jarvis_state.json  ──reads──▶  HUD
     (authority)                   (atomic, one-way)          (viewer only)
```

- **Transport**: `DispatchSource.makeFileSystemObjectSource` on the containing
  directory. `os.replace()` swaps the inode, so watching the *file* would break
  after the first update — the directory must be watched, and the descriptor
  re-armed. A **250 ms poll** is the fallback if that proves fiddly; at four
  small fields the cost is negligible either way.
- **Debounce**: coalesce events within ~50 ms; a turn can emit several states in
  quick succession.
- **Staleness**: treat state as unknown if `pid` is not alive
  (`kill(pid, 0)`) or `since` is far in the past. The runtime's write path
  swallows errors, so a fresh-looking file is not proof of a live runtime.

---

## SECURITY_BOUNDARY

The HUD is a **viewer**. It gets no capability it does not need to draw four
fields:

| forbidden | why |
|---|---|
| shell / `Process` / `NSTask` | a display surface that can run commands is an agent |
| any filesystem write outside its own container | it has nothing to persist but window position |
| secrets, API keys, Codex/Claude auth | it needs none — it renders state, not credentials |
| network of any kind | the state file is local; there is nothing to fetch |
| writing to anything the runtime reads | that would make the channel bidirectional |

Hardening for untrusted input — the state file is written by a trusted process,
but a HUD that trusts its input completely is a HUD that crashes on a bad write:

- cap every displayed string (e.g. 120 chars) before rendering
- treat unknown `state` values as a neutral "unknown" glyph, never crash
- ignore unknown JSON fields
- cap parsed file size (e.g. 64 KB) and ignore anything larger
- never interpret `detail` as markup, a URL, or a path

`HUD_SECURITY.md` in this directory already states this contract from an earlier
design pass; this audit is consistent with it.

---

## PROPOSED_DIRECTORY

```
~/AI-Lab/jarvis-hud/
```

**Separate from the runtime, not `hermes-jarvis/hud/`.** Reasons:
`~/AI-Lab/hermes-jarvis/` is not a git repository, holds the live production
runtime and its logs, and is the directory every prior phase has been told not
to disturb. A Swift package with its own build products, `.build/`, and
`DerivedData` does not belong inside it. A separate directory also means
deleting the HUD cannot touch the runtime.

---

## EXPECTED_RAM / EXPECTED_IDLE_CPU

Estimates, to be replaced by measurements at the end of Phase 1:

| | target |
|---|---|
| idle RAM | 30–60 MB |
| idle CPU | ~0 % — no timer, no animation while `IDLE`/`OFFLINE` |
| active CPU |低 — animation only during LISTENING / THINKING / SPEAKING |

Animation is driven by state, not by a free-running clock. `IDLE` draws a static
orb and schedules nothing.

---

## RISKS

1. **Stale state after a runtime crash.** The write path swallows `OSError`, so
   the file can stop updating silently. Mitigated by the `pid` liveness check —
   but it means "the HUD looks right" is not evidence the runtime is alive.
2. **`since` is wall-clock.** An NTP step or a sleep/wake can make elapsed time
   negative. The HUD must clamp rather than display nonsense. (The runtime's own
   `Timeline` uses `monotonic` for exactly this reason; the state file does not.)
3. **State can move faster than the eye.** `GATE=0ms` and `ROUTER=2674ms` mean
   some states last milliseconds. Without a minimum dwell time the HUD will
   flicker.
4. **`CODEX` / `CLAUDE` / `ERROR` / `CONFIRMATION_REQUIRED` are untested paths**
   for display — reachable in code, never yet seen in the log. Mock mode must
   cover them.
5. **Two surfaces both wanting attention.** The runtime already speaks; a HUD
   that also demands focus could be worse than none. Phase 1 stays passive: no
   focus stealing, no notifications, no sound.
6. **Scope creep toward Phase 3.** The approval UI is explicitly out of scope
   here, and the one-way property is what makes Phase 1 safe. It must not be
   quietly broken to "prepare" for later phases.

---

## PHASE1_IMPLEMENTATION_PLAN

Mock first, as the brief requires — the production runtime is not connected
until the mock is stable.

1. **Swift package skeleton** — `MenuBarExtra` + floating `NSPanel`, no state
   source yet.
2. **State model & reader** — Codable struct, tolerant decoding, size and length
   caps, staleness/pid logic. Pure, unit-testable, no I/O in the view.
3. **Mock driver** — cycles IDLE → LISTENING → THINKING → ROUTING → LOCAL_FAST →
   CODEX → CLAUDE → SPEAKING → ERROR → CONFIRMATION_REQUIRED from a fixture
   file, including hostile inputs (huge string, unknown state, malformed JSON,
   missing fields).
4. **Visual states** — orb, expand, route label, preview, error indicator, with
   a minimum dwell so fast transitions stay readable.
5. **Measure** — idle CPU/RAM over a sustained period; confirm no timer runs at
   idle and no leak across many transitions.
6. **Connect to the real file, read-only** — the only step that touches anything
   outside the HUD directory, and it only *reads*.
7. **Document** — README, security contract, rollback.

---

## FILES_TO_CREATE

All under `~/AI-Lab/jarvis-hud/` (new, empty today):

```
Package.swift
Sources/JarvisHUD/JarvisHUDApp.swift        MenuBarExtra + panel lifecycle
Sources/JarvisHUD/JarvisState.swift         model + tolerant decoding + caps
Sources/JarvisHUD/StateWatcher.swift        directory watch, debounce, staleness
Sources/JarvisHUD/OrbView.swift             idle/listening/thinking/speaking
Sources/JarvisHUD/PanelView.swift           route label, preview, error
Sources/JarvisHUD/MockDriver.swift          fixture-driven state cycling
Tests/JarvisHUDTests/StateDecodingTests.swift
mock/*.json                                 including malformed fixtures
README.md  SECURITY.md  ROLLBACK.md
```

## FILES_TO_MODIFY

**None.**

Phase 1 reads `~/AI-Lab/hermes-jarvis/logs/jarvis_state.json` and writes
nothing. The proposed richer schema above would require editing
`bin/jarvis_runtime.py`, and that is **not** part of Phase 1 — it is a separate
change needing its own approval, and this report stops short of it.
