# Minimal V2 — implementation review

**DESIGN ONLY. NOTHING IS IMPLEMENTED.** No runtime diff, no HUD diff, no schema
change. Audited 2026-08-31 against `bin/jarvis_runtime.py`
(`438ba16a7040e8beeafd519d1919078baf8fe6d469d0a5c6eb7f9b4b648439b7`), read-only.

Scope: `version`, `turn_id`, `route`, `latency_ms`. No `heard_text`, no
expansion of the existing 60-char reply in `detail`, no `activity`, no error
model.

---

## 0. Record correction

The privacy-hardening report stated both "3箇所、57行" and "差分は1行のみ". The
second was wrong as written: it described the SHA256 *listing* (one of four
filenames had a changed hash), not the patch. Measured:

```
PRIVACY_DIFF_STAT     3 hunks, +54 / -2
PRIVACY_DIFF_NUMSTAT  56 changed lines
PRIVACY_CHANGED_HUNKS 3   (@@ -77,7 / @@ -91,6 / @@ -141,8)
PRIVACY_CHANGED_LINES 56, of which 24 are code and 32 are comments
```

Corrected in `docs/PRIVACY_HARDENING.md`. **No production code was touched to
make the records agree** — the code was right; the sentence was not.

## 1. Authoritative sources

### turn_id

```
TURN_COUNTER_SOURCE      recorder_box["turn"]   (jarvis_runtime.py:674)
INCREMENT_POINT          line 674 — AFTER set_state("LISTENING") at line 673
RESET_BEHAVIOR           none within a process; strictly increasing
PROCESS_RESTART          recorder_box is local to main(), so the counter
                         restarts at 1 on every runtime restart
```

Use this counter. Do not add a second one — a turn identity that disagrees with
the one already in the log line (`recorder before TURN=3`) would make the two
impossible to correlate during exactly the kind of debugging this project keeps
needing.

**One ordering problem.** The increment is one line *after* the LISTENING
publish, so at that publish `turn_id` would still be the previous turn's value.
That is precisely the stale-context bug this field exists to prevent.

Fix: swap lines 673 and 674 so the counter increments first. Two lines, no
behaviour change — nothing between them reads either value.

Because the counter restarts at 1 per process, **`turn_id` is only meaningful
together with `pid`**. A HUD seeing 42 → 1 must read it as "new process", not
"went backwards". `pid` is already published, so no new field is needed, but the
HUD rule has to be written down.

### route

```
AUTHORITATIVE_ROUTE_SOURCE  decision["route"]  from jarvis_router.route()
                            (line 438), also assigned to tl.route at 439
VALUES                      LOCAL_FAST, LOCAL_TOOL, LOCAL, WEB, CODEX, CLAUDE,
                            plus CONFIRMATION_REQUIRED which route() can return
```

Publish the runtime's own decision. The HUD's `RouteTracker` infers the route by
watching state transitions; that inference must not be fed back into the
backend as if it were a source.

**Case: keep the values UPPERCASE, exactly as the runtime has them.** The
earlier schema sketch proposed lowercase. That was a mistake — these are the
same six tokens already published as `state`, and a second lowercase vocabulary
would be a second thing to keep in sync, plus a mapping layer in the HUD that
can drift. `"route": "CODEX"` lets the HUD reuse `JarvisPhase(rawValue:)`.

Note that `route()` can return `CONFIRMATION_REQUIRED`, which is a gate verdict
rather than a backend. It must NOT go in `route` — see the lifecycle below.

### latency

Every proposed stage maps to marks the runtime already takes. Line numbers are
current.

| stage | start mark | end mark | line | unit |
|---|---|---|---|---|
| wake → capture | `wake_detected` (660) | `capture_start` (679) | — | ms |
| capture | `capture_start` (679) | `capture_end` (687) | — | ms |
| stt | `stt_start` (704) | `stt_done` (706) | — | ms |
| router | `router_start` (426) | `router_done` (428) | — | ms |
| backend | `delegate_start` (469) | `delegate_done` (481) | — | ms |
| tts generate | `tts_request_start` (505) | `tts_audio_ready` (527) | — | ms |
| playback | `playback_start` | `playback_end` | 534–558 | ms |

All monotonic (`time.monotonic()`), all already computed by `Timeline._ms()`.

## 2. Availability at each publish point

This is the table that decides the design, because a field that is never
complete when the state is published is a field that is never shown.

