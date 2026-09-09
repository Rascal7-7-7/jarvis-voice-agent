# NEXT STEPS

Ordered. Each is independently reversible. Nothing here has been executed.

---

## 1. Grant Microphone — the one hard blocker (human only)

Everything else in the voice path is already proven; only live capture is not.

```
System Settings → Privacy & Security → Microphone
  enable the terminal app you launch Hermes from (iTerm / Ghostty / Terminal)
```

Or open the pane directly:

```sh
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
```

Then **quit and reopen that terminal** so the grant takes effect.

**Do NOT grant Full Disk Access.** `hermes doctor` suggests it to silence
per-folder prompts; granting it would undo the filesystem controls in
SECURITY_MODEL.md.

Note: a launchd-started process cannot display a TCC dialog, so the first voice
run must be started by hand from a GUI terminal.

## 2. Push-to-talk, once, by hand

```sh
hermes chat          # then the record key: ctrl+b
```

Say, in order: 「こんにちは」 / 「今日の日付を教えて」 / 「Pythonについて説明して」.

Watch for: transcript accuracy on real speech (the synthetic test got 2/3 exact),
and whether `voice.auto_tts` speaks the reply in `ja-JP-NanamiNeural`.

Keep terminal / file-write / browser / Codex / Claude out of the loop for this
run — the gate is armed, but the point of this step is the audio path only.

## 3. Decide the voice-latency question

This is the real design decision left, not a config tweak.

Measured: ≈ 4.7–6.3 s to first audio, of which 3.0–4.4 s is `gemma4:e2b`
thinking with nothing speakable coming out. Three ways forward:

| Option | Cost | Effect |
|---|---|---|
| Accept it | none | "Jarvis" answers in ~5 s |
| Find a non-thinking small model | a pull + a benchmark | most 2026 small models are thinking-tuned; may not exist |
| Hosted fast model for the voice tier only | a credential | sub-second is realistic; breaks local-only |

Recommended: run step 2 first. Perceived latency with real barge-in may be more
tolerable than the raw number suggests.

## 4. Enable wake word

Only after step 2 passes.

```yaml
wake_word:
  enabled: true      # currently false
```

Then measure false positives over a normal day — music, YouTube, meetings.
`sensitivity: 0.6` and `confirmation_frames: 3` are the tuning knobs. Synthetic
negatives scored 0.000, including Japanese speech, but that is not the same test.

## 5. Wire Codex (read-only first)

The upstream mechanism already exists: `/codex-runtime codex_app_server` hands a
turn to a Codex subprocess (`hermes_cli/codex_runtime_switch.py`).

Do **not** change `~/.codex/config.toml`. `sandbox_mode = "read-only"` and
`approval_policy = "on-request"` are what make this safe, and they carry through.

First prompt should be analysis only ("このPythonコードの問題点を調べて"). File
writes come later, and only behind the gate.

## 6. Wire Claude (read-only first)

**The absolute path is mandatory:**

```
~/.local/bin/claude          ✅
claude                       ❌ from a launchd PATH this resolves to the
                                ~/.claude root, whose settings.json registers
                                different hooks
```

As of 2026-08-29 `~/.claude/CLAUDE.md` and `~/.claude/rules` are symlinked to
`~/.claude-work/`, so instructions and rules now load on both paths — but
`settings.json` still differs, so the permission deny-list is not guaranteed.

## 7. Resolve the Codex trust scope (human decision)

`~/.codex/config.toml` lines 64-65:

```toml
[projects."/Users/Rascal"]
trust_level = "trusted"
```

Backup: `…/meta/codex-config.toml`, sha256 `5ac879a7…`.
Trade-off and reasoning: SECURITY_MODEL.md §3. Best done alongside step 5.

## 8. Address the SQLite WAL-reset exposure

`state.db` (84 MB) and `kanban.db` are in WAL mode under SQLite 3.51.0, which
has a known corruption bug. Fixed in 3.51.3+ / 3.50.7 / 3.44.6.

Since Hermes now runs from `~/.hermes-venv` built on pyenv 3.12.3, the fix is to
rebuild that pyenv version against a patched SQLite, then recreate the venv.
Pre-existing, unrelated to this migration — but it is the highest-value
integrity item left.

## 9. Close the network exposure

`AnyDesk *:7070`, Apple Remote Desktop `*:3283`, `mariadb *:3306`,
`node *:3000` all listen on every interface. Worth closing before the Mac
becomes always-listening.

## 10. Grow the assistant

In dependency order, all reachable without re-architecting:

```
Files / Terminal      done
Web search            done
Browser automation    `browser` toolset needs a system dep; browser-use works today
Coding                step 5 / 6
Git                   works (push is deny-gated on purpose)
Calendar / Mail       hermes mcp add
Home Assistant        pip install '.[homeassistant]'  (aiohttp==3.14.3)
                      the gateway platform slot and the smart-home skill already exist,
                      and the plugin already reports "configured"
```
