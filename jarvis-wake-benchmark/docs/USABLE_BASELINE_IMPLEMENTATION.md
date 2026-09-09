# Usable baseline — manual activation and the wake feed gate

## What changed

| file | before | after |
|---|---|---|
| `bin/jarvis_runtime.py` | `398b2ff5…4bafe` | `55c0e78badeb2589ef3a3af49d312a25420edcdeaaf2ecfb498bd62e40b51506` |
| `bin/shared_audio.py` | `6c140067…5de7` | `49213b6c01947a365c49ae6e1f457145d1747b534c3a23f8907573dc4bec4912` |
| `bin/activation_socket.py` | *(new)* | `b15698825d5fa64861f25c8325501f6787f1e4efc5c2e1d5f7ba4d423ba3a81e` |
| `tools/wake_word.py` | `0d39b668…b201b399` | **unchanged** |
| `tools/voice_mode.py` | `7cfc1e4d…ce1410` | **unchanged** |
| LaunchAgents | — | **unchanged** |

Rollback copies with their sums: `~/AI-Lab/jarvis-wake-benchmark/rollback/baseline-20260831-234838/`.

## Manual activation

```
Cmd+Shift+J (jarvis-hotkey)        jarvis-activate (CLI)
          └─────────┬───────────────┘
                    │  b"ACTIVATE"
                    ▼
   AF_UNIX SOCK_STREAM  ~/.hermes/runtime/jarvis-activate.sock
                    │   srw------- (0600), dir drwx------ (0700), uid 502
                    │   peer uid checked via LOCAL_PEERCRED
                    ▼
             on_wake("manual activation")     ← jarvis_runtime.py, shared with wake
                    ▼
             _handle_wake(box)                ← identical turn
```

`activation_socket.py` is a new file rather than sixty lines inside
`jarvis_runtime.py`. The runtime is already long, and a socket that accepts
input from outside the process is exactly the thing that should be testable on
its own — which it now is, 31 tests, most of them refusals.

### Security

Verified by test, not by inspection (`tests/test_activation_socket.py`):

- **AF_UNIX only.** An AST check asserts there is exactly one `socket()` call
  and its arguments are `AF_UNIX, SOCK_STREAM`, and that `AF_INET`/`AF_INET6`
  appear nowhere.
- **No shell, no eval, no recording.** AST checks on the import list and on
  every call name: no `subprocess`, `eval`, `exec`, `system`, `popen`, `spawn`,
  `fork`, `sounddevice`, `voice_mode`, `shared_audio`.
  (A first version of this test grepped the source and failed on the module's
  own docstring, which names those modules to say it does not import them. The
  parsed AST cannot be fooled that way.)
- **One verb.** 13 hostile payloads — `activate`, `ACTIVATEX`,
  `ACTIVATE ; rm -rf /`, `{"cmd":"shell","args":["/bin/sh"]}`,
  `ACTIVATE\x00EXTRA`, `/etc/passwd`, 32 bytes of `ACTIVATE` repeated, raw
  bytes, two verbs in one write — each starts no turn and increments
  `rejected_payload`. Reads are capped at `MAX_READ = 16`.
- **Peer uid.** `getsockopt(SOL_LOCAL, LOCAL_PEERCRED)`, `struct xucred`, with
  the version field checked so a layout change is noticed rather than
  reinterpreted as a uid. A uid that is not ours is refused; **`None` is also a
  refusal**, never an unknown-so-allow.
- **Filesystem.** Socket 0600 inside a 0700 directory, both asserted from the
  live socket. A regular file at the path is refused and **not deleted**; a
  symlink is refused and **not followed** (`lstat`, not `stat`) — both proved by
  checking the victim file's contents survive.
- **Not a denial of service.** 60 bad payloads in a row, then a good one still
  works.

### Measured against the live runtime

10 activations via the CLI, `activation_live_test.py`:

```
10 activations started 10 turns                       PASS   10/10
every turn had its own (pid, turn_id)                 PASS   (60957, 13..22)
no activation produced more than one turn             PASS   0 duplicated
a mid-turn activation is refused as busy              PASS   1 wake line for 6 activations
unknown payloads start no turn                        PASS   9 payloads -> 0 turns
PA_OPEN_COUNT is still 1                              PASS
PA_START_COUNT is still 1                             PASS
replacements is still 0                               PASS
the gate engaged at least once per turn               PASS   23 engagements
the feed reopened between turns                       PASS   engine_frames_fed rose
```

