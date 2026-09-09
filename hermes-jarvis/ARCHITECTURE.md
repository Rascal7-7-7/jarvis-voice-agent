# ARCHITECTURE — Hermes JARVIS

State as of 2026-08-29. Solid lines are wired and verified. Dashed lines are
configured but not yet proven end-to-end.

## Runtime layout

```
                    ┌──────────────────────────────────────────┐
                    │  launchd:  ai.hermes.gateway             │
                    │  RunAtLoad=true                          │
                    │  KeepAlive.SuccessfulExit=false          │
                    │  env: PATH / VIRTUAL_ENV / HERMES_HOME   │
                    │       (no secrets — verified)            │
                    └───────────────────┬──────────────────────┘
                                        │ exec
                                        ▼
              ~/.hermes-venv/bin/python -m hermes_cli.main gateway run --replace
                                        │
                    ┌───────────────────┴───────────────────┐
                    │        Hermes Agent 0.20.6            │
                    │  editable install pinned to tag       │
                    │  v2026.8.27 (5fc308a7)                │
                    │  source: ~/AI-Lab/hermes-jarvis/src/  │
                    └───────────────────┬───────────────────┘
                                        │
        ┌───────────────┬───────────────┼───────────────┬────────────────┐
        ▼               ▼               ▼               ▼                ▼
   webhook          cron ticker     kanban          state.db         skills
 127.0.0.1:8644     60 s / 35 jobs  dispatcher    schema v26         34 cats
                                                  3523 sessions
                                                  10563 messages
```

**Why a separate venv and not an in-place upgrade:** the pyenv 3.12.3
environment is shared — 68 automation scripts resolve `python3` through it, and
`edge-tts` / `playwright` live there too. Installing 120 pinned dependencies
into it would have put all of that at risk. Instead 0.20.6 lives in its own
venv and production switches by **two values in one plist**. The 0.15.2 install
is still present and functional, so rollback is a plist revert plus a DB
restore — not a reinstall.

## Voice path

```
  microphone  ─────────────────────────────────────────────┐
      │                                                    │  (TCC grant
      ▼                                                    │   NOT YET GIVEN)
  ┌─────────────────────────┐                              │
  │ wake word               │  openwakeword hey_jarvis     │
  │ on-device, no API key   │  onnxruntime 1.27.0          │
  │ ~8 MB models            │  CoreMLExecutionProvider     │
  │ STATUS: DISABLED        │  verified 0.859 / 0.999 hit  │
  └───────────┬─────────────┘           0.000 / 0.000 miss │
              │                                            │
              ▼                                            │
  ┌─────────────────────────┐                              │
  │ VAD                     │  silence 500 ms              │
  └───────────┬─────────────┘                              │
              ▼                                            │
  ┌─────────────────────────┐                              │
  │ STT  faster-whisper     │  model=small, int8, lang=ja  │
  │ 100% local              │  measured 0.67 – 0.87 s      │
  └───────────┬─────────────┘  2/3 exact on synth audio    │
              ▼                                            │
  ┌─────────────────────────┐                              │
  │ Hermes Agent            │◄─────────────────────────────┘
  │ approvals.mode=manual   │
  │ 40 deny globs           │
  └───────────┬─────────────┘
              ▼
  ┌─────────────────────────┐
  │ streaming TTS + barge-in│  edge / ja-JP-NanamiNeural  ~570 ms per clause
  │                         │  say-ja / Kyoko (local)     ~1060 ms per clause
  └───────────┬─────────────┘  stop phrases: stop / ストップ / 止めて
              ▼
          speaker
```

## Brain routing

```
                          Hermes Agent
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
  Local LLM               Codex CLI              Claude Code
  (wired)                 (present, unwired)     (present, unwired)
  Ollama 127.0.0.1:11434  0.149.1                2.1.251
  ├ gemma4:e2b   voice    sandbox=read-only      MUST be invoked as the
  │  3.0-4.4 s warm       approval=on-request    ABSOLUTE path
  │  natural JA           hermes_cli/            ~/.local/bin/claude
  │  tool calling OK      codex_runtime_switch   — a bare `claude` from a
  └ qwen3:4b     tools      .py exists upstream    launchd PATH resolves to
     76-78 tok/s                                   the ~/.claude root, whose
     tool calls OK                                 settings.json differs
     NOT speakable
```

**Measured, not assumed:** `qwen3:4b` was disqualified as the voice brain
because it is a thinking model that burns 300–600 thinking tokens on
"こんにちは" and returns empty `content` at `num_predict=600`. `think:false`
does not help — it relocates English chain-of-thought into `content`.
`gemma4:e2b` uses only ~110–170 thinking tokens and terminates with
`done_reason=stop`, so it is the voice brain; `qwen3:4b` is kept for tool work.

**Latency budget (local brain, estimated from measured parts):**

| Stage | Measured |
|---|---|
| VAD end-of-speech | 500 ms (configured) |
| STT (faster-whisper small int8) | 670 – 870 ms |
| LLM (gemma4:e2b, warm) | 3000 – 4400 ms |
| TTS first clause (edge) | ~570 ms |
| **total to first audio** | **≈ 4.7 – 6.3 s** |

The LLM stage dominates, and its thinking phase produces nothing speakable, so
streaming TTS cannot hide it. A sub-second "Jarvis" needs either a
non-thinking small model or a hosted fast model — both open questions.

## MCP and tools

```
              Hermes 0.20.6  (mcp 2.0.0 installed, 0 servers registered)
                     │
   ┌─────────────────┼──────────────────┬──────────────────┐
   ▼                 ▼                  ▼                  ▼
 terminal          file            browser-use          delegation
 sudo disabled   protected_       use_real_profile     a2a / acp
 40 deny globs   instruction_     = false              desktop_ui
                 files=true       (real Chrome 5.4 GB
                                   and Brave 2.9 GB
                                   never touched)
```

Claude Code separately has 3 MCP servers (`playwright`, `github`,
`brave-search`); Codex separately has `node_repl` enabled and
`computer-use` / `local-mcp` disabled. **Neither was modified.**

## Growth path

Everything below is reachable without re-architecting:

| Target | Mechanism | State |
|---|---|---|
| Web search / browser | `browser-use` toolset + `hermes mcp add` | tool present, `browser` needs a system dep |
| Coding | `hermes_cli/codex_runtime_switch.py` → Codex CLI | upstream support exists, not wired |
| Long reasoning | Hermes cron already drives `claude -p` today | pattern proven, not wired into voice |
| Git / GitHub | terminal toolset (push is deny-gated on purpose) | working |
| Calendar / Mail | `hermes mcp add` | not started |
| **Home Assistant** | `[homeassistant]` extra (`aiohttp==3.14.3`) + gateway platform slot + `smart-home` skill | plugin already reports **configured**; extra not installed |