Execution order is not source order: `_dispatch` is called at line 713, so the
ROUTING publish at 440 happens *between* THINKING (703) and SPEAKING (714).

| publish | line | intervals complete at that moment |
|---|---|---|
| `LISTENING` | 673 | none — `capture_start` is marked at 679, after |
| `THINKING` | 703 | wake→capture, capture |
| `ROUTING` | 440 | + stt, router |
| `CONFIRMATION_REQUIRED` | 446 | + stt, router |
| `<route> working` | after 446 | + stt, router |
| `SPEAKING` | 714 | + backend |
| `IDLE` (finally) | 722 | + tts generate, playback |

So five of the seven stages fill in at publish points that already exist. No new
`set_state` call is needed for them.

## 3. Field naming — two corrections to the earlier sketch

**`wake` → `wake_to_capture`.** "wake" reads as "how long detection took". It is
not: it is the gap between the wake firing and capture starting — 251 ms of
`pause_listening` today. And the ACK subsystem, when integrated, will put ~675 ms
of acknowledgement *inside that gap*. A field called `wake` would silently change
meaning; `wake_to_capture` stays true either way.

**`capture` → `capture_duration`.** In a latency object every other key is
overhead, and `capture` is not overhead — it is the user talking. Calling it
`capture` invites reading a 27.9 s recording as 27.9 s of lag. It is worth
publishing (the 30 s ceiling case is exactly what a diagnostic panel should
show), but under a name that says what it is.

## 4. LATENCY_FIELDS_RECOMMENDED

```json
"latency_ms": {
  "wake_to_capture": 251,
  "capture_duration": 3557,
  "stt": 731,
  "router": 2546,
  "backend": 3054
}
```

Five stages. Integers, milliseconds, `>= 0`, or `null` when not yet measured.

**`null` means "not measured yet". `0` means "measured, and it was zero".** They
are different and must not be conflated — `router: 0` is a real outcome on the
greeting fast path, where the gate settles the turn and the LLM never runs.

Absurd values clamped rather than published: negative is impossible from a
monotonic clock but a defensive `max(0, …)` costs nothing, and anything beyond a
sane ceiling (say 10 minutes) says the marks are wrong, not that the turn was.

### LATENCY_FIELDS_REJECTED_OR_DEFERRED

**`tts_generate` — DEFER.** Measured (505 → 527) and genuinely useful. But it
only completes inside `_speak`, which runs *after* the SPEAKING publish and
before the IDLE one. Including it means either a new publish between synthesis
and playback, or accepting that it appears only in the IDLE state — where the
HUD would be showing a number for a turn that has ended.

Neither is worth it for one value. §18 is right: do not add a state write to
make a number displayable. Revisit if a "last turn summary" is ever wanted, which
is a different feature.

**`playback` — REJECT for this schema.** Same availability problem, and less
justification: the user is *listening* to the playback, not waiting for it.
Putting it in a "why am I waiting" breakdown would repeat the exact mistake the
runtime's own comment records — conflating synthesis with playback once reported
8,282 ms for a 729 ms synthesis.

**`gate` — DEFER.** It exists, but `gate_done` is derived
(`mark_at(router_start + (total - llm))`) rather than measured, and it is
typically 0–1 ms. A derived near-zero number is noise in a diagnostic panel.

## 5. Lifecycle

### TURN_ID_SEMANTICS

```
IDLE / OFFLINE                       turn_id = null
LISTENING (after the reorder)        turn_id = N
THINKING / ROUTING / <route> /
CONFIRMATION_REQUIRED / SPEAKING     turn_id = N
ERROR                                turn_id = N if inside a turn, else null
back to IDLE                         turn_id = null
```

Not a security value. Not authentication, not authorization, not replay
protection, and **not the ACK subsystem's `request_id`** — that one correlates a
single IPC call to a playback helper and has nothing to do with turns. Sharing a
name between them would be a mistake; they should not even look alike.

### ROUTE_SEMANTICS

```
IDLE / OFFLINE / LISTENING / THINKING / ROUTING   route = null
after the route decision (line 438)               route = <ROUTE>
<route> working, SPEAKING                         route = <ROUTE>
CONFIRMATION_REQUIRED                             route = null
ERROR                                             route = whatever it was
back to IDLE                                      route = null
```

Three decisions worth stating explicitly:

