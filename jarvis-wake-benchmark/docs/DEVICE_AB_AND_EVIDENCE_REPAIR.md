# Input device A/B, and repairing the diagnostic's evidence

## The diagnostic tool repair

`wake_score_diagnostic.py` now saves every utterance and keeps a pre-roll.

What was removed:

```python
# was line 284
loudest = max(utterances, key=lambda u: u["peak_sample"])
```

That rule preserved one file per run, chosen by the single loudest sample in the
room. A knock, a click or friction beats speech on that metric almost every
time, and the clip it selected — peak 32767, crest 50.8×, 63 % of its energy
above 8 kHz — was then analysed for a day as if it represented the user's voice.
There is no such thing as "the loudest recording = representative user speech",
so nothing is selected any more.

What was added:

- **`USER_WAKE_U01.wav` … `USER_WAKE_UNN.wav`**, or `<LABEL>_Unn.wav` with
  `--label`. All of them, all scored.
- **Per-utterance metadata**: `start_time`, `end_time`, `duration`,
  `gated_duration`, `rms_p50`, `rms_p90`, `peak_sample`, `crest_factor`,
  `max_wake_score`, `preroll_blocks`, plus the existing score summary.
- **`PRE_ROLL_BLOCKS = 6`** (480 ms) held in a ring buffer. Without it the saved
  span began at the first block loud enough to open the gate, which for
  "Hey Jarvis" is usually somewhere inside "Jarvis" — measured segments held
  0.24–0.40 s of above-gate audio, less than the phrase takes to say.
- **`POST_ROLL_BLOCKS = 2`** (160 ms) on top of the eight quiet blocks.
- **`--device`** to select an input for that process only, and **`--label`** /
  **`--utterances`**.
- **`actual_format`** read back from the open stream rather than from what was
  requested, so a device that substitutes a rate is recorded, not hidden.

`crest_factor` is now recorded on purpose: it is the number that would have
exposed the old selection rule immediately. Speech sits around 5–15; the clip
that rule kept was at 50.8.

**The scoring loop was not touched.** Every block is still resampled and fed to
the engine in order whether or not a segment is open, so the pre-roll changes
what is *saved* and nothing about what is *measured*. That was verified rather
than asserted — see below.

### Repair verification

Replayed against `MY_HEY_JARVIS.wav`, a fixture with a known answer at both the
segmentation level (3 utterances) and the score level (0.9975). A fake stream
stands in for `sd.InputStream`, so no device is touched.

```
PASS  pre-roll is at least 300 ms as §1C requires — 480 ms
PASS  max(peak_sample) selection is gone
PASS  USER_WAKE_LOUDEST.wav is no longer written
PASS  per-utterance files are written
PASS  fixture rate matches the capture rate — 48000 Hz
PASS  all utterances are returned, none selected away — 3 found, 3 expected
PASS  every utterance carries the §1B metadata
PASS  pre-roll audio was actually retained — 6 5 6 blocks
PASS  the saved span is longer than the gated span — 2.0>1.52 1.76>1.36 1.84>1.36
PASS  each saved utterance begins before the gate opened — gate 186
PASS  sample ordering is preserved (saved audio is a contiguous slice)
PASS  WAV roundtrip is identical and the rate is correct
PASS  the wake score is unchanged by the repair — 0.9971 vs 0.9975 before
PASS  the same utterances still fire — 2/3 fire
```

The 0.9971 vs 0.9975 difference is the quarter-frame hop the earlier offline
scorer used and this one does not; it is not a change in behaviour.

## Device A/B

Both devices at 48 000 Hz mono int16, `matches_request: True` on each, one
stream per device, each opened once, started once, closed once. Never both open.
System default untouched throughout.

### A — 外部マイク (index 5, Core Audio, 1 ch, 48 000 Hz default)

```
noise floor  median 111  p90 302  min 44  max 491   → gate 333
offline control (same engine, no acoustic path)     0.9994  WOULD FIRE
```

| file | score | fire | dur | rms p50 | rms p90 | peak | crest | 0–8 k | 8–16 k | 16–24 k |
|---|---|---|---|---|---|---|---|---|---|---|
| EXT_U01 | 0.0234 | no | 5.04 | 261 | 707 | 3807 | 9.0 | 99.88 % | 0.12 % | 0.00 % |
| EXT_U02 | 0.0011 | no | 1.12 | 53 | 378 | 1344 | 6.6 | 99.81 % | 0.15 % | 0.04 % |
| EXT_U03 | 0.0014 | no | 3.68 | 187 | 413 | 6780 | 27.6 | 99.49 % | 0.49 % | 0.01 % |
| EXT_U04 | 0.0011 | no | 1.76 | 205 | 514 | 6951 | 28.3 | 98.10 % | 1.80 % | 0.10 % |
| EXT_U05 | 0.0000 | no | 2.32 | 171 | 508 | 2433 | 8.7 | 99.80 % | 0.19 % | 0.01 % |