All ten ran `LISTENING → THINKING → LOCAL_FAST → SPEAKING → IDLE`.

Identity is `(pid, turn_id)`, never `turn_id` alone: the counter restarts at 1
when the runtime restarts, so 9 → 1 means a new process. The first run of this
test scored that as a failure before the check was corrected.

### The hotkey

`Cmd+Shift+J`, via `RegisterEventHotKey` — the only global hotkey API on macOS
that needs no TCC grant. Registration succeeds with no permission prompt, and
the process is registered with the window server as a UIElement:

```
97) "JarvisHotkey" ASN:0x0-0xf28f28:
    pid = 66741 type="UIElement" flavor=3 Arch=ARM64
```

**A first build armed the hotkey without error and never received a key event.**
A bare command-line executable is not a registered application, so nothing
routed Carbon events to it. The fix is `NSApplication.shared` with
`.accessory`, `GetEventDispatcherTarget()` instead of
`GetApplicationEventTarget()`, and `app.run()` instead of `CFRunLoopRun()` — the
AppKit loop is what pumps the dispatcher the handler sits on.

**Keypress delivery is unverified.** Two windows totalling 290 s were opened
with the helper armed and no press arrived, so the end of the chain — a physical
`Cmd+Shift+J` reaching `sendActivate()` — has not been observed. Everything up
to it has: registration succeeds, the process is a registered UIElement, and the
socket it writes to demonstrably starts turns. Posting a synthetic keystroke
would need Accessibility, which is the permission this design exists to avoid.

## The wake feed gate

`SharedAudioInput.gate_wake_feed()` / `ungate_wake_feed()`. While gated the
physical callback, the `.copy()`, the 3840→1280 aggregation and the resample all
still run; only `ww.feed_audio(...)` is skipped. Counters:
`wake_feed_enabled`, `wake_feed_gated_frames`, `gate_engagements`.

Order in the turn, asserted by a source-order test because a helper-level test
cannot see it:

```
gate_wake_feed()  →  capture_start  …  playback_end
  →  wait 400 ms  →  resume_listening()  →  ungate_wake_feed()
```

`resume_listening()` before the ungate, deliberately: restarting the detector
drains its queue as it starts, so reopening the feed afterwards means no frame
from this turn can be waiting for it.

`SETTLE_AFTER_SPEAKING_S = 0.400`, a constant. `playback_end` marks when
`afplay` returned, not when the room went quiet.

### Correction: upstream already drains the queue

The design report said the detector's 5.12 s backlog was served the instant
`resume()` lifted the gate, and named that as the self-echo mechanism. **That
was wrong.** `wake_word._run` begins, in `external_audio` mode, with:

```python
if self.external_audio:
    # Drain any stale frames from a previous arm.
    while True:
        self._audio_q.get_nowait()
```

`resume()` calls `start()`, which starts `_run`, which drains. A backlog
accumulated during a turn is discarded before any inference runs.

So B1 is **not** a fix for a live bug. It is defence in depth for a property
currently guaranteed by another module's internals, plus the counters that make
"gated during SPEAKING" an observable fact rather than an inference from two
codebases agreeing. The settle period keeps its own justification.

### Measured

Over the generation running this code: 23 turns, 14 of them with TTS playback.

```
socket ACTIVATE accepted        39
  of which refused as busy      16
  → turns from manual           23
LISTENING publishes             23
  → turns from the detector      0

SELF_TTS_WAKE_EVENTS         = 0
DUPLICATE_WAKE_AFTER_RESUME  = 0
PA_OPEN_COUNT = 1   PA_START_COUNT = 1   replacements = 0   feed_errors = 0
gate_engagements = 23   wake_feed_gated_frames = 4447
```

`QUEUE_DEPTH_AT_PAUSE` / `QUEUE_DEPTH_BEFORE_RESUME` are **not reported**: both
would require reading `detector._audio_q`, and §7 forbids touching that private
queue from production code. The property that matters was measured behaviourally
instead — no duplicate wake after any of 23 resumes.

