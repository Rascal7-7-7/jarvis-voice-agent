# HUD Phase 2B — state schema and privacy design

**DESIGN ONLY. NOTHING IS IMPLEMENTED.** No runtime diff, no HUD diff, no schema
change. `jarvis_state.json` is exactly what it was; this document is the audit
and the recommendation, to be reviewed before any code moves.

Audited 2026-08-31 against `bin/jarvis_runtime.py`
(`d135f96717005e574f6a0d3ad8af76eefd08eea7a22e39477f910e622bd205a9`), read-only.

---

## The finding that changes the framing

Phase 1 established a privacy boundary: **no `heard_text` in the HUD**. That
boundary is real and should hold. But two things turned up in the audit that
were not part of the picture when it was drawn.

**1. The assistant's reply is ALREADY published to the state file.**

```python
# jarvis_runtime.py:661
reply = _dispatch(transcript, tl)
set_state("SPEAKING", reply[:60])
```

Observed live during this audit:

```json
{"state": "SPEAKING",
 "detail": "申し訳ありませんが、私はその内容について応答することができません。",
 "since": 1788123041.578835, "pid": 49704}
```

So `response_preview` is not a new exposure category. A 60-character version has
been on disk since before the HUD existed. The Phase 2B question is not "should
replies be published" — they are — it is "should the existing 60 characters
become 1000".

**2. The transcript is already persisted, in the log.**

```python
# jarvis_runtime.py:655
logger.info("transcript=%r", transcript)
```

29 transcripts are in `logs/jarvis_runtime.log` right now, including full
sentences of whatever was said or overheard in the room. Not in the state file
— the Phase 1 boundary held there — but "the user's speech is not written to
disk" was never true.

**3. Both files are world-readable.**

```
-rw-r--r--  Rascal staff  logs/jarvis_state.json
-rw-r--r--  Rascal staff  logs/jarvis_runtime.log
drwxr-xr-x  Rascal        logs/
drwxr-xr-x  Rascal        ~/AI-Lab/hermes-jarvis/
```

Mode 644 in a 755 chain: **any local user account can read both**. On a
single-user machine that is a small circle. It is still a larger circle than
"the HUD", and it is the circle every field discussed below actually lands in.

This is a pre-existing condition, not something Phase 2B creates. But it is the
thing to settle first, because every privacy argument below rests on who can
read the file, and today the answer is "anyone with a local account".

**Recommendation, independent of any schema change: tighten these to 0600 and
the directory to 0700 before adding a single sensitive field.** It costs
nothing, it is reversible, and it makes the rest of this document meaningful.

---

## CURRENT_SCHEMA

```json
{"state": "SPEAKING", "since": 1788123041.578835,
 "detail": "…", "pid": 49704}
```

Published by `set_state(name, detail)`: write `.tmp`, `os.replace()`. Four
fields, no version marker. Observed size 176 bytes.

Two properties worth naming because the design must preserve them:

- **One-way.** A file, not a socket. The HUD cannot reach back. Everything below
  keeps that; nothing here adds a channel.
- **No fsync.** `set_state` does not fsync, and it swallows `OSError`. A crash
  can therefore leave the previous contents, or a stale `.tmp`. That matters for
  retention (below) and is why a fresh-looking file is not proof of a live
  runtime.

## CURRENT_DATA_AVAILABLE

Everything the runtime already has in memory during a turn, and where it goes
today.

| data | source | lifetime | current location | sensitivity | safe to publish? |
|---|---|---|---|---|---|
| transcript | `vm.transcribe_recording()` → local var | one turn | **log file** (`transcript=%r`) | **HIGH** — the user's own speech, plus anything audible in the room | not as-is |
| route | `decision["route"]` | one turn | state (as the STATE), log | LOW — one of six fixed labels | yes |
| route reason | `decision["decided_by"]` | one turn | state `detail` at ROUTING, log | LOW — `llm` / `fast_path_greeting` etc. | yes |
| gate categories | `decision["categories"]` | one turn | state `detail` at CONFIRMATION_REQUIRED, log | LOW — enum-like (`DESTRUCTIVE`) | yes |
| reply | `_dispatch()` return | one turn | **state `detail`, first 60 chars**, log | **HIGH** — model output about the user's data | already published |
| timeline | `Timeline` (9 intervals, monotonic) | one turn | log only, at turn end | LOW — integers | yes |
| turn counter | `recorder_box["turn"]` | process | never published | LOW — a small integer | yes |
| error text | `f"…: {type(e).__name__}"` | one turn | state `detail`, log | LOW — **type name only, no repr, no path** | yes |
| device name | `_identity_label()` | process | state `detail` at IDLE | LOW — `'外部マイク'@48000Hz` | yes |
| tool activity | not modelled | — | — | — | does not exist |

