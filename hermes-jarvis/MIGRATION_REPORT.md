# MIGRATION_REPORT — Hermes 0.15.2 → 0.20.6

Executed 2026-08-29, 07:37 → 15:12 JST. All figures measured on this machine.

---

## 1. Target selection

```
TARGET_VERSION = v2026.8.27  (hermes-agent 0.20.6)
COMMIT         = 5fc308a70719a83cccdbba4c0e39c23f5a8239d5
SOURCE         = github.com/NousResearch/hermes-agent, stable tag
```

**Why not PyPI.** PyPI's newest `hermes-agent` is **0.19.0** (2026-07-20); the
0.20.x line exists only as GitHub calendar tags. 0.19.0 has no wake word, so
`pip install --upgrade` could not reach the goal. Upstream also uses calendar
tags: the installed 0.15.2 is tag `v2026.5.29.2`.

**Why not `hermes update`.** `hermes version` reported *"1 commit behind"* — that
is a stale cache (`~/.hermes/.update_check` ts = 2026-06-24); the real gap was
five minor versions. `hermes update` also targets the managed-venv layout, and
this install came from **pip into pyenv 3.12.3** (`dist-info/INSTALLER = pip`,
no `direct_url.json`).

**Why not the official installer.** `curl … | bash` is on the user's own Claude
`permissions.deny`, and the installer builds its own managed venv plus a managed
Node — which would orphan the LaunchAgent and the 35 active cron jobs, and
v0.20.0 requires **Node 26** while Claude Code 2.1.251 and Codex CLI 0.149.1
both live under `~/.nvm/versions/node/v24.14.1/lib/node_modules` (nvm default
alias `24`, and v24.14.1 is the only version installed). Switching the default
would remove `claude` and `codex` from PATH.

**What was used.** A shallow clone pinned to the tag, then the editable install
that upstream's own error message prescribes:

> `Building wheels or sdists for hermes-agent is not supported. … If you are
> developing, use an editable install instead: uv pip install -e .`

The first attempt (`uv pip install "hermes-agent[...] @ git+…"`) failed on that
guard. That was recorded as a failure and the method changed — not retried.

## 2. Install topology

| | Before | After |
|---|---|---|
| Interpreter | `~/.pyenv/versions/3.12.3/bin/python3.12` | `~/.hermes-venv/bin/python` |
| Package | pip / PyPI 0.15.2 | editable 0.20.6 from a pinned checkout |
| Source | site-packages | `~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27` (259 MB) |
| venv | — | `~/.hermes-venv` (511 MB) |
| Extras | (base) | `voice, wake, mcp, cron, edge-tts` |
| **pyenv 0.15.2** | in use | **untouched and still functional** |

The shared pyenv environment was deliberately left alone: 68 automation scripts
resolve `python3` through it, and `edge-tts` / `playwright` live there. Pushing
120 pinned dependencies into it would have put all of that at risk.

## 3. The switch

Three atomic changes, each verified:

