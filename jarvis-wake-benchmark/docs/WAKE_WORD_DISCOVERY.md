# Wake word improvement — discovery

The phase was opened to select a better wake engine, on the premise that the
`hey_jarvis` model does not respond to the user's natural speech. **That premise
does not survive measurement, and the previous phase's conclusion was wrong.**

## The finding

The user's own natural "Hey Jarvis", scored through the exact production model
and the exact production resampler:

```
MY_HEY_JARVIS.wav        0.9975   FIRE   (threshold 0.35)
USER_WAKE_LOUDEST.wav    0.0000
```

Both are the user. Both are 48 kHz mono int16. One is recognised with a score
2.85× the threshold; the other is not seen at all.

So the model is not deaf to this speaker, this accent, or this pronunciation.
`ROOT_CAUSE_LAYER = pronunciation / model mismatch`, reported at the end of the
live diagnostic, is **retracted**.

## What is actually wrong with the failing audio

Energy distribution of the 48 kHz originals, before any processing of mine:

| clip | 0–8 kHz | 8–16 kHz | 16–24 kHz | score |
|---|---|---|---|---|
| MY_HEY_JARVIS | 99.41 % | 0.57 % | 0.01 % | 0.9975 |
| USER_WAKE_LOUDEST | 36.96 % | **58.14 %** | 4.90 % | 0.0000 |
| SPEECH_UNCOVERED_AB | 74.42 % | 25.57 % | 0.01 % | 0.0506 |
| SPEECH_UNCOVERED | 99.94 % | 0.06 % | 0.00 % | 0.0028 |

Speech does not put 58 % of its energy above 8 kHz. And the excess is not
ambient — it tracks the speech:

| clip | 8–24 kHz during silence | during speech |
|---|---|---|
| MY_HEY_JARVIS | 4.63 % | 0.38 % |
| USER_WAKE_LOUDEST | 2.48 % | **47.64 %** |
| SPEECH_UNCOVERED_AB | 3.12 % | 13.58 % |
| NOISE_OPEN | 9.67 % | 20.05 % |

High-frequency content that is absent in silence and appears only when the
signal gets loud is the signature of a **non-linear distortion** in the capture
path, not of interference. Interference would be there in the quiet passages
too — and the previously unidentified intermittent room noise was measured in
silence, so this is a separate fault from that one.

## Ruled out

Each of these was tested against a control the model does recognise, rather
than argued from the numbers:

**Level.** Irrelevant across a 100× range. The English control still scores
0.9492 at rms 46 and 0.9257 at rms 4719. "Too quiet" cannot produce a zero.

**Clipping, on its own.** Amplifying the control until it clips does cost
score, but far too slowly to explain this:

| clipped samples | score |
|---|---|
| 0.00 % | 0.9492 |
| 1.55 % | 0.5445 |
| 6.02 % | 0.2857 |
| 20.20 % | 0.0183 |

`USER_WAKE_LOUDEST` has **0.005 %** clipped samples and scores 0.0000. Reaching
that by clipping alone would take ~20 %, four thousand times more. Its peak of
32767 is real, but it is a symptom of the overload, not the mechanism.

**Frame alignment.** Every score here is the maximum over four quarter-frame
offsets. A keyword landing astride a frame boundary cannot explain a zero.

**My own resampler.** Naive 3:1 decimation folds 8–16 kHz down into the speech
band, so the first pass' spectra were partly self-inflicted. Re-run through
production's own `_resample_audio_frame` (a box average over each 3840→1280
window), the ranking is unchanged and `USER_WAKE_LOUDEST` goes to 0.0000. The
anomaly is in the recorded file, measurable at 48 kHz before any resampling.

## Accent, measured rather than assumed

Worth recording, because it decides the phrase question regardless of the fault
above. macOS TTS through the same engine:

| rendition | score |
|---|---|
| "Hey Jarvis", Samantha (en_US) | 0.9395 |
| "Hey Jarvis", Alex (en_US) | 0.4951 |
| "ヘイ ジャービス", Kyoko (ja_JP) | 0.1803 |
| "ヘイ ジャービス", Kyoko, slower | 0.2427 |
| "Jarvis" alone, Samantha (en_US) | 0.0104 |
| "ジャービス" alone, Kyoko (ja_JP) | 0.0002 |

Two things follow:

1. A Japanese rendition scores 0.18–0.24 — below the 0.35 threshold, but
   **two to three thousand times higher** than the user's failing clip. Accent
   was never enough to account for a 0.0000. It is a real but secondary effect.
2. `hey_jarvis` genuinely requires the "Hey". The single word scores 0.0104 in
   native English. **"Jarvis" and "ジャービス" are not usable phrases with this
   model**, at any threshold that is not reckless.

Tolerance of the model to speed/pitch drift is narrow — 0.80–1.10× fires,
1.25× gives 0.0035. Brittle, but not the fault here.

## Effective inference backend — correction

