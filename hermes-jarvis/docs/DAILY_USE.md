# DAILY_USE

Everyday operation of the always-on JARVIS. Nothing here requires a Terminal
window: the runtime, watchdog, HUD and Hotkey all start at login.

The frozen configuration this describes is
[`JARVIS_DAILY_RC1_20260902`](./JARVIS_DAILY_RC1_BASELINE.md).

## Talking to JARVIS

```
Cmd+Shift+J   →   speak   →   response
```

That is the whole loop. The hotkey is served by **JARVIS Hotkey.app**, a login
item, and reaches the runtime over a 0600 unix socket — no window, no focus
change, no Terminal.

Saying **"Hey Jarvis"** is meant to work the same way and sometimes does, but it
is not currently dependable (see Known gaps). Cmd+Shift+J is the reliable path.

A turn ends by itself: the recorder stops after 1.2 s of silence, and the wake
listener re-arms about half a second after the reply finishes playing.

## Checking that everything is up

```sh
jarvis-status
```

One read-only command, about 0.3 s. It reports:

```
JARVIS STATUS — HEALTHY

Runtime            ✓ PID 14644
Watchdog           ✓ PID 94818
HUD                ✓ PID 95204
Hotkey             ✓ PID 94910
Ollama             ✓ PID 14732
Gateway            ✓ PID 94835
State              ✓ IDLE / v2
Wake lease         ✓ runtime (PID 14644)
Socket             ✓ 0600
Privacy            ✓ 0600 state / 0700 dirs
Audio              ✓ PA 1/1 / repl 0 / err 0
Startup warm       ✓ gemma4:e2b warm in 17.17s, ready after 4 attempt(s)
Ollama API         ✓ ready / gemma4:e2b resident
Hermes             ✓ ready (/health)
Watchdog log       ✓ 0 restarts, last seen 2026-09-02 12:31:24
HUD login item     ✓ registered, running (PID 95204)
Hotkey login item  ✓ registered, running (PID 94910)

Known gaps: 4
```

`jarvis-status --json` gives the same thing machine-readably. Exit code is 0 for
HEALTHY, 1 for DEGRADED, 2 for FAIL.

**It is safe to run at any time, including mid-turn.** It only reads: files,
`ps`, `lsof`, and two localhost GETs that neither load a model nor start a
session. It never opens the microphone, takes the wake lease, sends ACTIVATE, or
starts or stops anything. `tests/test_jarvis_status.py` asserts this by running
the tool against the live system and checking that every PID, the published
state, the turn id and the audio counters are unchanged afterwards.

### How it is installed

`jarvis-status` is on PATH as a symlink, so it runs from any directory:

```
~/.local/bin/jarvis-status → ~/AI-Lab/hermes-jarvis/bin/jarvis-status
```

`~/.local/bin` was already first on PATH and held no file by that name. No shell
rc file was edited. To undo: `rm ~/.local/bin/jarvis-status` — the tool still
works when called by its full path.

## When something feels wrong

**Run `jarvis-status` first.** The three verdicts mean different things:

| Verdict | Meaning |
|---|---|
| `HEALTHY` | Everything checked is as expected. If JARVIS still misbehaves, it is a behaviour problem, not a plumbing one. |
| `DEGRADED` | Usable, but something costs latency or is unproven — most often `gemma4:e2b` not resident, which just means the next turn pays the model load. |
| `FAIL` | Something is actually broken: no runtime, two runtimes, the wake lease held by someone else, a permission regression, or Ollama unreachable. |

The two that matter most in practice:

- **`Wake lease ✗ held by PID N, not the runtime`** — another process (usually
  an interactive `hermes chat`) has taken the machine-wide wake lock. The
  runtime is alive and listening to nothing. Quit that process.
- **`Runtime ! 2 processes`** — two runtimes are contending for the lease and
  the audio stream. Only one should exist.

Deeper troubleshooting is out of scope here; the logs are
`logs/jarvis_runtime.log` and `~/AI-Lab/jarvis-watchdog/logs/watchdog.log`.

## Idle cold-start policy

`gemma4:e2b` is 7.2 GB in GPU memory on a 32 GB machine shared with Claude and
Codex, so residency is bought deliberately rather than held forever:

- **At login**, the runtime warms the model once, waiting for Ollama to come up
  first. The first turn of the day does not pay the load.
- **After a turn that actually used Ollama**, residency is renewed to 60 minutes.
- **After a turn that used no Ollama inference** — a false wake, silence, an
  empty transcript — nothing is renewed. Whether the turn used the model is read
  from Ollama itself (`/api/ps`), not guessed from the route.
- **After more than 60 minutes with no real use**, Ollama unloads the model and
  gets its 7.2 GB back.
- **The first real turn after that** may cold-load, costing roughly 17 s once.

That last line is an intentional memory-for-latency trade, not a bug. Making the
model permanently resident would keep 7.2 GB pinned even on days JARVIS is never
spoken to. `jarvis-status` reflects this: a non-resident model after a quiet hour
is normal and does not make the status FAIL.

### Note on the renew, current Ollama

Measured 2026-09-02 on the installed Ollama:

- A plain `/v1/chat/completions` call — no `keep_alive` field — refreshed the
  model's expiry to **~60 m**, not to the server default of 10 m that
  `OLLAMA_KEEP_ALIVE` sets and that `_renew_residency`'s docstring assumes. An
  already-loaded runner appears to keep its own configured duration and re-arm
  from it on each use.
- So on this version the post-turn renew **appears redundant**: the inference
  that made the turn "used" had already re-armed the window. In the live CASE 2
  turn the renew moved expiry by only a few seconds.
- The renew is **kept anyway**, as a compatibility safeguard. It no longer runs
  on turns that used no inference, so its cost is now near zero, and the
  behaviour it guards against (a runner that drops to 10 m) may return with a
  different Ollama version or a cold-load path not exercised here.
- **Removing it is deferred** as a separate optimization, not part of this work.

## Known gaps

Read from `config/known_gaps.json`, so closing one is a config edit rather than
a code change. A gap never makes the status FAIL — it is a documented limit, not
a fault.

| Gap | State |
|---|---|
| **Wake word reliability** | "Hey Jarvis" was not detected in the 2026-09-02 post-login test. The feed is healthy (`feed_errors=0`); detection sensitivity is unproven. Use Cmd+Shift+J. |
| **Clamshell recovery** | Never observed across a clamshell sleep/wake cycle. |
| **Real-login warm validation** | The readiness-aware Ollama startup warm passed a controlled race test, but a real logout/login has not yet exercised it. |
| **Early turn during warm** | A turn begun while the startup warm is still retrying is unmeasured. Argued safe (Ollama serves one runner per model), not observed. |
