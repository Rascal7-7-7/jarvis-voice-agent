# Capture path integrity diagnostic

Five taps on one physical stream, one session, three real utterances. The
question was where between the microphone and the score the audio breaks.

**Answer: it does not break anywhere in software.** Every stage agrees with
every other stage, and the audio already scores ~0 at the raw callback.

## Stage results

One stream: 1 open, 1 start, 1 close. 1456 callbacks, **0 input overflows**.

| stage | score | rms | peak | crest | >8 kHz | rate |
|---|---|---|---|---|---|---|
| A raw callback (whole run) | 0.0001 | 170.5 | 9103 | 53.4 | 0.18 % | 48000 |
| B pipeline buffer (whole run) | 0.0003 | 213.5 | 8117 | 38.0 | 0.19 % | 48000 |
| C aggregate 48 k — U1 | 0.0001 | 220.2 | 1898 | 8.6 | 0.22 % | 48000 |
| D resampled 16 k — U1 | 0.0002 | 218.6 | 1867 | 8.5 | 0.00 % | 16000 |
| E segment — U1 | 0.0002 | 220.2 | 1898 | 8.6 | 0.22 % | 48000 |
| C / D / E — U2 | 0.0000 / 0.0000 / 0.0000 | 373.5 | 8117 | 21.7 | 0.26 % | |
| C / D / E — U3 | 0.0013 / 0.0032 / 0.0010 | 188.2 | 4282 | 22.8 | 1.03 % | |

No stage loses anything the next stage needed. The high band sits at
0.18–1.03 % throughout — normal for speech, and nothing like the 58–63 % that
started this investigation.

## Buffer safety

```
A vs B   identical: True   max_sample_diff: 0   rms_diff: 0.0   correlation: 1.0
```

And the hazard itself, measured rather than assumed:

```
PortAudio callback buffer reused: True   (1455 / 1455 stored views changed)
```

Every view held past its callback was overwritten. The `.copy()` that
`shared_audio` was fixed to do is not a precaution against a theoretical
problem — the buffer really is recycled on every callback on this machine.

## Serialization

```
WAV roundtrip identical for all stages : True
WAV sample rate correct for all stages : True   (48000 for A/B/C/E, 16000 for D)
```

`WAV_SERIALIZATION_CORRUPTION = NO`.

## Segmentation audit

`wake_score_diagnostic.py`:

```python
# line 284
loudest = max(utterances, key=lambda u: u["peak_sample"])
```

The single clip preserved from the nine-utterance run was chosen by **maximum
absolute sample value**. One sample decides it. The clip that won —
`USER_WAKE_LOUDEST.wav`, peak 32767, crest 50.8×, 63 % of its energy above
8 kHz — is the segment containing the sharpest transient in the room, which is
exactly what that rule selects for.

**So the spectral anomaly that framed the previous phase was a transient, not
distorted speech.** `CAPTURE_PATH_CORRUPTION` as I stated it is not supported,
and this phase's instruction to hold it at SUSPECTED rather than CONFIRMED was
the right call.

The gating rule itself:

```
gate            = max(120, noise_floor * 3.0)      = 174 this run
segment starts  at the first block whose RMS >= gate
segment ends    after 8 consecutive sub-gate blocks (0.64 s)
minimum length  3 blocks
padding         none, and no look-back before the gate opens
```

The missing look-back matters for the saved files: U1's segment holds ~0.40 s of
above-gate audio and U3's ~0.24 s, both shorter than "Hey Jarvis" takes to say.
The onset is cut off. It does **not** affect the scores — scoring runs
continuously on every block, independent of segmentation — but it makes the
saved segments unsuitable as training or reference material.

## Mechanisms tested and eliminated

