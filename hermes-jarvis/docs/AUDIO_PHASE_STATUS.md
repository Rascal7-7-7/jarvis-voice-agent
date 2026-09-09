# Audio phase — status at 2026-08-31 00:22

Paused, deliberately, because the room is too noisy to evaluate what is left.
Nothing is mid-change: the runtime and the watchdog are both live and healthy.

## Verdict

```
SHARED_STREAM_CORE       = PROVISIONAL PASS
AUDIO_QUALITY_VALIDATION = PENDING
3_TURN                   = PENDING
10_TURN                  = PENDING
20_TURN                  = PENDING
PHASE1C                  = BLOCKED

ACK_SUBSYSTEM            = PREPRODUCTION READY, FROZEN 2026-08-31
                           (not integrated; gated behind the turn tests above)
```

The ACK work below the fold is complete and closed. It changes nothing about
the gate: the turn tests still have to pass first, and they are still blocked on
a quiet room.

"Provisional" is doing real work in that sentence. What is proven is that the
CoreAudio deadlock's hot path is gone. What is not yet proven is that capture
quality is unchanged, because every attempt to measure it ran into background
noise that invalidates the measurement.

## What was proven, and how

The bug: four production deadlocks, always the same stack —

```
recorder thread   Pa_StartStream -> AudioDeviceStart_mac_imp
                  -> HALB_IOThread::StartAndWaitForState
                  -> HALB_Guard::WaitFor -> __psynch_mutexwait
IOThread.client   HALC_ProxyIOContext::IOWorkLoop -> mach_msg2_trap
                  (holding the guard, waiting on coreaudiod)
```

A fresh process opened the same device in ~85 ms while the wedged one stayed
stuck for minutes, and `coreaudiod` was healthy throughout, so the damage was
confined to that process's HAL client state — not recoverable from inside it.
Hangs 3 and 4 happened on a recorder's very first stream, so close/reopen was
not required; starting a stream at all was.

Option B removes that call from the turn path. Measured in production over
about 80 minutes and three real spoken turns:

```
PA_OPEN_COUNT      = 1
PA_START_COUNT     = 1
REPLACEMENTS       = 0
FEED_ERRORS        = 0
STATUS_EVENTS      = 0
idle_chunks        = 403,564      (~71 min of continuous stream at capture time)
engine_frames_fed  = 53,808       (0.0009% carried as a partial-frame remainder)
COREAUDIO_DEADLOCK = 0
WATCHDOG_RESTART   = 0 (false restarts; the 2 earlier ones were real hangs, pre-change)
wake -> command -> wake = PASS on every turn
```

No `created a new AudioRecorder`, no `recorder stream is not active`, no
controlled replacement, for the whole life of the process.

Automated tests: 9 pass (`tests/test_shared_audio.py`,
`tests/test_shared_audio_fidelity.py`), including 500 WAKE↔COMMAND transitions
with the stream untouched, and a fidelity test proving the adapter reproduces a
wake phrase's score exactly (0.4944 → 0.4944).

## What is NOT proven

Capture quality on the shared stream — head-clipping, STT correctness, capture
duration, silence detection. Three attempts, none conclusive:

| turn | background RMS | outcome |
|---|---|---|
| 23:16 | 383 | 27.9 s capture, wrong transcript — noise-blocked |
| 23:56 | 76 (quiet) | 5.5 s capture, silence fired correctly, no head-cut — but STT wrong because the speech itself was quiet (PEAK_RMS 620 vs 5213–6605 historically) |
| 23:59 | 534 | hit the 30 s ceiling, transcribed the room — noise-blocked |

The one turn in genuinely quiet conditions showed **no head-cut** (the first
second of audio was all background; the 271 ms wake→capture gap contained no
speech) and **correct silence behaviour**. Only the speech level was wrong.

## Two open problems, both outside the shared stream

**1. Intermittent room noise.** Median background RMS swung between 60 and 534
across ~70 minutes with nothing changed, against a `SILENCE_RMS_THRESHOLD` of
200. Not explained by system load — the quietest reading (60) came at load 4.23
and the loudest (470) at load 3.47 with the browser idle. Not the microphone
either: `RMS_MIN` reached 35, matching the historical ~30 floor, and `DC_OFFSET`
was 0.0 in every run. Measurements are in
`~/AI-Lab/jarvis-audio-debug/noise_tests.jsonl`.

**2. Speech reaching the microphone ~10x quieter than it used to.** Historic
successful turns peaked at 5213–6605; the quiet-condition turn peaked at 620.
Background went *up* (30 → 76) while speech went *down*, so this is not input
gain — gain would move both together. Distance, orientation, or volume.

## Restarting next time

