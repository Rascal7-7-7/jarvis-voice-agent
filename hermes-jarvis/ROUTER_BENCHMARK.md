# ROUTER_BENCHMARK

50 cases, 10 per category. `bench/router_bench.py`.

```
router_accuracy            : 50/50 = 1.000     (target >= 0.95)
dangerous_action_accuracy  : 10/10 = 1.000     (target 1.000)
false_codex_rate           : 0/50  = 0.000
false_claude_rate          : 0/50  = 0.000
false_web_rate             : 0/50  = 0.000
unnecessary_delegation     : 0/10  = 0.000

deterministic decisions    : 24/50   median   0.0 ms
llm decisions              : 26/50   median   2.4 s

decided_by: security_gate 10 · fast_path 10 · explicit_override 4 · llm 26
```

## Getting there — three measured defects

The first run scored **49/50**. Each subsequent fix was driven by a specific
observed failure, and the full suite was re-run after every change.

| # | observed | cause | fix |
|---|---|---|---|
| 1 | 「このテストが落ちる原因を調べて」 → WEB | 調べて is the canonical WEB verb and outweighed the debugging context | CODEX fast path on エラー/バグ/テストが落ち… |
| 2 | 「Next.jsの最新バージョンは?」 → CODEX | the fast path matched `.js` inside the product name | `.js`/`.ts` removed from the extension set; WEB evaluated before CODEX |
| 3 | 「…読んで要約して: /private/tmp/claude-502/…」 → CLAUDE | the CLAUDE override matched the sandbox **path**, not the intent | overrides and fast paths now run on path-stripped text |

Defect 3 is the one worth remembering: it means any utterance quoting a path
could previously steer its own routing. It was found by the prompt-injection
test, not by the router suite.

## Latency

| path | share | latency |
|---|---|---|
| security gate | 10/50 | **0.0 ms** |
| fast path | 10/50 | **0.0 ms** |
| explicit override | 4/50 | **0.0 ms** |
| LLM | 26/50 | 2.4 s |

**The <1 s router-overhead target is met for 48 % of traffic and missed for the
rest.** 2.4 s is the floor for gemma4:e2b: cutting `max_tokens` from 24 to 2
changed the median from 2.28 s to 2.19 s, so the cost is prompt processing, not
generation. The only lever that works is not calling the model — which is what
the fast paths do. Widening them further would trade accuracy for latency and
was not done.
