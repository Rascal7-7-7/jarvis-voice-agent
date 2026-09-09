# JARVIS ACK playback helper — PROTOTYPE

Plays one fixed clip on request, fast. **Not integrated with anything**: the
JARVIS runtime, the shared input stream, the watchdog and the HUD are all
untouched, and nothing launches this automatically.

## Why

`afplay` costs about **882 ms per invocation** on this machine, measured, and
that cost is per-invocation rather than per-sample — 50 ms of silence and 300 ms
of silence both take ~882 ms more than their own duration. Three explanations
were ruled out: it is not system load (same figure at load 5.4 and at load 3.8),
not the audio format (PCM_16 and FLOAT within noise), and not the output device
idling (holding the device open with a background silent stream moved the median
by 8.5 ms). The remaining candidate was the work `afplay` repeats every time:
spawn, dyld, open, decode, build an audio queue.

A process that has already done all of that should pay none of it. It does not:

| | afplay | this helper |
|---|---|---|
| trigger → audio (proxy) | not separable | **16.6 ms** (p50) |
| trigger → playback end | 1420 ms (p50) | **677 ms** (p50) |
| clip duration | 575 ms | 575 ms |
| overhead | ~845 ms | **~102 ms** |

## Scope, and why it is this small

It plays ONE file, whose path is a compile-time constant, in response to one of
three literal commands on stdin. **There is no command that takes a parameter**,
so no input can select what gets played, and none of the input is ever passed to
anything that executes.

```
PLAY   play the ACK, or report PLAY_IGNORED if one is already running
PING   liveness check
QUIT   exit
```

Anything else prints `UNKNOWN_COMMAND` and is discarded.

Verified on the release binary: no `NSTask`, `Process(`, `URLSession`,
`WKWebView`, `posix_spawn`, `system(`, `popen`, `/bin/sh` or `NSAppleScript`
strings; no `AVAudioRecorder` or `AVCaptureDevice` (it cannot open a
microphone); links only AVFAudio, CoreFoundation and Foundation. The only path
in the binary is the ACK asset itself.

No network. No microphone. No Hermes, Codex or Claude. It never touches the
shared input stream.

## Build and run

```sh
swift build -c release
python3 scripts/bench.py --plays 100 --gap 0.15 --idle-minutes 10
```

Interactively:

```sh
.build/arm64-apple-macosx/release/JarvisAckHelper
# type PLAY, PING, QUIT
```

Note that stdin closing exits the helper, so `printf 'PLAY\n' | helper` will
usually exit before the sound starts — the driver has to hold the pipe open.

## Measured — 100 consecutive plays

```
plays 100   success 100   failures 0   elapsed 83.1 s   asset 575 ms
```

| | p10 | p50 | p90 | min | max |
|---|---|---|---|---|---|
| trigger → play() call | 0.03 | **0.06** | 0.12 | 0.02 | 0.36 |
| trigger → play() returns | 12.95 | **16.56** | 19.45 | 5.65 | 51.92 |
| trigger → playback end | 674.05 | **677.34** | 680.27 | 666.54 | 712.44 |
| driver write → STARTED | 13.05 | 16.82 | 19.63 | 5.87 | 52.18 |
| driver write → ENDED | 674.38 | 677.56 | 680.84 | 666.80 | 712.72 |

The driver figures include the stdin pipe round trip and differ from the
helper's internal ones by ~0.3 ms, so the IPC is not a meaningful cost at this
scale.

`play()` returning is the closest observable proxy for first audio that
AVAudioPlayer offers. The remaining latency is the output device's own IO
buffer, which the API does not expose — so 16.6 ms is an upper bound on the
software path, not a measured first-sample timestamp.

Idle, 10 minutes: **RSS 26.5 → 26.3 MB**, CPU **0.01 s (0.0017 %)**, and a PING
answered in 0.1 ms afterwards. No polling loop — the process sits on a run loop.

RSS grows 13.6 → 26.5 MB across the first plays and then stops; the 10-minute
idle window after that shows no further growth.

## Overlap policy

A PLAY arriving while one is running is **ignored**, not queued. Verified:

```
STARTED seq=101 ...
PLAY_IGNORED reason=already_playing
ENDED seq=101 ok=true ...
```

Queuing would stack acknowledgements behind a user who says the wake word twice,
and 「はい はい」 is worse than one dropped ACK.

## TCC

No prompt appeared, playback worked, and `log show --predicate 'subsystem ==
"com.apple.TCC"'` recorded nothing for this binary. That is the outcome hoped
for but not assumed: `voice_mode._sounddevice_output_allowed()` returns false on
Darwin because PortAudio's OUTPUT initialisation triggers a
kTCCServiceMediaLibrary prompt, and the open question was whether AVFoundation
does the same. It does not, at least when run from a terminal by this user.
**Untested from a LaunchAgent context**, which is where the runtime lives.

## Output device

Whatever macOS routing is current at play time. No device is named, selected or
changed; there is no numeric index anywhere.

## If it fails

Nothing here can block a turn, because nothing is integrated. For a future
integration the intended posture is fail-open **on the audio UX only**: helper
unavailable → skip the ACK → proceed to command capture. That is a fallback for
a courtesy sound, not for a security control, and it must not be confused with
one.

## Not done

No runtime integration, no IPC decision for production, no `ACKNOWLEDGING`
state, no HUD change, no LaunchAgent, no login item. The production IPC
mechanism is deliberately still open — stdin was chosen here because it is the
simplest thing that measures cleanly, not because it is the right long-term
answer.
