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
import os
import re
import subprocess
import time

ACTION_APP = "OPEN_APP"
ACTION_URL = "OPEN_URL"
ACTION_PROJECT = "OPEN_PROJECT"          # Ghostty ウィンドウ + tmux + Claude を起動
ACTION_PROJECT_CLOSE = "CLOSE_PROJECT"   # tmux セッションごと終了
ACTION_REFUSE = "REFUSE"

# 終了の動詞。開くより**先に**見る（「起動しているアップを終了して」は終了）
CLOSE_TRIGGER = re.compile(r"(終了して|閉じて|落として|止めて|停止して)")

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

    # ── プロジェクト（Ghostty ウィンドウを開く / 閉じる）
    #
    # 別名はすべて 2026-09-10 に faster-whisper small / ja で**実測**した。
    # 推測で書くと外れる: 「エーピーピー」は BPP、「ボット」は ロット、
    # 「ディスコード」は リスコード、「エルピー」は lp になった。
    #
    # 除外は動詞があっても当たってしまう語だけに絞る。動詞の無い発話
    # （「ロットを確認して」等）はそもそも OPEN 経路に来ない。
    {"key": "app", "kind": ACTION_PROJECT, "value": "app", "label": "app",
     "pattern": re.compile(r"(アップ(?!デート|ロード|グレード)|エーピーピー|BPP"
                           r"|(?<![0-9A-Za-z])app(?![0-9A-Za-z]))", re.I)},
    {"key": "trade", "kind": ACTION_PROJECT, "value": "trade", "label": "trade",
     "pattern": re.compile(r"(トレード(?!オフ)|(?<![0-9A-Za-z])trade(?![0-9A-Za-z]))",
                           re.I)},
    {"key": "auto", "kind": ACTION_PROJECT, "value": "auto", "label": "auto",
     "pattern": re.compile(r"(オートメーション|オート(?!コンプリート|マチック|フォーカス|セーブ)"
                           r"|(?<![0-9A-Za-z])auto(?![0-9A-Za-z]))", re.I)},
    {"key": "bot", "kind": ACTION_PROJECT, "value": "bot", "label": "bot",
     "pattern": re.compile(r"((?<!ス)ロット|ボット|リスコード|ディスコード"
                           r"|(?<![0-9A-Za-z])bot(?![0-9A-Za-z]))", re.I)},
    {"key": "lp", "kind": ACTION_PROJECT, "value": "lp", "label": "lp",
     "pattern": re.compile(r"(エルピー(?!ガス)|(?<![0-9A-Za-z])lp(?![0-9A-Za-z]))",
                           re.I)},
    {"key": "mvp", "kind": ACTION_PROJECT, "value": "mvp", "label": "mvp",
     "pattern": re.compile(r"(エムブイピー|(?<![0-9A-Za-z])mvp(?![0-9A-Za-z]))", re.I)},
    {"key": "senkou", "kind": ACTION_PROJECT, "value": "senkou", "label": "senkou",
     "pattern": re.compile(r"(センコウ|선고|選考|専攻)")},
)


# open-project.sh の alias -> tmux セッション名。終了はセッションを落とす
PROJECT_SESSIONS = {"app": "app", "senkou": "app", "trade": "trade",
                    "auto": "automation", "bot": "bot", "lp": "lp",
                    "mvp": "mvp"}

OPEN_PROJECT_SH = os.path.expanduser("~/work/scripts/open-project.sh")


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
    apps = "、".join(t["label"] for t in TARGETS if t["kind"] != ACTION_PROJECT)
    projects = "、".join(t["label"] for t in TARGETS
                       if t["kind"] == ACTION_PROJECT)
    return ("それは開けません。開けるのは%s、プロジェクトは%sです。"
            % (apps, projects))


def looks_like_close(text):
    return bool(CLOSE_TRIGGER.search(text or ""))


def plan(text):
    """何をするかだけを決める。実行はしない。"""
    closing = looks_like_close(text)
    if not (closing or looks_like_open(text)):
        return {"action": ACTION_REFUSE, "value": "", "label": "",
                "speech": "開く対象が分かりませんでした。"}
    target = resolve_target(text)
    if target is None:
        return {"action": ACTION_REFUSE, "value": "", "label": "",
                "speech": _refusal_speech()}
    if closing:
        # 閉じられるのはプロジェクトだけ。ブラウザを閉じる機能は持たない
        if target["kind"] != ACTION_PROJECT:
            return {"action": ACTION_REFUSE, "value": "", "label": "",
                    "speech": "%sは閉じられません。閉じられるのはプロジェクトだけです。"
                              % target["label"]}
        return {"action": ACTION_PROJECT_CLOSE, "value": target["value"],
                "label": target["label"],
                "speech": "%sを終了します。" % target["label"]}
    return {"action": target["kind"], "value": target["value"],
            "label": target["label"], "speech": "%sを開きます。" % target["label"]}


def run(chosen):
    """plan の結果を実行する。値は TARGETS 由来のものしか来ない。"""
    action = chosen["action"]
    if action == ACTION_APP:
        argv = ["/usr/bin/open", "-a", chosen["value"]]
    elif action == ACTION_URL:
        argv = ["/usr/bin/open", chosen["value"]]
    elif action == ACTION_PROJECT:
        # Ghostty が独立ウィンドウを開き、その中で tmux + Claude が立つ。
        # detached tmux では Claude が非対話と判定されて即終了するが、
        # このスクリプトは Ghostty が即 attach するので問題ない（実測済み）。
        if not os.access(OPEN_PROJECT_SH, os.X_OK):
            return False
        argv = ["/bin/sh", OPEN_PROJECT_SH, chosen["value"]]
    elif action == ACTION_PROJECT_CLOSE:
        session = PROJECT_SESSIONS.get(chosen["value"])
        if not session:
            return False
        argv = ["tmux", "kill-session", "-t", session]
    else:
        return False
    # **出力を捕まえない。** open-project.sh は Ghostty をバックグラウンドで
    # 起動し、その子がパイプを継承する。capture_output=True だと
    # スクリプト自体が終わっても Python がパイプの EOF を待ち続け、
    # 15 秒でタイムアウトして「起動できませんでした」と誤報した
    # （2026-09-10 実測: セッションは実際には立っていた）。
    quiet = action in (ACTION_PROJECT, ACTION_APP, ACTION_URL)
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.DEVNULL if quiet else subprocess.PIPE,
            stderr=subprocess.DEVNULL if quiet else subprocess.PIPE,
            text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    if action == ACTION_PROJECT:
        # 「起動した」と言う前に、セッションが本当に立ったか確かめる
        return _session_exists(PROJECT_SESSIONS.get(chosen["value"], ""))
    return True


def _session_exists(session, attempts=10, interval=0.4):
    """tmux セッションの出現を待つ。Ghostty の起動は非同期。"""
    if not session:
        return False
    for _ in range(attempts):
        try:
            done = subprocess.run(["tmux", "has-session", "-t", session],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        if done.returncode == 0:
            return True
        time.sleep(interval)
    return False


def handle(text):
    """音声経路の入口。返り値がそのまま読み上げられる。"""
    chosen = plan(text)
    if chosen["action"] == ACTION_REFUSE:
        return chosen["speech"]
    if not run(chosen):
        if chosen["action"] == ACTION_PROJECT_CLOSE:
            # 動いていないものを「終了した」と言わない
            return "%sは動いていません。" % chosen["label"]
        return "%sを開けませんでした。" % chosen["label"]
    return chosen["speech"]


if __name__ == "__main__":
    import sys
    print(handle(sys.argv[1] if len(sys.argv) > 1 else ""))
