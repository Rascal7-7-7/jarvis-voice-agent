# TCC — can a background process actually hear you?

## The question

macOS does **not** return an error to a process without Microphone permission.
It returns digital silence. That is byte-identical to a dead device, so
"Terminal works" is not evidence that "launchd works" — the responsible process
and code identity differ between the two.

## The probe

`bin/tcc_mic_probe.py` captures 4 s from the default input and writes one JSON
line containing `distinct_values` — the number of distinct int16 sample values.
`distinct_values <= 1` means every sample is identical: digital silence, which
is exactly what an unpermitted process receives.

Run from a temporary LaunchAgent (`local.jarvis.tccprobe`, since removed), with
the same probe run from the terminal as a control.

## Result

| context | pid | ppid | device | peak | rms | distinct | verdict |
|---|---|---|---|---|---|---|---|
| terminal shell | 93679 | 93676 | 外部マイク | 2143 | 301.5 | 3040 | **REAL_AUDIO** |
| **launchd agent** | 94054 | **1** | 外部マイク | 2675 | 359.5 | 3781 | **REAL_AUDIO** |
| launchd agent | — | 1 | 外部マイク | 3090 | 457.8 | 4137 | **REAL_AUDIO** |
| launchd agent | — | 1 | 外部マイク | 2498 | 479.5 | 3965 | **REAL_AUDIO** |

**3 / 3 launchd runs captured real audio.** `ppid=1` and
`XPC_SERVICE_NAME=local.jarvis.tccprobe` confirm these were genuinely
launchd-spawned rather than inheriting anything from the terminal.

```
MIC_TCC = PASS
```

## What this means for the architecture

The Microphone grant follows the **binary** (`~/.hermes-venv/bin/python`), not
the parent process. Therefore:

- **No `.app` helper bundle is required.** No bundle identifier, no
  `NSMicrophoneUsageDescription`, no code-signing strategy, no helper-to-Hermes
  IPC layer. That entire branch of the design is unnecessary on this machine.
- Nothing in the prohibited list was attempted or is needed: no direct TCC
  database edit, no permission-reset utility, no privilege escalation, no Full
  Disk Access, no SIP change.

## Caveats

- This is a property of the **current grant state of this machine**. If macOS is
  upgraded, the venv is recreated at a different path, or the interpreter binary
  is replaced, the grant may not carry over. `bin/tcc_mic_probe.py` is kept so
  the check is a one-command re-run.
- The probe agent was removed after use: `local.jarvis.tccprobe` is unloaded and
  its plist moved out of `~/Library/LaunchAgents/`. Only
  `ai.hermes.gateway`, `local.ollama.serve` and `local.jarvis.runtime` remain.
