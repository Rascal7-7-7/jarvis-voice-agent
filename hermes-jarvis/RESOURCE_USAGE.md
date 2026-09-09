# RESOURCE_USAGE

Measured 2026-08-29 on M1 Max / 32 GB, with the runtime installed and waiting
for the microphone lease. No `sudo`-requiring instrumentation was used.

## Idle daemons

| process | CPU | RSS | uptime at sample |
|---|---:|---:|---|
| `local.jarvis.runtime` | **0.0 %** | **34.9 MB** | 1 m 52 s |
| `ai.hermes.gateway` | 0.1 % | 112.7 MB | 14 h 28 m |
| `local.ollama.serve` | 0.0 % | 72.0 MB | 7 h 32 m |
| **total daemon RSS** | | **≈ 220 MB** | |

System at the time: memory free 52 %, swap used 258 MB of 1024 MB, load
4.35 / 3.87 / 3.77.

The runtime's 34.9 MB is the waiting state. With the wake detector actually
running it also holds openWakeWord + onnxruntime; during the earlier acoustic
tests that produced no sustained CPU spike — inference is one 80 ms frame at a
time through `CoreMLExecutionProvider`, 16 kHz mono.

## The model-residency trade

`gemma4:e2b` was resident at 7.23 GB during measurement (left warm by the router
benchmark). Ollama's default `keep_alive` is 5 minutes, so it unloads on its own
between conversations.

| | resident | unloaded |
|---|---|---|
| RAM held while idle | **7.23 GB** | 0 |
| first turn after idle | warm: **2.45 s** to first speakable token | **+16.1 s** cold model load |

On a 32 GB machine that also runs Claude Code and Codex, 7.23 GB is not free.
**Not changed** — the brief forbids model changes, and `keep_alive` is a
behaviour worth deciding deliberately rather than as a side effect. The lever is
`OLLAMA_KEEP_ALIVE` in `local.ollama.serve.plist`, currently `10m`.

## Battery

No `powermetrics` reading was taken: it requires elevated privileges, which are
prohibited here. What can be said from the numbers above:

- the three daemons together sit at **0.1 % CPU** when idle, which is not a
  meaningful drain
- the real battery question is the wake detector's continuous audio capture and
  per-frame inference, which is **not yet measurable** because the listener has
  not yet held the lease for a sustained period
- a resident 7.23 GB model does not itself consume power, but it raises memory
  pressure, and swap was already 258 MB in use

```
BATTERY_IMPACT = NOT MEASURED — needs a sustained listening period, and
                 powermetrics is off-limits without elevated privileges
```

## What is still unmeasured

| item | why |
|---|---|
| idle CPU/RAM **with the listener actually running** | the lease is held by an interactive `hermes chat` |
| wakeups / interrupt rate | same |
| per-turn STT / LLM / TTS cost in the background path | needs a real spoken turn |
| battery drain over hours | needs the above, plus a long observation window |
