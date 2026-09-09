# LATENCY — background runtime optimization, 2026-08-29

Everything below is measured on this machine. Where a number is a component
measurement rather than an observed end-to-end turn, it says so.

## The turn that started this

The first real background turn, 22:59:02 → 23:00:56, reconstructed from
`logs/jarvis_runtime.log`:

| interval | ms | what it was |
|---|---|---|
| WAKE → capture | 520 | detector fires, stream closes, recorder opens |
| CAPTURE | 15 650 | see below — not a bug |
| plugin discovery | 1 010 | first import of the Hermes tool tree |
| whisper load | 6 160 | includes a huggingface metadata round trip |
| VAD | 180 | removed 13.931 s of 15.467 s |
| whisper decode | 13 920 | **never reproduced — see below** |
| ROUTER | 17 670 | gemma4:e2b cold load |
| LOCAL | 42 700 | agent turn, plus a second full router run |
| TTS synth | 1 600 | Edge TTS |
| **total to first audio** | **≈ 99 000** | |

## Four premises that turned out to be wrong

Recorded because the brief was written on them, and because a fix aimed at the
wrong cause is worse than no fix.

**1. "faster-whisper reloads every turn."** It does not. Upstream keeps a
module-global under a lock in `tools/transcription_tools.py`, and
`unload_after_idle_seconds` is 0 here, so it is never dropped. Measured
in-process: 2.07 s on the first call, then 0.69 s and 0.71 s. The log showed one
load because the log contained one wake.

**2. "The wake phrase leaked into the command recording."** It did not. Framewise
RMS over the actual recording puts the only speech at 11.74 s–12.46 s; the first
11.74 s is silence with a peak of 62. Nothing bled in.

**3. "The capture ran long because of a recording cap or a VAD fault."** The
recorder did exactly what it was configured to do:

```
 0.00 → 11.74 s   the user has not started speaking     (_max_wait was 15.0 s)
11.74 → 12.46 s   0.72 s of speech, 'ヘイジャービース'
12.46 → 15.47 s   3.01 s of silence → auto-stop         (silence_duration 3.0 s)
```

The transcript was the wake phrase because the user *said the wake phrase again*
— there is no cue that JARVIS is listening, since `voice.beep_enabled` is false
from the hands-free phase. That is the real cause of the 11.74 s, and it is a
product decision, not a latency bug. It is left alone here: the beeps were
switched off deliberately.

**4. "`hermes` CLI startup costs ~11 s per turn."** It costs **0.16 s**
(`hermes --version`, twice). The earlier 11.6 s figure was an agent turn being
read as process startup; this report corrects it. Process creation is not where
LOCAL's time goes — inference is.

A fifth measurement was mine and also wrong: mid-investigation I measured a
noise floor of p50=368 against a threshold of 200 and called it the cause. The
actual recording has a noise floor of 47. The ambient reading was a different
moment, not that turn. Retracted.

## RESOLVED 2026-08-30 — the 13.92 s decode was launchd QoS

The section below left this as *not explained*. It is explained now, and the
cause was neither whisper nor Ollama: the runtime's LaunchAgent carried
`ProcessType = Adaptive`, which put it in the background scheduling band.

Measured under launchd, same probe, same audio, three ProcessTypes:

| ProcessType | `ps` PRI | warm STT |
|---|---:|---:|
| **Interactive** | 31 | **0.67 s** |
| Adaptive (what we had) | 4 | 12.80 s |
| Background | 4 | 12.49 s |

`Adaptive` behaved identically to `Background` — a 19x penalty on CPU-bound
int8 inference, which on Apple Silicon means efficiency cores. Terminal
measurements never saw it because a terminal process runs at PRI 31.

Fixed by setting `ProcessType = Interactive`. Corroboration after the change:
the startup whisper warm dropped from 7.19 s to **1.38 s** in the same process
doing the same work.

The analysis below is left as written, because it was correct about what the
cause was *not*.

## What the 13.92 s decode was

Not the audio. The identical file, through the identical call, transcribes in
**0.75 s** and **0.76 s**. Decode time is also flat in input length — 2 s, 5 s,
10 s and 15.5 s of the same speech all take 0.75–0.90 s, because the VAD strips
silence before the decoder sees it.

It was the first inference in a process that had just downloaded the model. That
is not a satisfying mechanism and it is **not claimed as an explained one**. It
is removed from the hot path rather than explained: the model is now loaded at
startup, so no turn pays a first-load artifact of any size.

## Changes

