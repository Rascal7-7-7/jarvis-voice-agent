# SECURITY_MODEL — Hermes JARVIS

Last verified: 2026-08-29, Hermes 0.20.6 (tag `v2026.8.27`).
Everything in this file was measured, not assumed. Where it was not measured it says so.

---

## 1. Threat model

A hands-free, always-listening agent changes the threat model in one specific way:
**the human is no longer in the loop between "input arrives" and "tool runs."**

The chain that matters:

```
Web page / email / repo README
        │  (attacker-controlled text)
        ▼
   Agent context
        │
        ▼
   Tool call  ──►  Terminal / Files / Browser / Codex / Claude
```

With Claude Code the user types a request and reads the plan. With JARVIS a
misheard sentence, or an injected instruction inside a fetched web page, can
reach the same tools with no reading step. Every control below exists to put a
deterministic gate back into that chain — deterministic, because an LLM-based
judge is reachable by the same injection it is supposed to catch.

## 2. Controls in force (measured)

### 2.1 Confirmation gate — `approvals`

| Setting | Value | Why |
|---|---|---|
| `approvals.mode` | **`manual`** | Not `smart`. `smart` delegates the "is this safe?" decision to an auxiliary LLM, which is itself reachable by prompt injection. A voice agent must not have an LLM deciding whether an LLM may run a destructive command. |
| `approvals.cron_mode` | `deny` | Cron-context agent turns cannot self-approve. |
| `approvals.single_query_mode` | `deny` | `-q` one-shot sessions cannot self-approve. |
| `approvals.deny` | **40 globs** | Unconditional floor (see below). |
| `security.approval.transport_fallback` | **`deny`** | Fail-closed: if the approval transport is unavailable, the command is denied, not allowed. |

`approvals.deny` is the strongest control available. From the upstream source:

> a deny match fires BEFORE the yolo / mode=off bypass … Matching is
> case-insensitive and runs over the same normalized / deobfuscated command
> variants the dangerous-pattern detector uses, so quoting tricks (`r\m`,
> `git st""atus`) can't sidestep a rule.

So a denied command cannot be run through the agent even with `--yolo`,
`/yolo`, or `approvals.mode=off`.

Categories covered: SECRET_ACCESS, DESTRUCTIVE, PRIVILEGE/SECURITY_BYPASS,
remote-code-execution pipes, AGENT SELF-ESCALATION, EXTERNAL_SIDE_EFFECT.

### 2.2 Measured gate behaviour

Classification test over 23 commands (`scratchpad/gate_test2.py`, nothing executed):

| Verdict | Count | Notes |
|---|---|---|
| BLOCKED (hard floor) | **14** | includes both obfuscation attempts (`s""udo rm -rf /`, `r\m -rf …`) |
| PROMPT (consent required) | 1 | `rm /tmp/one-file.txt` → "delete in root path" |
| allow (no friction) | 8 | `ls`, `git status`, `cat`, `rg`, `git diff`, `echo >`, `git commit`, `mkdir -p` |
| **benign left unprompted** | **8 / 8** | no prompt fatigue on read-only work |
| **dangerous but allowed (leak)** | **NONE** | |

**A real gap was found and closed during this test.** `git push origin main`
initially passed with no prompt: Hermes ships only *force*-push in
`DANGEROUS_PATTERNS`, and the config exposes only `deny` (hard block) and
`command_allowlist` (auto-approve) — there is no user-extensible "ask me first"
tier. For a hands-free path the only mechanism that stops an un-consented
external artifact is `deny`, so `*git push *`, `*gh repo delete*`,
`*npm publish*`, `*docker push *` were added.

This is **deliberately stricter than the user's own Claude Code policy**
(which allows `Bash(git *)` and denies only force-push). The justification is
the threat model in §1, not a belief that the Claude policy is wrong.
**To relax:** delete the `*git push *` line from `approvals.deny` and restart
the gateway. Nothing else depends on it.

### 2.3 Secrets isolation — already satisfied, verified

| Check | Result |
|---|---|
| LaunchAgent `EnvironmentVariables` keys | **`PATH`, `VIRTUAL_ENV`, `HERMES_HOME` only** |
| Occurrences of `*API_KEY*` / `*TOKEN*` / `*SECRET*` in the plist | **0** |
| `~/.hermes/.env` | **does not exist** |
| `GITHUB_PERSONAL_ACCESS_TOKEN` / `BRAVE_API_KEY` defined in `~/.zshrc`, `~/.zprofile`, `~/.env_secrets`, `~/dotfiles/zsh/.zshrc` | **0 occurrences** |

launchd does not inherit the user's shell environment, so the gateway process
holds no GitHub PAT, no Brave key, no AWS credentials, no SSH keys, no Claude
auth and no Codex auth. Reading them from disk is additionally blocked by the
`approvals.deny` SECRET_ACCESS globs.

`~/.hermes/auth.json` holds one entry: `credential_pool.copilot` = CONFIGURED.
No value is printed anywhere in this repository of documents.

### 2.4 Browser isolation

| Setting | Value |
|---|---|
| `browser.use_real_profile` | **false** (0.20.6 default, pinned explicitly) |
| `browser.allow_private_urls` | false |
| `browser.allow_unsafe_evaluate` | false |
| `security.allow_private_urls` | false |

