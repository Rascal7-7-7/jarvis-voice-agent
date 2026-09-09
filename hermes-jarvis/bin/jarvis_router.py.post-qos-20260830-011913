"""JARVIS router — deterministic gate first, LLM second.

    utterance
       │
       ▼  normalize (NFKC, whitespace)
    [1] jarvis_gate.check()        ← deterministic. CONFIRMATION_REQUIRED stops here.
       │  safe
       ▼
    [2] explicit user override     ← deterministic. 「Codexに見てもらって」 etc.
       │  none
       ▼
    [3] gemma4:e2b, reasoning_effort=none, enum-constrained
       │
       ▼  LOCAL | WEB | CODEX | CLAUDE

The LLM is step 3 and only step 3. It never sees a dangerous utterance and it
cannot un-gate one, because step 1 returns before it runs. `reason` from the
model is carried for logging and is NEVER consulted for a safety decision.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jarvis_gate  # noqa: E402

OLLAMA = os.environ.get("JARVIS_OLLAMA", "http://127.0.0.1:11434/v1/chat/completions")
MODEL = os.environ.get("JARVIS_ROUTER_MODEL", "gemma4:e2b")
TIMEOUT = float(os.environ.get("JARVIS_ROUTER_TIMEOUT", "20"))

LABELS = ("LOCAL_FAST", "LOCAL_TOOL", "LOCAL", "WEB", "CODEX", "CLAUDE",
          "CONFIRMATION_REQUIRED")

SYSTEM = (
    "あなたはJarvisのルーターです。ユーザーの発話を次の5つのいずれか1つに分類します。\n"
    "LOCAL_FAST : 挨拶・お礼・雑談、および変化しない一般知識の説明。\n"
    "             例: こんにちは / ありがとう / Pythonとは / HTTPとHTTPSの違い\n"
    "LOCAL_TOOL : このMacの状態・日付・時刻・ファイル・ディレクトリ・Gitなど、\n"
    "             端末を調べないと答えられないもの。\n"
    "WEB        : 最新情報・ニュース・調べ物。インターネット検索が必要なもの。\n"
    "CODEX      : コードの修正・実装・デバッグ・エラー解決・テスト・コードレビュー。\n"
    "CLAUDE     : 設計・アーキテクチャ比較・脅威モデリング・長い推論・多面的な検討。\n"
    "迷ったら LOCAL_FAST ではなく LOCAL_TOOL を選びます。\n"
    "ラベルを1語だけ出力してください。説明は不要です。"
)

# ---- the LOCAL split -------------------------------------------------------
#
# LOCAL_FAST has no tools, no timestamp and no filesystem, so anything that
# depends on the world right now would be invented. Measured: asked for today's
# date with a bare system prompt, gemma4:e2b answered "本日は2024年5月16日です".
#
# This pattern is DETERMINISTIC and runs on the utterance, after the security
# gate and independently of whatever the model decided. It only ever moves a
# turn FAST -> TOOL: the safe direction. Nothing here can promote a turn, and
# nothing here can un-gate one.
_CURRENT_FACT = re.compile(
    r"(今日|本日|昨日|明日|今|現在|最新|今週|今月|今年)|"
    r"(日付|何日|何時|時刻|時間|曜日|カレンダー|予定|スケジュール)|"
    r"(天気|気温|weather|ニュース|news|株価|為替|レート|相場)|"
    r"(mac|マック|パソコン|pc|端末|システム|os)|"
    r"(cpu|メモリ|ram|ディスク|空き容量|ストレージ|容量|プロセス|バッテリー|"
    r"温度|負荷|使用率)|"
    r"(ファイル|フォルダ|ディレクトリ|一覧|中身|パス|file|folder|directory|ls)|"
    r"(git|コミット|ブランチ|リポジトリ|差分|ステータス)|"
    r"(メール|mail|gmail|受信|送信|通知)|"
    r"(私の|自分の|うちの|この(マシン|環境|プロジェクト|フォルダ|ディレクトリ))",
    re.IGNORECASE)

# ---- 0 ms LOCAL_FAST. Greetings and thanks only.
#
# Kept deliberately narrow. The brief warns against regexing "stable knowledge"
# broadly, and it is right: "Pythonとは" is stable, but "このプロジェクトの
# Pythonのバージョンは" is not, and no wording rule separates them reliably.
# Those go to the model, which then gets checked by _CURRENT_FACT anyway.
#
# Spelling here is deliberately loose, because these arrive from SPEECH
# RECOGNITION, not from a keyboard. Measured in a real turn: 「こんにちは」 came
# back from faster-whisper as 「こんにちわ」 — phonetically identical, and a
# perfectly ordinary way to write it — which missed the fast path and cost a
# 2.379 s round trip to the router model to conclude the obvious.
#
# Widening the spelling of a GREETING is safe in a way that widening "stable
# knowledge" is not: a greeting carries no object, so there is nothing for it
# to be current-fact ABOUT. And every match still passes through split_local(),
# so even a mis-fire here cannot answer a current-fact question.
_FAST_SAFE = re.compile(
    r"^(?:\s*)("
    # こんにちは / こんにちわ / こんちは / こんちわ / こにちは …
    r"こん[にち]*[はわ]|こんばん[はわ]|"
    r"おは(よう|よー)?(ございます)?|おっす|うぃっす|"
    r"やあ|やっほ[ーう]?|はじめまして|"
    r"ありがと[うー]?(ございます|ございました)?|"
    r"どうも(ありがと[うー]?)?|サンキュー|さんきゅ[ーう]?|"
    r"おやすみ(なさい)?|お疲れ(様|さま)?(です|でした)?|"
    r"よろしく(お願い(します|いたします))?|"
    r"ハロー|ハーイ|thanks|thank\s*you|hello|hi|hey|yo"
    r")(?:\s*[。、,.!！?？…~〜ー\s]*)$",
    re.IGNORECASE)


def split_local(text: str, proposed: str) -> tuple[str, str]:
    """Decide LOCAL_FAST vs LOCAL_TOOL. Returns (route, why).

    `proposed` is what the previous stage wants. This function may only make it
    safer, never freer.
    """
    if _CURRENT_FACT.search(text):
        # Demote regardless of who proposed FAST -- the model, a fast path, or
        # an explicit user override. "ローカルで今日の日付を" must not be able to
        # talk its way into a route that would invent one.
        return "LOCAL_TOOL", "current_fact_pattern"
    if proposed == "LOCAL_FAST":
        return "LOCAL_FAST", "kept"
    # Bare LOCAL is the old label and any unrecognised local-ish verdict:
    # resolve to the capable side, never the guessing side.
    return "LOCAL_TOOL", "ambiguous_default"

# ---- step 2: explicit overrides. Deterministic, so the user always wins over
# the model — but never over step 1.
_OVERRIDES = (
    ("CODEX", re.compile(r"(コーデックス|codex)", re.IGNORECASE)),
    ("CLAUDE", re.compile(r"(クロード|claude)", re.IGNORECASE)),
    ("WEB", re.compile(r"(ウェブで|webで|ネットで|検索して|ググって|クロームで|chromeで)", re.IGNORECASE)),
    ("LOCAL", re.compile(r"(ローカルだけ|ローカルで答え|オフラインで)", re.IGNORECASE)),
)

# ---- step 2b: high-precision deterministic fast paths.
#
# Two reasons this exists, both measured:
#   latency — the LLM step costs ~2.2 s on gemma4:e2b and does NOT shrink with
#     max_tokens (24 -> 2 changed nothing: 2.28 s -> 2.19 s). The only way under
#     that floor is to not call the model.
#   accuracy — 「このテストが落ちる原因を調べて」 routed to WEB because 調べて is
#     the canonical WEB verb, even though a failing test is a CODEX task.
#
# Only unambiguous signals belong here. Anything debatable stays with the LLM.
_FAST = (
    # WEB is evaluated FIRST: an explicit WEB signal (最新バージョン, ニュース…)
    # should win over a weaker CODEX hint in the same utterance.
    ("WEB", re.compile(
        r"(ニュース|天気|為替|円安|円高|株価|相場|時事|"
        r"最新(の)?(情報|動向|バージョン|リリース))", re.IGNORECASE)),
    ("CODEX", re.compile(
        r"(エラー|バグ|デバッグ|例外|スタックトレース|コンパイル|型エラー|"
        r"テストが落ち|テストが通らな|ビルドが通らな|リファクタ|"
        r"\.(py|tsx|jsx|php|rb|go|rs|sql|java|kt|swift)(?![A-Za-z0-9_])|"
        # NOTE: .js/.ts deliberately absent — they match inside product
        # names (Next.js, Node.js, Vue.js). Measured: "Next.jsの最新
        # バージョンは?" was routed CODEX by the .js branch.
        
        r"(?<![A-Za-z0-9_])(traceback|exception|syntaxerror|typeerror)(?![A-Za-z0-9_]))",
        re.IGNORECASE)),
)


def _llm_route(text: str) -> tuple[str | None, float, str]:
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": text}],
        "max_tokens": 24,
        "temperature": 0,
        "reasoning_effort": "none",   # the only flag Ollama's /v1 honours
    }
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.load(r)
        raw = (d["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        return None, time.perf_counter() - t0, f"llm_error:{type(e).__name__}"
    dt = time.perf_counter() - t0
    up = raw.upper()
    for lab in sorted(LABELS, key=len, reverse=True):
        if lab in up:
            return lab, dt, raw
    return None, dt, raw


# Paths and URLs are DATA, not intent. Measured: an utterance containing
# /private/tmp/claude-502/... matched the CLAUDE override and was delegated to
# Claude purely because the sandbox path happens to contain the word "claude".
# Strip those spans before looking for an explicit override or a fast path.
_PATHLIKE = re.compile(r"(https?://\S+|~?/[^\s\"']+|[A-Za-z]:\\\\[^\s\"']+)")


def _intent_text(text: str) -> str:
    return _PATHLIKE.sub(" ", text)


def route(utterance: str) -> dict:
    t0 = time.perf_counter()
    text = jarvis_gate.normalize(utterance)
    intent = _intent_text(text)

    gated = jarvis_gate.check(text)
    if gated["route"]:
        return {"route": "CONFIRMATION_REQUIRED", "decided_by": "security_gate",
                "categories": gated["categories"], "matched": gated["matched"],
                "llm_used": False, "latency": round(time.perf_counter() - t0, 3),
                "utterance": text}

    # 0 ms LOCAL_FAST: greetings and thanks. Still passed through split_local,
    # so even this cannot outrank the current-fact demotion.
    if _FAST_SAFE.match(intent):
        lab, why = split_local(text, "LOCAL_FAST")
        return {"route": lab, "decided_by": "fast_path_greeting",
                "local_split": why, "categories": [], "llm_used": False,
                "latency": round(time.perf_counter() - t0, 3), "utterance": text}

    for lab, pat in _OVERRIDES:
        if pat.search(intent):
            if lab == "LOCAL":
                lab, why = split_local(text, "LOCAL_TOOL")
                return {"route": lab, "decided_by": "explicit_override",
                        "local_split": why, "categories": [], "llm_used": False,
                        "latency": round(time.perf_counter() - t0, 3),
                        "utterance": text}
            return {"route": lab, "decided_by": "explicit_override",
                    "categories": [], "llm_used": False,
                    "latency": round(time.perf_counter() - t0, 3), "utterance": text}

    for lab, pat in _FAST:
        if pat.search(intent):
            return {"route": lab, "decided_by": "fast_path",
                    "categories": [], "llm_used": False,
                    "latency": round(time.perf_counter() - t0, 3), "utterance": text}

    lab, llm_dt, raw = _llm_route(text)
    total = time.perf_counter() - t0
    if lab is None or lab == "CONFIRMATION_REQUIRED":
        # The model must not be able to invent a CONFIRMATION verdict (that is
        # the gate's job) nor leave us without one. Unparseable -> LOCAL_TOOL,
        # which is the capable destination rather than the guessing one.
        return {"route": "LOCAL_TOOL", "decided_by": "llm_fallback",
                "local_split": "unparseable", "categories": [], "llm_used": True,
                "llm_raw": raw[:60], "llm_latency": round(llm_dt, 3),
                "latency": round(total, 3), "utterance": text}
    if lab.startswith("LOCAL"):
        lab, why = split_local(text, lab)
        return {"route": lab, "decided_by": "llm", "local_split": why,
                "categories": [], "llm_used": True, "llm_raw": raw[:60],
                "llm_latency": round(llm_dt, 3), "latency": round(total, 3),
                "utterance": text}
    return {"route": lab, "decided_by": "llm", "categories": [], "llm_used": True,
            "llm_raw": raw[:60], "llm_latency": round(llm_dt, 3),
            "latency": round(total, 3), "utterance": text}


if __name__ == "__main__":
    items = sys.argv[1:] or [l.strip() for l in sys.stdin if l.strip()]
    for it in items:
        print(json.dumps(route(it), ensure_ascii=False))
