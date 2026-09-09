"""Deterministic security gate for JARVIS — runs BEFORE the LLM router.

WHY THIS EXISTS SEPARATELY FROM approvals.deny
----------------------------------------------
`approvals.deny` in ~/.hermes/config.yaml matches SHELL COMMAND STRINGS at
execution time. The router's input is an UTTERANCE. 「このファイル全部消して」
never matches `*rm -rf *`, so the command-level deny list cannot classify it.

So there are two independent deterministic layers, and the LLM sits between
them rather than guarding either:

    utterance ─► [1] this gate ─► LLM router ─► tool call ─► [2] approvals.deny

The Brain Benchmark measured gemma4:e2b routing 「gitで強制プッシュして」 to
CODEX instead of CONFIRMATION_REQUIRED (dangerous_action_accuracy 0.75). That
is precisely why the LLM is not permitted to be the boundary.

This module is pure pattern matching. No model, no network, no I/O.
"""
from __future__ import annotations

import re
import unicodedata

CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"

# Each entry: (category, compiled pattern). Japanese and English, because STT
# output is Japanese but users mix in English verbs and command names.
_RULES: list[tuple[str, re.Pattern]] = []


def _add(category: str, *patterns: str) -> None:
    for p in patterns:
        _RULES.append((category, re.compile(p, re.IGNORECASE)))


def _w(token: str) -> str:
    r"""Word-boundary for an ASCII token that may sit next to Japanese.

    `\b` is wrong here. In `sudoで再起動して`, Python's `\b` finds no boundary
    between `o` and `で` because Unicode classifies `で` as a word character —
    so `\bsudo\b` silently fails to match, and a privilege-escalation utterance
    slips past the gate. (Measured: this exact string passed before the fix.)
    Anchor on ASCII word characters only.
    """
    return r"(?<![A-Za-z0-9_])" + token + r"(?![A-Za-z0-9_])"


# ---- destructive: delete / overwrite / reset -------------------------------
_add("DESTRUCTIVE",
     r"消して", r"消去", r"削除", r"消しといて", r"消しちゃって", r"抹消",
     r"initialize|初期化", r"まっさら", r"全部消",
     r"(?<![A-Za-z0-9_])delete(?![A-Za-z0-9_])", r"(?<![A-Za-z0-9_])remove(?![A-Za-z0-9_])", r"(?<![A-Za-z0-9_])wipe(?![A-Za-z0-9_])", r"(?<![A-Za-z0-9_])purge(?![A-Za-z0-9_])", r"(?<![A-Za-z0-9_])destroy(?![A-Za-z0-9_])",
     r"(?<![A-Za-z0-9_])rm(?![A-Za-z0-9_])", r"(?<![A-Za-z0-9_])rmdir(?![A-Za-z0-9_])", r"(?<![A-Za-z0-9_])unlink(?![A-Za-z0-9_])",
     r"上書き", r"overwrite",
     r"リセット", r"(?<![A-Za-z0-9_])reset(?![A-Za-z0-9_])",
     r"フォーマット", r"(?<![A-Za-z0-9_])format(?![A-Za-z0-9_])", r"(?<![A-Za-z0-9_])mkfs(?![A-Za-z0-9_])",
     r"ドロップ", r"(?<![A-Za-z0-9_])drop\s+(table|database|db)(?![A-Za-z0-9_])")

# ---- git: history rewriting / publishing -----------------------------------
_add("GIT_DESTRUCTIVE",
     r"強制プッシュ", r"force\s*push", r"push\s+--?f(?![A-Za-z0-9_])", r"push\s+--force",
     r"reset\s+--hard", r"ハードリセット",
     r"clean\s+-[a-z]*f", r"(?<![A-Za-z0-9_])git\s+clean(?![A-Za-z0-9_])",
     r"リベート|リベース.*強制|rebase.*--force",
     r"ブランチ.*削除", r"branch\s+-D(?![A-Za-z0-9_])")

_add("EXTERNAL_SIDE_EFFECT",
     r"プッシュして", r"(?<![A-Za-z0-9_])push(?![A-Za-z0-9_])", r"(?<![A-Za-z0-9_])deploy(?![A-Za-z0-9_])", r"デプロイ",
     r"公開して", r"publish", r"リリースして", r"(?<![A-Za-z0-9_])release(?![A-Za-z0-9_])",
     r"送信して", r"メール.*送", r"(?<![A-Za-z0-9_])send(?![A-Za-z0-9_]).*(?<![A-Za-z0-9_])(mail|email|slack|dm)(?![A-Za-z0-9_])",
     r"投稿して", r"ツイート", r"(?<![A-Za-z0-9_])post(?![A-Za-z0-9_]).*(?<![A-Za-z0-9_])(x|twitter|slack)(?![A-Za-z0-9_])",
     r"マージして", r"(?<![A-Za-z0-9_])merge(?![A-Za-z0-9_])", r"プルリク.*作", r"(?<![A-Za-z0-9_])pr(?![A-Za-z0-9_]).*作成")

