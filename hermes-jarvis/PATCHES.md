# PATCHES — local deltas against tag v2026.8.27

The checkout at `~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27` is pinned to
tag `v2026.8.27` (sha `5fc308a7`). One file is modified. Everything else is
pristine.

```
 tools/voice_mode.py | 60 +++++++++++++++++++++++++++++++++++++++++++++++++----
 1 file changed, 60 insertions(+), 4 deletions(-)
```

---

## P1 — AudioRecorder follows the OS default input device

### Why

`AudioRecorder` opened `sd.InputStream(...)` **with no `device=` argument** and
kept that one stream alive for the whole process (deliberate — it dodges a
CoreAudio hang on close/reopen). It also never logged which device it took.

Meanwhile `start()` re-reads `_default_input_samplerate(sd)` on **every** call,
while `_ensure_stream()` returned early whenever a stream already existed.

That combination is a latent bug, not just a missing feature:

| | old stream | `self._sample_rate` |
|---|---|---|
| user switches 48 kHz mic → 44.1 kHz device | still 48 kHz | becomes 44100 |

`_write_wav(audio_data, sample_rate=self._sample_rate)` then stamps **44.1 kHz
onto audio captured at 48 kHz** — roughly 9 % slow — and `min_samples =
int(self._sample_rate * 0.3)` is wrong at the same time. This fires in exactly
the scenario the build requires: wired headset / Bluetooth / external mic /
built-in mic switching.

`tools/wake_word.py` never had this problem: it passes `device=`, re-resolves
`_describe_input_device` and `_capture_sample_rate` on every `pause()`/`resume()`,
and logs the device it opened. **This patch brings the recorder to parity with
the wake listener** rather than inventing new behaviour.

### What changed

1. **New `_default_input_identity(sd)`** — returns
   `(name, hostapi, default_samplerate, max_input_channels)` for the current
   default input. **The PortAudio index is deliberately excluded** (it is
   renumbered whenever a device is attached or removed) and **nothing is ever
   written to disk** — this is runtime comparison state, not configuration.
2. **New `_identity_label(identity)`** — log formatting only.
3. **`AudioRecorder.__init__`** — added `self._stream_identity = None`.
4. **`AudioRecorder._ensure_stream`** — compares the live identity against the
   one the stream was opened with:
   - same → returns early, byte-for-byte the old behaviour
   - different → logs the change, calls the **existing**
     `_close_stream_with_timeout()` (3 s timeout, already written to survive a
     CoreAudio hang), then reopens on the new device
5. **`start()` log line** — now includes the resolved device, matching what
   `wake_word.py` already emits. This is what made the previous silent-mic
   incident hard to diagnose.

### Verification

```
no-change: same stream object reused          True    <- no regression
change   : stream reopened                    True
change   : live stream rate == new device     True    (44100.0 vs 44100)
change   : self._sample_rate == new device    True
change   : live rate == _sample_rate          True    <- the latent bug, fixed
```

Log during the switch:

```
Default input changed ('Clayのマイク'@48000Hz -> 'NoMachine Audio Adapter'@44100Hz)
  — reopening capture stream
Voice recording started (device='NoMachine Audio Adapter'@44100Hz, rate=44100, channels=1)
```

Upstream tests:

```
tests/test_voice_max_recording_seconds.py
tests/test_audio_playback_guard.py
7 passed in 0.43s
```

### Upstream compatibility

- Additive. No public API, config key, or return type changed.
- When the device does not change, the code path is identical to upstream.
- The persistent-stream design (the CoreAudio workaround) is preserved; a close
  happens only on an actual device change, and reuses upstream's own guarded
  close.
- No new config key, so the surface that could conflict with a future upstream
  bump is minimal.
- Suitable to file upstream as "voice recorder should re-resolve the default
  input like the wake listener does".

### Rollback

```sh
cd ~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27
git checkout -- tools/voice_mode.py
```

The venv is an **editable** install, so restoring the file restores the old
behaviour immediately — no reinstall, no service restart required beyond the
next `hermes` invocation.
