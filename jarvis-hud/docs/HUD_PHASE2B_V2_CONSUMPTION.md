# V2 schema consumption

The HUD now reads the four fields the Minimal V2 runtime publishes —
`version`, `turn_id`, `route`, `latency_ms` — and still works unchanged against
a v1 runtime.

No runtime change in this phase. Nothing was added to production display: no
transcript, no expanded reply. `detail` is still the only text, still capped at
120 characters for display.

## Version handling

| in the file | read as | behaviour |
|---|---|---|
| no `version` key | v1 | normal. Everything before Minimal V2 looks like this |
| `2` | v2 | full |
| `3`, `99` | future(n) | the fields it recognises are used, the rest ignored |
| `"two"`, `null`, `-1`, an object | v1 | not an integer, so no version |

A version the HUD does not know is **never** a reason to stop rendering.
Refusing to draw because the backend moved ahead would make the viewer the
fragile half of a pair that is deliberately deployed separately — the runtime is
restarted by a watchdog, the HUD is launched by hand, and neither waits for the
other.

Shown only in diagnostics, as `Schema v2`.

## Route priority

```
1. the published route, if it is one of the six dispatch labels
2. RouteTracker's inferred route
3. nothing
```

`RouteTracker` was **not** deleted. It is the fallback that lets this build work
against a v1 runtime, and it is what makes a runtime-only release safe.

`RouteResolution` carries where the value came from (`published` / `inferred` /
`none`), surfaced in the menu bar and as a VoiceOver value. That distinction is
worth keeping visible: one is what happened, the other is what was deduced.

Only these six are accepted:

```
LOCAL_FAST  LOCAL_TOOL  LOCAL  WEB  CODEX  CLAUDE
```

Everything else becomes `nil` — lowercase, empty, an object, an array, a SQL
fragment, and notably `CONFIRMATION_REQUIRED`, which is a valid state but not a
route: it is a gate verdict and nothing was dispatched for it. The badge cannot
be turned into a channel for arbitrary text.

### Disagreement between the two

If a published route contradicts the inferred one, the published value wins and
a counter is incremented for diagnostics. Nothing alarming is rendered. That can
only happen if the writer has a bug, and a viewer's job when the backend
misbehaves is to keep drawing something sane rather than to shout about it.

## Turn identity

```
identity = (pid, turn_id)
```

Never `turn_id` alone. The counter restarts at 1 when the runtime restarts, so
42 → 1 means a new process, not a counter going backwards. `TurnIdentity`
compares both, which is why "same turn number, different process" registers as a
different turn.

When the identity changes, the inferred route is reset before it can be shown
against a turn it did not belong to. The published route needs no such
protection — the runtime clears it at IDLE — but the fallback does.

`turn_id` accepts positive integers only. `0`, `-1`, `"42"`, `true`, `null` and
`3.5` all become `nil`.

Displayed as `Turn 42` in diagnostics. Not in the compact panel: it is for
debugging and for spotting stale context, not something to put in front of
someone all day.

## Latency

| schema key | UI label |
|---|---|
| `wake_to_capture` | Wake→Capture |
| `capture_duration` | Capture |
| `stt` | STT |
| `router` | Router |
| `backend` | Backend |

Schema names and UI labels are separate on purpose. `capture_duration` is
labelled "Capture" and is deliberately **excluded from the "so far" total**: it
is the user talking, not overhead, and a 30-second recording must not read as 30
seconds of lag.

### nil is not zero

`nil` means "not measured yet" and renders as an em dash. `0` means "measured,
and it was zero" and renders as `0 ms`. `Router 0 ms` is a real reading on the
greeting fast path, where the gate settles the turn and the LLM never runs.

**This is where a bug was found.** The first implementation guarded with
`raw is Bool`, which does not work on `JSONSerialization` output: numbers arrive
as `NSNumber`, and an `NSNumber` holding 0 or 1 bridges to `Bool` successfully.
So `is Bool` was true for the integers 0 and 1 — silently rejecting `router: 0`
and `turn_id: 1`, i.e. every first turn and the exact fast-path case a test had
been written for. `CFGetTypeID(...) == CFBooleanGetTypeID()` is the reliable
discriminator, and the tests that caught it are kept.

### Partial data is normal

Stages fill in as the turn runs, so a half-empty object is the expected state
rather than a fault:

| state | filled |
|---|---|
| LISTENING | nothing |
| THINKING | Wake→Capture, Capture |
| ROUTING / route working | + STT, Router |
| SPEAKING | + Backend |
| IDLE | cleared |

### Validation

Rejected as `nil`: negative, string, bool, float, array, object, and anything
above an hour. The hour ceiling matches the runtime's own — the point is to
agree with it, not to invent a second threshold.

A malformed `latency_ms` (a number, a string, an array) yields `nil` for the
whole object and damages nothing else in the payload.

## Compatibility matrix

| | runtime | HUD | result |
|---|---|---|---|
| A | v1 | this build | v1 fields, tracker fallback, no latency shown |
| B | v2 | Phase 2A build | works — the old decoder ignores the new keys |
| C | v2 | this build | full |
| D | v3 | this build | recognised fields used, rest ignored |

All four are tested. B was proven **before** the runtime was changed, which is
why the runtime could ship alone.

## Security

Unchanged. The HUD is a viewer.

`route`, `turn_id` and `latency_ms` feed **display only**. Nothing in this phase
uses them for a decision, and there is still no shell, no network, no write to
the runtime, and no approval action. CONFIRMATION_REQUIRED remains text with no
Allow or Deny.

Note for whoever builds Phase 3: the moment a confirmation UI can answer, every
field feeding that decision needs reviewing on its own terms. "It is only
display data" is what makes the current validation sufficient, and that
justification does not carry over.

## Mock vs production

Both paths exist in DEBUG and are labelled:

- **Production** — `state.latency` from the file, rendered by
  `ProductionLatencyView` as a labelled list.
- **Mock** (`FutureHUDContext`, `LatencyBreakdown`) — DEBUG-only types under a
  `NOT IN BACKEND — MOCK ONLY` header, rendered by `LatencyStripView`.

Separate types, separate views, separate sources. The release check
(`scripts/verify_bundle.sh`, 56 assertions) confirms the mock types and their
sample strings are absent from the shipped binary, while the production field
names are present — which is the correct split.

`Mock Sequence → V2 — Codex turn` replays v2 payloads through the real decoder,
so the production path is what gets exercised.

## Measured

| | CPU (idle) | RSS |
|---|---|---|
| compact, staged release | 0.039 % over 180 s | 83.0 → 80.2 MB |
| compact, debug | 0.000 % over 120 s | 84.3 → 84.0 MB |
| expanded, debug | 0.017 % over 120 s | 90.1 → 89.9 MB |

Against the Phase 2A baseline (compact 0.028 %, expanded 0.008 %, ~5 MB
difference) this is unchanged within measurement noise. Decoding four more
fields on a state transition costs nothing measurable, and no new timer was
added.

## Tests

```
Tests/JarvisHUDTests/   88   (23 new: V2ConsumptionTests + updated compatibility)
```

Two existing assertions were updated rather than kept, both because this phase
deliberately changed their premise:

- `decoderSurfaceIsFourKeys` → `decoderSurfaceIsTheV2Schema`. It existed to
  force a schema change to be noticed instead of passing silently, and it did
  exactly that.
- `unknownKeysAreInert` → `v1FieldsAreUnaffectedByV2Fields`. It asserted
  `plain == rich`, which held while the decoder ignored the v2 keys. Consuming
  them is what makes the two differ, so the comparison narrowed to the four
  fields that must not be affected.
