# Phase D rollback

Phase D installed two apps and registered two login items. Nothing else changed.
**Rolling this back must not touch the runtime, the audio path or the watchdog** —
their sources are unchanged by Phase D and are not part of this rollback.

## What Phase D changed

| what | where | before |
|---|---|---|
| HUD app | `~/Applications/JARVIS HUD.app` | did not exist |
| Hotkey app | `~/Applications/JARVIS Hotkey.app` | did not exist |
| HUD login item | `SMAppService.mainApp`, `application.local.jarvis.hud.*` | not registered |
| Hotkey login item | `SMAppService.mainApp`, `application.local.jarvis.hotkey.*` | not registered |
| HUD source | `Sources/JarvisHUD/JarvisHUDApp.swift` | +58 lines, login-item flags only |
| Hotkey source | `Sources/JarvisHotkey/main.swift` | + single-instance lock, + login-item flags |
| Hotkey bundle build | `scripts/build_app.sh` | did not exist |

Pre-install state is recorded in
`~/AI-Lab/jarvis-wake-benchmark/rollback/phase-d-20260901-173052/pre-install-state.txt`.
`~/Applications` held no JARVIS entries before this, so there was nothing to back
up; the directory listing and the login-item list at that moment are in that file.

`/Applications` was never touched. No `sudo`. No entitlements, no Developer ID,
no TCC grant.

## Rolling back

**1. Unregister the login items.** Each app registers itself, so each unregisters
itself — do this *before* deleting the bundles, because `SMAppService.mainApp`
needs the bundle it is being asked about to exist.

```
"$HOME/Applications/JARVIS HUD.app/Contents/MacOS/JarvisHUD"      --unregister-login-item
"$HOME/Applications/JARVIS Hotkey.app/Contents/MacOS/JarvisHotkey" --unregister-login-item
```

Expect `unregister: ok` and `status_after: notRegistered`. Confirm with:

```
osascript -e 'tell application "System Events" to get the name of every login item'
```

JARVIS HUD and JARVIS Hotkey should be gone from that list.

**2. Quit both apps.**

```
pkill -f "MacOS/JarvisHUD"
pkill -f "MacOS/JarvisHotkey"
```

**3. Remove the installed bundles.**

```
rm -rf "$HOME/Applications/JARVIS HUD.app"
rm -rf "$HOME/Applications/JARVIS Hotkey.app"
```

**4. Remove the hotkey lock, if the app is not running.**

```
rm -f ~/.hermes/runtime/jarvis-hotkey.lock
```

Harmless to leave: it is an empty 0600 file whose only role is to be flocked.

## What rollback does NOT do

- **The runtime keeps running.** `local.jarvis.runtime` and
  `local.jarvis.watchdog` are untouched by Phase D and by this rollback.
- **Manual activation still works from the CLI**:
  `~/AI-Lab/jarvis-activate/bin/jarvis-activate`. The activation socket lives in
  the runtime, not in the hotkey app, so removing the app removes the *key*, not
  the *entrance*.
- **The wake word is unaffected.** Still `hey_jarvis`, threshold 0.35,
  confirmation_frames 1.
- **The HUD can still be run from staging**:
  `open ~/AI-Lab/jarvis-hud/dist/JARVIS\ HUD.app`.

## Reverting the two source changes as well

Only needed if the flags themselves are unwanted. Both are additive and inert on
a normal launch.

```
cd ~/AI-Lab/jarvis-hud     && git diff -- Sources/JarvisHUD/JarvisHUDApp.swift
cd ~/AI-Lab/jarvis-hotkey  && git diff -- Sources/JarvisHotkey/main.swift
```

The HUD change is confined to `JarvisHUDApp.init()`, which returns immediately
unless one of three flags is present. The hotkey change adds the flock and the
same flags. Removing them means rebuilding with `scripts/build_app.sh` and
re-installing.

## Verifying after rollback

```
ls -d ~/Applications/JARVIS*                      # nothing
osascript -e 'tell application "System Events" to get the name of every login item'
launchctl print gui/$(id -u) | grep -i jarvis     # only runtime and watchdog
launchctl print gui/$(id -u)/local.jarvis.runtime  | grep -E 'state|pid'
launchctl print gui/$(id -u)/local.jarvis.watchdog | grep -E 'state|pid'
~/AI-Lab/jarvis-activate/bin/jarvis-activate --status
```

The last two must still be running, and the socket must still be there. If the
runtime stopped as part of a rollback, the rollback went further than Phase D
did.