Two things to note. The error strings are **already** sanitised at the source —
`type(e).__name__`, never `repr(e)` — which is the right call and should be
preserved rather than rebuilt. And "tool activity" has no source at all: the
dispatcher runs a subprocess and returns text; nothing enumerates files read or
queries issued. Publishing it would mean **inventing new instrumentation**, not
exposing something that exists.

## FIELD_CLASSIFICATION

| field | class | why |
|---|---|---|
| `version` | LOW | a small integer about the format |
| `turn_id` | LOW | a counter; correlates HUD views, identifies nothing |
| `route` | LOW | one of six labels the HUD already infers from the state |
| `latency_ms` | LOW | durations. Reveals that a turn was slow, not what it was about |
| `pid` | LOW | already published |
| `activity` (enum) | **MEDIUM** | "searching_web" says a web search happened. Not what for — but a fixed vocabulary is a shape of behaviour |
| `error.code` | LOW | if it stays an enum |
| `error.message` | MEDIUM | free text drifts toward paths and payloads over time |
| `heard_text` | **HIGH** | the user's speech, and the room's |
| `response_preview` | **HIGH** | model output about the user's data |
| full response | **HIGH** | as above, unbounded |
| tool arguments | **HIGH** | paths, queries, and the contents of neither |

`activity` sits at MEDIUM rather than LOW deliberately. A HUD that says
"Searching web…" is pleasant; a state file that records, in order, that this
machine searched the web at 03:14 and read files at 03:15 is a small behavioural
log. With an enum the leak is bounded. With free text it is not bounded at all.

---

## MINIMAL_V2 — the recommendation

No `heard_text`. No `response_preview` beyond what exists. Nothing that is not
already in the runtime's hands.

```json
{
  "version": 2,
  "state": "SPEAKING",
  "since": 1788123041.578835,
  "pid": 49704,
  "turn_id": 42,
  "route": "codex",
  "detail": "…",
  "latency_ms": {
    "wake": 251, "capture": 3557, "stt": 731,
    "router": 2546, "backend": 3054, "tts": 538
  }
}
```

Every field is either present today or a direct restatement of something the
runtime already computes. `detail` keeps its current meaning and its 120-char
cap — including the `reply[:60]` it carries at SPEAKING, which stays as it is.

Estimated size: ~330 bytes against today's 176. Against a 64 KB parser cap and a
few writes per turn, that is not a cost worth discussing.

## RICH_V2 — for comparison, not recommended as a default

```json
{
  "version": 2, "state": "SPEAKING", "since": …, "pid": …,
  "turn_id": 42, "route": "codex", "detail": "…", "latency_ms": {…},
  "activity": "reading_files",
  "error": null,
  "heard_text": "…",            // OPT-IN, default off, cleared at IDLE
  "response_preview": "…"       // OPT-IN, default off, cleared at IDLE
}
```

The two HIGH fields are **default off and opt-in**, and must be cleared on the
transition to IDLE rather than left to be overwritten by the next turn. Which
brings up the reason they should not be there at all.

## RETENTION_RISK — the argument against the HIGH fields

An atomically replaced file always holds its last value. For `state` and
`latency_ms` that is harmless. For `heard_text` it is not:

```
SPEAKING  { heard_text: "…" }
IDLE      { heard_text: null }        <- cleared, fine
```

but:

```
SPEAKING  { heard_text: "…" }
<crash, power loss, SIGKILL, watchdog restart>
                                      <- the last utterance stays on disk
                                         indefinitely, at 644
```

