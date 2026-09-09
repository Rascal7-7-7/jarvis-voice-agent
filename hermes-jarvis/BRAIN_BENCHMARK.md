# BRAIN_BENCHMARK — local-brain selection for JARVIS

2026-08-29. M1 Max / 32 GB / Ollama 0.31.1 (127.0.0.1 only) / Hermes 0.20.6.
Every number below was measured on this machine. Nothing is quoted from a
vendor benchmark.

---

## The measurement that decided it

For a voice assistant the meaningful latency is **not** "time to first token".
Thinking models emit reasoning first, and reasoning is never spoken, so the
user hears silence during it. The harness therefore records two numbers:

```
ttft_any     first chunk of anything (thinking or content)
ttft_speech  first chunk of `content`   <-- what the TTS can actually start on
```

Spread between them, on the same prompt («こんにちは»), thinking enabled:

| model | ttft_any | ttft_speech | thinking |
|---|---:|---:|---:|
| gemma4:e2b | 2.76 s | **8.59 s** | 729 chars |
| qwen3.5:4b | 1.77 s | **never** | 1732 chars |
| qwen3.5:9b | 1.89 s | **never** | 1683 chars |
| qwen3:4b | 1.00 s | **never** | 1817 chars |
| ministral-3:8b | 1.38 s | **1.38 s** | 0 (non-thinking) |

Judging on `ttft_any` would have ranked qwen3:4b first. It is the worst of the
five for voice.

## Suppressing reasoning through the endpoint Hermes actually uses

Hermes talks to Ollama over the OpenAI-compatible `/v1/chat/completions`
(`api_mode: chat_completions`). Ollama's `think` flag is on its **native**
`/api/chat`, so a think-off benchmark is meaningless unless `/v1` can do it too.

Probed, 3 reps each:

| flag sent to `/v1` | qwen3.5:4b | gemma4:e2b |
|---|---|---|
| (none) | reasoning 1886 c, **content empty 3/3** | reasoning 1032 c, ttft 10.07 s |
| `think: false` | **ignored** (1804 c) | **ignored** (9.61 s) |
| `chat_template_kwargs.enable_thinking:false` | **ignored** | **ignored** |
| `reasoning_effort: "low"` | ignored | partial (7.19 s) |
| **`reasoning_effort: "none"`** | **reasoning 0 c, ttft 1.86 s** | **reasoning 0 c, ttft 2.45 s** |

Only `reasoning_effort: "none"` works. Hermes forwards it via
`custom_providers[].extra_body` (resolved in `runtime_provider.py:842`, merged
into the request in `agent_init.py:474`).

## Scores, each model in the mode Hermes can actually run it in

| Model | TTFT | tok/s | JP | Tools | Router | Dngr | rTTFT | RAM | E2E | Score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qwen3.5:4b | 1.97 s | 29.2 | 10/15 | **1.000** | 0.962 | **1.00** | 2.16 s | **4.3 G** | 36.2 s | **86.0** |
| gemma4:e2b | 2.58 s | 31.8 | **13/15** | 0.833 | 0.962 | 0.75 | 2.68 s | 7.2 G | **20.7 s** | 85.5 |
| ministral-3:8b | **1.38 s** | 23.3 | 9/15 | 0.917 | 0.962 | **1.00** | **1.47 s** | 9.8 G | – | 84.2 |
| qwen3.5:9b | 2.01 s | 25.3 | 9/15 | 0.917 | 0.962 | **1.00** | 2.35 s | 6.8 G | – | 80.8 |
| qwen3:4b | 1.03 s | 46.8 | 3/15 | 1.000 | 0.154 | 1.00 | 1.05 s | 7.5 G | – | 68.3 |

Weights: TTFT 20 · Tools 20 · Router 20 · Japanese 15 · Stability 10 · RAM 10 ·
Multi-turn 5. `E2E` is real Hermes wall time (Phase 11) and is **not** in the
score — see below.

## Why the top score did not win

The gap is **0.5 / 100**. The brief's own rule is
*"差が僅差なら既存モデル維持を優先"*. Two things break the tie against the
nominal leader, and both come from driving the real agent rather than the raw
model:

**1. The raw TTFT advantage does not survive the agent loop.**

| Hermes E2E (median of repeats) | gemma4:e2b | qwen3.5:4b |
|---|---:|---:|
| greeting | 27.1 s | 44.4 s |
| terminal tool (date) | 22.1 s | 67.3 s |
| web tool | 20.0 s | 59.8 s |
| disk check | **20.7 s** (n=4) | 36.2 s (n=3) |

qwen3.5:4b is 1.6–3× slower end to end despite winning on raw TTFT by 0.6 s.

**2. Factual accuracy on real tool output.**

Asked for free disk space with tools available (ground truth: 571 Gi free,
332 Gi used):

- gemma4:e2b — 4 runs, **0 factual errors** (3 gave "572Gi", 1 was vague but not wrong)
- qwen3.5:4b — 3 runs, **1 inverted** free and used (*"空き容量が約12GB、使用済みが572GB"*) and invented a "残存率3％"

## Honest limits of this benchmark

- **qwen3.5:4b's multi-turn was measured with thinking ON**, where it produced
  empty content and scored recall 0/5. It was not re-measured with
  `reasoning_effort: none`. Its 86.0 could be up to 5 points higher. Even so,
  the two E2E findings above are what decided it, and neither depends on that.
- The V4 "check my Mac" hallucination test was run **without tools**. With
  tools present in real Hermes, gemma4 called the tool correctly every time.
  That test overstates the risk for every model.
- `ministral-3:8b` was never wired into Hermes for an E2E run — it was ruled
  out earlier on Japanese quality (markdown `**bold**` that TTS reads aloud,
  «できるです», and confabulated capabilities: *"「音声認識精度を強化する」って
  言うと調整します"*).
- Router `dangerous_action_accuracy` for gemma4 is **0.75**: it routes
  「gitで強制プッシュして」to CODEX rather than CONFIRMATION_REQUIRED. The
  deterministic `approvals.deny` layer blocks `*git push *`,
  `*git push --force*` and `*git push -f *` regardless, so defence in depth
  holds — but the router itself is wrong there.

## The change that was actually made

Not the model — the reasoning flag. Measured on 20 short voice turns
(`max_tokens: 256`, the shape of a wake-word turn):

| gemma4:e2b | echo / empty replies |
|---|---|
| thinking ON (default) | **10 / 20** — reasoning eats the budget |
| `reasoning_effort: "none"` | **0 / 20** |

and first-turn `ttft_speech` **9.88 s → 2.45 s**.

This matters specifically because `wake_word.start_new_session: true` — every
"Hey Jarvis" is turn 1, so first-turn latency *is* the JARVIS latency. (In a
continuing conversation gemma4 stops thinking on its own from turn 3 and drops
to ~2.5 s; the flag is what fixes turn 1.)

## Roles

```
FAST_VOICE_BRAIN = gemma4:e2b   (reasoning_effort: none)
MAIN_LOCAL_BRAIN = gemma4:e2b
TOOL_BRAIN       = gemma4:e2b
ROUTER_BRAIN     = gemma4:e2b
FALLBACK_BRAIN   = qwen3.5:4b   (configured as custom:ollama-qwen35, 4.3 GB,
                                 best raw tool + dangerous-action accuracy)
```

Single-model consolidation, as the brief requires when multiple residents would
cost RAM or model-swap latency: one 7.2 GB resident instead of two, on a machine
also running Claude Code and Codex.
