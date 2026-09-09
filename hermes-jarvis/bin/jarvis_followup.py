#!/usr/bin/env python3
"""確認待ちへの返事と、要約の続き読み。

なぜ必要か（2026-09-09）:
  C8 は要約の最後に「詳細も読み上げますか？」と聞いていたが、
  **それに答える経路が存在しなかった**。果たせない約束をしていた。
  秘書の確認待ちも同じで、bare「はい」は LLM 経路で LOCAL_FAST に落ち、
  `jarvis_secretary.handle()` の確認処理には届いていなかった
  （「秘書、はい」と名前を呼ぶ必要があった）。

安全側の設計:
  返事とみなすのは**発話全体が返事だけ**のとき（`answer_only`）。
  確認待ちは 180 秒有効なので、その間の普通の発話が「お願い」を
  含むだけで書き込みを承諾してしまってはいけない。
  否定を先に見て、混在（「はい、やめて」）は拒否へ倒す。

  判定はすべて決定的なパターン。モデルには委ねない
  （DELEGATION_SECURITY.md「LLM Router ≠ security boundary」）。
"""
import json
import os
import re
import time

ANSWER_YES = "YES"
ANSWER_NO = "NO"
ANSWER_MORE = "MORE"

# 応答本文が入るので state と同じ 0600。§17 PRIVACY を満たす
PENDING_PATH = os.path.expanduser("~/AI-Lab/hermes-jarvis/logs/jarvis_followup.json")
PENDING_TTL_SECONDS = 180

# 溜め込まない。読み上げ用なので長い本文を全部持つ意味がない
MAX_DETAIL_CHARS = 8000

# 「詳細も読み上げますか？」を付けてよい下限。要約より十分長いときだけ聞く
MIN_EXTRA_CHARS = 200

# 続きがあるときに末尾へ足す問い。**読み上げ上限から先に差し引く**。
# 後から足すと sanitize() の 180 字上限でこの問い自体が切り落とされ、
# 続きがあるのに聞かない状態になる
CONTINUE_SUFFIX = " 続けますか。"

_YES = re.compile(r"^(はい|ハイ|うん|ええ|そう(です|だね|ですね)?|"
                  r"お願い(します)?|おねがい(します)?|やって|やろう|"
                  r"進めて|すすめて|ok|オーケー|おーけー|いいよ|いい|"
                  r"頼む|たのむ|実行)$", re.IGNORECASE)
_NO = re.compile(r"^(いいえ|いえ|いや|やめ|やめて|やめる|止めて|とめて|"
                 r"キャンセル|きゃんせる|違う|ちがう|だめ|ダメ|不要|なし|"
                 r"no|大丈夫)$", re.IGNORECASE)
_MORE = re.compile(r"^(続けて|つづけて|続き|つづき|もっと|全部|全部読んで|"
                   r"詳細|詳細を|詳細をお願い|詳しく|くわしく|読んで|"
                   r"読み上げて)$", re.IGNORECASE)

# 返事の切れ目。句点・読点・空白・感嘆で割る
_SPLIT = re.compile(r"[\s、,。．.！!？?]+")


def answer_only(utterance):
    """発話が**返事だけ**なら種類を返す。そうでなければ None。

    全トークンが返事語であることを要求する。一部一致で拾うと、
    確認待ちの 180 秒間の普通の発話で誤発火する。
    """
    if not utterance:
        return None
    tokens = [t for t in _SPLIT.split(utterance.strip()) if t]
    if not tokens:
        return None
    kinds = []
    for token in tokens:
        if _NO.match(token):
            kinds.append(ANSWER_NO)
        elif _MORE.match(token):
            kinds.append(ANSWER_MORE)
        elif _YES.match(token):
            kinds.append(ANSWER_YES)
        else:
            return None  # 返事以外の語が混ざるなら返事ではない
    # 否定が混ざれば拒否。安全側へ倒す
    if ANSWER_NO in kinds:
        return ANSWER_NO
    if ANSWER_MORE in kinds:
        return ANSWER_MORE
    return ANSWER_YES


# ---------------------------------------------------------------- 保留の保存

def remember(detail, route, path=PENDING_PATH):
    """次の発話で読み上げられるよう本文を置く。失敗しても黙って諦める。

    書き込みに失敗して例外を上げると、答え自体が返らなくなる。
    続き読みができないことより、答えが消えることの方が害が大きい。
    """
    data = {"detail": (detail or "")[:MAX_DETAIL_CHARS], "offset": 0,
            "route": route or "", "created": time.time()}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.chmod(path, 0o600)  # 既存ファイルを開いた場合に備える
    except OSError:
        return False
    return True


def pending(path=PENDING_PATH, ttl=PENDING_TTL_SECONDS):
    """保留を読む。期限切れは無効として扱い、ファイルも消す。"""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "detail" not in data:
        return None
    if time.time() - float(data.get("created") or 0) > ttl:
        clear(path)
        return None
    data["offset"] = int(data.get("offset") or 0)
    return data


def clear(path=PENDING_PATH):
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------- 読み上げ分割

def next_chunk(detail, offset):
    """`offset` から一度に読める分を切る。

    戻り値は (読み上げる文, 次の offset, まだ続きがあるか)。
    文の切れ目で切る。途中で切ると聞き取れない。
    """
    import jarvis_speech as sp

    detail = detail or ""
    if offset >= len(detail):
        return "", offset, False
    rest = detail[offset:]
    if len(rest) <= sp.MAX_SPOKEN_CHARS:
        return rest.strip(), len(detail), False

    # 続きがある場合は問いの分を残す
    budget = sp.MAX_SPOKEN_CHARS - len(CONTINUE_SUFFIX)
    head = rest[:budget]
    cut = max(head.rfind("。"), head.rfind("\n"), head.rfind("、"))
    if cut < budget // 3:
        cut = len(head) - 1
    chunk = head[:cut + 1]
    new_offset = offset + len(chunk)
    return chunk.strip(), new_offset, new_offset < len(detail)


def has_more_detail(said, detail):
    """「詳細も読み上げますか？」を付けてよいか。

    LLM に付けさせると詳細が無いときにも聞く。実測でそれが起きた。
    """
    return len(detail or "") - len(said or "") >= MIN_EXTRA_CHARS


# ---------------------------------------------------------------- 音声経路の入口

def handle(utterance, path=PENDING_PATH):
    """返事を処理して読み上げる文を返す。保留が無ければ None。"""
    waiting = pending(path=path)
    if waiting is None:
        return None
    answer = answer_only(utterance)
    if answer is None:
        return None
    if answer == ANSWER_NO:
        clear(path)
        return "わかりました。"

    chunk, new_offset, more = next_chunk(waiting["detail"], waiting["offset"])
    if not chunk:
        clear(path)
        return "以上です。"
    if more:
        waiting["offset"] = new_offset
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(waiting, fh, ensure_ascii=False)
        except OSError:
            clear(path)
        return chunk + CONTINUE_SUFFIX
    clear(path)
    return chunk


if __name__ == "__main__":
    import sys
    print(handle(sys.argv[1] if len(sys.argv) > 1 else "") or "")
