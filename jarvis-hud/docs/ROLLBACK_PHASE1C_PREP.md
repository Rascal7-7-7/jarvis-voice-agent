# Rollback — HUD Phase 1C-prep

Self-contained. Nothing outside `~/AI-Lab/jarvis-hud/` was touched: no install,
no login item, no LaunchAgent, and no change to the JARVIS runtime, the audio
stack, or the watchdog. So there is nothing to roll back on those sides, and
this document only has to undo staging.

## What Phase 1C-prep added

New files:

```
scripts/build_app.sh                          stages dist/JARVIS HUD.app
scripts/verify_bundle.sh                      45 checks on the staged bundle
Sources/JarvisHUD/Utilities/PanelPlacement.swift
Tests/JarvisHUDTests/PanelPlacementTests.swift
docs/ROLLBACK_PHASE1C_PREP.md                 this file
dist/JARVIS HUD.app                           staging artifact, not installed
logs/perf_bundle.txt                          idle measurement
```

Modified:

```
Sources/JarvisHUD/JarvisHUDApp.swift               panel origin now goes through PanelPlacement
Sources/JarvisHUD/Services/MockStateProvider.swift whole file wrapped in #if DEBUG
README.md                                          Phase 1C-prep status
```

## Undo

Stop the staged app and delete the artifact:

```sh
pkill -f "JARVIS HUD.app/Contents/MacOS/JarvisHUD"
rm -rf ~/AI-Lab/jarvis-hud/dist
```

That is the whole rollback if you only want the staging gone. The source
changes are independent improvements and are safe to keep — the panel clamp
fixes a real recovery bug, and the `#if DEBUG` guard removes the mock provider
from release builds.

To also revert the source changes:

```sh
cd ~/AI-Lab/jarvis-hud
git checkout -- Sources/JarvisHUD/JarvisHUDApp.swift \
                Sources/JarvisHUD/Services/MockStateProvider.swift
rm -f Sources/JarvisHUD/Utilities/PanelPlacement.swift \
      Tests/JarvisHUDTests/PanelPlacementTests.swift \
      scripts/build_app.sh scripts/verify_bundle.sh
swift test          # expect the original 29 to pass
```

(If this directory is not under version control, restore those two files from a
backup instead; the edits are small and described above.)

## Verify nothing leaked outside the project

```sh
ls ~/Applications/ /Applications/ | grep -i jarvis          # expect no output
ls ~/Library/LaunchAgents/ | grep -i jarvis                 # expect runtime + watchdog only
sfltool dumpbtm 2>/dev/null | grep -i "local.jarvis.hud"    # expect no output (no login item)
```

And that audio is untouched:

```sh
shasum -a 256 ~/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py \
              ~/AI-Lab/hermes-jarvis/bin/shared_audio.py \
              ~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27/tools/voice_mode.py \
              ~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27/tools/wake_word.py
```

Expected:

```
d135f96717005e574f6a0d3ad8af76eefd08eea7a22e39477f910e622bd205a9  bin/jarvis_runtime.py
6c140067731b86a0ee103f76eaa23ab8c580aa54ba64755089793461c0c55de7  bin/shared_audio.py
7cfc1e4d251a20fb3681d6054545da6237ec9bb91cd9413d43771de7aece1410  tools/voice_mode.py
0d39b668217e92f8f5189dd45b40b9f99ce950c8a898316b7d50b091b201b399  tools/wake_word.py
```
