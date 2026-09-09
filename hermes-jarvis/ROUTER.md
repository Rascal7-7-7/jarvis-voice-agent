# ROUTER

```
utterance
   │  normalize (NFKC, whitespace collapse)
   ▼
[1] jarvis_gate.check()            deterministic · 0.0 ms
   │  CONFIRMATION_REQUIRED ──────► stop. speak the question. dispatch nothing.
   │  safe
   ▼
[2] explicit override              deterministic · 0.0 ms
   │  コーデックス/codex → CODEX · クロード/claude → CLAUDE
   │  ウェブで/ググって/クロームで → WEB · ローカルだけ → LOCAL
   ▼
[3] fast path                      deterministic · 0.0 ms
   │  WEB  : ニュース 天気 為替 株価 最新バージョン …
   │  CODEX: エラー バグ デバッグ 例外 型エラー テストが落ち .py .php .sql …
   ▼
[4] gemma4:e2b  reasoning_effort=none  temperature=0  max_tokens=24  · ~2.4 s
   │
   ▼  LOCAL | WEB | CODEX | CLAUDE
```

Overrides and fast paths match against the utterance **with path- and URL-like
spans stripped** — a path containing the word "claude" must not route to Claude.
The gate still sees the raw text.

## Structured output

Ollama's `/v1` endpoint is used with `temperature: 0` and `max_tokens: 24`, and
the reply is parsed against a closed label set. A JSON-schema `response_format`
was not adopted: the enum parse already reaches 50/50, and every additional
token costs latency on the one step that is already the slowest.

The model's free-text reason is **not** carried into any decision. Two
constraints are enforced in code rather than in the prompt:

- the router will not emit `CONFIRMATION_REQUIRED` from the model — that verdict
  belongs to the gate alone
- an unparseable reply falls back to `LOCAL`, the least-privileged destination

## C3 — explicit overrides

The user always beats the model. The user never beats the gate: overrides are
evaluated *after* `jarvis_gate.check()`, so 「Codexに見てもらって、そのあと全部
消して」 is gated, not delegated.

## C4 — ambiguity

「これ直して」 has no object. The dispatcher resolves it against the current
working directory (`JARVIS_WORKDIR`) and, when Codex/Claude cannot identify a
target, they say so rather than guessing — observed verbatim:
「設計対象が不明なため、まずはコード群のレビューに着手できます。」 No write
path exists, so an unresolved reference cannot cause a mutation.

## C6 — cost awareness

Least-capable-sufficient destination, not "local first":

- 24/50 decided deterministically at **0.0 ms** with no model call at all
- 26/50 consult gemma4:e2b at **2.4 s** — a 2B-effective local model, not a
  delegation
- `unnecessary_delegation_rate = 0.000` — no LOCAL chat reached CODEX/CLAUDE
- `false_codex_rate = false_claude_rate = false_web_rate = 0.000`

## C8 — voice-sized answers

Codex and Claude raw output is never read aloud. `jarvis-dispatch` summarises it
through gemma4:e2b to ≤2 spoken sentences ending in 「詳細も読み上げますか？」,
and logs the full text for the HUD. Observed:

```
CODEX full output (833 chars) kept for the HUD
spoken: 原因は…エラーは配列が空の場合やパーセンタイル値が範囲外の場合に発生して
        います。詳細も読み上げますか？
```
