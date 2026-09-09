# ROLLBACK — Hermes JARVIS Migration

Backup root: `~/AI-Lab/backups/hermes-jarvis-migration-20260829-074823`
Created 2026-08-29 07:48 JST · `BACKUP_VERIFIED=PASS` · `ROLLBACK_TEST=PASS`

---

## Why rollback is not just "downgrade the package"

The `0.15.2 → 0.20.6` state.db migration is **one-way**.

- `schema_version` went `13 → 26`.
- Migration **v25** (`_dedupe_legacy_system_prompts`) moved `sessions.system_prompt`
  into a new content-addressed `system_prompts` table **and cleared the migrated rows**.
  (Measured impact: exactly **3 rows** — 3 inline prompts became 3 rows in the new table.)
- 0.15.2 has no knowledge of the `system_prompts` table.

**Reinstalling 0.15.2 against an already-migrated state.db leaves those system
prompts appearing empty.** A correct rollback ALWAYS restores `state.db` from a
snapshot; the package version alone is not enough.

## Why rollback is nonetheless cheap

The upgrade never touched the pyenv 3.12.3 installation of Hermes 0.15.2.
0.20.6 lives in its own venv, and production switched by **two values in one
plist**. The 0.15.2 interpreter, its site-packages, and the 68 automation
scripts' `python3` resolution were untouched throughout.

---

## Full rollback

### R0. Stop the services (do not `kill -9`)

```sh
launchctl unload ~/Library/LaunchAgents/ai.hermes.gateway.plist
launchctl unload ~/Library/LaunchAgents/local.ollama.serve.plist
pgrep -fl "hermes_cli.main gateway"   # expect no output
```

### R1. Revert the LaunchAgent

```sh
BK=~/AI-Lab/backups/hermes-jarvis-migration-20260829-074823
cp "$BK/meta/ai.hermes.gateway.plist" ~/Library/LaunchAgents/ai.hermes.gateway.plist
shasum -a 256 ~/Library/LaunchAgents/ai.hermes.gateway.plist
# must equal 3e47887896764f982251f24b6b14413a52d425a35249300b07c107c82705fff2
```

There is also an untouched inline copy at
`~/Library/LaunchAgents/ai.hermes.gateway.plist.pre-0.20.6-20260829-080329`.

### R2. Restore `~/.hermes`

Move the current one aside — never delete it:

```sh
mv ~/.hermes ~/.hermes.failed-$(date +%Y%m%d-%H%M%S)
rsync -a "$BK/hermes/" ~/.hermes/
# hermes-final/ holds a second, later snapshot of the two DBs taken immediately
# before the switch; its state.db hash is identical to the 07:48 one, which is
# itself the proof that nothing wrote to production during the dry run.
cp "$BK/hermes-final/state.db"  ~/.hermes/state.db
cp "$BK/hermes-final/kanban.db" ~/.hermes/kanban.db
chmod 700 ~/.hermes
```

### R3. Verify

```sh
sqlite3 ~/.hermes/state.db "pragma integrity_check"              # ok
sqlite3 ~/.hermes/state.db "select version from schema_version"  # 13
sqlite3 ~/.hermes/state.db \
  "select (select count(*) from sessions)||'/'||(select count(*) from messages)"
# 3523/10563
ls ~/.hermes/skills | wc -l    # 34  (0.15.2 materialises the `dogfood` builtin here)
python3 -c "import json;print(len(json.load(open('$HOME/.hermes/cron/jobs.json'))['jobs']))"  # 63
```

Reference hashes (`checksums.sha256`, 18,283 files, all verified):

| file | sha256 |
|---|---|
| `hermes/state.db` | `66bd90d313211decb2c7b700de956e0279944aa6ad16f8aa706e50a049cc8f5b` |
| `hermes-final/state.db` | `66bd90d313211decb2c7b700de956e0279944aa6ad16f8aa706e50a049cc8f5b` |
| `hermes/kanban.db` | `3aa6ff59d41f99747d5e5b7decb2bc6f8fe5c92defe68795aea355758bdec199` |
| `hermes/config.yaml` | `6658cb1405fbe37ff5ab51d631816206e8391d4df0fdfb70498df6dd20a85fb6` |
| `meta/ai.hermes.gateway.plist` | `3e47887896764f982251f24b6b14413a52d425a35249300b07c107c82705fff2` |
| `meta/codex-config.toml` | `5ac879a7dd39abf2bcf5f79e66df4839cf54cbd722cfd5e6f2cfff62eb85fdc4` |

