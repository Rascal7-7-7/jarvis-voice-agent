# CURRENT_STATE — Hermes JARVIS Build

Baseline captured: **2026-08-29 07:37 JST** (`PHASE1_BASELINE=PASS`)
All values below are measured on this machine, not assumed.

## Host

| | |
|---|---|
| Model | MacBook Pro — `MacBookPro18,2` |
| SoC | Apple M1 Max — 10-core CPU (8P+2E), 32-core GPU |
| Memory | 32 GB unified — free 84 %, **swap 0.00 MB** |
| Disk | 926 GiB — **600 GiB free** |
| macOS | 26.5.2 (25F84), Darwin 25.5.0, arm64 |
| Security | SIP enabled / Gatekeeper enabled / FileVault On |
| GPU wired limit | `iogpu.wired_limit_mb = 0` (OS default ≈ 21–24 GB addressable) |

## Hermes (before migration)

| | |
|---|---|
| Version | **0.15.2** — tag `v2026.5.29.2` |
| Install | **pip from PyPI** into pyenv **3.12.3** (`dist-info/INSTALLER = pip`, no `direct_url.json`) |
| Path | `~/.pyenv/versions/3.12.3/lib/python3.12/site-packages` |
| Service | LaunchAgent `ai.hermes.gateway` — `RunAtLoad=true`, `KeepAlive.SuccessfulExit=false` |
| Program | `/Users/Rascal/.pyenv/versions/3.12.3/bin/python3.12 -m hermes_cli.main gateway run --replace` |
| Gateway env | **only `PATH`, `VIRTUAL_ENV`, `HERMES_HOME`** — no secrets |
| Listening | **127.0.0.1:8644** (webhook platform, loopback only) |
| `state.db` | 85,250,048 B — `schema_version = 13`, **sessions 3,523 / messages 10,563**, FTS5 inline + trigram |
| `state_meta` | `ghost_session_prune_v1=1`, `orphaned_compression_finalize_v1=1` |
| `kanban.db` | 106,496 B — tasks/task_events/task_runs/task_links/task_comments/kanban_notify_subs (tasks = 0) |
| `config.yaml` | 1,430 B — `platforms.webhook`, `cron.script_timeout_seconds=2400`, `onboarding.seen`. **`_config_version` key ABSENT → effective 0** |
| Skills | **34** categories |
| Scripts | **68** automation shell scripts |
| Cron | **63** jobs |
| MCP servers | **0 configured** (`No MCP servers configured.`) |
| Credentials | `credential_pool: { copilot: CONFIGURED }`; `providers: {}`. All TTS provider keys NOT CONFIGURED |
| Messaging platforms | telegram/discord/slack/whatsapp/… **all empty** — attack surface minimal |

## Other AI stack

| Component | Version | Running | Note |
|---|---|---|---|
| Claude Code | 2.1.251 | 3 procs | config root `~/.claude-work` via wrapper `~/.local/bin/claude` |
| Claude Desktop | 1.34493.1 | yes | MCP servers: 0 |
| Codex CLI | 0.149.1 | 6 procs | `sandbox_mode=read-only`, `approval_policy=on-request` |
| ChatGPT Desktop | 26.820.60940 | yes | provides `node_repl` MCP to Codex |
| Ollama | 0.31.1 (brew) | **stopped** | **0 models (`~/.ollama/models` = 0 B)** |
| Node | v24.14.1 (nvm, default alias `24`, only version installed) | — | **claude + codex both live under this node tree** |
| uv | 0.11.6 | — | |

### Claude config-root routing (2026-08-29 state)

- Normal path: `PATH → ~/.local/bin/claude` → `exec env CLAUDE_CONFIG_DIR=$HOME/.claude-work <real cli>`
- Bypass path: `~/.claude` is a live alternate root. As of 2026-08-29,
  `~/.claude/CLAUDE.md → ~/.claude-work/CLAUDE.md` and `~/.claude/rules → ~/.claude-work/rules`
  are **symlinked**, so global instructions and rules now load even on bypass.
  `~/.claude/settings.json` still registers its own hooks, so behaviour is not identical.
- **Consequence for JARVIS:** any Hermes→Claude call must use the absolute wrapper path
  `~/.local/bin/claude`, never a bare `claude` resolved from a launchd PATH.

## Voice stack (before)

| Layer | State |
|---|---|
| Wake word | **absent** — no `porcupine` / `openwakeword` / wake code in 0.15.2 |
| VAD | implemented in `tools/voice_mode.py` (continuous, silence-stop, 3-strike exit) |
| STT | `faster-whisper` based, with `is_whisper_hallucination()` filter |
| TTS | `tools/tts_tool.py` — edge (default) / ElevenLabs / OpenAI / Gemini / MiniMax / xAI / **Piper (local)** / KittenTTS / custom `type: command` |
| **Blocker** | in the 3.12.3 env: `sounddevice` **MISSING**, `faster_whisper` **MISSING**, `numpy` **MISSING** → voice cannot run |
| Assets elsewhere | brew python3.14 has faster-whisper 1.2.1 / ctranslate2 4.7.1 / numpy 2.4.4 / openai-whisper / torch 2.11.0 (wrong env) |
| Whisper models | `~/.cache/whisper/base.pt` (139 MB) only; whisper.cpp has only `for-tests-ggml-tiny.bin` |
| macOS `say` | 184 voices, **9 × ja_JP** (Kyoko, Eddy, Flo, Grandma, Grandpa, Reed, Rocko, Sandy, Shelley) |
| Microphones | 3 detected (built-in, external, default) |

## Ports in use (relevant)

| Port | Bind | Owner |
|---|---|---|
| 8644 | 127.0.0.1 | **Hermes gateway webhook** |
| 3001 | 127.0.0.1 | `work/automation/bridge/server.js` |
| 3000 | `*` | node |
| 3306 | `*` | mariadb |
| 17841 / 61840 | — | **not listening** (Codex `openai_base_url` proxy is not resident) |
| 9222 | — | not listening (no Chrome CDP exposure) |

## Known security posture

| ID | Finding | Status |
|---|---|---|
| H-1 | `~/.codex/config.toml` has `[projects."/Users/Rascal"] trust_level="trusted"` (1 of 33 trusted entries) | **deferred — HUMAN_APPROVAL_REQUIRED** (see SECURITY_MODEL.md) |
| H-2 | AnyDesk `*:7070`, ARD `*:3283`, mariadb `*:3306`, node `*:3000` bound to all interfaces | out of scope of this migration; flagged |
| H-4 | Claude bypass root | **mitigated 2026-08-29** by CLAUDE.md/rules symlinks; settings/hooks still diverge |
| — | Secrets isolation for Hermes gateway | **already satisfied** (verified: plist env has no secrets; no `~/.hermes/.env`) |
| — | Agent-dedicated browser profile | **not yet created** — real Chrome (5.4 GB) and Brave (2.9 GB) profiles exist and must stay untouched |
