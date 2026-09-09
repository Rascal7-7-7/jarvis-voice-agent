# JARVIS HUD

A read-only status viewer for the JARVIS runtime. It renders four fields of JSON
and has no way back into the runtime — no shell, no network, no writes.

```
Swift 6 · SwiftUI · macOS 14+ · arm64 · zero external dependencies
```

## Status

```
Phase 1A  mock HUD                     COMPLETE
Phase 1B  production read-only source  COMPLETE
Phase 1C-prep  staging .app bundle     COMPLETE
Phase 2A  expanded HUD + mock data     COMPLETE (59 tests)  <- you are here
Phase 1C  finalization                 NOT APPROVED
Phase 2B  backend schema decision      NOT STARTED
```

**PHASE 1C-PREP ONLY. PRODUCTION INSTALL NOT APPROVED.** The bundle is staged
under `dist/` and deliberately goes no further: it is not in `~/Applications` or
`/Applications`, is not a login item, has no LaunchAgent, and does not start
automatically.

Phase 1B's live end-to-end gate is still open, and it is blocked behind audio
validation — see **Audio phase dependency** below.

## Build and run

Development:

```sh
swift run                      # debug, with the mock affordances
swift test                     # 59 tests
```

Staging bundle:

```sh
./scripts/build_app.sh         # -> dist/JARVIS HUD.app  (ad-hoc signed)
./scripts/verify_bundle.sh     # 56 checks on the staged artifact
open "$HOME/AI-Lab/jarvis-hud/dist/JARVIS HUD.app"
```

There is no Dock icon (`LSUIElement`). Everything is in the menu bar: **Show
HUD**, **Hide HUD**, **Show Expanded / Collapse**, **Quit**. Quitting from there
is the only quit path, which is why the menu is never hidden along with the
panel. Clicking the panel itself toggles compact/expanded.

## Compact and expanded

Two sizes. **Compact** is the default and is what floats over your work all day:
an orb, the state, one line of detail, and the route once dispatch has happened.
**Expanded** adds sections — activity, route badge, diagnostics — and opens
according to a disclosure policy.

The expanded panel is fixed in width (472 pt) and sized to its content in
height. A fixed height left a release build's panel two-thirds empty, because
the future-fields section does not exist there at all.

Three disclosure policies ship so they can be compared on real transitions:

| policy | opens |
|---|---|
| `MANUAL_ONLY` | never by itself |
| `TURN_ACTIVITY` | from LISTENING, through the whole turn |
| `ROUTE_ONLY` (default) | once dispatch has a route, plus ERROR and CONFIRMATION_REQUIRED |

`ROUTE_ONLY` is the default because a two-second LOCAL_FAST turn does not
deserve a panel. Collapse delay after IDLE is selectable (immediate / 2s / 5s /
manual); 2s is the default. A manual show/hide overrides the policy for that
turn and clears when the next turn begins — otherwise one dismissal would
silently disable the panel forever.

The policy and delay switches are `#if DEBUG`. A release build ships one decided
behaviour, not a menu of them.

## Route display

The runtime publishes no `route` field — the route IS the state during dispatch
(`set_state(route, "working")`), so by the time JARVIS is SPEAKING it is gone
from the state file. `RouteTracker` remembers it for the duration of the turn
and clears at IDLE or OFFLINE. That is derivation from what the runtime
actually published, not invention.

Always words: `LOCAL FAST`, `LOCAL TOOL`, `LOCAL`, `WEB`, `CODEX`, `CLAUDE`.
Colour is decoration.

## Production data vs future data

This is the line that matters most in Phase 2A.

**Production** (`jarvis_state.json` really contains these): `state`, `since`,
`detail`, `pid` — plus `elapsed` and the remembered route, both derived from
them.

**Future, mock-only**: heard text, response preview, latency breakdown, tool
summary. The backend publishes none of these. `FutureHUDContext` and
`LatencyBreakdown` are declared inside `#if DEBUG` at file scope, so **the types
do not exist in a release binary** and the expanded panel there has no code path
that could render them. Verified mechanically by `scripts/verify_bundle.sh`,
which asserts the type names and the sample strings are absent from the shipped
executable.