This is not hypothetical here. The runtime has been SIGKILLed by the watchdog
twice in this project's history, both times mid-turn, both times from LISTENING.
A crash during SPEAKING is the same class of event, and `set_state` does not
fsync — so the file that survives is whatever the OS last flushed.

There is no way to write "delete this on abnormal exit" into a file that is
replaced by another process's crash. The clearing step only runs when the
process is healthy enough not to need it.

**That is the argument against `heard_text` in the state file, and it does not
depend on a threat model or on trusting other software.** It is a property of
the mechanism.

---

## Per-field recommendations

### HEARD_TEXT_RECOMMENDATION = **REJECT for the state file** (option A, with C available)

Options as framed:

| | verdict |
|---|---|
| A. not in the production schema | **recommended** |
| B. opt-in only | rejected — an opt-in that survives a crash is still on disk |
| C. separate transient mechanism | acceptable if the HUD ever truly needs it |
| D. redacted/summary only | rejected as a safety argument (see below) |

The user decided in Phase 1 not to show heard text. Nothing in this audit gives
a reason to reverse that, and the retention property gives a new reason not to.

Note the separate fact this audit turned up: the transcript is **already** in
`jarvis_runtime.log`. If the concern behind the Phase 1 decision was "the user's
speech should not be sitting on disk", then the log is the thing to look at, not
the schema — and that is a question worth putting to the user explicitly rather
than fixing quietly, because log retention is also what makes debugging possible.

### RESPONSE_PREVIEW_RECOMMENDATION = **keep the existing 60 chars; DEFER any expansion**

A 60-char preview is already published and is what the HUD renders today. It is
enough to recognise a reply; it is not enough to reconstruct one.

Expanding to 1000 characters changes the character of the file: from "a status
line that happens to quote the first sentence" to "a transcript of what the
assistant said, retained on crash". The retention argument applies with the same
force, and the value is smaller — the reply is being *spoken aloud* as it is
written.

If a longer preview is wanted later, the transient mechanism (below) is the
right vehicle, not the state file.

### ROUTE_RECOMMENDATION = **ADD NOW**

```json
"route": null | "local_fast" | "local_tool" | "local" | "web" | "codex" | "claude"
```

Privacy cost is negligible: the same six labels are already published as states.
The value is real — `RouteTracker` currently reconstructs the route by
remembering state transitions, which works but is inference. An explicit field
makes it a fact, survives a HUD restart mid-turn (the tracker does not), and
lets the HUD drop that code.

Lowercase, and `null` outside a turn.

### TURN_ID_RECOMMENDATION = **ADD NOW**

```json
"turn_id": 42
```

The counter exists (`recorder_box["turn"]`). Publishing it lets the HUD tell
"still the same turn" from "a new turn that happens to be in the same state",
which is what stale route and stale preview both need.

Constraints: monotonically increasing within a process, resets at restart (so it
must be read together with `pid`, or the HUD will see 42 → 1 as going
backwards). **Not a security token, not an identifier, not for authentication.**
Distinct in every way from the ACK subsystem's `request_id`, which correlates
one IPC call — sharing a name between them would be a mistake.

### LATENCY_RECOMMENDATION = **ADD NOW, with one caveat**

```json
"latency_ms": { "wake": 251, "capture": 3557, "stt": 731,
                "router": 2546, "backend": 3054, "tts": 538 }
```

Integers or null. Never negative, never a string, absurd values clamped. Partial
data is normal and expected.

The caveat is about **when the values exist**. `Timeline` accumulates marks
through the turn and its intervals are only complete at the end — which is why
it is logged in the `finally` block. So a state published at THINKING can carry
`wake`, `capture` and `stt` but not `backend` or `tts`. The HUD must render
missing stages as absent, not as zero. A zero would read as "instant", which is
the opposite of the truth.

`backend` is already measured: `delegate_start` → `delegate_done`
(marked at jarvis_runtime.py:416 and :428, computed at :218). It is not in the
`INTERVALS` tuple, so it does not appear in the `timeline …ms` log line, but the
value exists and needs no new instrumentation.

So all six proposed stages map onto marks the runtime already takes:

| field | marks |
|---|---|
| `wake` | `wake_detected` → `capture_start` |
| `capture` | `capture_start` → `capture_end` |
| `stt` | `stt_start` → `stt_done` |
| `router` | `router_start` → `router_done` |
| `backend` | `delegate_start` → `delegate_done` |
| `tts` | `tts_request_start` → `tts_audio_ready` |

`tts` is deliberately synthesis only, not playback. Conflating the two once
reported 8,282 ms for a 729 ms synthesis — the runtime's own comment records
that lesson, and the schema should not undo it. Playback duration is a separate
number and does not belong in a "why am I waiting" breakdown, because the user
is listening to it rather than waiting for it.

### ACTIVITY_RECOMMENDATION = **DEFER**

An enum is the right shape if this is ever done:

```json
"activity": null | "transcribing" | "routing" | "reading_files"
          | "searching_web" | "waiting_backend" | "speaking"
```

But two things argue for waiting. First, **the data does not exist** — the
dispatcher runs a subprocess and gets text back; nothing reports what it did.
Adding this means new instrumentation inside the delegation path, which is a
larger change than a schema field.

Second, most of the value is already in `detail` and in the state itself. "Which
route, and how long has it been" answers "why am I waiting" nearly as well.

Free-text activity: **REJECT**. It starts as "Reading files…" and ends as
"Reading /Users/Rascal/Documents/…". A vocabulary that cannot express a path
cannot leak one.

### ERROR_MODEL_RECOMMENDATION = **DEFER; keep the current discipline**

```json
"error": { "code": "MIC_SILENT", "message": "…" }   // if ever added
```

The runtime already does the important part: error details are
`type(e).__name__`, never `repr(e)`, never a traceback, never a path. That is
better discipline than most structured error models achieve, and it is already
shipping.

A `code` enum would let the HUD show something specific per failure. A
`message` free-text field would, over time, accumulate exactly the paths and
payloads the current code carefully avoids. If this is done, **code only**.

### TRANSIENT_CONTEXT_RECOMMENDATION = **design C, do not build it yet**

If HIGH-sensitivity content is ever genuinely needed by the HUD:

| | privacy | complexity | one-way | crash isolation | cleanup |
|---|---|---|---|---|---|
| A. second transient file | poor — same retention problem, second file to forget | low | yes | none | manual |
| B. memory-only IPC | good — nothing on disk | **high** — a socket, and the HUD is deliberately socket-free | **breaks it**: a socket is bidirectional by nature | perfect | automatic |
| C. publish only while needed | good if the window is short | medium | yes | partial | on transition |
| D. not supported | perfect | none | yes | perfect | n/a |

B is the tempting one and the dangerous one. The single property that makes this
HUD safe to run beside a voice assistant is that it *cannot* talk back. Adding a
socket to carry a transcript would trade that away for a display feature.

**D today. C if a concrete need appears.** Not B.

---

## SCHEMA VERSION and BACKWARD_COMPATIBILITY

```json
"version": 2
```

Absent means v1. Four combinations, all of which must work:

| runtime | HUD | behaviour |
|---|---|---|
| v1 | v1 | today |
| v1 | v2 | no `version` ⇒ v1. `route`/`turn_id`/`latency_ms` absent ⇒ HUD falls back to `RouteTracker` and shows no latency. **Already true of the Phase 2A HUD**, which treats all of these as optional. |
| v2 | v1 | unknown keys ignored — `JarvisState.decode` reads only the keys it knows and drops the rest. Verified in the existing tolerant-decode tests. |
| v2 | v2 | full |

Two rules for the HUD, both already satisfied by Phase 2A's decoder:

1. An unknown `version` (3, 99, `"two"`) must not crash. Use the fields it
   recognises; ignore the rest. Never refuse to render.
2. Every new field is optional. A missing field is not an error.

This means **either side can be updated first**, and there is no coordinated
rollout. Worth keeping: the runtime and the HUD are separately installed, and
one of them is restarted by a watchdog.

## PUBLISH_FREQUENCY

Unchanged: **on state transition only**. A turn writes 6–8 times.

`latency_ms` must not become a reason to write more often. The temptation is to
republish after each Timeline mark — 18 marks, so ~3x the writes, for numbers
nobody reads until the turn ends. Attach the intervals that are complete to the
transitions that already happen.

