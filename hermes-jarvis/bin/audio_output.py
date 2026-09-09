"""出力デバイスの固定。JARVIS の音声を必ず狙った機器へ流す。

なぜ必要か（2026-09-09 実測）
----------------------------
JARVIS の再生は ``afplay`` 経由で、``afplay`` に**デバイス指定オプションが無い**
（実測: 音量・時間・レートのみ）。したがってシステム既定出力にしか出せなかった。

そして既定は安定しない。内蔵マイクの切り分けでジャックを抜いた際に既定出力が
DisplayLink ドックへ移り、挿し直しても macOS は戻さなかった。手動で戻しても
ディスプレイのスリープ復帰で**ドックが既定を奪い返す**（2 回発生）。
結果、応答音声が数時間にわたって無音だった。

解決の鍵は 2 つ:

1. ``ffmpeg`` の audiotoolbox 出力は ``-audio_device_index`` を持つ。
   実機検証: 既定=イヤホンの状態で index 8（本体スピーカー）を指定したら
   **本体から鳴った**。システム既定を無視して指定先へ流せる。
2. JARVIS は自前の ``_speak()``（``jarvis_runtime.py``）から
   ``play_audio_file(p)`` を呼んでいる。**上流を触らずに差し替えられる。**

UID で照合する
--------------
``ffmpeg`` が出す index はデバイスの抜き差しで振り直されるが、UID
（``BuiltInHeadphoneOutputDevice`` 等）は不変。毎回 UID から index を解決する。
これは入力側（``audio_devices``）で index 保存が潜在バグになった反省でもある。

未知のデバイスは「選ばない」
--------------------------
入力側では未知の名前を実マイクとみなした（USB マイクの可能性が高く、仮想ドライバは
除外リストで落とせるため）。**出力は逆にする。** 「開けるが音が出ない」機器
（繋がっていないドック・スピーカー無しの HDMI モニタ）が実在し、それを選ぶと
今日と同じ無音事故になる。確実に鳴ると分かっているものだけを選ぶ。

USB ヘッドセット等を使いたい場合は ``JARVIS_OUTPUT_UID`` で明示指定する。
"""
from __future__ import annotations

import os
import re
import subprocess

# 小さいほど優先。
TIER_JACK = 1        # ジャック出力（イヤホン/ヘッドフォン）
TIER_SPEAKER = 2     # 内蔵スピーカー。クラムシェルでも鳴ることを実機確認済み
TIER_EXCLUDED = 99   # 選ばない

ENV_OVERRIDE = "JARVIS_OUTPUT_UID"

# 確実に鳴ると分かっている出力先だけを許可する。
_ALLOWED = {
    "BuiltInHeadphoneOutputDevice": TIER_JACK,
    "BuiltInSpeakerDevice": TIER_SPEAKER,
}

_DEVICE_RE = re.compile(r"\[(\d+)\]\s+(.*?),\s*(\S.*?)\s*$")

# ffmpeg のプロセス起動は afplay より重い。読み上げ 1 回あたりの追加なので
# 許容範囲だが、ハングさせないよう上限を置く。
_PLAY_TIMEOUT = 120.0


def parse_devices(ffmpeg_output: str | None) -> list[dict]:
    """``ffmpeg -f audiotoolbox -list_devices true`` の出力を構造化する。

    行の形式::

        [AudioToolbox @ 0x…] [6]   (null), BuiltInHeadphoneOutputDevice

    UID にコロンが含まれる（``AppleUSBAudioEngine:DisplayLink:…``）ので、
    名前と UID の分割は**最初のカンマ**で行い、UID 側は分割しない。
    """
    out: list[dict] = []
    if not ffmpeg_output:
        return out
    for line in ffmpeg_output.splitlines():
        if "CoreAudio devices" in line:
            continue
        m = _DEVICE_RE.search(line)
        if not m:
            continue
        name = m.group(2).strip()
        out.append({"index": int(m.group(1)),
                    "name": "" if name == "(null)" else name,
                    "uid": m.group(3).strip()})
    return out


def classify_output(uid: str | None) -> int:
    """UID から tier を決める。許可リストに無いものは選ばない。"""
    if not uid:
        return TIER_EXCLUDED
    return _ALLOWED.get(uid.strip(), TIER_EXCLUDED)


def select_output(devices, *, override_uid: str | None = None) -> dict | None:
    """優先順位で 1 台選ぶ。候補が無ければ ``None``。

    ``override_uid`` が実在すればそれを最優先する。実在しなければ黙って
    優先順位へ落ちる（設定ミスで無音になるより、鳴る方を選ぶ）。
    """
    devs = list(devices or ())
    if override_uid:
        for d in devs:
            if d.get("uid") == override_uid:
                return dict(d, tier=classify_output(d.get("uid")))

    ranked = []
    for d in devs:
        tier = classify_output(d.get("uid"))
        if tier == TIER_EXCLUDED:
            continue
        ranked.append(dict(d, tier=tier))
    ranked.sort(key=lambda d: (d["tier"], d["index"]))
    return ranked[0] if ranked else None


def list_devices(timeout: float = 8.0) -> list[dict]:
    """実機のデバイス一覧。取得できなければ空リスト。"""
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "anullsrc",
             "-t", "0.01", "-f", "audiotoolbox", "-list_devices", "true", "-"],
            capture_output=True, text=True, errors="replace",
            timeout=timeout, check=False)
        return parse_devices((p.stderr or "") + (p.stdout or ""))
    except (OSError, subprocess.SubprocessError):
        return []


def resolve() -> dict | None:
    """今使うべき出力デバイス。"""
    return select_output(list_devices(),
                         override_uid=os.environ.get(ENV_OVERRIDE) or None)


def play(path: str, device: dict | None = None) -> bool:
    """指定デバイスへ再生する。成功したら True。

    失敗しても例外は出さない。呼び出し側は False を見て
    ``play_audio_file``（afplay / システム既定）にフォールバックする。
    """
    if not path or not os.path.isfile(path):
        return False
    dev = device if device is not None else resolve()
    if dev is None:
        return False
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
             "-i", path, "-f", "audiotoolbox",
             "-audio_device_index", str(dev["index"]), "-"],
            capture_output=True, text=True, errors="replace",
            timeout=_PLAY_TIMEOUT, check=False)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
