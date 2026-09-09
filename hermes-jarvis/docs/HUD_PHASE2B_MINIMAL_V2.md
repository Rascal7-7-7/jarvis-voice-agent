# State schema v2 — Minimal V2

Implemented 2026-08-31. Four fields added to `jarvis_state.json`:
`version`, `turn_id`, `route`, `latency_ms`.

Nothing else changed. `detail` still carries `reply[:60]` at SPEAKING, exactly
as before, and no transcript, no expanded response, no activity and no error
object were added.

## Schema

```json
{
  "version": 2,
  "state": "SPEAKING",
  "since": 1788125256.014105,
  "detail": "解析が完了しました。重要な点を三つ報告します。",
  "pid": 97559,
  "turn_id": 42,
  "route": "CODEX",
  "latency_ms": {
    "wake_to_capture": 251,
    "capture_duration": 3557,
    "stt": 731,
    "router": 2546,
    "backend": 3054
  }
}
```

Measured sizes: 270 bytes at IDLE, 607 bytes worst case (120-character Japanese
`detail`, every latency stage populated, a large turn id). The HUD's parser cap
is 64 KB — about 100x of headroom.

## version

Integer, `2`, present on every publish. It numbers **this file's format** and
nothing else: not Hermes, not JARVIS, not a package version.

A file with no `version` is v1. That is what everything written before this
change looks like, and a reader must treat it that way rather than rejecting it.

## turn_id

`null` outside a turn; a positive integer during one.

```
IDLE / OFFLINE                                          null
LISTENING THINKING ROUTING <route> SPEAKING             N
CONFIRMATION_REQUIRED                                   N
ERROR                                                   N (if inside a turn)
back to IDLE                                            null
```

Source is the existing counter, `recorder_box["turn"]` — not a second counter.
A new one would disagree with the `recorder before TURN=3` line already in the
log, and correlating the two is exactly what this is for.

**One ordering fix was required.** The increment used to run one line *after*
the LISTENING publish, so that publish carried the previous turn's id — the
stale context this field exists to prevent. The increment now precedes
`begin_turn()`, which precedes the publish. Three tests pin it, including one
that reads the source, because the bug is purely one of ordering and driving the
helpers directly cannot reveal it.

**`turn_id` is process-local.** The counter restarts at 1 when the runtime
restarts, so it must be read together with `pid`: 42 → 1 means a new process,
not a counter going backwards. It is not globally unique and does not try to be.

**It is not a security value.** Not authentication, not authorization, not
replay protection. It is unrelated to the ACK subsystem's `request_id`, which
correlates a single IPC call to the playback helper — the two should not even
be compared.

## route

`null`, or one of exactly six labels:

```
LOCAL_FAST  LOCAL_TOOL  LOCAL  WEB  CODEX  CLAUDE
```

Same tokens the runtime already publishes as `state`. A lowercase second
vocabulary was considered and rejected: it would be another thing to keep in
sync plus a mapping layer that can drift.

`set_turn_route()` accepts only those six. Anything else leaves the field
`null` — the route never becomes free text.

```
LISTENING THINKING ROUTING                              null
<route> working, SPEAKING                               the route
CONFIRMATION_REQUIRED                                   null
ERROR                                                   the route, if dispatched
IDLE / OFFLINE                                          null
```

Three decisions worth stating:

**ROUTING publishes `null`,** even though the decision is made one line
earlier. `detail` already says `"CODEX via llm"` there, and the state means
"deciding". Publishing a route at the instant the state says it is still
deciding is a small lie that renders as a flicker.

**CONFIRMATION_REQUIRED publishes `null`.** `jarvis_router.route()` can return
that value, but it is a gate verdict, not a backend — nothing was dispatched.
Putting it in `route` would make the field sometimes a destination and sometimes
a refusal.

**ERROR keeps the route.** "It failed, in Codex" is more useful than "it
failed", and `turn_id` still ties it to the turn that failed.

## latency_ms

Five stages. Integer milliseconds, `>= 0`, or `null`.

| field | from | to |
|---|---|---|
| `wake_to_capture` | wake accepted | command capture begins |
| `capture_duration` | capture begins | capture ends |
| `stt` | transcription starts | transcript available |
| `router` | routing starts | route decided |
| `backend` | dispatch to the backend | result returned |

All were already measured by `Timeline`; nothing new is instrumented.

**`null` means "not measured yet". `0` means "measured, and it was zero".**
They are different, and conflating them would be the most misleading thing this
object could do. `router: 0` is a real outcome on the greeting fast path, where
the gate settles the turn and the LLM never runs.

Two names are deliberate:

**`wake_to_capture`, not `wake`.** "wake" reads as "how long detection took". It
is the gap between the wake firing and capture starting — 251 ms of
`pause_listening` today. When the ACK subsystem lands, its ~675 ms of playback
will sit inside that same interval. A field called `wake` would silently change
meaning; this name stays true.

**`capture_duration`, not `capture`.** In a latency object every other key is
overhead. This one is the user talking. Naming it `capture` invites reading a
27.9-second recording as 27.9 seconds of lag.

Negatives are clamped to 0 (impossible from a monotonic clock, but free to
guard). Anything over an hour is published as `null`, because at that point the
marks are wrong rather than the turn.

### Not included

