# LOCAL_ARCHITECTURE — capability-scoped local answering, 2026-08-30

Built on the latency baseline in [LATENCY.md](./LATENCY.md). The principle is
the brief's: not "remove features to go faster", but **hand the turn only the
capability it needs**.

## The shape

```
utterance
   │
   ▼
[1] jarvis_gate            deterministic. CONFIRMATION_REQUIRED stops here.
   │                       40 approvals.deny globs unchanged. Not appealable.
   ▼
[2] explicit override      user's words win over the model — never over [1]
   │
   ▼
[3] deterministic paths    greetings/thanks -> LOCAL_FAST at 0 ms
   │                       WEB/CODEX signals -> those routes at 0 ms
   ▼
[4] gemma4:e2b router      LOCAL_FAST | LOCAL_TOOL | WEB | CODEX | CLAUDE
   │
   ▼
[5] split_local()          DETERMINISTIC. Current-fact pattern demotes
   │                       LOCAL_FAST -> LOCAL_TOOL. One-way, safe direction.
   ▼
execution
   ├── LOCAL_FAST   no agent, no tools, 722 B system prompt
   │        └── NEED_TOOL ──► falls through to LOCAL_TOOL, silently
   ├── LOCAL_TOOL   the agent, scoped toolset (profile)
   ├── WEB          the agent, web toolset
   ├── CODEX        jarvis-codex   --sandbox read-only
   └── CLAUDE       jarvis-claude  --permission-mode plan
```

The security order is unchanged: the gate is step 1 and the LLM is step 4, so
the model never sees a dangerous utterance and cannot un-gate one. `split_local`
can only ever make a turn *less* capable.

## LOCAL_FAST

No agent process, no tools, no filesystem, no shell, no network beyond the
loopback Ollama call. A text-in/text-out function, which is why it is safe to
reach without the agent's approval machinery — there is nothing for approvals to
guard.

It must never answer anything that depends on the world right now, because it
would have to invent it. **Measured**: asked for today's date with a bare system
prompt, gemma4:e2b answered 「本日は2024年5月16日です」 with full confidence.

Three layers stop that reaching the speaker, and only the middle one is a model:

| layer | mechanism | where |
|---|---|---|
| 1 | `_CURRENT_FACT` regex demotes the utterance to LOCAL_TOOL | `jarvis_router.split_local` |
| 2 | the system prompt requires `NEED_TOOL` for those categories | `jarvis_local_fast.SYSTEM` |
| 3 | `_looks_like_current_fact` re-checks the **answer** and converts it | `jarvis_local_fast.ask` |

Layer 3 matches the *shape* of a current-fact claim — a concrete date, clock
time, or byte size — rather than its topic, because by then the question is
gone. Unparseable model output is treated as NEED_TOOL rather than spoken: that
is exactly the case where we cannot tell whether layer 2 was obeyed.

`NEED_TOOL` is invisible to the user. The dispatch falls through to LOCAL_TOOL
and they hear an answer, never a routing failure.

## LOCAL_TOOL profiles

Same agent, same approvals, same system prompt. Only the toolset narrows, via
upstream's own `-t/--toolsets`. Nothing forked, nothing cached.

| profile | toolsets | tools | schema B | total prompt B | vs full |
|---|---|---:|---:|---:|---:|
| FULL (before) | all | 20 | 41,316 | 64,181 | — |
| `LOCAL_TIME` | *(none)* | 0 | 0 | 22,865 | 36 % |
| `LOCAL_SYSTEM` | terminal | 2 | 4,653 | 27,518 | 43 % |
| `LOCAL_FILES_READ` | file | 4 | 6,905 | 29,770 | 46 % |
| `LOCAL_GENERAL` | file, terminal, clarify | 7 | 13,194 | 36,059 | 56 % |
| LOCAL_FAST | *(no agent)* | 0 | 0 | **722** | **1 %** |

**Why LOCAL_TIME carries no tools.** Measured: `hermes -t '' -z "今日は何日ですか"`
answers correctly. The timestamp is part of the agent's *system prompt*, not
something a tool fetches. LOCAL_FAST gets the date wrong because it lacks the
prompt, not because it lacks a tool — so the fix is the agent with zero tools,
which is the cheapest correct configuration available.

An unknown toolset name is not a harmless typo: `hermes -t __none__` returned an
empty answer in 0.56 s. Profile names are validated against the measured
registry at import, and an empty agent answer triggers a full-agent retry rather
than silence at the speaker.

## System prompt slimming — what was actually possible

`LOCAL_FAST` drops the entire 22,865 B system prompt including the 11,968 B
skills index, because it does not use the agent at all.

For `LOCAL_TOOL` the skills index **could not be removed per invocation**, and
this was checked rather than assumed:

| lever | result |
|---|---|
| `-t/--toolsets` | works — this is what the profiles use |
| `--ignore-rules` | no change (10.60 s vs 10.19 s baseline) |
| `--ignore-user-config` | no change (10.04 s) |
| `--safe-mode` | **slower** (29.16 s) |
| `--skills` | *adds* preloaded skills; it is not a suppressor |
| `hermes skills opt-out` | global config — would change `hermes chat` too, so not used |

Recorded as investigated and not safely reducible per turn.

## Tool scoping benchmark (section 9)

Full agent (A) vs scoped profile (B), median of 2 runs each, correctness judged.

| query | variant | prompt KB | tools | median s | verdict |
|---|---|---:|---:|---:|---|
| 今日は何日ですか | A full | 62.7 | 20 | 11.00 | OK |
| | B LOCAL_TIME | 22.3 | 0 | **10.25** | OK |
| 今の時刻を教えて | A full | 62.7 | 20 | 12.16 | OK/WRONG |
| | B LOCAL_TIME | 22.3 | 0 | 11.01 | WRONG |
| このMacの空き容量は | A full | 62.7 | 20 | 12.41 | TOOLCALL_LEAK/WRONG |
| | B LOCAL_SYSTEM | 26.9 | 2 | 28.65 | OK/TOOLCALL_LEAK |
| メモリ状態を教えて | A full | 62.7 | 20 | 11.75 | WRONG |
| | B LOCAL_SYSTEM | 26.9 | 2 | 21.08 | OK/TOOLCALL_LEAK |
| ディレクトリの一覧 | A full | 62.7 | 20 | 22.62 | OK |
| | B LOCAL_FILES_READ | 29.1 | 4 | **7.52** | OK/TOOLCALL_LEAK |

Three things this says, and one it does not:

**Scoping did not cost accuracy.** The brief's rule was to drop any profile that
loses accuracy; none did. `LOCAL_SYSTEM` was more often *correct* than the full
agent.

**The tool-call leak is not caused by scoping.** The full agent produced
`terminal command: df -h /` and `session_search(query='Macのメモリ状態')` as
prose too. gemma4:e2b is simply unreliable at emitting tool calls, at any
toolset size. That is a model property and is reported, not worked around.

**`LOCAL_SYSTEM` is slower than full, and the comparison is confounded**: the
full agent was faster on those rows largely because it gave up without running
anything. Faster and wrong is not a win.

**What it does not say**: with 2 repetitions and verdicts that differ *between*
repetitions, this is not enough evidence for a reliability claim about either
variant. The latency figures are usable; "how often is it right" is not
established here.

## Router fast paths

Greetings and thanks only, anchored to a whole utterance:

```
こんにちは / こんばんは / おはよう / ありがとう / お疲れ様です / hello / hi …
```

Deliberately narrow. The brief warned against regexing "stable knowledge"
broadly and it is right: 「Pythonとは」 is stable, 「このプロジェクトのPythonの
バージョンは」 is not, and no wording rule separates them reliably. Those go to
the model — and are then checked by the current-fact demotion anyway.

Even the greeting path is routed through `split_local`, so it cannot outrank the
demotion either.

Measured over the 100-case dataset: **51 % decided at 0 ms**, 49 % by the model.

## Files

| file | status |
|---|---|
| `bin/jarvis_local_fast.py` | NEW — LOCAL_FAST engine |
| `bin/jarvis_profiles.py` | NEW — capability profiles |
| `bin/jarvis_router.py` | LOCAL split, current-fact demotion, greeting path |
| `bin/jarvis-dispatch` | LOCAL_FAST branch, NEED_TOOL fallthrough, scoped `-t` |
| `bin/jarvis_runtime.py` | timeline reports LOCAL_FAST / LOCAL_TOOL separately |
| `tests/route_dataset.py` | NEW — 100 cases |
| `tests/test_routes.py` | NEW — section 7 |
| `tests/test_local_fast_quality.py` | NEW — section 8 |
| `tests/regression.py`（pytest 対象外・単体スクリプト） | NEW — section 14 |

No upstream file was touched. `~/.hermes/config.yaml` was not written — verified
identical to the pre-alwayson backup in the voice section, and `silence_duration`
is not a config key at all (the 3.0 s is `tools/voice_mode.py`'s constant, which
is still 3.0).

## Rollback

```sh
cd ~/AI-Lab/hermes-jarvis/bin
cp jarvis_router.py.pre-capscope-20260830-000501   jarvis_router.py
cp jarvis-dispatch.pre-capscope-20260830-000501    jarvis-dispatch
cp jarvis_runtime.py.pre-capscope-20260830-000501  jarvis_runtime.py
rm -f jarvis_local_fast.py jarvis_profiles.py      # new files, nothing depends on them
launchctl kickstart -k gui/$(id -u)/local.jarvis.runtime
```

That returns routing to the single `LOCAL` label and the full 20-tool agent. No
config, plist, upstream source, model or Ollama setting has to be undone,
because none was changed.
