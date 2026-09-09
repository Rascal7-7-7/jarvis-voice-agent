# CODEX_DELEGATION

```
CODEX_INTEGRATION_METHOD = B — Codex CLI subprocess behind a fixed-argv wrapper
```

## Why not the built-in path

Hermes 0.20.6 ships `agent/transports/codex_app_server.py` and a
`/codex-runtime codex_app_server` switch. It spawns `codex app-server` and hands
**entire turns** to Codex — a runtime *replacement*, not a delegation. JARVIS
needs Hermes to keep the turn, delegate a subtask, and summarise the result for
TTS. The built-in path would also inherit the broken `openai_base_url` from
`~/.codex/config.toml`. MCP was rejected as an extra layer with no benefit here.

## The blocker that had to be solved first

Codex was **non-functional** at the start of this phase:

```
failed to connect to websocket: Connection refused (os error 61)
  url: ws://127.0.0.1:17841/v1/responses
ERROR: Reconnecting... waiting for network      ← forever; >10 min, no exit
```

`openai_base_url = http://127.0.0.1:17841/v1` is a proxy managed by
"Codex Web GPT.app" (3.0.3), which is not running. Per-invocation probes:

| override | result |
|---|---|
| none (as configured) | hangs indefinitely |
| `openai_base_url=""` | fails in 5.9 s: *"The 'chatgpt-web/high' model is not supported when using Codex with a ChatGPT account"* |
| `openai_base_url="https://api.openai.com/v1"` | 401, missing scope `api.responses.write` |
| **`openai_base_url="" -m gpt-5.5`** | **exit 0, 20.8 s, correct answer** |

`chatgpt-web/high` is a synthetic model name only that proxy understands. With a
real model name and an empty base URL, Codex routes through the ChatGPT
subscription directly — no API billing, no proxy, **and `~/.codex/config.toml`
is never written** (sha256 verified identical before and after every run).

## `bin/jarvis-codex`

Pinned in argv where the model cannot reach them:

```
codex exec --sandbox read-only --skip-git-repo-check
           -C <allowlisted dir> -c openai_base_url="" -m gpt-5.5 "<prompt>"
```

- `--sandbox read-only` — the only thing enforcing read-only; `approval_policy`
  from config does **not** apply to `codex exec` (it reports `approval: never`)
- directory allowlist — `~/AI-Lab/hermes-jarvis`, `~/work`; else exit 77
- hard timeout (default 150 s) — mandatory, see the blocker above
- output trimmed to Codex's final answer; the execution trace is dropped so the
  TTS does not read a shell log aloud

Exit codes: 64 usage · 66 missing dir · 69 codex not executable · 70 codex error
· 75 timeout · 77 outside allowlist.

## A3 — read-only smoke

Fixture `fixtures/codex-readonly/stats.py` with three planted bugs.

```
exit=0  wall=25.8s
→ mean([]) の ZeroDivisionError / clamp の上限境界 / percentile(p=1.0) の IndexError
fixture sha256 unchanged · ~/.codex/config.toml sha256 unchanged
```

## A4 — delegation accuracy, 10 cases

| case | result | wall |
|---|---|---|
| C01 Python debug | OK | 12.5 s |
| C02 Laravel (N+1 + missing 404) | OK | 19.1 s |
| C03 TypeScript type errors | OK | 29.2 s |
| C04 SQL review (missing GROUP BY) | OK | 14.4 s |
| C05 REST API design | OK | 17.9 s |
| C06 Dockerfile (`:latest`, hardcoded key) | OK | 14.6 s |
| C07 git diff review (DEBUG/ALLOWED_HOSTS/SECRET_KEY) | OK | 24.3 s |
| C08 security review (SQLi + MD5) | OK | 19.8 s |
| C09 test-failure analysis | OK | 46.6 s |
| C10 non-coding control ("日本の首都") | OK | 5.3 s |

**10 / 10**, median 19.1 s.

## A5 — write path

```
CODEX_WRITE_POLICY = NOT_ENABLED
```

`--sandbox read-only` is hardcoded; there is no write mode in the script. A
write path, if ever added, will be a separate reviewed script with an explicit
human confirmation step. Verified: asked to modify and save `stats.py`, the file
was unchanged and Codex reported it could not write.

## A6 — failure cases

| case | behaviour |
|---|---|
| timeout | exit 75 at 5.2 s with a 5 s budget → 「Codexを利用できませんでした（応答なし）」 |
| Codex unavailable (dead proxy) | never self-terminates; the wrapper's timeout is what saves it |
| sandbox denial (write attempt) | file unchanged, Codex declines |
| project not trusted | `codex exec` ignores `trust_level` entirely — refuses even inside `/Users/Rascal` |
| outside allowlist | exit 77 before Codex is launched |
| missing dir / no prompt | exit 66 / 64 |

Hermes never crashes; the dispatcher speaks 「Codexを利用できませんでした。」

```
CODEX_DELEGATION      = PASS
CODEX_READ_ONLY       = PASS
CODEX_WRITE           = NOT_ENABLED
CODEX_CONFIG_CHANGED  = NO   (sha256 5ac879a7… before and after)
```
