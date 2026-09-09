# ROLLBACK

Phase 1A is self-contained. Deleting the directory removes all of it:

```
~/AI-Lab/jarvis-hud/
```

Plus one preferences domain, if the panel was ever dragged:

```sh
defaults delete JarvisHUD
```

## What was NOT touched

| | state |
|---|---|
| `~/AI-Lab/hermes-jarvis/bin/` | untouched — `jarvis_runtime.py` sha256 `65a65a2dd66792905fa9e1e81a9e9671` before and after |
| `~/AI-Lab/hermes-jarvis/logs/` | not written; `jarvis_state.json` was never even opened by the HUD |
| `~/.hermes/`, Hermes, Ollama, Codex, Claude | untouched |
| LaunchAgents | none added, none changed |
| voice / TTS / router / security gate | untouched |
| JARVIS runtime process | left running throughout |

The HUD writes exactly one thing outside its own directory: the panel position,
in its own `UserDefaults` domain. Nothing else on disk is modified.

No launch agent was installed — the HUD runs only when started by hand, and
stops when quit.
