# RUNTIME_QOS — why the background runtime was 19x slower, 2026-08-30

## The reported problem

A real turn: a 3.7 s clip (1.4 s of speech after VAD) took **24.58 s** to
transcribe, while the same file transcribed standalone in 0.71 s. The wake-time
Ollama warm ran for 17.01 s inside that window, so contention was the obvious
suspect.

## Contention was measured, and refuted

Same whisper model, same audio — the actual recording from the slow turn —
three Ollama states:

| condition | Ollama during STT | STT | free RAM | swapins |
|---|---|---:|---:|---:|
| UNLOADED | not loaded | 0.71 s | 1149 MB | 0 |
| COLD_CONCURRENT | **loading (17.64 s)** | **0.73 s** | 1151 → 77 MB | 0 |
| RESIDENT_IDLE | loaded, idle | 0.73 s | 79 → 68 MB | 0 |

Second pass, reversed order: 0.81 / 0.72 / 0.72 s.

STT does not care. Not while Ollama is loading, not with 7.23 GB resident, not
with free memory down to 60 MB. **The hypothesis is refuted**, so the wake-time
warm was removed for being useless rather than kept as insurance.

## The actual cause

The standalone runs were fast and the runtime's runs were slow, with the same
file and the same code. The difference was not the work — it was the scheduler.

```
plist ProcessType = "Adaptive"
runtime process    PRI = 4
interactive shell  PRI = 31
```

Confirmed **under launchd**, not in a terminal — the discipline this project
adopted after macOS handed an unpermitted process digital silence rather than an
error. A probe agent was run at each ProcessType:

| ProcessType | PRI | load+first | warm runs | transcript |
|---|---:|---:|---|---|
| **Interactive** | 31 | 1.87 s | **0.68 / 0.67 / 0.67** | 'こんにちわ' |
| Adaptive | 4 | 20.08 s | 12.80 / 11.95 / 12.90 | 'こんにちわ' |
| Background | 4 | 19.35 s | 12.49 / 11.81 / 12.72 | 'こんにちわ' |

`ppid=1` and `XPC_SERVICE_NAME` on every row confirm genuine launchd spawns.

`Adaptive` is documented as able to move between bands. On this machine it sat
in the background band and performed identically to `Background`. For a voice
assistant whose whole job is to answer quickly, `Interactive` is the correct
classification — Apple's own description of it is work that is critical to the
user experience and should not be throttled.

This also retroactively explains the 13.92 s decode recorded as **NOT
EXPLAINED** in [LATENCY.md](./LATENCY.md) in the previous phase. Same cause.

**Corroboration after the change**: startup whisper warm, same process, same
work, went from 7.19 s to **1.38 s**.

## Model residency

`keep_alive` is accepted **per request** on Ollama's native `/api/chat`
endpoint. Verified: one call with `"keep_alive": "60m"` and `ollama ps` reports
`UNTIL: 59 minutes from now`. The OpenAI-compatible `/v1` surface has no such
field, which is why warm-up moved to the native endpoint.

| option | idle cost | verdict |
|---|---|---|
| `keep_alive` 10m (server default) | 0 between turns | a quiet hour cost 17 s on the next turn |
| `keep_alive` -1 / persistent | 7.23 GB pinned forever | taxes Claude and Codex all day on a 32 GB machine |
| **`keep_alive` 60m, renewed each turn** | 7.23 GB while in use, released 1 h after the last turn | **chosen** |

Renewal is necessary and not decorative: the router and LOCAL_FAST both call
`/v1`, which carries no `keep_alive` and therefore resets the window to the
server default of 10 m. Without renewing, the 60 m residency would survive
exactly one turn. Renewal runs on a thread **after** the wake listener is
already re-armed, so a slow Ollama can never delay the next "Hey Jarvis".

`OLLAMA_KEEP_ALIVE=10m` in `local.ollama.serve.plist` was **not** touched, so
nothing else on this machine changes behaviour.

## TTS timeline

`hvoice.speak_text()` synthesizes and plays in one blocking call and returns
only when the audio has finished. The single mark it can honestly produce is
"playback ended" — and reporting that as first audio is what produced
`TTS=8282ms` for a synthesis that had actually finished in 729 ms.

The runtime now composes the same upstream pieces in the same order
(`prepare_spoken_text` → `text_to_speech_tool` → `play_audio_file`) so the two
costs are timed separately, and falls back to `speak_text` whole if any piece is
missing. Verified:

```
TTS_GENERATION=500ms  PLAYBACK_START_DELAY=0ms  PLAYBACK_DURATION=2755ms
TOTAL_TO_FIRST_AUDIO=552ms
```

**Honest limit.** `playback_start` is the instant the player is handed the file.
The gap between that and the first sample leaving the speaker is inside `afplay`
and is not observable from here — small, but not zero. `TOTAL_TO_FIRST_AUDIO` is
therefore a lower bound. What it is no longer is the playback-*end* time, which
is what it used to report.

## Barge-in

```
BARGE_IN_SUPPORTED = YES, but only as interactive-CLI internals
BARGE_IN_ENABLED   = NO
```

**Why.** Barge-in exists (193 references) but lives entirely in `cli.py` as
methods on the interactive session object — `_voice_barge_monitor`,
`_voice_barge_capture`, `_voice_submit_barge_utterance`. There is no reusable
API in `tools/`, so using it from the background runtime means reimplementing
the noise-floor calibration, the grace window, and the echo handling, not wiring
something up.

The echo handling is the part that argues for caution. Upstream had to add this,
at `cli.py:15091`:

> Dropping playback-phase barge transcript as TTS echo

That is upstream discovering, in its own code, that the assistant's own voice
comes back through the microphone. Our runtime would additionally have to hold
the wake mic **open during playback**, which is exactly the self-trigger case
the brief said not to force.

`stop_playback()` does exist in `tools/voice_mode.py`, so the interrupt half is
available whenever this is revisited deliberately.

## Files changed

| file | change |
|---|---|
| `~/Library/LaunchAgents/local.jarvis.runtime.plist` | `ProcessType` Adaptive → **Interactive** |
| `bin/jarvis_runtime.py` | native-endpoint warm + `keep_alive=60m` + per-turn renewal; wake-time warm removed; split TTS; new timeline marks |
| `bin/jarvis_router.py` | greeting spelling normalization |
| `bin/qos_stt_probe.py` | NEW — the launchd QoS probe |
| `tests/test_greetings.py` | NEW — 61 cases |

## Rollback

The plist has its own snapshot, so the QoS change reverts on its own:

```sh
cp ~/AI-Lab/hermes-jarvis/logs/local.jarvis.runtime.plist.pre-qos-20260830-010703 \
   ~/Library/LaunchAgents/local.jarvis.runtime.plist
launchctl bootout gui/$(id -u)/local.jarvis.runtime
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.jarvis.runtime.plist
```

**The source changes do not have a snapshot of their own.** No
`.pre-qos-*` copy of `jarvis_runtime.py` or `jarvis_router.py` was taken before
this phase's edits — stated plainly rather than papered over with a path that
does not exist. The nearest snapshots are:

| snapshot | reverts to |
|---|---|
| `*.pre-capscope-20260830-000501` | before LOCAL_FAST/LOCAL_TOOL — undoes this phase **and** the capability split |
| `*.pre-latency-20260829-231308` | before the instrumentation phase as well |

So a source rollback today is coarser than one phase. `launchctl bootout` stops
the runtime entirely if that is what is wanted in the meantime.

Ollama's LaunchAgent, `~/.hermes/config.yaml`, the upstream tree, the model and
the TTS provider were not changed and need no rollback.
