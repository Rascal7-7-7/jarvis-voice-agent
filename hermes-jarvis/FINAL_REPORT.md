# FINAL_REPORT — Hermes JARVIS build, 2026-08-29

```
JARVIS_BUILD_STATUS = PARTIAL — BLOCKED ON HUMAN

HERMES_BEFORE = 0.15.2  (tag v2026.5.29.2, pip/PyPI into pyenv 3.12.3)
HERMES_AFTER  = 0.20.6  (tag v2026.8.27, sha 5fc308a7, editable in ~/.hermes-venv)

WAKE_WORD = CONFIGURED, DISABLED — openWakeWord hey_jarvis, on-device, no API key
            verified on synthetic audio: 0.859 / 0.999 detect, 0.000 / 0.000 no-trigger
            NOT verified against a live microphone
STT       = WORKING — faster-whisper small int8, ja, 0.67–0.87 s, 2/3 exact on synth audio
TTS       = WORKING — edge/ja-JP-NanamiNeural ~570 ms per clause
            local fallback declared: say-ja/Kyoko ~1060 ms, one config value away

LOCAL_LLM = WORKING — Ollama 127.0.0.1:11434 (supervised by launchd)
            gemma4:e2b  voice, 3.0–4.4 s warm, natural Japanese, tool calling OK
            qwen3:4b    tools, 76–78 tok/s, tool calls correct, NOT speakable
CODEX     = PRESENT, NOT WIRED — 0.149.1, sandbox=read-only, approval=on-request, untouched
CLAUDE    = PRESENT, NOT WIRED — 2.1.251, untouched; cron already drives `claude -p`
BROWSER   = WORKING — web_search verified; real Chrome/Brave profiles never touched
MCP       = READY — mcp 2.0.0 installed, 0 servers registered (unchanged)

SECURITY_GATE         = PASS — 14 hard-blocked, 8/8 benign unprompted, 0 leaks
PROMPT_INJECTION_TEST = PASS — payload treated as data; 4/4 steps would also have
                        been blocked deterministically

BACKUP   = PASS — 322 MB, 18,283 files checksummed, all verified
ROLLBACK = PASS — tested by restoring into an isolated HERMES_HOME and driving it with 0.15.2
```

## What changed

| # | Change | Reversal |
|---|---|---|
| 1 | plist: interpreter → `~/.hermes-venv/bin/python`, `VIRTUAL_ENV` → venv. **PATH unchanged.** | restore plist (inline backup kept) |
| 2 | `state.db` 13 → 26 | restore snapshot |
| 3 | `~/.local/bin/hermes` → 0.20.6 | delete one symlink |
| 4 | `config.yaml` + stt/tts/voice/wake_word | `.pre-voice-*` |
| 5 | `config.yaml` + approvals/browser/security | `.pre-approvals-*` |
| 6 | `config.yaml` + model/custom_providers | `.pre-ollama-*` |
| 7 | `local.ollama.serve.plist` | unload + move aside |
| 8 | `~/.hermes-venv` 562 MB, `src/` 292 MB, models 9.0 GB | see ROLLBACK.md ordering |

Untouched: `~/.codex/*`, `~/.claude-work/*`, `~/.claude/*`, nvm, pyenv,
the 0.15.2 install, `~/.hermes/skills`, `~/.hermes/scripts`, `cron/jobs.json`.

## Data integrity

| | Before | After |
|---|---|---|
| sessions | 3,523 | 3,523 (+3 from my own test turns) |
| messages | 10,563 | 10,563 (+10 from my own test turns) |
| integrity_check | ok | ok |
| skills listed | 100 | 118 |
| cron enabled | 35 | 35, listing byte-identical |
| live cron runs after upgrade | — | 3 / 3 `last_status=ok` |

## KNOWN_ISSUES

1. **Voice latency is dominated by the local LLM.** ≈ 4.7–6.3 s to first audio;
   3.0–4.4 s of that is `gemma4:e2b` thinking, which streaming TTS cannot hide.
2. **`qwen3:4b` cannot speak.** Thinking model; returns empty `content` at
   `num_predict=600`. `think:false` relocates English CoT into `content`.
3. **SQLite 3.51.0 WAL-reset bug** — `state.db` (84 MB) and `kanban.db` exposed.
   Pre-existing (pyenv's SQLite), not caused by this migration.
4. **`_config_version` stayed at effective 0** vs 0.20.6's v39. Auto-migration
   floor is v12, so it did not fire. Benign — config was not rewritten.
5. **Codex trust scope** — `[projects."/Users/Rascal"] trust_level="trusted"`
   still present. Deferred to the human. See SECURITY_MODEL.md §3.
6. **`git push` is hard-blocked** for the agent. Deliberate; one line to relax.
7. **AnyDesk `*:7070` / ARD `*:3283` / mariadb `*:3306` / node `*:3000`** listen
   on all interfaces. Out of scope here, worth closing before always-listening.

## NEXT_RECOMMENDED_STEP

Grant Microphone permission to the terminal app, then run push-to-talk once by
hand. Everything else in the voice path is already proven.

See NEXT-STEPS.md.