1. 10 s noise gate — `~/AI-Lab/jarvis-audio-debug/noise_probe.py LABEL 10`
2. median < 200 → one turn ("Hey Jarvis" → 「こんにちは」). Aim for
   PEAK_RMS ≥ 3000; that is a diagnostic reference, not a threshold to encode.
3. STT correct → 3 turns
4. 3-turn PASS → 10 turns
5. 10-turn PASS → 20 turns, then HUD Phase 1B live E2E, then Phase 1C

## Live state

```
runtime  pid 83301  IDLE  "waiting for wake word"   uptime 1:35
watchdog pid 33852  running (threshold 45 s, poll 5 s, LISTENING only)

bin/jarvis_runtime.py    d135f96717005e574f6a0d3ad8af76eefd08eea7a22e39477f910e622bd205a9
bin/shared_audio.py      6c140067731b86a0ee103f76eaa23ab8c580aa54ba64755089793461c0c55de7
tools/voice_mode.py      7cfc1e4d251a20fb3681d6054545da6237ec9bb91cd9413d43771de7aece1410
tools/wake_word.py       0d39b668217e92f8f5189dd45b40b9f99ce950c8a898316b7d50b091b201b399  (unmodified)
```

Unchanged and to stay that way until validation resumes: wake sensitivity,
`confirmation_frames`, `SILENCE_RMS_THRESHOLD`, input volume, the shared stream,
the watchdog, the HUD.

Rollback: `docs/ROLLBACK_SHARED_STREAM.md`.

## Available while audio validation waits

HUD Phase 1C design and preparation that touches no production file. Phase 1B is
complete (39 tests) and its live E2E gate is still blocked behind audio.

---

# Future work — wake acknowledgement (REQUIREMENT ONLY, NOT IMPLEMENTED)

Recorded 2026-08-31. Not scheduled until 1/3/10/20-turn validation is done; this
is a UX phase of its own, deliberately after the reliability work.

## The ask

When "Hey Jarvis" is recognised, JARVIS should say a short 「はい」 so the user
knows the wake landed and it is their turn to speak. Today the only feedback is
the HUD, which the user may not be looking at.

## Proposed flow

```
WAKE -> ACKNOWLEDGING -> COMMAND -> WAKE
```

## Constraints that come from what has already been measured

**Do not start command capture while the acknowledgement is playing.** Speaker
output couples back into the microphone — this session proved that path exists
in both directions: a phrase played through the speakers fired the production
wake detector at 21:39, and a 30 s capture at 23:59 transcribed the room rather
than the user. An ACK played into an open capture would put JARVIS's own 「はい」
into the STT input.

**Do not stop the physical input stream for it.** The shared stream exists
precisely because starting a stream per turn deadlocked CoreAudio four times.
During ACKNOWLEDGING:

```
physical stream   ACTIVE          (never touched)
wake consumer     paused
command consumer  paused / discarding
audio playback    the ACK
```

and after the ACK finishes, ownership moves to COMMAND. So the invariant holds:

```
PA_OPEN_COUNT  = 1
PA_START_COUNT = 1
```

This fits the existing state machine — `AudioRecorder._recording` is already the
gate that starves the wake consumer, and an ACK window is one more state in which
neither consumer takes frames. No new stream, no new device.

**Do not synthesise the ACK per turn.** Generate one short WAV once, offline,
and play the cached file. Per-turn TTS would add its generation latency
(measured 418–538 ms for Edge TTS) to the very gap the shared stream just
shortened, and would make the acknowledgement depend on a network provider.

Voice for the cached clip, from the concluded voice phase:

```
ENGINE        Chatterbox Multilingual V3
seed          1
exaggeration  0.5
cfg_weight    0.5
```

Note that this would be the **first** use of that voice in the runtime — JARVIS
currently speaks through Edge TTS. Whether the ACK matching the eventual JARVIS
voice while replies do not is acceptable is an open question for that phase.

## Assets — generated 2026-08-31 (asset prep phase, not integrated)

`~/AI-Lab/jarvis-voice-final-benchmark/outputs/wake-ack/`, generated by
`run_wake_ack.py` with the frozen voice settings. All four reproduced
bit-identically on a second pass at the same seed, so caching one generated
file is sound.

| clip | text | duration | lead silence | speech | trail silence | peak |
|---|---|---|---|---|---|---|
| ACK_A_HAI | はい | 960 ms | 101 ms | 415 ms | 444 ms | 0.220 |
| ACK_B_HAI_PERIOD | はい。 | 2240 ms | 33 ms | 1642 ms | 566 ms | 0.323 |
| ACK_C_HAI_DOZO | はい、どうぞ。 | 1600 ms | 119 ms | 942 ms | 539 ms | 0.832 |
| ACK_D_OYOBI | お呼びでしょうか。 | 1400 ms | 187 ms | 718 ms | 496 ms | 0.250 |