# ---- credentials / secrets --------------------------------------------------
_add("SECRET_ACCESS",
     r"パスワード", r"password", r"credential", r"資格情報",
     r"秘密鍵", r"private\s*key", r"ssh\s*key", r"id_rsa", r"id_ed25519",
     r"キーチェーン", r"keychain",
     r"api\s*key", r"APIキー", r"エーピーアイキー",
     r"アクセストークン", r"access\s*token", r"トークンを?(教え|見せ|表示|出し)",
     r"シークレット", r"secret", r"クレデンシャル", r"(?<![A-Za-z0-9_])token(?![A-Za-z0-9_])",
     r"\.env(?![A-Za-z0-9_])", r"環境変数.*(見せ|表示|教え)",
     r"auth\.json")

# ---- privilege / system mutation -------------------------------------------
_add("PRIVILEGE",
     r"(?<![A-Za-z0-9_])sudo(?![A-Za-z0-9_])", r"管理者権限", r"root権限", r"権限を(上げ|昇格)",
     r"chmod", r"chown", r"パーミッション.*変更",
     r"csrutil", r"sip.*(無効|disable)", r"gatekeeper.*(無効|disable)",
     r"filevault.*(無効|disable)", r"tccutil")

_add("SYSTEM_CHANGE",
     r"(?<![A-Za-z0-9_])(brew|apt|yum|pip|npm|gem|cargo)\s+(install|uninstall|remove|upgrade)(?![A-Za-z0-9_])",
     r"インストールして", r"アンインストール",
     r"グローバルに.*入れて", r"システム.*設定.*変更",
     r"launchd|launchctl", r"再起動して.*(mac|システム)", r"シャットダウン")

_add("CLOUD_MUTATION",
     r"(?<![A-Za-z0-9_])(aws|gcloud|az|terraform|kubectl)(?![A-Za-z0-9_])",
     r"クラウド.*(削除|作成|変更)", r"インスタンス.*(削除|停止|作成)",
     r"本番.*(反映|適用|デプロイ)", r"production.*(deploy|apply)")

# Utterances that merely ASK ABOUT a dangerous CONCEPT are not themselves
# dangerous. "rm の使い方を教えて" should reach LOCAL, not the gate.
#
# Deliberately narrow: only conceptual markers. 「教えて」「見せて」「表示して」
# are NOT here — 「APIキーを教えて」 is a request to reveal a secret, not a
# question about one, and an earlier draft that treated 教えて as conceptual
# let exactly that utterance through.
_INQUIRY = re.compile(
    r"(とは|とは何|意味は?|使い方|違いは|なぜ|どうやって|方法は|"
    r"what\s+is|how\s+to|why)",
    re.IGNORECASE)
# ...unless it is clearly an imperative on a concrete target.
_IMPERATIVE = re.compile(r"(して(ください)?$|しといて|してくれ|やって|実行して)")

# Categories where "asking" IS the dangerous act — no inquiry exemption,
# UNLESS the utterance is strictly definitional (see _REVEAL below).
_NEVER_EXEMPT = {"SECRET_ACCESS"}

# Verbs that turn a mention of a secret into a request to disclose it.
# 「APIキーとは何ですか」 has none of these and is a definition question;
# 「APIキーを教えて」 has 教え and must be gated. When in doubt the gate asks
# for confirmation rather than staying silent — a needless prompt costs a
# second, a disclosed credential cannot be undone.
_REVEAL = re.compile(r"(教え|見せ|表示|出して|取って|コピー|読んで|中身|確認して)")


def normalize(text: str) -> str:
    """NFKC + collapse whitespace. STT output varies in width and spacing."""
    t = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", t).strip()


def check(utterance: str) -> dict:
    """Classify an utterance BEFORE the LLM sees it.

    Returns {"route": ...} — either CONFIRMATION_REQUIRED with the matched
    categories, or {"route": None} meaning "safe to hand to the LLM router".
    """
    text = normalize(utterance)
    hits: list[tuple[str, str]] = []
    for category, pat in _RULES:
        m = pat.search(text)
        if m:
            hits.append((category, m.group(0)))

    if not hits:
        return {"route": None, "categories": [], "matched": [], "normalized": text}

    cats_hit = {c for c, _ in hits}
    # A pure question about a dangerous CONCEPT is not a dangerous request.
    # For secret-bearing categories that only holds when no disclosure verb is
    # present, so a definition question passes but a reveal request does not.
    secret_blocked = bool(cats_hit & _NEVER_EXEMPT) and bool(_REVEAL.search(text))
    if (_INQUIRY.search(text) and not _IMPERATIVE.search(text)
            and not secret_blocked):
        return {"route": None, "categories": [], "matched": [],
                "normalized": text, "note": "inquiry_about_dangerous_topic"}

    cats = sorted({c for c, _ in hits})
    return {"route": CONFIRMATION_REQUIRED, "categories": cats,
            "matched": [m for _, m in hits], "normalized": text}


if __name__ == "__main__":
    import json
    import sys
    for line in sys.argv[1:] or [l.strip() for l in sys.stdin if l.strip()]:
        print(json.dumps({"input": line, **check(line)}, ensure_ascii=False))
