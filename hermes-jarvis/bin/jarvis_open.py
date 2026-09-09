#!/usr/bin/env python3
"""「〜を開いて」— アプリと URL を開く。

設計（2026-09-09）:
  **境界は許可リスト。LLM ではない。**
  router 側の override は「開いて と言われた」ことしか判定しない。
  何を開くかは必ずこのモジュールの TARGETS で決め、
  載っていないものは開かずに拒否する（DELEGATION_SECURITY.md と同じ立て方）。

  - `open` に渡す値は**必ず TARGETS 由来**。発話の文字列を値に混ぜない。
    混ぜると発話がそのままコマンド引数になる
  - subprocess は list 形式で呼ぶ。shell を経由しない
  - システム設定・ターミナル等は**意図的に載せていない**。
    ウィンドウを開くだけでも、そこから先は設定変更の入口になる
  - URL は既定ブラウザで開く（この機では Brave = com.brave.browser）。
    JARVIS 側でブラウザを決め打ちせず、ユーザーの既定に従う
"""
import re
import subprocess

ACTION_APP = "OPEN_APP"
ACTION_URL = "OPEN_URL"
ACTION_REFUSE = "REFUSE"

# 「開いて」と言われたかの判定のみ。対象の判定はここではしない。
# router からも参照される（パターンを2箇所に持つと片方だけ直して穴が開く）。
#
# `(?<!再)` は必須。2026-09-09 実測: これが無いと「Macを再起動して」が
# OPEN へ流れた。gate が捕まえるのは「sudoで再起動して」だけで、
# 「Macを再起動して」は素通りする。再起動は開く操作ではないので除外する。
TRIGGER = re.compile(r"(開いて|ひらいて|開けて|(?<!再)起動して|立ち上げて)")

# 単独の x / X は他の語の中で誤爆する（xcode, max）。前後を英字以外に限る
_X = r"(?<![0-9A-Za-z])[xX](?![0-9A-Za-z])"

TARGETS = (
    {"key": "brave", "kind": ACTION_APP, "value": "Brave Browser",
     "label": "Brave", "pattern": re.compile(r"(ブレイブ|ブレーブ|brave)", re.I)},
    {"key": "chrome", "kind": ACTION_APP, "value": "Google Chrome",
     "label": "Chrome", "pattern": re.compile(r"(クローム|chrome)", re.I)},
    {"key": "safari", "kind": ACTION_APP, "value": "Safari",
     "label": "Safari", "pattern": re.compile(r"(サファリ|safari)", re.I)},
    {"key": "google", "kind": ACTION_URL, "value": "https://www.google.com",
     "label": "Google", "pattern": re.compile(r"(グーグル|google)", re.I)},
    {"key": "youtube", "kind": ACTION_URL, "value": "https://www.youtube.com",
     "label": "YouTube", "pattern": re.compile(r"(ユーチューブ|youtube)", re.I)},
    {"key": "github", "kind": ACTION_URL, "value": "https://github.com",
     "label": "GitHub", "pattern": re.compile(r"(ギットハブ|github)", re.I)},
    {"key": "x", "kind": ACTION_URL, "value": "https://x.com",
     "label": "X", "pattern": re.compile(r"(エックス|ツイッター|twitter|" + _X + ")")},
)


def looks_like_open(text):
    return bool(TRIGGER.search(text or ""))


def resolve_target(text):
    """許可リストの中から対象を決める。載っていなければ None。

    先に定義したものが勝つ。Chrome より Google が後なのは
    「Google Chrome」と言われたときにアプリ側を先に拾わせないため
    ではなく、`chrome` を含む発話は Chrome を開くのが素直だから。
    """
    text = text or ""
    for target in TARGETS:
        if target["pattern"].search(text):
            return target
    return None


def _refusal_speech():
    labels = "、".join(t["label"] for t in TARGETS)
    return "それは開けません。開けるのは%sです。" % labels


def plan(text):
    """何をするかだけを決める。実行はしない。"""
    if not looks_like_open(text):
        return {"action": ACTION_REFUSE, "value": "", "label": "",
                "speech": "開く対象が分かりませんでした。"}
    target = resolve_target(text)
    if target is None:
        return {"action": ACTION_REFUSE, "value": "", "label": "",
                "speech": _refusal_speech()}
    return {"action": target["kind"], "value": target["value"],
            "label": target["label"], "speech": "%sを開きます。" % target["label"]}


def run(chosen):
    """plan の結果を実行する。値は TARGETS 由来のものしか来ない。"""
    action = chosen["action"]
    if action == ACTION_APP:
        argv = ["/usr/bin/open", "-a", chosen["value"]]
    elif action == ACTION_URL:
        argv = ["/usr/bin/open", chosen["value"]]
    else:
        return False
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def handle(text):
    """音声経路の入口。返り値がそのまま読み上げられる。"""
    chosen = plan(text)
    if chosen["action"] == ACTION_REFUSE:
        return chosen["speech"]
    if not run(chosen):
        return "%sを開けませんでした。" % chosen["label"]
    return chosen["speech"]


if __name__ == "__main__":
    import sys
    print(handle(sys.argv[1] if len(sys.argv) > 1 else ""))