| hypothesis | test | verdict |
|---|---|---|
| dropped capture blocks | resample+predict costs 2.43 ms mean, 2.98 ms max, of an 80 ms budget; 0 overflows | rejected |
| spliced audio explains the spectrum | dropping blocks from good audio kills the score (1-in-3 → 0.0012) but leaves >8 kHz at 0.5–5.2 %, not 63 % | rejected as the spectral cause |
| the diagnostic's loop cannot report a positive | replaying MY_HEY_JARVIS through it reports 0.9971, unchanged for gates 120–600 | loop is healthy |
| level | control holds 0.93 from rms 46 to rms 4719 | rejected |
| clipping alone | 0.005 % clipped here; ~20 % needed to reach 0.0000 | insufficient |
| low-frequency loss | removing everything below 600 Hz still scores 0.9858 | rejected |
| speaking rate | stretching live utterances 0.7×–2.0× peaks at 0.0666 | rejected |
| tflite vs onnx backend | 0.9975 / 0.9975, 0.0002 / 0.0002 — agree everywhere | rejected |
| WAV serialization | roundtrip identical | rejected |

## Correction: the effective backend is tflite, not ONNX

The previous report said production had been running on onnxruntime, on the
grounds that `tflite_runtime` and `tensorflow` are both absent. That was wrong.
**`ai_edge_litert` 2.1.6 is installed**, and `wake_word.py` deliberately selects
tflite on macOS ARM64 because openWakeWord's ONNX backend is broken there
(upstream #336). The fallback warning I saw came from my own probes constructing
`openwakeword.model.Model` directly, which does not know about `ai_edge_litert`.

Production runs **tflite**. My earlier correction is withdrawn.

The probes that used ONNX are still valid: the two backends were compared
directly on the same audio and agree to within 0.0002.

## Production wake has been firing all along

38 wake events in `jarvis_runtime.log` between 2026-08-29 22:59 and
2026-08-31 21:35, the last one 21:35:43 — minutes after this diagnostic's own
runtime restore, on the same 外部マイク.

Several look like false positives rather than successful detections:
`PEAK_RMS` of 342, 344, 495 and 567; four captures where
`silence_cb_fired=False` with `FRAMES=2813`, i.e. the 8-second ceiling was hit
with nothing to end on. The 21:35:43 event captured 15.1 s and transcribed as
`設定に許され セントリングが世界を寄り落とし` — not a command.

So "the wake word never fires" is false as a general statement. The reproducible
finding is narrower and stranger: **it fires on some ambient sounds and not on
the user's deliberate "Hey Jarvis".**

## What is still open

The gap that started this: same person, same phrase, same model, same backend,
same resampler, a pipeline now proven clean at every stage —

```
MY_HEY_JARVIS.wav     0.9975   FIRE
live U1 / U2 / U3      0.0002 / 0.0001 / 0.0013
```

Ruled out for MY_HEY_JARVIS: that it is a recording of the synthetic control
played through speakers. Cross-correlation against the diagnostic's own control
clip and three TTS renditions peaks at 0.20–0.23, which is no match.

Not established: that both were captured on the same microphone, at the same
distance, with the same gain. `SAME_PHYSICAL_MIC = NOT PROVEN` and
`SAME_GAIN = NOT PROVEN`, as instructed.

One observation that bears on it. The default input is **外部マイク** — an
Apple 1-channel input on the 3.5 mm jack — and the default output is
**外部ヘッドフォン** on the same jack. So the wake listener is being fed by a
headset microphone, while `MacBook Proのマイク` sits unused. A headset mic
hanging at chest height is a different acoustic proposition from a built-in
array with beamforming, and it is the obvious thing to compare.

Comparing them means opening a diagnostic stream on a non-default device, which
§15 forbids without approval. It is the recommended next test, not something
done here.

## Listen for yourself

Numbers have taken this as far as they can. §11's point stands:

```
afplay ~/AI-Lab/jarvis-wake-benchmark/taps/A_raw_callback.wav
afplay ~/AI-Lab/jarvis-wake-benchmark/taps/U1_E_segment48.wav
afplay ~/AI-Lab/jarvis-wake-benchmark/taps/U2_E_segment48.wav
afplay ~/AI-Lab/jarvis-wake-benchmark/taps/U3_E_segment48.wav
```

against the one that works:

```
afplay ~/AI-Lab/jarvis-audio-debug/noise-identification/MY_HEY_JARVIS.wav
```

If the live segments sound clear and close, the mic is not the answer and the
next place to look is the utterance itself. If they sound distant, thin or
muffled next to MY_HEY_JARVIS, the microphone A/B is the test to run.
