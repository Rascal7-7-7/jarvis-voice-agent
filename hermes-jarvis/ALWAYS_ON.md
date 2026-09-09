# ALWAYS_ON

```
LOGIN_AUTOSTART   = INSTALLED (local.jarvis.runtime)
TERMINAL_REQUIRED = NO  (for the runtime itself)
BACKGROUND_WAKE   = INSTALLED, NOT YET OBSERVED HOLDING THE LEASE
```

## What runs at login

| LaunchAgent | what | listener |
|---|---|---|
| `local.ollama.serve` | Ollama | `127.0.0.1:11434` |
| `ai.hermes.gateway` | Hermes gateway (webhook, cron, kanban) | `127.0.0.1:8644` |
| **`local.jarvis.runtime`** | **wake listener + STT + gate + router + delegation + TTS** | **none** |

All three are user LaunchAgents. No root, no `sudo`, no Full Disk Access, no
system-level daemon.

## Verified

| | |
|---|---|
| launchd can capture the microphone | **3/3 REAL_AUDIO** — see TCC.md |
| runs under launchd | pid 98134, stable across samples |
| does not crash-loop when the lease is taken | same pid across three 8 s samples while `hermes chat` held it |
| single instance | second start refused by `flock` |
| clean shutdown | exits on SIGTERM, releases the lease, publishes `OFFLINE` |
| state publication | `logs/jarvis_state.json` updates on every transition |
| router reachable from the runtime's own imports | the five C7 intents route correctly |
| idle cost | 0.0 % CPU, 34.9 MB RSS |

## Not yet verified — and why

**The runtime has not yet held the wake lease.** An interactive `hermes chat`
(pid 43976, started 16:34) owns `~/.hermes/runtime/wake-word.lock`, and the lease
is machine-wide. The runtime is waiting for it, correctly and quietly.

Consequently these remain open:

| item | blocked on |
|---|---|
| background wake actually firing | the lease |
| dynamic mic switching in the background | the lease |
| sleep/wake recovery | the lease, plus a real sleep cycle |
| idle CPU/RAM *with the listener running* | the lease |
| battery over hours | the above |
| login E2E (no Terminal, say "Hey Jarvis") | the lease + a re-login |

## To finish it

1. **Quit the running `hermes chat`** (pid 43976). Its wake listener will not
   restart: `wake_word.surface` is now `gui`, so `cli.py:15280` returns before
   claiming. Ctrl+B push-to-talk still works there as the manual fallback.
2. Within ~60 s the runtime takes the lease. Confirm:
   ```sh
   cat ~/AI-Lab/hermes-jarvis/logs/jarvis_state.json
   # expect: {"state": "IDLE", "detail": "listening on 外部マイク", ...}
   ```
3. Say **"Hey Jarvis"**, then a command. Watch:
   ```sh
   tail -f ~/AI-Lab/hermes-jarvis/logs/jarvis_runtime.log
   ```
4. Log out and back in, open no Terminal, and say "Hey Jarvis" — the final
   acceptance condition.

## Rollback

```sh
launchctl unload ~/Library/LaunchAgents/local.jarvis.runtime.plist
mv ~/Library/LaunchAgents/local.jarvis.runtime.plist ~/local.jarvis.runtime.plist.disabled
cp ~/.hermes/config.yaml.pre-alwayson-20260829-224053 ~/.hermes/config.yaml   # restores surface: auto
```

Restoring the config returns `wake_word.surface` to `auto`, at which point
`hermes chat` claims the wake microphone again exactly as before this phase.
Nothing else in the voice configuration was touched.