Whether any of it becomes real is a Phase 2B decision about the BACKEND, taken
on its own merits. Building the view first must not become the argument for
changing the schema.

## What it shows

| runtime state | panel |
|---|---|
| `IDLE` | collapsed orb, no motion |
| `LISTENING` | expanded, pulsing rings |
| `THINKING` | expanded, sweeping arc |
| `ROUTING` | expanded, route named in `detail` |
| `LOCAL_FAST` `LOCAL_TOOL` `LOCAL` `WEB` `CODEX` `CLAUDE` | expanded, route label, static |
| `SPEAKING` | expanded, waveform bars, reply preview (≤120 chars) |
| `CONFIRMATION_REQUIRED` | expanded, text only — **no Allow/Deny buttons** |
| `ERROR` | expanded, error text |
| `OFFLINE` | collapsed, dimmed, no motion |
| anything else | `Unknown (RAW_VALUE)` |

Every state is identifiable from **text**, never colour alone. Animation is
suppressed under Reduced Motion; the panel carries a VoiceOver label and value.

## State source

```
~/AI-Lab/hermes-jarvis/logs/jarvis_state.json      READ ONLY
```

The runtime publishes atomically — write `.tmp`, then `os.replace()` — which
swaps the inode. A watcher attached to the *file* would receive one event and go
permanently deaf, so the HUD watches the containing **directory** and, on each
event, opens the file fresh (`O_RDONLY`), reads, closes. Nothing holds a
descriptor on the file. Proven against 120 atomic replaces in tests and 60 in a
CLI stress harness.

Liveness comes from `kill(pid, 0)` — `EPERM` means alive, `ESRCH` means gone —
never from file age. A long-idle JARVIS legitimately leaves the file untouched
for hours.

## Offline and fault behaviour

| condition | HUD |
|---|---|
| runtime alive | current state, immediately on launch |
| runtime not running | `OFFLINE`, no modal, app stays up |
| state file missing | `unavailable` — a HUD fault, not a runtime `ERROR` |
| malformed JSON | last good state kept, `HUD DATA ERROR` shown |
| file over 64 KB | refused, last good state kept |
| unknown state string | `Unknown (RAW)`, no crash |
| `since` in the future | elapsed clamps to 0 |
| saved panel position off-screen | pulled back onto an existing display |

HUD faults are kept distinct from runtime faults throughout: the HUD never
renders its own problem as JARVIS being in `ERROR`.

## Security boundary

The HUD is a viewer. It has, and needs, none of the following:

- shell, `Process`, `NSTask`, subprocess of any kind
- network — no `URLSession`, no WebView, no URL opening
- filesystem writes outside its own preferences
- secrets, API keys, Codex or Claude auth
- any path back into the runtime, including `launchctl`

Verified on the staged release bundle (`scripts/verify_bundle.sh`, 56 checks):
0 network sockets, 0 child processes, 0 entitlements, no `NSTask` / `Process(` /
`URLSession` / `WKWebView` / `posix_spawn` / `popen` / `/bin/sh` strings, and no
link against Network, CFNetwork or WebKit. The only production path in the
binary is the state file itself; `launchctl`, `kickstart` and `jarvis_runtime`
do not appear.

While running, the only production handle is the `logs` **directory**, opened
read-only. Its own writes are `/dev/null` (stdout/stderr) and macOS's Metal
shader cache inside the app's own container.

Input is untrusted even though the writer is trusted:

| guard | value |
|---|---|
| file size cap | 64 KB |
| `state` string cap | 64 chars |
| displayed `detail` cap | 120 chars |
| `detail` rendering | plain `Text` — no markdown, no links, no paths |
| control characters | stripped before display |
| negative elapsed | clamped to 0 |

Debug affordances (Mock State, Mock Sequence, Hostile Input, and the
`JARVIS_HUD_*` environment hooks) are `#if DEBUG` and are **absent from the
release binary** — including the `MockStateProvider` type itself, which is
guarded at file scope so it does not exist in a release build at all.