**ROUTING publishes `route = null`,** even though the decision has been made one
line earlier. `detail` already carries `"CODEX via llm"` at that moment, and the
ROUTING state means "deciding". Setting `route` at the same instant the state
says "still deciding" is a small lie that a HUD would render as a flicker.
Set it on the next publish, `set_state(route, "working")`.

**CONFIRMATION_REQUIRED sets `route = null`.** `route()` can return
`CONFIRMATION_REQUIRED`, but that is a gate verdict, not a backend — nothing was
dispatched, nothing ran. Putting it in `route` would mean the field sometimes
names a destination and sometimes names a refusal.

**ERROR keeps the route.** If a turn failed after dispatching to Codex, "it
failed, in Codex" is more useful than "it failed". The `turn_id` still marks it
as belonging to that turn, so it cannot be mistaken for the next one.

### STALE_RESET_DESIGN

The failure to prevent:

```
turn 41  route=CODEX, latency={...}
IDLE
turn 42  LISTENING   ← must NOT still say CODEX or carry turn 41's numbers
```

One reset point, at the `finally` block that already publishes IDLE (line 722).
Not a reset scattered across call sites — that is how one path gets missed.

## 6. STATE_CONTEXT_DESIGN

`set_state(name, detail="")` has ~20 call sites. Adding `turn_id`, `route` and
`latency` as parameters would mean touching all of them and would make every
future field another signature change. Worse, it would put the reset obligation
on every caller.

Instead: a module-level turn context, guarded by the lock `set_state` already
takes, merged into the payload at publish time.

```python
_turn = {"id": None, "route": None, "timeline": None}

def begin_turn(turn_id, timeline):   # LISTENING
def set_turn_route(route):           # set_state(route, "working")
def end_turn():                      # the finally block
```

`set_state` then builds `latency_ms` from `_turn["timeline"]` — whichever
intervals are complete at that instant, `null` for the rest.

Three new call sites, one reset point, no signature change, and no caller can
forget to clear anything. `Timeline` is already per-turn and already lives in
`_handle_wake` as `tl`, so nothing new is constructed.

**Not a framework.** Three module-level functions and a dict, matching the
existing `_state` / `_state_lock` pattern directly above them.

## 7. STATE_PUBLISH_POINTS

Unchanged. The same 6–8 writes per turn, now carrying more per write.

```
LISTENING   turn_id=N  route=null  latency={all null}
THINKING    turn_id=N  route=null  latency={wake_to_capture, capture_duration}
ROUTING     turn_id=N  route=null  latency={+stt, router}
<route>     turn_id=N  route=R     latency={+stt, router}
SPEAKING    turn_id=N  route=R     latency={+backend}
IDLE        turn_id=null route=null latency=null
```

No per-mark republishing. `jarvis_state.json` stays a status file; the log
already has per-stage timing at full resolution for anyone who needs it.

## 8. VERSION SEMANTICS

```json
"version": 2
```

Integer constant, present on every publish. Not a string, not semver, not the
Hermes package version — this numbers the state-file format and nothing else.

Reader rules:

- **absent ⇒ v1.** That is what every file written before this change looks like.
- **greater than known ⇒ parse the known fields, ignore the rest, do not
  refuse.** A HUD that blanks on `version: 3` is a HUD that breaks itself on the
  next backend update.

## 9. BACKWARD_COMPATIBILITY

| | runtime | HUD | outcome |
|---|---|---|---|
| A | v1 | current | today |
| B | v1 | future v2 | no `version` ⇒ v1; new fields absent ⇒ `RouteTracker` fallback, no latency shown |
| C | **v2** | **current Phase 2A** | **works** — see below |
| D | v2 | future v2 | full |

**C is the one that matters**, because it is what happens if the runtime is
updated first — and it will be, since Phase 2B touches only the runtime.

`JarvisState.decode` reads exactly four keys:

```swift
dict["state"]   dict["since"]   dict["detail"]   dict["pid"]
```

Everything else in the dictionary is untouched. Adding keys is structurally
invisible to it. Measured size: v1 81 bytes → v2 239 bytes, against a 64 KB
parser cap — 274x of headroom, and ~590 bytes even with a full 120-character
Japanese `detail`.

**Gap found: there is no test asserting this.** The existing decoding tests
cover missing fields, malformed JSON, oversized files and unknown *state
values*, but nothing feeds the decoder a payload with unknown *keys*. The
tolerance is real by construction, but it is untested, and "we read it and it
looked fine" is not the standard this project has been holding. Adding that test
is a prerequisite of implementation, not an optional extra.

