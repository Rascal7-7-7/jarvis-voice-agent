# JARVIS External Watchdog — Phase 1

A recovery fail-safe. **Not** a fix for the underlying bug.

The JARVIS runtime intermittently deadlocks inside CoreAudio while starting the
microphone input stream. Three occurrences have been reproduced in production.
The stack is always the same:

```
recorder thread   Pa_StartStream -> AudioDeviceStart_mac_imp
                  -> HALB_IOThread::StartAndWaitForState
                  -> HALB_Guard::WaitFor -> __psynch_mutexwait
IOThread.client   HALC_ProxyIOContext::IOWorkLoop -> mach_msg2_trap
                  (holding the guard, waiting on coreaudiod)
```

`Pa_OpenStream` succeeds; `Pa_StartStream` never returns. A fresh process opens
the *same* device in ~85 ms while the wedged runtime stays stuck indefinitely,
and `coreaudiod` is healthy throughout — so the damage is confined to the
runtime's own HAL client state.

That is why recovery has to come from outside. In-process recovery is not
merely difficult, it is unavailable: `close()` needs the same guard the IO
thread is holding, and abandoning the blocked thread leaks the mutex and makes
the next open worse. The only remaining lever is restarting the process.

The trigger is still unknown. Isolated harnesses did not reproduce it —
recorder-only 100/100, wake-handoff 100/100, playback-handoff 50/50 all passed.
Device transition is not required (the device was unchanged across a hang), and
neither is the close/reopen path (the third hang happened on turn 1 with a
freshly created recorder). Because the trigger is unidentified, no preventive
change to the audio architecture has been made: there would be no way to show
it worked.

## What it does

Every 5 seconds it reads `jarvis_state.json` (read-only). If the runtime stays
in `LISTENING` for 45 seconds by the **watchdog's own monotonic clock**, and the
pid is alive, it runs one fixed command:

```
/bin/launchctl kickstart -k gui/<uid>/local.jarvis.runtime
```

Then it verifies recovery: a *different* pid must reach `IDLE` within 25 s. A
zero exit from `launchctl` is not treated as success.

`LISTENING` is the only state it acts on. It is the only one with a proven
failure mode.

## Why 45 seconds

From the dwell-time distribution of real turns in the runtime log:

| | measured |
|---|---|
| normal completed turns | 2.39 – 8.13 s |
| normal turn with a long silence | 15.76 s |
| capture failure that **recovers by itself** (30 s wait path) | 30.01 / 30.01 / 30.17 s |
| CoreAudio deadlock | > 700 s, ended only by restart |

15 s and 20 s would kill legitimate turns. 30 s would restart a runtime that was
about to recover on its own. 45 s clears the self-recovering path with margin
while still firing ~15× sooner than the deadlock was previously noticed.

## Why a monotonic clock, not `since`

`jarvis_state.json`'s `since` is wall-clock. NTP steps, sleep/resume, and manual
clock changes make it discontinuous, and a watchdog that restarts a healthy
runtime because the clock jumped is worse than no watchdog. The watchdog starts
its own `time.monotonic()` when it first observes `LISTENING` for a given pid.
`since` is carried into the log and never compared against. Tests drive the wall
clock both backwards and far into the future to prove it cannot fire anything.

## Security posture

- No network, no microphone, no CoreAudio, no Hermes config, no HUD control.
- `jarvis_state.json` is opened read-only and never written.
- Writes go only to `logs/` in this directory.
- Exactly one control action, with a **constant** argv: no shell, no `sh -c`, no
  interpolation, no value from the state file, no PATH lookup. A unit test
  asserts the argv is 4 fixed elements and that no shell-execution token appears
  anywhere in `src/`.
- Decisions use only the state enum, the pid, and locally measured elapsed time.
  `detail` is untrusted text from another process and is a log field only.
- No `sudo`.

## Circuit breaker

Three restarts within 10 minutes opens a 15-minute lockout on automatic
restarts. The watchdog keeps polling and logging throughout — it never stops
itself, because a watchdog that exits leaves the next deadlock unattended.

## Layout

```
bin/jarvis_watchdog.py     main loop (I/O only, no judgement)
src/watchdog_core.py       decision engine: pure, no clock, no files
src/state_reader.py        read + validate jarvis_state.json; liveness
src/restarter.py           the one fixed launchctl action + recovery check
src/wlog.py                JSON-lines log, 1 MB rotation, 1 generation
tests/test_watchdog.py     28 decision/validation tests
tests/test_restart_action.py  5 end-to-end tests against a DUMMY service
docs/ROLLBACK.md
```

## Install / rollback

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.jarvis.watchdog.plist
launchctl bootout   gui/$(id -u)/local.jarvis.watchdog
```

Rollback is self-contained: the JARVIS runtime, its plist, Hermes, and the HUD
were not modified, so removing the watchdog restores the previous system
exactly. See `docs/ROLLBACK.md`.

## Known limitations

- It restarts the runtime; it does not prevent the deadlock. A turn in progress
  when the hang occurs is lost, and the user must speak again.
- Detection costs 45–50 s. That is the price of not false-positiving on the
  measured 30.17 s self-recovering path.
- `LISTENING` only. A hang in `THINKING` or `SPEAKING` would not be caught.
  Measured ceilings for those are 38.97 s and 19.53 s, so 90 s and 60 s would be
  defensible thresholds — but there is no evidence they ever hang, and adding
  unevidenced restart triggers to a fail-safe is how fail-safes cause outages.
- `/usr/bin/python3` on this machine resolves through the Xcode developer dir.
  Only the standard library is used, so a fallback to CommandLineTools is fine,
  but if that shim breaks the watchdog will not start. `KeepAlive` plus the
  launchd status makes that visible rather than silent.
- If the restart itself fails, that is logged as `RECOVERY_FAILED`; the watchdog
  does not escalate to any stronger action.
