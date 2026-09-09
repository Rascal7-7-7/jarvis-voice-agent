"""SECTION 8 — is LOCAL_FAST's output actually speakable?

The route is only worth having if what comes back can be read aloud. Checked
per the brief: empty, echo, reasoning leakage, markdown, over-length, unnatural
Japanese, and invented current facts.
"""
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))

import jarvis_local_fast as F  # noqa: E402

QUESTIONS = ["こんにちは", "ありがとう", "Pythonとは？",
             "HTTPとHTTPSの違い", "REST APIとは？", "Dockerとは？"]
REPS = 4                       # 24 runs, above the brief's minimum of 20
SPEAKABLE_MAX_CHARS = 200      # ~3 spoken sentences of Japanese

MARKDOWN = re.compile(r"(```|\*\*|^\s*[-*+]\s|^\s*#{1,6}\s|\|\s*-{3,})", re.MULTILINE)
REASONING = re.compile(r"(<think>|</think>|思考|まず考え|Let me think|Step 1|"
                       r"ユーザーは|analysis:)", re.IGNORECASE)
# Latin letters are fine in a Japanese answer (Python, HTTP); a whole Latin
# SENTENCE means it answered in English.
ENGLISH_SENTENCE = re.compile(r"[A-Za-z][A-Za-z ,'\-]{40,}")
JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龠]")


def check(q: str, r: dict) -> list[str]:
    a = r["answer"]
    bad = []
    if r["status"] != "ANSWER":
        return ["NEED_TOOL"]          # not a defect for these, but not an answer
    if not a.strip():
        bad.append("EMPTY")
    if a.strip() == q.strip():
        bad.append("ECHO")
    if MARKDOWN.search(a):
        bad.append("MARKDOWN")
    if REASONING.search(a):
        bad.append("REASONING_LEAK")
    if len(a) > SPEAKABLE_MAX_CHARS:
        bad.append(f"TOO_LONG({len(a)})")
    if not JAPANESE.search(a):
        bad.append("NOT_JAPANESE")
    if ENGLISH_SENTENCE.search(a):
        bad.append("ENGLISH_SENTENCE")
    if F._looks_like_current_fact(a):
        bad.append("CURRENT_FACT")
    return bad


def main() -> int:
    runs, lat, defects, lens = 0, [], [], []
    print(f"{'question':<20}{'rep':>4}{'s':>7}{'chars':>7}  verdict")
    print("-" * 74)
    for q in QUESTIONS:
        for i in range(1, REPS + 1):
            r = F.ask(q)
            runs += 1
            lat.append(r["latency"])
            bad = check(q, r)
            if r["status"] == "ANSWER":
                lens.append(len(r["answer"]))
            if bad and bad != ["NEED_TOOL"]:
                defects.append((q, bad, r["answer"][:60]))
            print(f"{q[:18]:<20}{i:>4}{r['latency']:>7.2f}{len(r['answer']):>7}  "
                  f"{'OK' if not bad else '/'.join(bad)}")
    lat.sort()
    print("\n" + "-" * 74)
    print(f"runs           = {runs}")
    print(f"latency        = median {statistics.median(lat):.2f}s  "
          f"min {lat[0]:.2f}s  max {lat[-1]:.2f}s   target < 4s")
    print(f"answer length  = median {statistics.median(lens):.0f} chars"
          if lens else "answer length  = n/a")
    print(f"defects        = {len(defects)}/{runs}")
    for q, bad, sample in defects:
        print(f"  [{'/'.join(bad):<22}] {q}  ->  {sample!r}")

    json.dump({"runs": runs, "median_latency": statistics.median(lat),
               "max_latency": lat[-1], "defects": len(defects),
               "median_answer_chars": statistics.median(lens) if lens else None},
              open(os.path.join(HERE, "local_fast_quality.json"), "w"), indent=1)
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