## 10. HUD_ROUTE_FALLBACK_DESIGN

```
if the payload has an explicit route  -> use it
else                                  -> RouteTracker, as today
```

Keep the tracker. Deleting it the moment the field exists would make the HUD
require a v2 runtime, which throws away the rolling compatibility that case C
buys. The tracker is ~40 lines and already tested.

This also means **the runtime can ship alone**. No coordinated release, which
matters here: the runtime is restarted by a watchdog, and the HUD is launched by
hand.

## 11. CRASH_BEHAVIOR

A SIGKILL mid-turn leaves the last published object on disk, including
`turn_id`, `route` and `latency_ms`.

All three are LOW sensitivity — an integer, one of six labels, and durations —
so unlike `heard_text` this is acceptable retention. The HUD already handles the
display side: `Liveness` uses `kill(pid, 0)`, so a dead pid renders OFFLINE and
the stale route is not presented as an active turn.

This is a real scenario, not a hypothetical: the watchdog has SIGKILLed this
runtime twice, both times mid-turn.

## 12. WRITER SECURITY

The privacy baseline must survive unchanged:

```
logs/                 0700
jarvis_state.json     0600, on every one of 100+ replacements
publication           write tmp (O_CREAT|O_TRUNC|O_NOFOLLOW, 0600) -> os.replace
```

Implementation must extend the payload dict **inside the existing writer**. Not
`open()`, not `Path.write_text()`, not a new helper that reimplements the write.
The mode comes from the temp file; anything that bypasses that silently returns
the file to 0644 on the next publish. `tests/test_state_permissions.py` already
proves that and will catch it.

## 13. DIFF SCOPE

```
PRODUCTION_FILES_TO_MODIFY   bin/jarvis_runtime.py   (only)
HUD_FILES_TO_MODIFY          none, for the runtime change to be useful
NEW_MODULES                  none
```

Estimated: 5 small edits.

1. `SCHEMA_VERSION = 2` next to the other constants.
2. `_turn` dict + `begin_turn` / `set_turn_route` / `end_turn`, beside `_state`.
3. `set_state` merges `version`, `turn_id`, `route`, `latency_ms`.
4. Swap lines 673/674 so the counter increments before the LISTENING publish;
   call `begin_turn`.
5. `set_turn_route` at `set_state(route, "working")`; `end_turn` in the `finally`.

A separate HUD change to consume the fields is its own phase, and can be done
whenever — including never, without the runtime being wrong.

## 14. TEST_PLAN

Extending `tests/test_state_permissions.py`, which already loads the runtime
with a redirected `LOG_DIR`.

**Schema** — `version == 2` on every publish; JSON valid; key set exact;
`latency_ms` values are int-or-null and never strings; mode still 0600.

**turn_id** — null at IDLE; N at LISTENING (**this is the test that fails
without the 673/674 reorder**); stable across THINKING → ROUTING → route →
SPEAKING; null again at IDLE; restarts at 1 in a fresh process.

**route** — null until dispatch; each of the six values published correctly;
retained through SPEAKING; null at CONFIRMATION_REQUIRED; retained at ERROR;
null at IDLE.

**Stale contamination** — run turn 41 to completion, begin turn 42, assert the
payload carries neither turn 41's route nor its latency. The single most
important test here.

**latency** — null before measurement; integer ms after; never negative; `0`
distinguished from `null` on the greeting fast path; stages appear at the
publish points in the table above and not before.

**Compatibility** — a v2 payload through a v1-shaped reader yields the same four
fields; a v1 payload (no `version`) is read as v1. **Plus the HUD-side test that
does not exist yet**: unknown keys in `jarvis_state.json` must be ignored by
`JarvisState.decode`.

**Permissions** — the existing 100-replacement test, unchanged, must still pass
with the larger payload.

**Size** — a worst-case payload (120-char Japanese detail, all latency stages
present) stays far below 64 KB.

## 15. ROLLBACK

```
ROLLBACK_BASELINE = 438ba16a7040e8beeafd519d1919078baf8fe6d469d0a5c6eb7f9b4b648439b7
```

That is the **post-privacy-hardening** runtime. Rolling Minimal V2 back means
returning to this hash — **never to `d135f967…`**, which is the pre-hardening
runtime and would silently restore 0644 state files on the next publish.

A fresh backup must be taken before implementation, and its README must say
which of the two baselines it is.