| # | Change | Downtime |
|---|---|---|
| 1 | plist `ProgramArguments[0]` → venv python; `VIRTUAL_ENV` → `~/.hermes-venv`. **`PATH` deliberately unchanged** (the venv's `bin` contains `python`/`python3`, which would shadow pyenv for the automation scripts). | 14 s |
| 2 | Deliberate `state.db` migration with the gateway stopped. 0.20.6 defers migration until first use; rather than let a cron agent trigger it unsupervised, it was forced and watched. | **2 s** |
| 3 | `ln -s ~/.hermes-venv/bin/hermes ~/.local/bin/hermes` (first on PATH). Safe because **no script invokes `hermes`** — the only occurrence in the 68 scripts is inside a comment. | 0 s |

## 4. state.db migration 13 → 26

Proven first against an isolated copy (`HERMES_HOME=<scratch>`), then run on
production with identical results.

| Migration | What it does | Verdict |
|---|---|---|
| v16 | tags `sessions.model_config` with a `_delegate_from` JSON key | additive |
| v18 | best-effort backfill from `sessions.json`, wrapped in try/except | non-destructive |
| v20 | `INSERT OR IGNORE` into the new `session_model_usage` | additive, idempotent |
| v22 | rebuilds `session_model_usage` PK (new table here, so a no-op) | n/a |
| v23 | FTS storage redesign — **opt-in only**; sets `fts_optimize_available=1` and touches nothing | not executed |
| **v25** | de-duplicates `sessions.system_prompt` into `system_prompts` | **the only row-rewriting migration** |

`SCHEMA_SQL` is the single source of truth and `_reconcile_columns()` applies
`ALTER TABLE … ADD COLUMN` automatically, so there are no per-column migrations
to go wrong. No `DROP`/`DELETE` touches `sessions` or `messages`.

### Result

| | Before | After |
|---|---|---|
| `integrity_check` | ok | **ok** |
| `schema_version` | 13 | **26** |
| **sessions** | 3,523 | **3,523** |
| **messages** | 10,563 | **10,563** |
| new tables | 0 | **7** |
| inline `system_prompt` rows | **3** | **0** |
| `system_prompts` table rows | — | **3** |
| file size | 85,250,048 B | 88,158,208 B |

v25's real impact was **3 rows**, an order of magnitude smaller than the
pre-migration risk assessment assumed.

## 5. Regression

| Item | Before | After | Status |
|---|---|---|---|
| Gateway | PID 38523 | PID 41543, **6 h 57 m uptime, no crash loop** | PASS |
| Port | 127.0.0.1:8644 | 127.0.0.1:8644 | PASS |
| webhook / cron ticker / kanban dispatcher | up | up | PASS |
| Old sessions readable | yes | yes (2026-08-21 rows render) | PASS |
| skills listed by Hermes | 100 | **118** | PASS |
| `~/.hermes/skills` dirs | 34 | 33 | **explained** ¹ |
| cron enabled | 35 | 35, listing **byte-identical** | PASS |
| `cron/jobs.json` | 63 entries | 63 entries | PASS |
| **live cron runs** | — | health-check 08:00:44 **ok** · publish-x 08:02:14 **ok** · publish-note 08:07:28 **ok** | PASS |
| MCP servers | 0 | 0 | PASS |
| `config.yaml` immediately after upgrade | `6658cb14…` | `6658cb14…` | PASS |
| Codex integration | `codex_runtime_switch.py` | present | PASS |
| Claude integration | cron drives `claude -p` | path unchanged | PASS |
| unexpected port exposure | — | none | PASS |

¹ `dogfood` is a **builtin**. 0.15.2 materialised a copy under
`~/.hermes/skills/dogfood`; 0.20.6 serves it from the package
(`skills/software-development/dogfood`) and still lists it as
`builtin / enabled`. Nothing was lost.

## 6. Risks that were open before the migration, and how they resolved

| Risk | Resolution |
|---|---|
| (a) Node 26 requirement | **Avoided.** No Node major enforcement in `hermes_cli` — it lives in the installer/heal/upgrade paths. `hermes doctor` reports `Node.js ✓`. nvm default stays `24`; claude and codex untouched. |
| (b) `_config_version` effective 0 < the v12 auto-migration floor | **Confirmed, benign.** Auto-migration did not fire; `hermes doctor` shows `Config version outdated (v0 → v39)` as a ⚠. `config.yaml` was not rewritten and Hermes runs normally. |
| (c) v25 rewrites state.db one-way | **Confirmed, 3 rows.** Rollback still requires a DB restore — see ROLLBACK.md. |
| (d) 120 pinned deps polluting the shared pyenv env | **Fully avoided** by the separate venv. |

## 7. New finding (pre-existing, not caused by the migration)

```
MEDIUM — SQLite 3.51.0 is subject to the WAL-reset corruption bug
         https://sqlite.org/wal.html#walresetbug
         state.db (84 MB) and kanban.db are in WAL mode and exposed.
         Fixed in SQLite 3.51.3+ / 3.50.7 / 3.44.6.
         Source: the SQLite linked into pyenv 3.12.3 — the same interpreter
         0.15.2 used, so this predates the migration.
         Hermes already self-mitigates cron/executions.db (journal_mode=DELETE).
```