## Layout

```
Sources/JarvisHUD/
  JarvisHUDApp.swift              MenuBarExtra + NSPanel lifecycle
  Models/JarvisState.swift        14 states, tolerant decoding, display caps
  Services/FileStateProvider.swift  directory watch, atomic-replace safe
  Services/StateProvider.swift    ProviderStatus: HUD fault vs runtime ERROR
  Services/MockStateProvider.swift  DEBUG-only, whole file guarded
  Utilities/StatePresenter.swift  300 ms dwell; urgent states bypass it
  Utilities/PanelPlacement.swift  pure geometry: off-screen recovery
  Utilities/DisclosurePolicy.swift  when the expanded panel opens and closes
  Models/HUDContext.swift         RouteTracker; FutureHUDContext (DEBUG only)
  Views/ExpandedPanelView.swift   expanded panel, route badge, latency strip
  Views/                          OrbView, HUDPanelView, MenuBarView
Tests/JarvisHUDTests/             59 tests
scripts/build_app.sh              stage dist/JARVIS HUD.app
scripts/verify_bundle.sh          45 artifact checks
docs/ROLLBACK.md, docs/ROLLBACK_PHASE1C_PREP.md
```

## Future install (design only — not done)

Installing would mean copying the bundle to `~/Applications`. Nothing in this
phase does that, and it has not been approved.

## Future login item (design only — not registered)

`SMAppService`, verified against the local SDK
(`ServiceManagement.framework/Headers/SMAppService.h`):

- `SMAppService.mainApp` registers the app itself as a login item — macOS 13+.
- Status is one of `notRegistered`, `enabled`, `requiresApproval`, `notFound`.
- Approval is the user's: the entry appears under System Settings → General →
  Login Items, and the app cannot enable itself silently.
- **"Apps that use SMAppService APIs must be code signed"** (header, line 49).
  Whether the current ad-hoc signature satisfies that is untested.
- No entitlement is declared for `mainApp` registration; the alternatives
  (`loginItem(identifier:)`, `agent(plistName:)`) require a helper bundle or a
  plist inside `Contents/Library/`, neither of which exists here.

Nothing has been registered: `register()` was not called, System Settings was
not modified, and no LaunchAgent was created for the HUD. Its lifecycle stays
separate from the runtime's and the watchdog's.

## Measured

Phase 1B (`swift run -c release`):

| state | CPU over 20 s | RSS |
|---|---|---|
| IDLE | 0.03 s (~0.15 %) | 80.6 MB |
| LISTENING | 1.58 s (~7.9 %) | 85.6 MB |
| THINKING | 1.76 s (~8.8 %) | 88.0 MB |
| SPEAKING | 1.65 s (~8.3 %) | 87.6 MB |

Only the three animated states cost anything: `TimelineView` is `paused` with a
`nil` `minimumInterval` for every other state, so no timer runs at idle.

Phase 1C-prep bundle idle figures are in `logs/perf_bundle.txt`.

## Audio phase dependency

Phase 1C finalization is blocked until the audio work validates, in this order:

```
10 s noise gate -> 1 turn -> 3 turns -> 10 turns -> 20 turns -> turn after long idle
then HUD Phase 1B live E2E
```

Current audio status is `SHARED_STREAM_CORE = PROVISIONAL PASS` with quality
validation pending; see `~/AI-Lab/hermes-jarvis/docs/AUDIO_PHASE_STATUS.md`.

## Known limitations

- No `heard_text`. Deliberate: showing the transcript widens what a bystander
  sees from "the assistant is busy" to "what the user said at home".
- No latency display — the runtime logs it but does not publish it.
- No Allow/Deny UI. Approval stays with the deterministic gate, not a viewer.
- If the runtime's `set_state()` write fails it swallows the `OSError`, so a
  fresh-looking file is not proof of a live runtime. This is why liveness uses
  the pid rather than file age.
- Single instance relies on macOS Launch Services (verified: three `open` calls
  yield one process). There is no custom lock.
- The staged bundle carries no custom icon; that is a later phase.