Pinned rather than left implicit so a future upstream default change cannot
silently hand the agent the user's real profiles — Chrome (**5.4 GB**) and
Brave (**2.9 GB**) both exist on disk with live cookies and sessions.
Chrome CDP (9222) is **not listening**.

**Residual:** Codex's `node_repl` MCP server carries
`BROWSER_USE_AVAILABLE_BACKENDS = "chrome,iab"`. That is a Codex-side setting,
not a Hermes one, and it is out of scope of this migration. Whether that
backend attaches to the real Chrome profile is **NOT VERIFIED**. The JARVIS
browser path does not use it.

### 2.5 Network exposure

| Port | Bind | Owner | Change |
|---|---|---|---|
| 8644 | **127.0.0.1** | Hermes gateway (webhook) | unchanged by this migration |
| 11434 | **127.0.0.1** | Ollama | new, loopback only |

No new externally-reachable listener was created.

`gateway.run` reports *"No user allowlists configured. All unauthorized users
will be denied."* — that is the safe default, and all 25 messaging platforms
are unconfigured, so the gateway has no inbound path except the loopback
webhook.

### 2.6 Terminal backend

`hermes status` reports `Sudo: ✗ disabled` for the terminal backend, on top of
the `sudo *` / `*sudo *` deny globs.

## 3. Deferred — requires a human decision

```
STATUS=HUMAN_APPROVAL_REQUIRED
ITEM=Codex trust scope
REASON  ~/.codex/config.toml line 64-65 contains
          [projects."/Users/Rascal"]
          trust_level = "trusted"
        1 of 33 trusted entries; the other 30 are already-listed subdirectories
        of the home directory (work, work/AI_Trade, work/automation, Documents/Codex/*).
        Whether trust_level inherits into subdirectories is NOT VERIFIED, and
        non-interactive automation drives Codex (tmux 指揮官 x2, 35 cron jobs), so
        removing it could leave a non-interactive run blocked on a trust prompt.
CURRENT_EFFECT
        Bounded by the global settings sandbox_mode = "read-only" and
        approval_policy = "on-request", so this is not home-wide write access.
        Risk is real but lower than a first reading suggests.
RECOMMENDED
        Remove only the "/Users/Rascal" entry; keep the 30 specific projects.
RISK IF REMOVED   a non-interactive Codex run in an unlisted directory may stall.
RISK IF KEPT      after JARVIS goes always-listening, injection reaching Codex
                  has a broad trust surface.
BACKUP            ~/AI-Lab/backups/hermes-jarvis-migration-20260829-074823/meta/codex-config.toml
                  sha256 5ac879a7dd39abf2bcf5f79e66df4839cf54cbd722cfd5e6f2cfff62eb85fdc4
SAFE_TO_RESUME_AFTER  Phase 13 (Codex integration). Nothing else is blocked by it.
```

## 4. Pre-existing findings, not introduced by this migration

| Sev | Finding | Note |
|---|---|---|
| MEDIUM | **SQLite 3.51.0 WAL-reset corruption bug** (`sqlite.org/wal.html#walresetbug`). `state.db` (84 MB) and `kanban.db` are in WAL mode and exposed. Fixed in 3.51.3+ / 3.50.7 / 3.44.6. | Comes from the SQLite linked into pyenv 3.12.3 — the same interpreter 0.15.2 used, so this predates the migration. Hermes already self-mitigates `cron/executions.db` by forcing `journal_mode=DELETE`. |
| HIGH | AnyDesk `*:7070`, Apple Remote Desktop `*:3283`, mariadb `*:3306`, node `*:3000` listening on all interfaces | Out of scope of this migration. Worth closing before JARVIS goes always-listening. |
| MEDIUM | `~/.claude/settings.json` registers 7 lifecycle hooks all calling `node ~/.pixel-agents/hooks/claude-hook.js`; that script's provenance is **NOT VERIFIED** | A hook observes every tool call. |
| INFO | Claude's `github` and `brave-search` MCP servers reference `${GITHUB_PERSONAL_ACCESS_TOKEN}` / `${BRAVE_API_KEY}`, which are not defined in any shell rc file — they may be silently unauthenticated | Claude-side; not touched. |

## 5. Not yet verified

| Item | Status | Blocker |
|---|---|---|
| Prompt-injection isolation end-to-end (Phase 16) | **NOT VERIFIED** | needs a working LLM brain + a live agent turn |
| Microphone / Screen Recording / Accessibility TCC grants | **NOT VERIFIED** | `TCC.db` returns `authorization denied`; requires a human to read System Settings. Confirmed: the shell running this session does **not** have Full Disk Access. |
| Wake-word false-positive rate against real ambient audio | **NOT VERIFIED** | synthetic negatives scored 0.000, but real speech/music/video was not tested — needs a microphone |
| Codex `node_repl` chrome backend profile binding | **NOT VERIFIED** | Codex-side |

**Full Disk Access is deliberately NOT granted.** `hermes doctor` suggests it to
silence per-folder macOS prompts; granting it would defeat the point of the
filesystem controls above.