## MEASURED PROBLEM: afplay is too slow for this

Median over 12 runs each, and the clip's own duration subtracted:

| clip | audio | afplay total | overhead |
|---|---|---|---|
| ACK_A_HAI | 960 ms | 1953 ms | 993 ms |
| ACK_B_HAI_PERIOD | 2240 ms | 3226 ms | 986 ms |
| ACK_C_HAI_DOZO | 1600 ms | 2465 ms | 865 ms |
| ACK_D_OYOBI | 1400 ms | 2207 ms | 807 ms |

Bare silence isolates the fixed cost: 50 ms of silence takes ~881 ms, 300 ms
takes ~884 ms — the same, so this is per-invocation overhead, not per-sample.
Format makes no difference (PCM_16 and FLOAT within noise of each other).

That is the whole problem. **The wake→command gap today is 251 ms.** Playing the
shortest ACK through the production path would make it ~1950 ms — roughly 8x
worse — and most of that is `afplay` starting up, not the 「はい」 itself.

Caveat: measured under load average 4.5–5.4 with this session's own work
running. Minima were much lower (430 ms for the silence floor, 1430 ms for
ACK_A), so an idle machine would do better; the median is the pessimistic end of
a wide spread, and this should be re-measured on a quiet system before any
design decision rests on it.

Two independent reductions are available before touching the playback mechanism:

- Trim the clips. ACK_A is 960 ms of file for 415 ms of speech; 444 ms of that
  is trailing silence that delays the command capture for nothing.
- ACK_A's peak is 0.220 — the quietest of the four, and quiet enough that
  audibility over room noise is a real question. ACK_C peaks at 0.832.

But neither touches the ~880 ms floor. If the ACK has to feel immediate, the
integration phase needs a playback path that is not a fresh `afplay` process per
turn, and the reason `afplay` is used at all is a TCC constraint
(`_sounddevice_output_allowed()` returns false on Darwin because initialising
PortAudio for OUTPUT triggers a kTCCServiceMediaLibrary prompt). Working around
that is a real piece of design, not a tweak.

## Asset optimisation — 2026-08-31

Selected: **ACK_A_HAI**「はい」. The raw clip is preserved untouched; every
variant is a new file in the same directory.

| asset | duration | peak | gain | lead | trail | clipped |
|---|---|---|---|---|---|---|
| ACK_A_HAI (raw) | 960.0 ms | 0.220 | — | 101 ms | 444 ms | 0 |
| ACK_A_RUNTIME | 575.2 ms | 0.220 | 1.00x | 40 ms | 120 ms | 0 |
| ACK_A_RUNTIME_P050 | 575.2 ms | 0.500 | 2.28x | 40 ms | 120 ms | 0 |
| ACK_A_RUNTIME_P060 | 575.2 ms | 0.600 | 2.73x | 40 ms | 120 ms | 0 |
| ACK_A_RUNTIME_P070 | 575.2 ms | 0.700 | 3.19x | 40 ms | 120 ms | 0 |

Trim removed 385 ms (40 %) without touching the utterance. Gain is a single
constant — no compression, no EQ — so timbre is unchanged by construction. Edge
discontinuity checks: first and last samples are 0.00000 after a 5 ms fade, and
no variant has a sample at or above 0.999.

## afplay under lower load — and an unexplained bimodality

Load average 3.76, CPU 76.5 % idle. 20 reps each.

| | audio | median | p10 | p90 | min | overhead (median) |
|---|---|---|---|---|---|---|
| 50 ms silence | 50 | 931.8 | 443.4 | 958.9 | 434.8 | 881.8 |
| 300 ms silence | 300 | 1183.2 | 684.8 | 1200.5 | 677.6 | 883.2 |
| ACK_A_RUNTIME | 575.2 | 1431.8 | 935.8 | 1469.3 | 915.0 | 856.6 |
| ACK_A_RUNTIME_P060 | 575.2 | 1420.1 | 929.5 | 1457.8 | 922.5 | 844.9 |

`AFPLAY_STARTUP_OVERHEAD ≈ 882 ms` — unchanged from the earlier high-load run,
so the overhead is not load-driven.

The distribution is **bimodal**, not noisy: roughly 25 % of plays land near
940 ms and 75 % near 1440 ms, a gap of about 500 ms. What it is not:

- not gap-dependent — measured at 0, 0.5, 1, 2, 5 and 10 s between plays; fast
  and slow runs interleave at every interval;
