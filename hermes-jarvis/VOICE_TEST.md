# VOICE_TEST — measured results

All of this was measured **without a microphone**, by synthesising audio with
macOS `say` and feeding it into the real pipeline. That covers STT and wake
word genuinely; it does not cover live capture, which is blocked on a TCC grant.

---

## 1. TTS — Japanese, two paths

Short clause ("はい、確認します。"), 3 runs each, file output:

| Provider | Voice | Run 1 | Run 2 | Run 3 | Local? |
|---|---|---|---|---|---|
| **edge** (default) | `ja-JP-NanamiNeural` | 674 ms | 571 ms | **570 ms** | ✗ text goes to Microsoft |
| **say-ja** | `Kyoko` | 1075 ms | 1066 ms | **1058 ms** | ✓ 100 % on-device |

Longer sentence ("こんにちは。ジャービスです。本日のMacの状態を確認しますか。"):
edge 1.46 s / 44 KB mp3 · say 1.79 s / 244 KB wav.

Only two Japanese edge voices exist: `ja-JP-NanamiNeural` (F),
`ja-JP-KeitaNeural` (M).

**Configured:** `tts.provider: edge`, with `say-ja` declared as a
`type: command` provider. One config value switches the whole stack to
zero-egress:

```yaml
tts:
  provider: say-ja
```

## 2. STT — faster-whisper `small`, int8, `language: ja`

Input synthesised with `say -v Kyoko … --data-format=LEI16@16000`:

| Spoken | Transcribed | Time | Verdict |
|---|---|---|---|
| こんにちは | `こんにちは` | 0.87 s | exact |
| 今日の日付を教えて | `今日の日付を教えて` | 0.67 s | exact |
| パイソンについて説明して | `バイソンについて説明して` | 0.72 s | 1 char |

Model load (first run, includes download): 11.0 s. All three detected as `ja`.

The one error is a `パ`→`バ` confusion on a katakana loanword, most likely an
artefact of how Kyoko renders "パイソン" rather than a whisper failure — real
human speech is a different test. `small` was chosen over the default `base`
because `base` is materially weaker on Japanese; it costs ~500 MB and fits the
machine easily.

## 3. Wake word — openWakeWord `hey_jarvis`

Fully on-device. No API key. Models ~8 MB
(`hey_jarvis_v0.1.onnx` / `.tflite`, plus melspectrogram / embedding /
silero_vad). `onnxruntime` 1.27.0 exposes **`CoreMLExecutionProvider`**, and
every `.so` in the venv is arm64 — no Rosetta.

Scored in 80 ms frames, detection threshold 0.6:

| Audio | Peak score | Result |
|---|---|---|
| "Hey Jarvis" | **0.859** | DETECT |
| "hey jarvis, what is the weather" | **0.999** | DETECT |
| English sentence, no wake word | **0.000** | no trigger |
| Japanese conversation | **0.000** | no trigger |

Clean separation, and no false positive on Japanese speech — which matters,
because the assistant is being driven in Japanese while the wake phrase is
English.

**`wake_word.enabled` is still `false`** and must stay false until push-to-talk
passes with a real microphone.

### Why openWakeWord and not Porcupine

Porcupine ships a literal `jarvis` keyword and Hermes defaults
`wake_word.porcupine.keyword` to `"jarvis"` — but it requires
`PORCUPINE_ACCESS_KEY`, i.e. a credential decision that belongs to the human.
openWakeWord's `hey_jarvis` is a stock pretrained model needing nothing, so it
was chosen. Switching later is a two-line config change.

## 4. Voice behaviour configured

| Setting | Value |
|---|---|
| `voice.auto_tts` | true |
| `voice.barge_in` | true (0.20.0 feature — interrupt mid-sentence by speaking) |
| `voice.stop_phrases` | `stop`, `ストップ`, `止めて` |
| `stt.local.vad` | true, `vad_min_silence_ms: 500` |

## 5. Latency budget

Composed from the measured parts above plus the measured local-LLM turn:

| Stage | Measured |
|---|---|
| VAD end-of-speech | 500 ms (configured) |
| STT | 670 – 870 ms |
| LLM (`gemma4:e2b`, warm) | **3000 – 4400 ms** |
| TTS first clause (edge) | ~570 ms |
| **to first audio** | **≈ 4.7 – 6.3 s** |

**The LLM dominates, and streaming TTS cannot hide it** — `gemma4:e2b` spends
110–170 tokens thinking before it emits anything speakable, so the user waits in
silence for that stretch. A snappy "Jarvis" needs either a non-thinking small
model or a hosted fast model. That is the single biggest open question in the
build.

## 6. Not tested — needs a human

| Item | Blocker |
|---|---|
| Live microphone capture | Microphone TCC grant |
| Push-to-talk round trip with real speech | same |
| Wake word false-positive rate against music / video / meetings | same |
| Barge-in behaviour | same |
| launchd-started voice (TCC dialogs cannot be shown from launchd) | same |