| field | why |
|---|---|
| `tts_generate` | measured, but only completes inside `_speak`, after the SPEAKING publish. Including it needs either a new publish or "show it during IDLE". Not worth a state write for one number. |
| `playback` | same availability problem, and the user is *listening* to it, not waiting for it. Putting it in a "why am I waiting" breakdown repeats the mistake that once reported 8,282 ms for a 729 ms synthesis. |
| `gate` | derived rather than measured, typically 0–1 ms. Noise. |

## When each field is filled

Publish count is unchanged: the same 6–8 writes per turn, each carrying whatever
is complete at that moment. No stage republishes, and `jarvis_state.json` is
not a telemetry channel — the log already has per-stage timing at full
resolution.

| publish | turn_id | route | latency stages complete |
|---|---|---|---|
| LISTENING | N | null | none |
| THINKING | N | null | wake_to_capture, capture_duration |
| ROUTING | N | null | + stt, router |
| `<route>` working | N | route | + stt, router |
| SPEAKING | N | route | + backend |
| IDLE | null | null | none (cleared) |

## Turn context

```python
_turn = {"id": None, "route": None, "timeline": None}

begin_turn(turn_id, timeline)   # before the LISTENING publish
set_turn_route(route)           # at set_state(route, "working")
end_turn()                      # before the IDLE publish
```

Guarded by `_state_lock` — the same lock `set_state` already takes. A second
lock would introduce an ordering to get wrong for no benefit. None of the three
helpers may be called from inside `set_state`'s locked region, and none is.

`set_state(name, detail="")` keeps its signature. Threading metadata through 20
call sites would have made every future field another signature change, and
would have put the reset obligation on every caller.

`set_state` reads id, route and timings **together under the lock**, so a
publish can never pair one turn's route with another turn's numbers.
Serialisation happens outside the lock.

### One reset point

`end_turn()` is called once, in the `finally` block, **before** IDLE is
published. The order matters: publishing IDLE first would leave the finished
turn's route and timings in the resting state for a viewer to show as if a turn
were still running. A source-order test pins this too.

## Backward compatibility

| | runtime | HUD | outcome |
|---|---|---|---|
| A | v1 | current | unchanged |
| B | v1 | future v2 | no `version` ⇒ v1; new fields absent ⇒ fallback |
| C | **v2** | **Phase 2A** | **works** — verified |
| D | v2 | future v2 | full |

C is the case that actually happens, because this phase ships the runtime alone.
`JarvisState.decode` reads exactly `state`, `since`, `detail`, `pid` and ignores
everything else.

That was proven before the runtime was touched, in
`Tests/JarvisHUDTests/SchemaCompatibilityTests.swift` (6 tests): a v2 payload
plus an unknown future field decodes identically to the four-key minimum; a
version of 3, 99, `"two"`, `null` or `-1` does not make the decoder give up; and
hostile shapes in the new fields (`turn_id` as a string, `route` as an object,
`latency_ms` as an array) cannot damage the four fields in use. One test reads
`JarvisState.swift` and asserts the decoder still touches only those four keys,
so a future refactor cannot quietly break this.

Live: the watchdog read the new pid from a v2 payload, and the Phase 2A HUD
opened the state directory and stayed up.

## Privacy

Unchanged, and re-verified after the schema change:

```
logs/                 0700
jarvis_state.json     0600, including after 100 v2 replacements
publication           write tmp (O_CREAT|O_TRUNC|O_NOFOLLOW, 0600) -> os.replace
```

The new fields are all LOW sensitivity — an integer, one of six labels, and
durations. Unlike a transcript, their surviving a crash on disk is acceptable.
The HUD already renders a dead pid as OFFLINE, so a stale route is not shown as
an active turn.

**Not added, deliberately:** `heard_text`, an expanded `response_preview`, the
full response, `activity`, a free-text error, tool arguments. A test asserts
none of those key names appear.

## Tests

```
tests/test_state_schema_v2.py            31
tests/test_state_permissions.py          10
Tests/JarvisHUDTests/                    65  (6 new compatibility tests)
```

The two that matter most:

- `test_no_cross_turn_contamination` — turn 41 completes with a route and
  timings; IDLE clears everything; turn 42's LISTENING carries neither. Not one
  field leaks.
- `test_begin_turn_precedes_the_listening_publish` — the ordering bug, asserted
  against the source.

One existing assertion was updated rather than kept: `test_e` in the permission
suite asserted the four-key v1 schema. The schema change is deliberate, so the
expectation moved with it; the rest of that test — the four v1 fields round-trip
with non-ASCII intact, no temp file left behind — is unchanged and still passes.

## Rollback

```
BASELINE  438ba16a7040e8beeafd519d1919078baf8fe6d469d0a5c6eb7f9b4b648439b7
BACKUP    backups/minimal-v2-20260831-062409/
```

```sh
B=~/AI-Lab/hermes-jarvis/backups/minimal-v2-20260831-062409
launchctl bootout gui/$(id -u)/local.jarvis.runtime
cp "$B/jarvis_runtime.py" ~/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.jarvis.runtime.plist
```

That baseline is the **post-privacy-hardening** runtime. Rolling back returns
the schema to v1 and the turn increment to its original position, and keeps the
0600/0700 writer.

**Do NOT roll back to `d135f967…`** — that is the pre-hardening runtime, and it
publishes state files at the umask default, so `jarvis_state.json` would return
to 0644 on the very next write.