- not format — PCM_16 and FLOAT are within noise of each other;
- not the output device idling — holding the device open with a background
  silent stream changed the median by 8.5 ms (1455.1 → 1446.5) and the fast
  fraction by one run in ten.

The cause is not identified. It is recorded as an observation, not explained.

## Verdict

```
ACK_A_RUNTIME_P060 playback, median          1420 ms
plus pause_listening before it (measured)     251 ms
wake -> command capture would become        ~1670 ms   (today: 251 ms)
```

Against the stated bands, the ACK playback alone is **CONDITIONAL** (1.2–1.5 s).
Measured end to end from wake detection, ~1.67 s exceeds them.

## Persistent ACK helper — DESIGN ONLY, NOT IMPLEMENTED

Worth building only because the cost is per-invocation, not per-sample: a
process that already holds the decoded clip and a prepared player has none of
the work `afplay` repeats every time (spawn, dyld, file open, decode, audio
queue setup). Note the keep-warm experiment above shows device warmth is NOT
the mechanism, so a helper must actually preload and hold a player, not merely
keep the device open.

| | afplay (today) | Swift/AVFoundation helper |
|---|---|---|
| per-play cost | ~882 ms measured | untested; AVAudioPlayer.prepareToPlay is the point |
| process model | new process per ACK | one long-lived process |
| asset | path passed per call | fixed asset, preloaded at start |
| input stream | untouched | untouched — output only, never opens an input device |
| TCC | none (afplay is Apple's) | **unverified**: `_sounddevice_output_allowed()` returns false on Darwin because PortAudio's OUTPUT init triggers a kTCCServiceMediaLibrary prompt. AVFoundation is a different API and may not, but that has to be tested, not assumed |
| security surface | none added | must accept no path, no URL, no shell; a fixed embedded asset and a single "play" trigger |
| idle cost | zero | needs measuring; a prepared player should idle at ~0 % CPU |
| failure mode | play fails, turn continues | helper death must not block a turn — the runtime has to treat a missing ACK as skippable |
| lifecycle | none | separate from the runtime and the watchdog, like the HUD |

Constraints any implementation must satisfy: fixed ACK asset only; no network;
no arbitrary path input; no shell; preload at start; process-separate from the
runtime; and it must never touch the shared input stream.

### Prototype built and measured — 2026-08-31

`~/AI-Lab/jarvis-ack-helper/` (Swift + AVFoundation, stdin control channel).
Still NOT integrated: the runtime, shared stream, watchdog and HUD are
untouched and nothing launches it.

100 consecutive plays, 100 successes, 0 failures:

| | p10 | p50 | p90 | min | max |
|---|---|---|---|---|---|
| trigger → play() call | 0.03 | 0.06 | 0.12 | 0.02 | 0.36 |
| trigger → play() returns (first-audio proxy) | 12.95 | **16.56** | 19.45 | 5.65 | 51.92 |
| trigger → playback end | 674.05 | **677.34** | 680.27 | 666.54 | 712.44 |

```
afplay   1420 ms   (575 ms clip + ~845 ms overhead)
helper    677 ms   (575 ms clip + ~102 ms overhead)
saved     743 ms
```

Idle 10 min: RSS 26.5 → 26.3 MB, CPU 0.0017 %, PING answered in 0.1 ms after.
Overlap policy verified: a second PLAY during playback returns `PLAY_IGNORED`,
not a queued clip. TCC: no prompt, no TCC log entry — but **untested from a
LaunchAgent context**, which is where the runtime actually runs.

Projected end-to-end, using the measured `pause_listening` cost:

```
pause_listening   251 ms
helper ACK        677 ms
wake -> command   928 ms      (afplay would give ~1671 ms; today, with no ACK, 251 ms)
```

Against the stated bands that is inside 1.1 s. Note it is still ~3.7x today's
gap: the ACK buys feedback and costs latency, and that trade has not been
accepted, only measured.

## Open questions for the implementing phase

- Playback path: see above. Is ~1 s of added latency acceptable, or does the ACK
  need a warm output path? Any warm path has to avoid the TCC prompt and must
  not open a CoreAudio *input* stream.
- Does the ACK window need a hold-off after playback ends, or is the existing
  `engine.reset()` on mode return enough to stop the ACK re-triggering wake?
- Barge-in: if the user speaks over the ACK, that audio is discarded. Acceptable,
  or should the ACK be cut short?
- The ACK would be the first use of the Chatterbox voice in the runtime while
  replies still come from Edge TTS. Is that mismatch acceptable?

## HUD

An `ACKNOWLEDGING` state could be added to the HUD later. `JarvisPhase` already
decodes unknown states tolerantly, so the runtime could publish it before the
HUD understands it without breaking anything. **Not implemented, and not part of
Phase 1C-prep.**
