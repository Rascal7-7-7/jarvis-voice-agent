"""LOCAL_TOOL capability profiles — hand the turn only the tools it needs.

The full agent offers 20 tools in 41,316 B of schema on every turn, on top of a
22,865 B system prompt. A profile keeps the agent, its approval machinery and
its system prompt exactly as they are, and narrows only the toolset, through
upstream's own `-t/--toolsets` flag. Nothing is forked and nothing is cached.

Byte figures come from `hermes prompt-size --json` on this machine
(2026-08-30), which reports schema size per toolset:

    file            4 tools   6,905 B      web             2 tools   1,910 B
    delegation      1 tool    5,781 B      tts             1 tool    1,868 B
    terminal        2 tools   4,653 B      clarify         1 tool    1,636 B
    skills          3 tools   3,693 B      todo            1 tool    1,518 B
    memory          1 tool    3,178 B      vision          1 tool    1,360 B
    session_search  1 tool    3,178 B      code_execution  1 tool    2,548 B
    browser-use     1 tool    3,048 B

WHY LOCAL_TIME CARRIES NO TOOLS
  Measured: `hermes -t '' -z "今日は何日ですか"` answers "現在の日付は2026年8月
  30日です" correctly. The timestamp is part of the agent's system prompt, not
  something a tool fetches. So the date needs the agent -- but zero tools. This
  is the clearest case of the whole exercise: LOCAL_FAST gets it wrong because
  it lacks the *prompt*, not because it lacks a tool.

An unknown toolset name is not a harmless typo: `hermes -t __none__ -z ...`
returned an empty answer in 0.56 s. Every name below is checked against the
measured registry at import, so a typo fails here rather than silently
producing silence at the speaker.
"""
from __future__ import annotations

import json
import re
import sys

# toolset -> (tool_count, schema_bytes). From `hermes prompt-size --json`.
REGISTRY: dict[str, tuple[int, int]] = {
    "file": (4, 6905),
    "delegation": (1, 5781),
    "terminal": (2, 4653),
    "skills": (3, 3693),
    "memory": (1, 3178),
    "session_search": (1, 3178),
    "browser-use": (1, 3048),
    "code_execution": (1, 2548),
    "web": (2, 1910),
    "tts": (1, 1868),
    "clarify": (1, 1636),
    "todo": (1, 1518),
    "vision": (1, 1360),
}

FULL_TOOL_COUNT = 20
FULL_TOOL_SCHEMA_BYTES = 41316
FULL_SYSTEM_BYTES = 22865
SKILLS_INDEX_BYTES = 11968

# The agent's system prompt is the same for every profile. There is no
# per-invocation lever that drops the 11,968 B skills index -- checked:
# --ignore-rules and --ignore-user-config change neither, and `hermes skills
# opt-out` is global config that would also change `hermes chat`. Recorded as
# investigated and not safely reducible per turn, rather than left implied.
PROFILES: dict[str, tuple[str, ...]] = {
    # date / time — the answer is in the system prompt; no tool can improve it
    "LOCAL_TIME": (),
    # this Mac: disk, memory, CPU, processes
    "LOCAL_SYSTEM": ("terminal",),
    # reading files and listing directories
    "LOCAL_FILES_READ": ("file",),
    # the catch-all when the utterance needs tools but not obviously which
    "LOCAL_GENERAL": ("file", "terminal", "clarify"),
}

DEFAULT_PROFILE = "LOCAL_GENERAL"

_unknown = {t for ts in PROFILES.values() for t in ts if t not in REGISTRY}
if _unknown:
    raise RuntimeError(f"unknown toolset(s) in PROFILES: {sorted(_unknown)}")


# Profile selection is deterministic and orders itself most-specific first.
# It chooses only how much CAPABILITY the turn is handed; it can never widen
# what the security gate already decided, and every profile still runs inside
# the same agent with the same approvals.deny globs.
_SELECTORS: tuple[tuple[str, "re.Pattern[str]"], ...] = ()


def _compile_selectors():
    import re as _re
    return (
        # Files before system: 「このフォルダの空き容量」 is a disk question, but
        # 「このフォルダの中身」 is a listing, and the listing verbs are the
        # narrower signal.
        ("LOCAL_FILES_READ", _re.compile(
            r"(ファイル|フォルダ|ディレクトリ|一覧|中身|リスト|"
            r"読んで|開いて|表示して|ls|directory|folder)", _re.IGNORECASE)),
        ("LOCAL_SYSTEM", _re.compile(
            r"(空き容量|ストレージ|ディスク|容量|メモリ|ram|cpu|プロセス|"
            r"バッテリー|温度|負荷|使用率|バージョン|uptime|システム|mac|マック)",
            _re.IGNORECASE)),
        ("LOCAL_TIME", _re.compile(
            r"(今日|本日|明日|昨日|日付|何日|何時|時刻|時間|曜日|now|date|time)",
            _re.IGNORECASE)),
    )


_SELECTORS = _compile_selectors()


def select(text: str) -> str:
    """Pick the narrowest profile that can still answer. Unknown -> GENERAL."""
    for name, pat in _SELECTORS:
        if pat.search(text):
            return name
    return DEFAULT_PROFILE


def toolsets(profile: str) -> tuple[str, ...]:
    return PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])


def toolsets_arg(profile: str) -> str:
    """The value for `hermes -t`. Empty string means 'no toolsets'."""
    return ",".join(toolsets(profile))


def cost(profile: str) -> dict:
    ts = toolsets(profile)
    count = sum(REGISTRY[t][0] for t in ts)
    schema = sum(REGISTRY[t][1] for t in ts)
    return {
        "profile": profile,
        "toolsets": list(ts),
        "tool_count": count,
        "tool_schema_bytes": schema,
        "system_prompt_bytes": FULL_SYSTEM_BYTES,
        "total_prompt_bytes": FULL_SYSTEM_BYTES + schema,
        "saved_vs_full_bytes": FULL_TOOL_SCHEMA_BYTES - schema,
    }


def table() -> str:
    full = FULL_SYSTEM_BYTES + FULL_TOOL_SCHEMA_BYTES
    rows = [f"{'profile':<18}{'tools':>6}{'schema B':>10}{'total B':>10}{'vs full':>10}",
            f"{'FULL (agent)':<18}{FULL_TOOL_COUNT:>6}{FULL_TOOL_SCHEMA_BYTES:>10}{full:>10}{'-':>10}"]
    for name in PROFILES:
        c = cost(name)
        pct = 100.0 * c["total_prompt_bytes"] / full
        rows.append(f"{name:<18}{c['tool_count']:>6}{c['tool_schema_bytes']:>10}"
                    f"{c['total_prompt_bytes']:>10}{pct:>9.0f}%")
    return "\n".join(rows)


if __name__ == "__main__":
    if "--json" in sys.argv:
        print(json.dumps({n: cost(n) for n in PROFILES}, ensure_ascii=False, indent=2))
    else:
        print(table())
