# BACKGROUND_WAKE — hosting the wake listener without a Terminal

```
BACKGROUND_METHOD  = C — dedicated JARVIS runtime process under launchd
UPSTREAM_SUPPORTED = partial (detector / STT / TTS are upstream APIs; the loop is local)
PATCH_REQUIRED     = NO
```

## Why not the upstream options

| option | verdict | evidence |
|---|---|---|
| A. Hermes background / gateway voice | **no** | the gateway hosts no wake listener. `tui_gateway/server.py:16229` starts one, but only when a **connected transport** calls `wake.start`, and that transport owns the lease. Not a headless host. |
| B. headless `hermes chat` | **no** | `cli.py` drives the listener from the interactive prompt_toolkit TUI |
| D. launchd wrapper around `hermes chat` | **no** | still a TUI process; a terminal UI supervised by launchd is fragile and visible |
| E. macOS `.app` helper | **not needed** | see TCC.md — launchd captures real audio 3/3 |

## The machine-wide microphone lease

`tools/wake_word.py` takes an exclusive `flock` on
`~/.hermes/runtime/wake-word.lock`. **Only one surface may own the wake
microphone at a time.** An interactive `hermes chat` claims it, and while it
holds the lease `start_listening` raises `WakeWordInUse`.

Two consequences, both handled:

**1. The CLI must stop competing.** `cli.py:15280` gates on
`wake_surface_enabled("cli")`, so setting `wake_word.surface: gui` (from the
default `auto`) stops `hermes chat` claiming the lease. Push-to-talk (Ctrl+B) is
a separate mechanism and remains available as the manual fallback.

**2. The runtime must wait, not die.** The first implementation exited 75 on
`WakeWordInUse`; under `KeepAlive` that is a crash loop. It now retries with
backoff (5 s, growing to a 60 s cap) and takes the lease the moment it frees.
Verified: the launchd pid stayed constant across three 8-second samples while an
interactive session held the lease — waiting, not restarting.

## The runtime

`bin/jarvis_runtime.py`. Everything except the loop itself is upstream API —
`tools/` is not forked. The only local change to the Hermes tree remains the
AudioRecorder device-follow patch from an earlier phase.

```
wake fires ─► pause_listening                          (releases the mic)
           ─► AudioRecorder.start(on_silence_stop=…)   VAD, 30 s hard ceiling
           ─► transcribe_recording                     faster-whisper small, ja
           ─► jarvis_gate ─► jarvis_router ─► jarvis-dispatch
           ─► speak_text                               edge ja-JP-NanamiNeural
           ─► resume_listening
```

| requirement | how |
|---|---|
| single instance | `flock` on `logs/jarvis_runtime.lock` |
| re-entrancy | non-blocking lock drops a second wake while a turn is in flight |
| clean shutdown | SIGTERM/SIGINT stop the loop, release the lease, shut the recorder down |
| restart on crash | `KeepAlive.SuccessfulExit=false` — a deliberate unload stays stopped |
| bounded restart | `ThrottleInterval 30` |
| log rotation | the runtime caps its own log at 5 MB (launchd does not rotate) |
| no secrets in plist | env is `PATH` / `VIRTUAL_ENV` / `HERMES_HOME` / `JARVIS_WORKDIR` only |
| loopback only | it opens no listener at all; it talks to 127.0.0.1:11434 and the local CLIs |
| no root | runs as the login user; no privilege escalation anywhere |
| explicit PATH | pinned in the plist, and the venv `bin` is deliberately **not** first — it contains `python`/`python3` and would shadow pyenv for automation scripts a delegation might run |

## State publication

`logs/jarvis_state.json`, written atomically (temp + rename):

```json
{"state": "IDLE", "since": 1788010897.0, "detail": "listening on 外部マイク", "pid": 98134}
```

A **file**, deliberately. The HUD reads it and has no channel back into the
runtime — see HUD_SECURITY.md.

## Dependency order

launchd offers no ordering between LaunchAgents, and none is asserted. The
runtime does not require `ai.hermes.gateway` or `local.ollama.serve` to be up
first: it waits for the microphone lease, and a delegation that cannot reach
Ollama or the gateway degrades to a spoken failure sentence rather than a crash.