Full check: `cd "$BK" && shasum -a 256 -c checksums.sha256 | grep -v ': OK$'`

### R4. Restart

```sh
launchctl load ~/Library/LaunchAgents/ai.hermes.gateway.plist
lsof -nP -iTCP:8644 -sTCP:LISTEN
~/.pyenv/versions/3.12.3/bin/hermes version   # v0.15.2 (2026.5.29.2)
```

### R5. Only if the pyenv 0.15.2 install itself was damaged

```sh
~/.pyenv/versions/3.12.3/bin/pip install --force-reinstall --no-deps \
  "$BK/rollback-wheels/hermes_agent-0.15.2-py3-none-any.whl"
~/.pyenv/versions/3.12.3/bin/pip install -r "$BK/meta/pip-freeze-3.12.3.txt"
```

---

## Partial rollbacks (each is independent)

| Undo | Command | Effect |
|---|---|---|
| **The whole 0.20.6 switch** | restore the plist (R1) + restore state.db (R2/R3) | back on 0.15.2 |
| **`hermes` on the PATH** | `rm ~/.local/bin/hermes` | `hermes` resolves via pyenv shims to 0.15.2 again. No script depends on it — the only `hermes` occurrence in the 68 automation scripts is inside a comment. |
| **Voice config** | `cp ~/.hermes/config.yaml.pre-voice-* ~/.hermes/config.yaml` | drops stt / tts / voice / wake_word |
| **Confirmation gate** | `cp ~/.hermes/config.yaml.pre-approvals-* ~/.hermes/config.yaml` | back to `approvals.mode=smart`, no deny list |
| **Local LLM wiring** | `cp ~/.hermes/config.yaml.pre-ollama-* ~/.hermes/config.yaml` | Hermes has no brain again |
| **Just the `git push` block** | delete the `"*git push *"` line from `approvals.deny`, restart the gateway | pushes allowed again |
| **Ollama at login** | `launchctl unload ~/Library/LaunchAgents/local.ollama.serve.plist` then move the plist aside | Ollama no longer supervised. **Do this together with the config rollback**, or Hermes points at a dead endpoint. |
| **Ollama models (9.0 GB)** | `ollama rm gemma4:e2b` / `ollama rm qwen3:4b` | reclaims disk |
| **The 0.20.6 venv (511 MB)** | `mv ~/.hermes-venv ~/.hermes-venv.discarded-$(date +%Y%m%d-%H%M%S)` | do this last |
| **The pinned source checkout (259 MB)** | `mv ~/AI-Lab/hermes-jarvis/src ~/AI-Lab/hermes-jarvis/src.discarded` | the venv is an **editable** install, so removing the checkout breaks 0.20.6 — only do this after the venv is gone |

**Ordering rule:** config first, then services, then artefacts. Never remove
`~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27` while the LaunchAgent still
points at `~/.hermes-venv` — the editable install resolves its code from there.

## What is NOT backed up

| Not backed up | Why |
|---|---|
| `~/.codex/auth.json`, `~/.ssh`, `~/.aws` | credentials — never copied, never read. Codex was not modified. |
| `~/.claude-work/` beyond `settings.json` | Claude Code was not modified. |
| pyenv site-packages tree | GB-scale; reconstructable from `meta/pip-freeze-3.12.3.txt` + PyPI. |
| Ollama models | re-pullable: `ollama pull gemma4:e2b`, `ollama pull qwen3:4b`. |

## Rollback test evidence (2026-08-29 07:50 JST)

The backup was restored into an isolated `HERMES_HOME` and driven with the
0.15.2 binary:

```
rsync -a $BK/hermes/ $RT/                     -> 227M restored
sqlite3 $RT/state.db "pragma integrity_check" -> ok
schema_version                                -> 13
sessions/messages                             -> 3523/10563
HERMES_HOME=$RT hermes sessions list          -> rc=0, real session rows rendered
HERMES_HOME=$RT hermes skills list            -> rc=0, skills table rendered
```

`ROLLBACK_TEST=PASS`
