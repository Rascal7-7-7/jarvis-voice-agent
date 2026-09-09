# Usable baseline — Phase A and B design

Design only. §0 forbids editing `jarvis_runtime.py`, `shared_audio.py`,
`wake_word.py`, `voice_mode.py`, the HUD, the watchdog, ACK, the router, the
security gate, TTS, the Hermes config and the LaunchAgents, so nothing here is
implemented. The patch points are named exactly so that implementation is
mechanical once approved.

## Why this is the priority

A 150-second observation window was opened for Phase C with the runtime IDLE and
the wake listener armed. **No activation occurred.** The state file never left
IDLE. That matches the device A/B result from the previous phase — 0/5 fire,
median score 0.0011 — and it is the concrete reason requirement 2 of the usable
baseline exists.

There is no existing control surface to fall back on: the runtime's only
external interface is the state file, and `set_state` is explicit that it is a
one-way publish — *"A file, not a socket: the HUD is read-only"*. So today a
turn can only begin when the wake model fires, and the wake model currently
does not fire for this user on this microphone.

---

# Phase A — manual activation fallback

## Shape

```
  Cmd+Shift+J  (global hotkey helper)          jarvis-activate  (CLI, for tests)
             │                                        │
             └──────────────┬─────────────────────────┘
                            │  writes the fixed bytes  b"ACTIVATE\n"
                            ▼
        AF_UNIX SOCK_STREAM  ~/.hermes/runtime/jarvis-activate.sock
                            │      socket 0600, directory 0700
                            ▼
        runtime: activation listener thread
                            │      verifies peer uid, compares payload == b"ACTIVATE"
                            ▼
                    on_wake()          ← jarvis_runtime.py:882, unchanged
                            │
                            ▼
                    _handle_wake(box)  ← the identical turn, unchanged
```

## Hotkey choice: Cmd+Shift+J

Conflicts were checked against the live system, not assumed.

| candidate | verdict |
|---|---|
| **Option+Space** | **rejected.** `com.apple.symbolichotkeys` id 60 is *enabled* and bound to 前の入力ソース — the IME switch, pressed constantly by a Japanese-language user. |
| Shift+Option+Space | rejected. id 156 enabled. |
| Cmd+Space / Space | rejected. Spotlight and Finder search (ids 64, 65, both enabled). |
| **Cmd+Shift+J** | **chosen.** No entry in `symbolichotkeys` uses keycode 38 (J). Mnemonic. Not a combination typed by accident. |

No third-party hotkey owner is installed: no Alfred, Raycast, LaunchBar,
Quicksilver, Keyboard Maestro, BetterTouchTool, Hammerspoon or
Karabiner-Elements, and none running.

## Why `RegisterEventHotKey` and not an event tap

`RegisterEventHotKey` (Carbon, still supported) is the only global hotkey API on
macOS that needs **no TCC permission**. The alternatives both add a prompt and a
new privacy surface:

| API | permission required |
|---|---|
| `RegisterEventHotKey` | none |
| `CGEventTap` | Input Monitoring |
| `NSEvent.addGlobalMonitorForEvents` | Accessibility |

Adding an Accessibility or Input-Monitoring grant — which lets the holder see
every keystroke on the machine — to build a *reliability fallback* would be a
poor trade. The helper needs to know about one key combination, and
`RegisterEventHotKey` tells it exactly that and nothing else.

The helper is a separate binary. **The HUD stays viewer-only**; no trigger goes
in it.

## Security properties, against the §SECURITY requirements

