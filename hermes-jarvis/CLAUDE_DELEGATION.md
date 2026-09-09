# CLAUDE_DELEGATION

```
CLAUDE_METHOD = Claude Code CLI subprocess behind a fixed-argv wrapper,
                with the config root PROVEN before every call
```

## B1 — environment (read-only)

| | |
|---|---|
| version | 2.1.251 |
| wrapper | `~/.local/bin/claude` (3304 B) |
| real CLI | `~/.nvm/versions/node/v24.14.1/bin/claude` |
| config root | `~/.claude-work` |
| `~/.claude/CLAUDE.md` | → `~/.claude-work/CLAUDE.md` (symlink) |
| `~/.claude/rules` | → `~/.claude-work/rules` (symlink) |
| permissions | deny 17 · allow 40 |
| MCP | playwright · github · brave-search |

Launcher selftest:

```
REAL_CLAUDE              = ~/.nvm/versions/node/v24.14.1/bin/claude
RECURSION                = no
CHILD_CLAUDE_CONFIG_DIR  = /Users/Rascal/.claude-work
```

## B2 — the invocation requirement, enforced not assumed

The known hazard: a bare `claude` resolved from a launchd/cron PATH lands on
`~/.claude`, whose `settings.json` registers different hooks and does not carry
the 17 deny rules. Using the absolute wrapper path is necessary but not
sufficient — the wrapper could itself be replaced or shadowed.

`bin/jarvis-claude` therefore runs `CLAUDE_LAUNCHER_SELFTEST=1` before every
delegation and **fails closed**:

- `CHILD_CLAUDE_CONFIG_DIR != ~/.claude-work` → exit 78, nothing runs
- `RECURSION != no` → exit 78
- selftest cannot run at all → exit 69

Verified by pointing `JARVIS_CLAUDE_BIN` at the raw CLI: refused with exit 69.

## B3 — read-only analysis

`--permission-mode plan` is pinned in argv. Plan mode cannot edit files.

```
exit=0  wall=28.0s
prompt: stats.py の設計上の弱点をセキュリティ・保守性の観点から日本語3行で
```

Claude returned exactly what Codex did not: input-validation as a *class* of
failure across all three functions, the spec/implementation divergence in
`clamp` as a maintenance hazard, and the absence of tests/type hints/error
policy as the root problem — plus it noticed the file is a deliberate fixture
and said so. That division of labour (Codex = line-level defects, Claude =
structural judgement) is the reason both exist.

Fixture sha256 unchanged.

## B4 — where Claude is the right destination

Routed to CLAUDE by the router benchmark, 10/10:

architecture comparison · microservices-vs-monolith trade-offs · threat
modelling · multi-axis evaluation · staged migration planning · long-term
technical-debt analysis · criteria-based option comparison · whole-codebase
design review · operability-and-cost analysis · explicit 「クロードにも考えさせて」

`false_claude_rate = 0.000` across all 50 cases — no pure coding task was sent
to Claude.

## B5 — failure cases

| case | behaviour |
|---|---|
| wrong config root | exit 78, refuses to run — **fail closed** |
| wrapper missing / selftest broken | exit 69 |
| outside allowlist | exit 77 before Claude launches |
| timeout | exit 75 → 「Claudeを利用できませんでした（応答なし）」 |
| non-zero exit | exit 70, stderr tail preserved |

The dispatcher speaks a safe sentence in every case; Hermes does not crash.

```
CLAUDE_DELEGATION      = PASS
CLAUDE_WRAPPER         = PASS
CLAUDE_CONFIG_ROOT     = PASS (verified per call, fail-closed)
CLAUDE_CONFIG_CHANGED  = NO
```
