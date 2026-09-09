"""PHASE 7 — greeting normalization.

Two halves, and the second matters more:
  MUST_FAST  spelling variants a speech recogniser plausibly produces
  MUST_NOT   things that merely start like a greeting, or contain one, but are
             actually a task or a current-fact question

The rule being tested is that widening greeting SPELLING did not widen greeting
SCOPE.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))

import jarvis_gate  # noqa: E402
import jarvis_router as R  # noqa: E402

MUST_FAST = [
    "こんにちは", "こんにちわ", "こんちは", "こんちわ", "こんにちは。", "こんにちは！",
    "こんばんは", "こんばんわ",
    "おはよう", "おはようございます", "おはよー", "おは",
    "やあ", "やっほー", "やっほう", "はじめまして",
    "ありがとう", "ありがとうございます", "ありがとうございました", "ありがとー",
    "どうも", "どうもありがとう", "サンキュー", "さんきゅー",
    "おやすみ", "おやすみなさい",
    "お疲れ様です", "お疲れさま", "お疲れ様でした", "お疲れ",
    "よろしく", "よろしくお願いします",
    "ハロー", "ハーイ", "hello", "Hello", "hi", "Hi", "hey", "yo",
    "thanks", "thank you", "Thank You",
    "おっす", "うぃっす",
    "こんにちは  ", "  ありがとう", "こんにちは〜", "ありがとう！！",
    "hello.", "hi!",
]

# Must NOT take the 0 ms greeting path -- either because they are a real task,
# or because they are a current-fact question wearing a greeting's clothes.
MUST_NOT = [
    "こんにちは、今日の天気を教えて",
    "おはよう、今日の予定は？",
    "ありがとう、次はMacの空き容量を見せて",
    "こんにちはという挨拶を英語でなんと言う",
    "hello worldをPythonで書いて",
    "やあ、このバグを直して",
    "お疲れ様、gitのステータスを見せて",
    "こんばんは、明日の天気は",
    "どうも、このディレクトリの一覧を見せて",
    "よろしく、CPUの使用率を教えて",
]


def main() -> int:
    bad = []
    print(f"{'utterance':<34}{'route':<14}{'decided_by':<22}verdict")
    print("-" * 84)

    for u in MUST_FAST:
        d = R.route(u)
        ok = d["route"] == "LOCAL_FAST" and d["decided_by"] == "fast_path_greeting"
        if not ok:
            bad.append((u, d, "want LOCAL_FAST via fast_path_greeting"))
        print(f"{u[:32]:<34}{d['route']:<14}{d['decided_by']:<22}{'ok' if ok else 'BAD'}")

    print()
    for u in MUST_NOT:
        d = R.route(u)
        # It may go anywhere sensible -- but never via the greeting fast path,
        # and never to LOCAL_FAST if it asks about the world right now.
        greeted = d["decided_by"] == "fast_path_greeting"
        cf = bool(R._CURRENT_FACT.search(jarvis_gate.normalize(u)))
        leaked = cf and d["route"] == "LOCAL_FAST"
        ok = not greeted and not leaked
        if not ok:
            bad.append((u, d, "greeting path or current-fact leak"))
        print(f"{u[:32]:<34}{d['route']:<14}{d['decided_by']:<22}{'ok' if ok else 'BAD'}")

    n = len(MUST_FAST) + len(MUST_NOT)
    print("-" * 84)
    print(f"cases = {n}  (must-fast {len(MUST_FAST)}, must-not {len(MUST_NOT)})")
    print(f"passed = {n - len(bad)}/{n}")
    for u, d, why in bad:
        print(f"  BAD {u!r} -> {d['route']} by {d['decided_by']}  ({why})")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
