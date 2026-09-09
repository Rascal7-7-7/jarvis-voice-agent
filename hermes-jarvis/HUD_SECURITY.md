# HUD_SECURITY

```
HUD_HAS_SHELL_ACCESS = NO
```

Design contract, to be enforced when the HUD is built.

## The contract

The HUD is a **viewer**. Its entire input is one JSON file the runtime writes:
`logs/jarvis_state.json`. It has no channel back into the runtime, because a
file read is not a channel.

| the HUD must never have | why |
|---|---|
| shell / subprocess execution | a display surface that can run commands is an agent, and inherits the whole threat model |
| filesystem write to anything the runtime reads | the one exception is the future single-use approval file, which is polled and consumed |
| secrets of any kind | it needs none: it renders state, not credentials |
| Codex auth (`~/.codex/auth.json`) | delegation happens in the runtime, behind fixed-argv wrappers |
| Claude auth / `CLAUDE_CONFIG_DIR` | same |
| the ability to change a route | routing is the gate's and the router's decision |
| the ability to un-gate a `CONFIRMATION_REQUIRED` | the deterministic gate is not appealable through a UI |

## Why this rules out one candidate outright

`xaspx/hermes-control-interface` ships a browser-based terminal and file
explorer. That is shell reachability by design and is disqualified regardless of
its other merits.

`joeynyc/hermes-hudui` is not disqualified but is not neutral either: it has an
`Update hermes` action and plugin enable/disable/update. If it is ever adopted
for Hermes introspection, those features are out of scope and it should be
treated as a separate, more-privileged tool — not as the JARVIS HUD.

## Regression checks that must still pass after any HUD lands

Every one of these was verified before the HUD phase and must be re-verified
after:

| control | current state |
|---|---|
| deterministic utterance gate | 25/25 dangerous caught, 0 false positives |
| `approvals.deny` | 40 globs, command-level, evaluated before yolo/mode=off |
| `approvals.mode` | `manual` |
| browser isolation | `browser.use_real_profile = false`, real Chrome/Brave profiles untouched |
| Codex | `--sandbox read-only` pinned in argv; no write path exists |
| Claude | `--permission-mode plan` pinned; config root proven per call, fail-closed |
| listeners | `127.0.0.1:8644` and `127.0.0.1:11434` only; the runtime opens none |
| secrets in the runtime's launchd env | none — `PATH` / `VIRTUAL_ENV` / `HERMES_HOME` / `JARVIS_WORKDIR` |

## The approval file, when it exists

Not built. When it is:

- written by the HUD to a path the runtime polls, e.g. `logs/approval_reply.json`
- carries a nonce the runtime issued in the request, so a stale or replayed file
  is rejected
- consumed and deleted on read — single use
- `ALLOW ONCE` and `DENY` only; **no `ALLOW ALWAYS`**
- absence of a reply within a timeout means **deny**, matching
  `security.approval.transport_fallback = deny` already in the Hermes config