Two files, both ours. No upstream file was touched; `~/.hermes/config.yaml` was
not written.

### `bin/jarvis_runtime.py`

- **Timeline instrumentation.** 15 monotonic marks, reported as one log line:
  `timeline WAKE= CAPTURE= STT= GATE= ROUTER= LOCAL= CODEX= CLAUDE= TTS=
  TOTAL_TO_FIRST_AUDIO=` in ms, plus a `marks` line of offsets. `monotonic` and
  not wall time, so a clock step across a sleep/wake cannot produce a negative
  interval that looks like a pipeline bug.
  GATE and ROUTER share one `route()` call, so the boundary is taken from the
  numbers that call already returns rather than timed twice from outside.
- **Startup warm-up**, on threads, after the listener is already up:
  faster-whisper (via the ordinary public transcribe path, on a throwaway tone —
  silence would be VAD-stripped and warm nothing) and gemma4:e2b.
- **Wake-time warm-up.** The instant the wake word fires, a background thread
  asks Ollama for one token. The model load then overlaps the seconds the user
  spends speaking instead of following them.
- **Recorder tuning, per instance**: `_silence_duration` 3.0 → 1.2 s,
  `_max_wait` 15.0 → 8.0 s. `_silence_threshold` is deliberately **not** touched
  — that is voice sensitivity, and it was measured innocent.
- **The route is passed to `jarvis-dispatch`** instead of being re-derived.

### `bin/jarvis-dispatch`

- Honours `JARVIS_ROUTE` / `_BY` / `_CATS` when set, and still routes for itself
  when run standalone.
- Three interpreters parsing the same JSON collapsed into one.

The inherited route is a **fast path, not a trust boundary**:
`CONFIRMATION_REQUIRED` is still refused, an unknown label still falls through to
the safe branch, and the deterministic gate has already run upstream of the LLM.
Both were tested directly.

## Model residency — why warm-on-wake, not resident

gemma4:e2b is 7.23 GB on a 32 GB machine that also runs Claude and Codex.

| option | idle cost | router latency |
|---|---|---|
| `keep_alive=-1`, always resident | 7.23 GB, all day | 2.3–3.9 s |
| `keep_alive=10m` (current), cold on a quiet machine | 0 | **17.67 s** |
| **`keep_alive=10m` + warm at startup and on wake** | 0 between turns | 2.3–3.9 s |

The third buys the resident number without the resident footprint, because the
load overlaps speech. The model was not changed, and `OLLAMA_KEEP_ALIVE` in
`local.ollama.serve.plist` was not touched.

## LOCAL: the target that was not reached

`hermes -z` takes 7–11 s with the model already warm, and `user` CPU time is
1.6 s of it — the wall time is Ollama, not Python. `hermes prompt-size` says why:

```
System prompt :  22,867 B   (skills index alone 11,968 B)
Tool schemas  :  41,316 B   (20 tools)
             ≈  64 KB  ≈ 16,000 tokens of prompt processing, every turn
```

Two measured levers, neither wired in:

| | latency | cost |
|---|---|---|
| `hermes -t clarify -z` | 7.0–8.0 s | removes file/terminal/web/delegation tools from LOCAL |
| direct Ollama call, no agent | **2.8–3.5 s** | no tools at all — and it answered "本日は2024年5月16日", because nothing injects the date |

The second meets the < 4 s target and is the reason it is not enabled: it buys
the number by giving up correctness. Both are left as decisions rather than
taken unilaterally, because both change what LOCAL can *do*, which is a
capability question and not an optimization.

The resident gateway on 8644 was checked as an alternative host, per the brief:
it answers `/health` and nothing else. There is no agent endpoint, and exposing
one means enabling the Hermes API server platform — a new listener, and an
approval item. It would also not have helped: startup is 0.16 s, and a resident
process would still send the same 64 KB per turn.

## Cost of the warm-up

`jarvis_runtime` idle RSS went from **34.9 MB to ~336 MB**, because the whisper
model is now held in the process instead of being loaded on first use. That is
the price of taking 6.16 s off the first turn, and it is a real change, not a
rounding difference.

## Rollback

```sh
cd ~/AI-Lab/hermes-jarvis/bin
cp jarvis_runtime.py.pre-latency-20260829-231308 jarvis_runtime.py
cp jarvis-dispatch.pre-latency-20260829-231308  jarvis-dispatch
launchctl kickstart -k gui/$(id -u)/local.jarvis.runtime
```

Nothing else has to be undone: no config file, no plist, no upstream source, no
model, and no Ollama setting was changed.