| requirement | how |
|---|---|
| network不要 | `AF_UNIX` / `SOCK_STREAM` only. No socket is ever created in `AF_INET`/`AF_INET6`, so there is nothing to reach from off-host. |
| shell injection不可 | The payload is compared `== b"ACTIVATE"`. No `subprocess`, no `eval`, no path, no format string. A non-matching payload is dropped and counted. |
| fixed command/eventのみ | One verb, no parameters — the same decision the ACK helper made (PLAY/PING/STATUS/QUIT, no parameterised command). A read is capped at 16 bytes so an unbounded write cannot grow memory. |
| user uidのみ | Socket 0600 inside a 0700 directory, plus an explicit peer check: `getsockopt(SOL_LOCAL, LOCAL_PEERCRED)` and reject any uid that is not `os.getuid()`. Mode alone is not relied on. |
| runtimeへ「turn開始」を伝えるだけ | The socket carries no command text, no arguments, no file path. |
| Router/Security Gateを迂回しない | Entry is `on_wake()`, so the turn is byte-identical afterwards: capture → STT → `_dispatch` → gate → backend → TTS. Nothing is skipped and nothing new is reachable. |
| command contentをtrigger IPCに含めない | The command is **spoken** and captured by the microphone exactly as after a wake. The trigger only says "start listening". |

Stale-socket handling copies the ACK helper: `O_NOFOLLOW`-equivalent check,
directory ownership verified, an existing socket removed only when it is a
socket owned by this uid.

## Architecture: no second microphone stream

This is the constraint §IMPORTANT ARCHITECTURE sets, and it is satisfied
structurally rather than by care:

- The activation path never calls `SharedAudioInput.open_once()`,
  `voice_mode.AudioRecorder()`, or anything in `sounddevice`. The code that
  opens a stream is **not reachable** from the listener thread.
- It calls `on_wake()`, which runs the existing turn on the existing recorder
  against the existing single physical stream.
- Therefore `PA_OPEN_COUNT=1` / `PA_START_COUNT=1` are preserved because no
  additional open or start exists to be counted, not because a counter is
  guarded.
- `on_wake()` already takes `inflight` with `blocking=False`, so pressing the
  hotkey during a turn logs "a turn is already in flight" and returns, instead
  of starting a second turn.

## Patch points

Additive, one production file:

1. **`bin/jarvis_runtime.py`** — a module-level `_serve_activation(on_wake, stop)`
   (~60 lines) and one `threading.Thread(target=_serve_activation, args=(on_wake, _stop), daemon=True).start()`
   placed immediately after `set_state("IDLE", f"listening on {device_name}")`
   (currently line 929), so the socket only exists once a turn can actually be
   served.
2. Nothing else. `shared_audio.py`, `wake_word.py` and `voice_mode.py` are not
   touched.

New, outside production: `jarvis-activate` (CLI) and `jarvis-hotkey` (the Swift
helper), both in their own directory with their own tests.

## One open decision, for approval

`_handle_wake` begins with:

```python
if not ww.pause_listening(owner=owner):
    logger.warning("wake fired but the listener lease was not ours")
    return
```

The wake microphone is a machine-wide lease; an interactive `hermes chat` can
hold it, and then the runtime sits in OFFLINE waiting. A manual activation
arriving in that state would abort here.

- **(a) Leave it.** Manual activation requires an armed detector. This covers
  the failure actually observed — the detector is armed and healthy, it simply
  does not score this user's voice — and changes `_handle_wake` not at all.
  **Recommended.**
- **(b) Add a `manual=True` path** that proceeds without the lease. More useful
  if the lease itself is the problem, but it modifies shared turn logic and
  needs its own reasoning about two surfaces owning the mic.

Not decided here.

---

# Phase B — self-echo wake gate

## The mechanism, located

Reading the code rather than inferring from symptoms:

```
wake_word.py:1098   def pause(self):  self._halt_thread()      # consumer thread only
wake_word.py:1483   feed_audio(...)   → det.feed(pcm)          # no paused check
wake_word.py:1033   def feed(...):    self._audio_q.put_nowait(chunk)
wake_word.py:1101   def resume(self): self.start()             # consumer restarts
```

`pause()` stops the *consumer*. `feed()` keeps *producing*. Neither drains
`_audio_q`, which holds 64 frames of 1280 samples at 16 kHz — **5.12 seconds**.
`shared_audio` goes on feeding it for the whole turn.

So when `resume_listening()` runs in `_handle_wake`'s finally block, the
consumer restarts and immediately processes up to 5.12 s of audio captured
*while JARVIS was speaking*.

