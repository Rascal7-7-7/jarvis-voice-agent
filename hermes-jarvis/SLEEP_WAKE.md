# SLEEP_WAKE

```
SLEEP_WAKE = DESIGNED, NOT TESTED
```

**Not tested, and I will not claim otherwise.** Forcing the Mac to sleep would
have interrupted a live interactive session and every other running process. The
recovery path below is implemented and its individual mechanisms are verified;
the sleep/wake cycle itself is not.

## The failure mode being defended against

After a sleep/wake cycle CoreAudio may invalidate the device while the
`InputStream` handle survives. The stream stays "live" and keeps delivering
frames — all of them zero. This is the same signature already measured on this
machine in a different context: a dead device yields correct frame counts with
`peak RMS = 0`, not an error.

So a naive listener does not crash after wake. It goes quietly deaf, which is
worse.

## Two independent recoveries

**1. Upstream's own silence detector.** `tools/wake_word.py` counts consecutive
near-zero frames and sets `audio_silent = True` after `_SILENCE_ALERT_SECONDS`
(10 s), logging `"mic delivers only silence"`. That flag is public on the
detector object.

**2. The runtime watchdog.** `jarvis_runtime.py` polls every 5 s:

```python
if detector.audio_silent and not inflight.locked():
    detector.pause(); detector.resume()
```

`resume()` calls `start()`, whose thread body re-runs `_describe_input_device`
and `_capture_sample_rate` — so the restart **re-resolves the default device and
its sample rate**, which is exactly what is needed if the device set changed
across sleep. Rate-limited to one restart per 60 s so a genuinely silent room
cannot cause a restart storm.

Verified independently in an earlier phase: a `pause()` / `resume()` cycle moved
the listener from `Clayのマイク @48000` to `NoMachine Microphone Adapter @44100`,
re-resolving both device and rate.

## Also relevant after wake

| component | behaviour |
|---|---|
| `AudioRecorder` | reopens its stream whenever the default input identity changes (the device-follow patch). A post-sleep device change is the same code path as unplugging a headset. |
| Ollama | `local.ollama.serve` has `KeepAlive`; a delegation that cannot reach it degrades to a spoken failure sentence |
| Hermes gateway | `ai.hermes.gateway` has `KeepAlive`; the runtime does not hold a connection to it |
| the wake lease | held by this process across sleep; nothing else can take it |

## How to test it properly

1. Confirm `jarvis_state.json` reads `IDLE … listening on <device>`
2. Sleep the Mac, wait a minute, wake it
3. Watch `logs/jarvis_runtime.log` for either continued silence (good — the
   stream survived) or `listener reports silence — restarting it` followed by
   `listener restarted on device=…`
4. Say "Hey Jarvis" and confirm a turn completes

Step 3 is the one that matters: it distinguishes "survived" from "recovered",
and both are acceptable outcomes. Never getting either is the failure.
