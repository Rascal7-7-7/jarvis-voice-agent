# HUD_ARCHITECTURE

Design only. Not implemented — the brief puts HUD work after always-on passes.

## Separation

```
   ┌──────────────────────────────────────────────┐
   │ JARVIS runtime (launchd)                     │  ← all authority lives here
   │   wake listener · STT · gate · router        │
   │   Codex/Claude delegation · TTS              │
   └───────────────┬──────────────────────────────┘
                   │ writes, atomically (temp + rename)
                   ▼
        logs/jarvis_state.json      ← a FILE, one direction
                   │
                   │ reads
                   ▼
   ┌──────────────────────────────────────────────┐
   │ HUD                                          │  ← display only
   │   no shell · no filesystem write · no secrets│
   │   no Codex auth · no Claude auth             │
   └──────────────────────────────────────────────┘
```

**Why a file and not a socket or websocket.** A socket is bidirectional by
construction; keeping it read-only then depends on the server's discipline. A
file the runtime writes and the HUD reads has no reverse channel to secure. The
HUD cannot send anything back because there is nothing to send it to.

The runtime already does this today — the transport is finished, only the viewer
is missing.

## The state document

```json
{"state": "CODEX", "since": 1788010897.0, "detail": "working", "pid": 98134}
```

`state` is one of the PHASE 13 labels; `detail` is a short human string;
`since` lets the HUD render elapsed time without polling history.

## States and transitions

```
OFFLINE ──► IDLE ──► LISTENING ──► THINKING ──► ROUTING ──┬─► LOCAL  ─┐
   ▲         ▲                                            ├─► WEB    ─┤
   │         │                                            ├─► CODEX  ─┼─► SPEAKING ──► IDLE
   │         │                                            ├─► CLAUDE ─┘
   │         │                                            └─► CONFIRMATION_REQUIRED ──► IDLE
   │         └──────────────────────── ERROR ─────────────────────────────────────────────┘
   └── stopped / lease not held
```

Every one of these is already emitted by `jarvis_runtime.py`
(`set_state(...)`) — `TOOL_RUNNING` is the one PHASE 13 label not yet
distinguished; it is currently folded into the route state's `detail`.

## Activation — PHASE 16

| option | verdict |
|---|---|
| A. always-visible HUD | rejected — the brief rules out permanent full-screen, and a persistent panel competes with real work |
| B. menu bar + expand on wake | **recommended** |
| C. small orb at a screen edge, expands on wake | good second choice; more novel, more to build |
| D. full-screen Iron-Man HUD | rejected for daily use; reasonable as a "show me" mode bound to an explicit command |

```
menu bar (state glyph, always) ──► "Hey Jarvis" ──► HUD expands
                                                        │
                                                   task completes
                                                        ▼
                                                   auto-collapse
```

The menu-bar item shows the current state as a glyph and nothing else; the panel
appears on wake and collapses when the runtime returns to `IDLE`. That keeps the
"system online" presence the brief wants without occupying the screen.

## Voice / HUD split — PHASE 14

Already implemented in `jarvis-dispatch`: the delegated answer is summarised to
≤2 spoken sentences ending in 「詳細も読み上げますか？」, and the full text is
logged with a `--- <ROUTE> full output (N chars) kept for the HUD ---` marker.

Spoken: 「Codexで確認しました。原因は型の不一致です。詳細を画面に表示しています。」
HUD: the full Codex output, the files it read, the diagnostics.

The runtime should extend `jarvis_state.json` with a `last_result` path (a file
under `logs/`) so the HUD can render the detail without the runtime having to
push it.

## Approval UI — PHASE 15

Designed, deliberately inert. There is no write path in either delegation
wrapper today, so nothing can request approval yet.

```
Jarvis wants to:
  Modify   src/api/user.ts
  Reason   Fix validation bug

  [ DENY ]   [ ALLOW ONCE ]
```

- **No `ALLOW ALWAYS`.** Per the brief, and because a standing allowance is
  indistinguishable from no gate at all once it is granted.
- The HUD would write the decision to a **separate** single-use file the runtime
  polls, keeping the one-directional property: the HUD never calls the runtime.
- The decision would gate the *dispatch*, never the deterministic gate. An
  utterance the gate stopped is not appealable through the HUD.
