"""LOCAL_FAST — stable knowledge and conversation, with no agent underneath.

    utterance ──► gemma4:e2b, minimal system prompt, no tools
                       │
                       ├─ {"status":"ANSWER",    "answer": "..."}  ─► speak it
                       └─ {"status":"NEED_TOOL", "answer": ""}     ─► LOCAL_TOOL

WHAT THIS IS FOR
  Greetings, thanks, and knowledge that does not change: "Pythonとは",
  "HTTPとHTTPSの違い", "再帰を説明して". Measured at 2.8-3.5 s against the full
  agent's 7-11 s, because it sends roughly 400 bytes of system prompt instead of
  the agent's ~64 KB of prompt and tool schemas.

WHAT IT MUST NEVER DO
  Answer anything that depends on the world right now -- the date, the time, the
  weather, the news, this Mac's disk or memory, a file, a repository, the user's
  own data. It has no tools and no timestamp, so an answer to any of those would
  be invented. Measured, and the reason NEED_TOOL exists: asked "今日は何日" with
  a bare system prompt, gemma4:e2b answered "本日は2024年5月16日です" with
  complete confidence.

THREE LAYERS KEEP THAT FROM REACHING THE USER, and only the middle one is a
model:
  1. jarvis_router demotes anything matching a deterministic current-fact
     pattern to LOCAL_TOOL before this module is ever called.
  2. the system prompt below requires NEED_TOOL for exactly those categories.
  3. _looks_like_current_fact() re-checks the ANSWER on the way out and
     converts it to NEED_TOOL. A model that ignores instruction 2 still cannot
     get an invented date to the speaker.

This module executes nothing. No filesystem, no network beyond the loopback
Ollama call, no shell. It is a text-in/text-out function by construction, which
is why it is safe to reach without the agent's approval machinery.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

OLLAMA = os.environ.get("JARVIS_OLLAMA", "http://127.0.0.1:11434/v1/chat/completions")
MODEL = os.environ.get("JARVIS_FAST_MODEL", "gemma4:e2b")
TIMEOUT = float(os.environ.get("JARVIS_FAST_TIMEOUT", "30"))
MAX_TOKENS = int(os.environ.get("JARVIS_FAST_MAX_TOKENS", "220"))

# Deliberately small. Every byte here is paid on every LOCAL_FAST turn, and the
# whole point of the route is that this is ~400 B where the agent is ~64 KB.
SYSTEM = (
    "あなたはJarvis。音声で読み上げられる短い日本語で答えます。\n"
    "規則:\n"
    "1. 1〜3文。箇条書き・記号・コードブロックは使わない。\n"
    "2. 変化しない一般知識と挨拶にだけ答える。\n"
    "3. 次は絶対に答えず status を NEED_TOOL にする: 今日の日付、現在時刻、"
    "天気、ニュース、株価、最新情報、このMacの状態、CPU、メモリ、ディスク、"
    "プロセス、ファイル、Git、カレンダー、メール、Web、ユーザー個人のデータ。\n"
    "4. 推測で現在の状況を述べない。分からなければ NEED_TOOL。\n"
    "5. 出力は次のJSONのみ:\n"
    '{"status":"ANSWER","answer":"..."} または {"status":"NEED_TOOL","answer":""}'
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

# Layer 3. Deliberately matches the SHAPE of a current-fact claim rather than
# its topic: a concrete date, clock time, temperature, percentage or byte size
# is something this route has no way to know. Topic filtering is layer 1's job
# and it runs on the utterance; this runs on the answer.
_CURRENT_FACT_SHAPE = re.compile(
    r"(20\d{2}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}\s*時\s*\d{1,2}\s*分|"
    r"\d+(\.\d+)?\s*(GB|TB|MB|ギガ|テラ)|"
    r"(現在|本日|今日|ただいま)\s*(の)?(日付|時刻|時間)は)",
    re.IGNORECASE)

NEED_TOOL = "NEED_TOOL"
ANSWER = "ANSWER"


def _looks_like_current_fact(answer: str) -> bool:
    """A last check on the way out, on the answer rather than the question."""
    return bool(_CURRENT_FACT_SHAPE.search(answer))


def _parse(raw: str) -> tuple[str, str]:
    """Read the model's JSON, tolerating the wrappers small models add.

    A model that returns prose instead of JSON is treated as NEED_TOOL rather
    than having its prose spoken: unparseable output is exactly the case where
    we cannot tell whether rule 3 was followed.
    """
    m = _JSON_BLOCK.search(raw)
    if not m:
        return NEED_TOOL, ""
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return NEED_TOOL, ""
    if not isinstance(d, dict):
        return NEED_TOOL, ""
    status = str(d.get("status") or "").strip().upper()
    answer = str(d.get("answer") or "").strip()
    if status != ANSWER or not answer:
        return NEED_TOOL, ""
    return ANSWER, answer


def ask(utterance: str) -> dict:
    """Returns {status, answer, latency, raw, demoted}. Never raises."""
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": utterance}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "reasoning_effort": "none",   # the only flag Ollama's /v1 honours
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.load(r)
        raw = (d["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        # An unreachable or slow Ollama must fall back, not surface an error to
        # the speaker. LOCAL_TOOL can still answer.
        return {"status": NEED_TOOL, "answer": "", "demoted": "transport",
                "latency": round(time.perf_counter() - t0, 3),
                "raw": f"error:{type(e).__name__}"}

    dt = round(time.perf_counter() - t0, 3)
    status, answer = _parse(raw)
    demoted = ""
    if status == ANSWER and _looks_like_current_fact(answer):
        status, answer, demoted = NEED_TOOL, "", "current_fact_shape"
    return {"status": status, "answer": answer, "demoted": demoted,
            "latency": dt, "raw": raw[:200]}


def system_prompt_bytes() -> int:
    return len(SYSTEM.encode("utf-8"))


if __name__ == "__main__":
    if "--bytes" in sys.argv:
        print(json.dumps({"fast_system_bytes": system_prompt_bytes(),
                          "fast_tool_schema_bytes": 0, "tool_count": 0},
                         ensure_ascii=False))
        raise SystemExit(0)
    for it in sys.argv[1:] or [l.strip() for l in sys.stdin if l.strip()]:
        print(json.dumps(ask(it), ensure_ascii=False))