Config asks for tflite. Neither `tflite_runtime` nor `tensorflow` is installed
in the Hermes venv, so openWakeWord logs a fallback and runs on
**onnxruntime 1.27.0**. Production has been on ONNX all along.

The earlier report's `INFERENCE_BACKEND = tflite` was the configured value read
back, not the observed one. Corrected here.

This is worth knowing but is not the fault: ONNX scores the English control at
0.9395 and the user's good clip at 0.9975, so the backend recognises the
keyword correctly on this machine.

## Provenance caveat — unresolved

`MY_HEY_JARVIS.wav` (16:59:38) was **not** written by `wake_score_diagnostic.py`,
which emits only `USER_WAKE_LOUDEST.wav` (16:45:17), and no script in the tree
writes that filename. Its duration is exactly 8.000000 s and it carries no
`LIST`/`INFO` chunk, which is consistent with a fixed-duration recorder but
identifies nothing.

So it is **not established** that the two clips came through the same
microphone and the same gain setting. That distinction decides the shape of the
fix:

- Same path → the production capture path is **intermittently** distorting, and
  the fault is a level or device condition to be found.
- Different path → the production input device or its gain is distorting
  **consistently**, and the good clip merely proves the model and the user's
  pronunciation are fine.

Either way the wake engine is exonerated. Only the remedy differs.

Observed, read-only, no change made: default input is `[5] 外部マイク` at
48 kHz, system input volume **35**. Other inputs present: `MacBook Proのマイク`,
`Clayのマイク`.

## Engine research

### openWakeWord custom training

- Code Apache 2.0. **Pre-trained models are CC BY-NC-SA 4.0** — non-commercial,
  which is fine for personal use but is a licence to be aware of.
- Positives are **synthetic**, generated with Piper TTS via
  `piper-sample-generator` (MIT). The README asks for "a minimum of several
  thousand" positives, and the shipped models used ~30 000 hours of negatives.
  **The user does not record thousands of clips** — that number refers to TTS
  output.
- The training tooling is present locally (`openwakeword/train.py`, 902 lines;
  `auto_train` defaults to 50 000 steps). Model selection requires
  ≤0.5 false positives/hour against an 11.3-hour validation set at ≥0.20
  recall.
- The official Colab notebook has bit-rotted: Python 3.12 `piper-phonemize`
  wheels, torchaudio 2.x changes, and package-layout drift. Community forks
  that fix it target **WSL2/Linux + CUDA**. This machine has no NVIDIA GPU;
  Piper generation runs on CPU but slowly (the cited 100 samples/s figure is a
  2080 Ti).
- Piper's speaker-embedding generator is **English-only**, so a Japanese
  custom model cannot be built from the standard synthetic path without
  sourcing Japanese voices separately.

### openWakeWord custom verifier — does not apply

`custom_verifier_model.py` builds a speaker-specific second stage. Reading it
settles the question: `get_reference_clip_features` only keeps features from
clips that already score **above the threshold**. It is a false-positive
filter that runs after a detection. With a base score of 0.0000 there is
nothing for it to filter, so it cannot raise sensitivity.

### Porcupine

Measured locally, `pvporcupine` 4.0.3 installed into an isolated venv:

- **`jarvis` is a built-in keyword.** `jarvis_mac.ppn` ships, and
  `lib/mac/arm64/libpv_porcupine.dylib` is a native arm64 binary. A single-word
  "Jarvis" is available with no console, no training, no cloud.
- Only `porcupine_params.pv` (English) is bundled. Japanese needs the separate
  `porcupine_params_ja.pv` plus a **custom** keyword.
- `process()` takes caller-supplied int16 PCM of exactly `frame_length` samples
  at `sample_rate`. **Shared-Stream compatible** — the engine never opens a
  device, so `PA_OPEN_COUNT=1 / PA_START_COUNT=1` is preserved. Exact frame
  length needs a valid key to read from the library.
- An **AccessKey is mandatory** and blocks any further measurement.
  `create()` with a bogus key fails locally with "Failed to parse AccessKey" and
  no network call, which indicates the key is verified on-device and **runtime
  is offline**.
- Free personal accounts can train custom keywords, non-commercially. Picovoice
  material states personal-account custom models run on **x86_64**; if that
  holds it would block a custom Japanese keyword on this arm64 Mac. Built-in
  keywords are unaffected. **Unverified** — it needs an account to confirm.

`TRAINING/CUSTOMIZATION CLOUD` — Porcupine: required (Picovoice Console) for
custom keywords, not for built-ins. openWakeWord: none required; Colab is
optional convenience.
`RUNTIME CLOUD` — both: none. No wake audio leaves the machine in either case.

## Why no bake-off was run

A comparison matrix between engines would measure which engine best recognises
audio that **is already known to be corrupted**, and would be won or lost by
each engine's incidental tolerance to distortion. It would also cost the user
a recording session and a Picovoice account to produce a number that cannot
inform the decision.

The engine is not the failing component. Fixing the capture path is the
prerequisite, and it may well close the issue on its own — the same voice,
captured cleanly, already scores 0.9975.
