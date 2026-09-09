# Rollback

The watchdog is additive. The JARVIS runtime, `local.jarvis.runtime.plist`,
Hermes, the router, voice/TTS, and the HUD were not modified, so removing the
watchdog returns the system to its exact prior state. There is nothing to roll
back on the runtime side.

## Stop it (reversible, keeps the files)

```sh
launchctl bootout gui/$(id -u)/local.jarvis.watchdog
```

Verify it is gone:

```sh
launchctl print gui/$(id -u)/local.jarvis.watchdog   # expect "Could not find service"
pgrep -f jarvis_watchdog.py                          # expect no output
```

To bring it back:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.jarvis.watchdog.plist
```

## Remove it completely

```sh
launchctl bootout gui/$(id -u)/local.jarvis.watchdog
rm ~/Library/LaunchAgents/local.jarvis.watchdog.plist
rm -rf ~/AI-Lab/jarvis-watchdog
```

## Verify the runtime is untouched

```sh
shasum -a 256 ~/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py
# expect 65a65a2dd66792905fa9e1e81a9e9671d1055724cc5e791ea11f876ea6b39ad1
```

## Files this project created

| path | purpose |
|---|---|
| `~/AI-Lab/jarvis-watchdog/` | all watchdog source, tests, docs, logs |
| `~/Library/LaunchAgents/local.jarvis.watchdog.plist` | the new LaunchAgent |

Nothing else was added, and no existing file was edited.

A temporary `local.jarvis.wdtest` LaunchAgent existed only during the restart
action test and was removed by that test's teardown. Confirm with:

```sh
launchctl print gui/$(id -u)/local.jarvis.wdtest   # expect "Could not find service"
ls ~/Library/LaunchAgents/ | grep jarvis           # expect runtime + watchdog only
```
