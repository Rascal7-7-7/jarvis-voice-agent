# Rollback — Phase B shared input stream

Backup: `~/AI-Lab/hermes-jarvis/backups/phaseB-20260830-223147/`

That directory holds the pre-change copies of all three modified files plus
`SHA256_BEFORE.txt` and `existing_voice_mode.patch` (the pre-existing
dynamic-mic patch, so restoring does not silently drop it).

## Files changed

| file | before | after |
|---|---|---|
| `bin/jarvis_runtime.py` | `65a65a2dd66792905fa9e1e81a9e9671d1055724cc5e791ea11f876ea6b39ad1` | see `SHA256_AFTER.txt` |
| `src/hermes-agent-v2026.8.27/tools/voice_mode.py` | `0d39b668217e92f8f5189dd45b40b9f99ce950c8a898316b7d50b091b201b399` | see `SHA256_AFTER.txt` |

New files (no rollback needed beyond deletion):

- `bin/shared_audio.py`
- `tests/test_shared_audio.py`
- `tests/test_shared_audio_fidelity.py`
- `tests/diag_shared_wake.py`
- `docs/ROLLBACK_SHARED_STREAM.md`

`wake_word.py` was NOT modified — the shared design uses upstream's existing
`external_audio` mode as-is.

## Rollback

```sh
B=~/AI-Lab/hermes-jarvis/backups/phaseB-20260830-223147
launchctl bootout gui/$(id -u)/local.jarvis.runtime

cp "$B/jarvis_runtime.py" ~/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py
cp "$B/voice_mode.py"     ~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27/tools/voice_mode.py
rm -f ~/AI-Lab/hermes-jarvis/bin/shared_audio.py

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.jarvis.runtime.plist
```

Verify:

```sh
shasum -a 256 ~/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py
# expect 65a65a2dd66792905fa9e1e81a9e9671d1055724cc5e791ea11f876ea6b39ad1
diff "$B/SHA256_BEFORE.txt" <(cd ~/AI-Lab/hermes-jarvis && shasum -a 256 \
  bin/jarvis_runtime.py \
  src/hermes-agent-v2026.8.27/tools/wake_word.py \
  src/hermes-agent-v2026.8.27/tools/voice_mode.py)
# expect no output

cat ~/AI-Lab/hermes-jarvis/logs/jarvis_state.json    # expect IDLE with a fresh pid
```

The pre-existing dynamic-mic patch is inside the restored `voice_mode.py`, so
`git -C src/hermes-agent-v2026.8.27 diff --stat tools/voice_mode.py` should
report the original 99-line patch again, not a clean tree.

Rolling back restores the previous behaviour **including the CoreAudio
deadlock** — it is a return to a known-bad state, not a fix. The external
watchdog is unaffected either way and keeps recovering from that deadlock.

## Not touched by this change, and not part of any rollback

- `local.jarvis.runtime.plist` and `local.jarvis.watchdog.plist`
- `~/AI-Lab/jarvis-watchdog/` (threshold 45 s, poll 5 s, LISTENING only)
- `~/AI-Lab/jarvis-hud/`
- Hermes config (`config.yaml`), `approvals.deny`, `jarvis_gate.py`
- `tools/wake_word.py`