```
FIRE_COUNT 0/5   median 0.0011   min 0.0000   max 0.0234
```

The audio is spectrally clean — 98.1–99.9 % below 8 kHz, nothing like the 63 %
high band of the mis-selected clip. The engine is working, proven on the same
run by the offline control at 0.9994. The user's voice through this microphone
scores three orders of magnitude below the threshold.

Two utterances have crest factors of 27.6 and 28.3 with peaks near 6900, which
is transient-shaped rather than speech-shaped; the room was also noisier this
run (floor median 111 against 58 earlier, peaks to 491), pushing the gate to
333. So some of these five segments may be capturing something other than the
utterance. It does not change the conclusion — none of the five came close —
but it is a reason to prefer the listening check over the table.

### B — MacBook Proのマイク (index 7, Core Audio, 1 ch, 48 000 Hz default)

```
noise floor  median 0  p90 0  min 0  max 0
utterances captured: 0
```

Confirmed directly afterwards by reading the device on its own:

```
samples 145920   min 0   max 0   non-zero 0   distinct values 1  ([0])
```

**`INTERNAL_CAPTURE_STATUS = DIGITAL_SILENCE`.** Every one of 145 920 samples is
exactly zero — not quiet, not below a gate, but literally no signal. This is the
prior history §10 warned about, so it is recorded as a separate problem and not
touched here. The internal microphone is not evidence for or against anything in
this phase.

### Classification

**Case E.** Internal digital silence, so the external result stands alone. The
comparison this phase was designed to make could not be made, and confirming
whether a different physical microphone behaves differently needs a known-good
one.

## False positive audit

Read-only over `jarvis_runtime.log`, 2026-08-29 22:59 → 2026-08-31 21:45.
Classification requires a signal that is not the transcript, because Whisper
invents fluent text from noise and mangles real speech from a distant mic, so a
transcript alone settles nothing in either direction.

```
TOTAL_WAKE_EVENTS         41
CONFIRMED_USER             0
LIKELY_USER               10
LIKELY_FALSE_POSITIVE     11
CONFIRMED_FALSE_POSITIVE   8
UNKNOWN                   12

STRICT_FALSE_POSITIVE_RATE      19/41 = 46.3 %   (UNKNOWN in the denominator)
KNOWN_EVENT_FALSE_POSITIVE_RATE 19/29 = 65.5 %   (UNKNOWN excluded)
UNKNOWN                         12/41 = 29.3 %
```

`CONFIRMED_USER` is 0 by construction: nothing in the log records a person
confirming an event was theirs.

### What the transcripts show

**A Japanese TV drama has been playing in the room.** The transcripts include
`伊勢島ホテルで120億の損失が出たことを`, `私はそうは思いません 銀行は初戦金貸しですよ`,
`親会社として、もっと小会社の面倒を見てやったらど`, `あんたには感謝してる でもあんたがくれたのは金だ`,
`まさかこんなところであなたと会うなんて`. This is dialogue, not commands.

That is very likely the intermittent room noise this project measured for a day
and never identified — RMS 60–763, acoustic, load-independent, mic-independent.

**The runtime has also been recording its own voice.** Event 12 transcribed as
`ご視聴どうぞ。お帰りなさいシステムは正常です。 ご視聴。` — the greeting, coming back
through the microphone. Events 9 and 10 (`何かを手伝いできることはありますか`,
`ご指示をどうぞ`) are the same thing. The headset is plugged into the same 3.5 mm
jack as the microphone, so this is a plausible acoustic loop and a distinct
fixable cause.

**And the wake word has fired on the user.** Event 37, 2026-08-31 16:59:32,
transcribed `エイザービース`; event 1 transcribed `ヘイジャービース`. Both are
"Hey Jarvis" through STT. So the detector is not deaf to this user — it is
unreliable, which is a different problem from the one this investigation started
with.

Two of the ten `LIKELY_USER` events should be read with suspicion: 28
(`伊勢島ホテルで120億の損失が出たことを`) and 40 (`これこそが企業にとって最も重要なのができる`)
are loud, self-terminating, phrase-length and routed, which is what the rule
looks for — but they read as drama dialogue. The classifier cannot tell a clear
sentence from the television apart from a clear sentence from the user, and it
should not pretend otherwise.

## Listening

```
afplay ~/AI-Lab/jarvis-audio-debug/noise-identification/EXT_U01.wav
afplay ~/AI-Lab/jarvis-audio-debug/noise-identification/EXT_U02.wav
afplay ~/AI-Lab/jarvis-audio-debug/noise-identification/EXT_U03.wav
afplay ~/AI-Lab/jarvis-audio-debug/noise-identification/EXT_U04.wav
afplay ~/AI-Lab/jarvis-audio-debug/noise-identification/EXT_U05.wav
```

against the one that scores 0.9975:

```
afplay ~/AI-Lab/jarvis-audio-debug/noise-identification/MY_HEY_JARVIS.wav
```

There are no `INT_*.wav` files to compare, because the internal microphone
produced nothing.
