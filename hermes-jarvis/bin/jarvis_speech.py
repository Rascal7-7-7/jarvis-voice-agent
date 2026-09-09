"""読み上げ直前のサニタイズ。

なぜ必要か（2026-09-09 17:26 実測）
----------------------------------
``LOCAL_TOOL`` / ``WEB`` 経路は C8 の読み上げ整形を通らない。C8 は
``jarvis-dispatch`` の ``esac`` の**後**にあり、``CODEX`` / ``CLAUDE`` の
フォールスルーにしか適用されないため。結果 hermes の戻り値がそのまま TTS に渡り、
**33 秒間ツール呼び出しの生 JSON を読み上げた**::

    state=SPEAKING clarify{questions:[{choices:[<|"|>症状の詳細を教えてください<|"|>,…
    PLAYBACK_DURATION=32992ms

``<|"|>`` は hermes の Python ソースに存在しない。つまり gemma4:e2b が
**テキスト形式の壊れたツール呼び出しを出力した**もので、hermes はそれを回答として
返し、JARVIS がそのまま読み上げた。モデル側の修正は上流の話なので、
**読み上げる直前という JARVIS 自身の境界**で落とす。

handoff §7 は「production で表示しないもの: heard_text / expanded response /
raw tool args」と規定している。読み上げも同じ扱いにする。

設計
----
判定はすべて決定的（モデルを使わない）。読み上げ長には形に関係なく効く上限を置く。
33 秒読み上げのような事故は、パターンの網羅ではなく上限で止めるのが確実。
"""
from __future__ import annotations

import re

# 読み上げの上限。Edge TTS の実測でおよそ 1 文字 0.17 秒なので、
# 300 文字で 50 秒。ここでは「1 ターンの読み上げは 30 秒以内」を狙って 180 文字にする。
MAX_SPOKEN_CHARS = 180

# 取り出せなかったときの定型文。無音より短い一言の方が状況が伝わる。
FALLBACK = "うまく応答できませんでした。もう一度お願いします。"
EMPTY_FALLBACK = "応答がありませんでした。"

# モデル固有の特殊トークン。`<|"|>` `<|im_start|>` など。
_SPECIAL_TOKEN = re.compile(r"<\|.*?\|>")

# `name{...` 形のツール呼び出し。行頭・文中どちらでも拾う。
_TOOL_CALL = re.compile(r"(?:^|\s)[a-z_][a-z0-9_]*\{", re.IGNORECASE)

# 構造化フィールドの痕跡。
_STRUCT_FIELD = re.compile(r"(questions|choices|arguments|parameters)\s*:\s*\[")

# clarify の中身。`<|"|>...<|"|>` に挟まれた人間可読な文字列を取り出す。
_QUOTED = re.compile(r'<\|"\|>(.*?)<\|"\|>')
_QUESTION_FIELD = re.compile(r'question\s*:\s*<\|"\|>(.*?)<\|"\|>')


def looks_like_tool_call(text: str | None) -> bool:
    """読み上げてはいけない構造かどうか。決定的に判定する。"""
    if not text:
        return False
    if _SPECIAL_TOKEN.search(text):
        return True
    if _STRUCT_FIELD.search(text):
        return True
    if _TOOL_CALL.search(text):
        return True
    return False


def _extract_clarify(text: str) -> str | None:
    """clarify らしき構造から読み上げるべき一文を取り出す。

    ``question`` フィールドがあればそれを使う。無ければ最初の引用文字列
    （多くの場合は選択肢の1つ目）で代替する。どちらも無ければ None。
    """
    m = _QUESTION_FIELD.search(text)
    if m and m.group(1).strip():
        return m.group(1).strip()
    quoted = [q.strip() for q in _QUOTED.findall(text) if q.strip()]
    return quoted[0] if quoted else None


def _cap(text: str) -> str:
    """読み上げ長を上限で切る。可能なら文の境界で切る。"""
    if len(text) <= MAX_SPOKEN_CHARS:
        return text
    head = text[:MAX_SPOKEN_CHARS]
    # 「。」で切れるならそこまで。句点が早すぎる位置しかない場合は諦めて切り詰める。
    idx = head.rfind("。")
    if idx >= MAX_SPOKEN_CHARS // 3:
        return head[:idx + 1]
    return head


def sanitize(text: str | None) -> str:
    """読み上げ用に整える。

    通常の文はそのまま返す（空白の整理のみ）。ツール呼び出し構造は
    人間可読部分を取り出すか、取り出せなければ定型文に落とす。
    最後に必ず長さの上限を適用する。
    """
    if text is None:
        return EMPTY_FALLBACK
    stripped = text.strip()
    if not stripped:
        return EMPTY_FALLBACK

    if looks_like_tool_call(stripped):
        extracted = _extract_clarify(stripped)
        if extracted:
            return _cap(extracted)
        return FALLBACK

    return _cap(stripped)
