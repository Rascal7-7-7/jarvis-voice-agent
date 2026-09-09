# JARVIS_DAILY_RC1_20260902 — release candidate baseline

The configuration JARVIS is being used with daily, frozen so that the outstanding
real logout/login validation has a fixed thing to validate.

This document is a record, not a change. Nothing was restarted or modified to
produce it.

| | |
|---|---|
| **RC name** | `JARVIS_DAILY_RC1_20260902` |
| **Frozen** | 2026-09-02 14:07 JST |
| **Manifest** | [`JARVIS_DAILY_RC1_SHA256SUMS.txt`](./JARVIS_DAILY_RC1_SHA256SUMS.txt) — 25 files, all verified |
| **Daily status** | `YES_WITH_KNOWN_GAP` |
| **Ready for daily use** | **YES** |
| **Outstanding** | `REAL_LOGIN_WARM_VALIDATION = PENDING` |

No git tag was created. This is a documentation baseline.

## Verifying the baseline

```sh
cd ~ && shasum -a 256 -c ~/AI-Lab/hermes-jarvis/docs/JARVIS_DAILY_RC1_SHA256SUMS.txt
```

25 lines, every one `OK`, is the definition of "still RC1". The manifest covers
the runtime and its router/gate/dispatch chain, the two upstream Hermes audio
modules, the watchdog, the installed HUD and Hotkey executables, and all four
LaunchAgent plists.

Identity is the hashes, **not PIDs** — every PID here changes at the next login
and none of them is part of the baseline.

## Live status at freeze

`jarvis-status` → **HEALTHY**, all seventeen checks passing.

```
Runtime  IDLE / v2      Wake lease  held by the runtime
Watchdog 0 restarts     Socket      0600, owner-only
HUD      running        Privacy     0600 state / 0700 dirs
Hotkey   running        Audio       PA 1/1, repl 0, err 0
Ollama   ready, gemma4:e2b resident
Hermes   ready (/health)
Startup warm  gemma4:e2b warm in 7.09s, ready after 1 attempt
Login items   HUD and Hotkey both registered
```

## Production contract

**Activation**

| | |
|---|---|
| Primary | "Hey Jarvis" — *reliability tuning pending, see gaps* |
| Reliable fallback | **Cmd+Shift+J** (JARVIS Hotkey.app, a login item) |

**Turn**

```
activation → LISTENING → STT → Router → backend → TTS → IDLE
```

**HUD** — state schema v2: `turn_id`, explicit `route`, `latency_ms` breakdown.

**Watchdog** — recovers a stuck LISTENING and a persistent ERROR; turn-identity
aware, so it does not mistake a long legitimate turn for a stall.

**Audio** — one shared CoreAudio stream: `PA_OPEN_COUNT=1`, `PA_START_COUNT=1`.
The wake detector owns no device; it is fed from the shared stream.

## Ollama policy (frozen)

| Stage | Behaviour |
|---|---|
| Startup | Readiness-aware warm — waits for `/api/tags`, bounded retry (1/2/3/5 s, 45 s budget), then warms via the existing warm path |
| `keep_alive` | **60m**, set per request on `/api/chat`. The LaunchAgent's `OLLAMA_KEEP_ALIVE=10m` is untouched |
| Actual inference | On this Ollama build, a `/v1` call **itself** refreshes expiry to ~60 m |
| Post-turn renew | Only when `/api/ps` shows the turn actually used the model. Kept as a compatibility safeguard |
| No speech / false wake | **No renew**, and no `/api/ps` query at all |
| >60 min true idle | Model may unload; the next real turn may cold-start (~17 s once) |

That last row is an **intentional memory/latency trade-off**: `gemma4:e2b` is
7.2 GB on a 32 GB machine shared with Claude and Codex. A non-resident model
after a quiet hour is normal and does not make `jarvis-status` fail.

## Acceptance results at freeze

| Item | Result |
|---|---|
| Manual CLI activation | PASS |
| Physical hotkey 10/10 | PASS |
| Real turn E2E | PASS |
| HUD live, schema v2 | PASS |
| Shared stream | PASS |
| Watchdog alias fix | PASS |
| Persistent ERROR recovery | staged PASS |
| Real login startup | PASS |
| Post-login hotkey turn | PASS |
| Daily health check | PASS |
| Renew policy, live | PASS |

Test suites: startup warm 11/11, jarvis-status 61/61, renew policy 26/26.
Whole suite 183 passed, 7 environment-blocked (`WakeWordInUse` — those tests need
exclusive microphone ownership, which the running runtime legitimately holds).
The 7 are **not** counted as passes.

## Known gaps

Authoritative list. `config/known_gaps.json` carries the first four, which
`jarvis-status` prints; the rest are backlog recorded here only.

| Gap | State |
|---|---|
| `WAKE_WORD_RELIABILITY` | **TUNING_PENDING** — detector and pipeline alive (`feed_errors=0`, a detector-origin wake observed firing on room noise), but intentional-user recall and the ambient false-positive rate are both unreliable |
| `CLAMSHELL_SPECIFIC_RECOVERY` | **UNVERIFIED** — never observed across a clamshell sleep/wake |
| `REAL_LOGIN_WARM_VALIDATION` | **PENDING** — startup warm passed a controlled race test; a real logout/login has not exercised it |
| `EARLY_TURN_DURING_STARTUP_WARM` | **UNVERIFIED** performance edge — argued safe (Ollama serves one runner per model), not measured |
| Early-return IDLE turn-context | BACKLOG, non-blocking |
| Renew removal | **DEFERRED** optimization — see the Ollama policy note above |
| Internal mic `DIGITAL_SILENCE` | FINAL TUNING / backlog |
| Chatterbox, ACK, HUD cosmetics | FINAL TUNING / backlog |

## Freeze rule

Until `REAL_LOGIN_WARM_VALIDATION` is done, **production source does not change.**
Anything found in the meantime is filed as `BLOCKER` or `BACKLOG`; only a blocker
earns a patch.

Experimental leftovers — `JarvisAckHelper` and similar — are deliberately **not**
cleaned up yet, so that the validation runs against unchanged system state.
Cleanup is a later phase.

## Checklist for the real login validation

Run after a genuine logout → login, with **nothing started by hand**.

**1. Processes** — exactly one of each, no duplicates:

```sh
jarvis-status        # expect HEALTHY
```

runtime, watchdog, HUD, Hotkey, Ollama, Hermes gateway.

**2. Startup ordering** — from `logs/jarvis_runtime.log`, confirm the wake
listener comes up *before* the warm finishes. That ordering is the property that
keeps "Hey Jarvis" usable while Ollama is still loading:

```
state=OFFLINE starting
wake listener up  ...          ← first
state=IDLE                     ← first
ollama startup readiness: ...  ← after
ollama warm (startup): gemma4:e2b ready in ...s, keep_alive=60m
```

**3. Warm success gate.** Do **not** take the first-turn measurement until the
`ollama warm (startup)` success line is present and `gemma4:e2b` is resident. If
the 45 s budget expires without success, record `REAL_LOGIN_WARM = FAIL` and stop.

**4. First turn** — Cmd+Shift+J, short greeting. From the `timeline` line:

| | Pre-fix (measured) | Expected now |
|---|---|---|
| backend | 17,866 ms | ≈ 4–6 s |
| first audio | 26,124 ms | ≈ 8–10 s |

Not an SLA. The judgement is whether the ~14 s cold-start penalty is gone.

**5. Regression** — `PA_OPEN_COUNT=1`, `PA_START_COUNT=1`, `replacements=0`,
`feed_errors=0`, watchdog restarts 0, runtime back to IDLE.