The wake listener is already gated across SPEAKING — `pause_listening()` is
called before capture and `resume_listening()` only after `_speak` returns, and
playback is synchronous (`for p in paths: play_audio_file(p)` then
`tl.mark("playback_end")`). What is missing is not the gate. It is that the
backlog accumulated behind the gate is served the instant the gate lifts, and
that there is no settle period at all between playback ending and re-arming.

## Evidence status — honest

**Real in code. Weakly supported in the log.**

Of 41 wake events, 2 fell within 6 s of an IDLE publish — 4.07 s and 4.72 s,
both inside the 5.12 s window. The other 39 are 6 s to 40 minutes later
(median 1401 s). Four to five seconds is also a perfectly ordinary interval for
a person saying "Hey Jarvis" again, so those two are consistent with the
mechanism without demonstrating it.

### Retraction

The previous report stated that the runtime had been recording its own voice,
citing events 9, 10 and 12 (`ご指示をどうぞ`, `何かを手伝いできることはありますか`,
`ご視聴どうぞ。お帰りなさいシステムは正常です。`). **That was wrong.** Checked against
every `state=SPEAKING` line in the log, JARVIS never said any of those:

```
'ご指示をどうぞ'                    as a reply: 0 times
'何かを手伝いできることはありますか'  as a reply: 0 times
'お帰りなさいシステムは正常です'      as a reply: 0 times
'ご視聴'                           as a reply: 0 times
```

All three events were quiet — PEAK_RMS 652, 862, 567. `ご視聴` is the
best-known Whisper Japanese hallucination, learned from video sign-offs, and
Whisper reliably invents polite stock phrases from near-silence. These were
quiet-room false positives with hallucinated transcripts, not self-echo. The
dominant false-positive source remains room noise and the television.

## Measured now, before any change

Ten playbacks of a real JARVIS reply, runtime IDLE and armed, output on its
normal device:

```
SELF_TTS_WAKE_EVENTS = 0 / 10
PA_OPEN_COUNT = 1   PA_START_COUNT = 1   replacements = 0   feed_errors = 0
state = IDLE   turn_id = None
```

Caveat: output is 外部ヘッドフォン, so the acoustic coupling is whatever that
headset provides — this is the real production condition, but it is not proof of
immunity with speaker output. And it exercises the **armed** state, not the
paused→resume backlog path, which needs a real turn to reach.

## Proposed fix

Two options. The stream is never stopped or closed in either.

**B1 — prevent the backlog (recommended).** In `shared_audio`, skip the
`ww.feed_audio(...)` call while a gate flag is set, and have the runtime set it
for the duration of the turn. Nothing accumulates, so nothing is served on
resume. One additive method plus one condition in `shared_audio.py`; the
physical callback, the copy, the aggregation and the resample all continue
untouched, so `PA_OPEN_COUNT` / `PA_START_COUNT` / `replacements` cannot move.

**B2 — drain the backlog.** In `jarvis_runtime.py`'s finally block, empty the
detector's queue before `resume_listening()`. Confined to one file, but it
reaches into `detector._audio_q`, a private attribute of another module, and it
cleans up a condition B1 prevents from arising.

**Settle period — needed either way.** Insert a bounded wait between
`playback_end` and `resume_listening`. `playback_end` marks when `afplay`
returns, which is not when the room goes quiet: buffer drain and immediate
reverb outlast it. **400 ms** recommended — long enough to cover both, short
enough not to be felt before the next "Hey Jarvis". A constant, not a tunable.

## Test plan, once implemented

```
SELF_ECHO_GATE            gated during SPEAKING, re-armed after the settle
SELF_TTS_WAKE_EVENTS      0, over >= 10 real turns with TTS playback
PA_OPEN_COUNT             1
PA_START_COUNT            1
REPLACEMENTS              0
```

Plus one negative control: with the gate disabled, a self-echo wake should be
*producible* on demand. A gate that is never exercised has not been shown to
work, and B1 is only worth its lines if the condition it prevents is real.