`wake_feed_gated_frames` is smaller than a turn's length suggests because the
idle listener is not called at all while the recorder is capturing; the gate
only counts frames between `capture_end` and the ungate.

## Real turn and HUD v2 lifecycle

Turn 26, via manual activation, watched through the state file the HUD reads:

```
  0.00s  IDLE        turn=None  route=None        lat={}
  2.40s  LISTENING   turn=26    route=None        lat={}
 10.10s  THINKING    turn=26    route=None        lat={wake_to_capture:227, capture_duration:7714}
 14.25s  LOCAL_FAST  turn=26    route=LOCAL_FAST  lat={+stt:1048, +router:3097}
 18.14s  SPEAKING    turn=26    route=LOCAL_FAST  lat={+backend:3898}
 30.20s  IDLE        turn=None  route=None        lat={}
```

12 of 12 lifecycle checks pass: schema 2 throughout; `turn_id` an integer from
the LISTENING publish, identical across the turn, null at IDLE; `route` null
through THINKING, explicit and equal to the state while the backend works, held
through SPEAKING, null at IDLE; `latency_ms` filling monotonically (0→2→4→5
stages) and cleared at IDLE.

HUD release build: `verify_bundle.sh` 56 passed / 0 failed. `heard_text`,
`MockStateProvider`, `LatencyBreakdown`, `FutureHUDContext` and the
`MOCK ONLY` header are all absent from the shipped binary.

### One narrow finding

On the **early-return** paths — `no speech`, `empty transcript` — the runtime
publishes IDLE from inside the `try` block, before the `finally` clause runs
`end_turn()`. That publish still carries the turn's `turn_id` and its
latencies:

```
10.33s  IDLE  turn=25  route=None  lat={wake_to_capture:1, capture_duration:8006, stt:97}
```

Transient, a few hundred milliseconds, and harmless to the HUD, which renders
IDLE without turn context anyway. It does not occur on a full turn — the check
`no IDLE publish carries turn context` passes for turn 26. Left as-is: fixing it
means moving `end_turn()` ahead of two early returns in shared turn logic, which
is not a change to make at the end of a session. Recorded for the backlog.

## Also found: a watchdog aliasing defect

The first ten-activation run tripped the external watchdog:

```
{"event": "restart_decision", "pid": 60044, "listening_s": 45.0,
 "reason": "LISTENING for 45.0s (threshold 45s)"}
{"event": "restart_result", "ok": true, "old_pid": 60044, "new_pid": 60957,
 "recovery_ms": 1512, "note": "runtime restarted and reached a healthy state"}
```

No turn was actually stuck. The watchdog polls every 5 s and only registers a
state change when it *sees* one; ten back-to-back 8-second silent captures with
~1 s IDLE gaps never presented an IDLE at a poll instant, so its monotonic
LISTENING timer never reset and nine consecutive turns aliased into one
45-second LISTENING.

The watchdog behaved correctly on the evidence it had, and recovered the process
in 1512 ms. But **rapid successive turns can trigger a spurious restart**, and
that is reachable in ordinary use now that a turn can be started by pressing a
key. Not fixed here — the watchdog is on §0's untouchable list — and not caused
by these changes; manual activation is what made it observable.

Suggested shape when it is addressed: reset on the `since` field changing, not
only on the state string changing. A new `since` with the same state is a new
turn.

## Test inventory

```
tests/test_activation_socket.py    31 passed   security + behaviour, isolated sockets
tests/test_wake_feed_gate.py       11 passed   gate, stream untouched, source order
tests/  (excluding test_regression)85 passed
tests/test_regression.py           26/26       run as a script; it calls SystemExit on import
activation_live_test.py            10/10       live, 10 turns + busy + bad payloads
turn_observer.py                   12/12       live, turn 26 lifecycle
jarvis-hud verify_bundle.sh        56/56
```

`tests/test_shared_audio.py::test_wake_is_starved_while_recording` failed once
and then passed 3/3 on this code **and** 3/3 on the untouched baseline. It reads
`idle_chunks` before calling `rec.start()`, so the audio thread can deliver one
more frame in between — a pre-existing race, off by exactly one frame, not a
regression.