`jarvis_state.json` is a status file, not a telemetry channel. If per-stage
timing is ever wanted at that resolution, the log already has it.

## SIZE_BUDGET

| field | backend cap | UI cap |
|---|---|---|
| `detail` | 120 chars (current) | 120 |
| `route` | enum | — |
| `turn_id` | integer | — |
| `latency_ms` | 6 integers | — |
| `heard_text` | 300 if ever adopted | 300 |
| `response_preview` | 1000 if ever adopted | 1000 |

Backend and UI caps stay separate. The HUD's 64 KB file cap is a parser
guard against a corrupt file and should not be relaxed because the schema grew.

## SECURITY_REVIEW

| field | confidentiality | integrity | staleness | display |
|---|---|---|---|---|
| `version` | none | none | none | none |
| `turn_id` | none | none | none | none — never render as an ID |
| `route` | none | none | **fixes** a staleness bug | none, enum |
| `latency_ms` | none | none | partial data is normal | must render missing as absent, not 0 |
| `activity` enum | low — behavioural shape | none | low | none, enum |
| `error.code` | none | none | none | none, enum |
| `error.message` | **medium, drifting** | none | none | must stay plain text |
| `heard_text` | **HIGH** | none | **HIGH — survives a crash** | must stay plain text |
| `response_preview` | **HIGH** | none | **HIGH — same** | must stay plain text |

Integrity is "none" throughout for one reason: the HUD never acts on any of it.
It draws. A corrupted field produces a wrong pixel, not a wrong action. That
stops being true the moment a Phase 3 confirmation UI can answer — at which
point every field feeding a decision needs re-reviewing, and this table does not
carry over.

**No heuristic redaction anywhere.** Detecting an API key or a password in text
is best-effort, and best-effort is not a boundary. It may be worth adding as
defence in depth; it is not a reason to publish a field that would otherwise be
rejected.

## DECISION_MATRIX

| field | value | privacy cost | complexity | recommendation |
|---|---|---|---|---|
| `version` | enables everything else | none | trivial | **ADD NOW** |
| `turn_id` | kills stale-context bugs | none | trivial | **ADD NOW** |
| `route` | explicit, survives HUD restart | none | trivial | **ADD NOW** |
| `latency_ms` | real answer to "why the wait" | none | small | **ADD NOW** |
| `activity` enum | modest | low-medium | **needs new instrumentation** | DEFER |
| `error.code` | better error display | none | small | DEFER |
| `error.message` free text | small | medium, drifting | small | REJECT |
| `heard_text` | high UI value | **HIGH + crash retention** | small | **REJECT** (transient later) |
| `response_preview` (expand to 1000) | modest — it is spoken aloud | **HIGH + crash retention** | small | **DEFER** |
| full response | low | HIGH, unbounded | small | **REJECT** |
| tool arguments | low | HIGH — paths, queries | needs instrumentation | **REJECT** |

```
RECOMMENDED_V2_FIELDS = version, turn_id, route, latency_ms
DEFERRED_FIELDS       = activity (enum), error.code,
                        response_preview expansion, transient context
REJECTED_FIELDS       = heard_text (state file), error.message (free text),
                        full response, tool arguments
```

## What this buys, honestly

The four recommended fields do not unlock the expanded HUD's most eye-catching
content. The transcript and the long reply stay out, so the panel keeps showing
state, detail, route and diagnostics — much what it shows now.

What changes is that route becomes a fact rather than an inference, stale
context becomes detectable, and "why am I waiting" gets a real answer. Those are
the parts worth having, and they are exactly the parts that cost nothing in
privacy.

The Phase 2A mock deliberately shows more than this recommends. That is the
system working: the mock made it possible to see what those fields would look
like, and then to decide against most of them on their merits rather than
because they were hard to build.

## Prerequisite before ANY of this

```
chmod 600 ~/AI-Lab/hermes-jarvis/logs/jarvis_state.json
chmod 700 ~/AI-Lab/hermes-jarvis/logs
```

Not done — this document changes nothing. But the reply text is world-readable
right now, and that should be settled before the schema grows.
