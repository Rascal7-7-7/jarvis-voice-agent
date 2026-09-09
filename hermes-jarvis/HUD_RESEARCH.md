# HUD_RESEARCH — 2026-08-29

## Candidates

### joeynyc/hermes-hudui

| | |
|---|---|
| stars / commits / forks | 1,800 · 157 · 230 |
| license | MIT |
| **verified against** | **Hermes v0.20.3, state.db schema v26** ← our exact schema |
| data access | reads `~/.hermes/` and the `hermes` CLI directly |
| network posture | local-only server, rejects cross-site and non-loopback |
| platform | macOS, Linux, WSL |
| requires | Python 3.11+, Node 22.12+ |
| **execution capability** | **yes** — an `Update hermes` action (two-click, shows logs) and plugin enable/disable/update |

Best-maintained and the only one verified against schema v26. But it **can act**,
not merely display, which is the one thing PHASE 12 rules out.

### eadmin2/jarvis_ai

| | |
|---|---|
| stars / commits | 144 · **7** |
| license | MIT |
| aesthetic | the Iron-Man HUD the brief describes |
| **approval UI** | **yes** — "approval cards, dangerous commands pause for your ALLOW/DENY" |
| tool activity | live tool calls with command previews |
| transcript | live transcription while speaking |
| platform | macOS tested, launchd templates included |
| **requires ElevenLabs** | **yes** — paid TTS, free tier ~0.5 credits/char |
| **requires** | Hermes API server enabled (`API_SERVER_ENABLED=true`) on **port 8642** |

Closest to the brief's visual and approval goals, but 7 commits total, adds a
paid dependency we do not need (our TTS is edge/`say`), and needs a Hermes API
listener that is currently **not** exposed — `GET /api/jobs` returns 404 on 8644,
confirming the API server platform is off.

### xaspx/hermes-control-interface

**Disqualified.** It ships a browser-based terminal and file explorer. That is
precisely the shell reachability PHASE 12 and PHASE 17 forbid.

### EKKOLearnAI/hermes-studio

Web dashboard for sessions / scheduled jobs / usage analytics. Hermes-management
oriented; no wake state, no router state, no approval UI. Not evaluated further.

## The gap none of them fill

Every one of these monitors **Hermes**. None of them knows anything about
JARVIS's own pipeline — wake state, which route the deterministic gate or the
router chose, whether Codex or Claude is working, whether a confirmation is
pending. Those are states of `jarvis_runtime.py`, not of Hermes, and they are
exactly the list PHASE 13 asks for.

`jarvis_runtime.py` already publishes them to `logs/jarvis_state.json`.

```
HUD_CANDIDATE_1 = joeynyc/hermes-hudui
                  adopt for DEEP HERMES INTROSPECTION only, and only in a
                  read-only posture — its update/plugin actions are out of scope
HUD_CANDIDATE_2 = eadmin2/jarvis_ai
                  closest to the brief visually and the only one with an
                  approval UI, but 7 commits, needs ElevenLabs, and needs a new
                  Hermes API listener on 8642

HUD_RECOMMENDATION = build a minimal JARVIS HUD over logs/jarvis_state.json,
                     borrowing the approval-card interaction from jarvis_ai.
```

**Why build rather than adopt.** The JARVIS states are the point of the HUD, and
no existing project has them. A reader over a JSON file is a few hundred lines,
has structurally zero authority (it cannot write anywhere the runtime reads), and
carries no paid dependency and no extra listener. `hermes-hudui` can run
alongside it for Hermes-side depth if that is wanted, with its action features
treated as off-limits.

This is a recommendation, not an implementation — the brief puts HUD work after
always-on passes, and always-on is not yet fully verified.
