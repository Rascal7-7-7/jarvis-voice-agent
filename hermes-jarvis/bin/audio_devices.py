"""入力デバイスの優先順位選択。

なぜ必要か（2026-09-08/09 実測）
--------------------------------
upstream（``tools/voice_mode.py``）は「OS の既定入力」をそのまま開く。macOS の
既定は「最後に接続したものが勝つ」なので、固定優先順位（外部 > 無線 > 内蔵）
とは一致しない。さらに ``shared_audio.open_once`` は
``follow_default_device = False`` を設定している（CoreAudio の close→reopen が
デッドロックするため、稼働中のストリームは差し替えない）。結果として、
起動時にどのデバイスを掴むかが実質的に唯一の選択機会になる。

そこにクラムシェル運用が重なった。蓋を閉じている間、内蔵マイクは
**列挙されるが全サンプル 0 を返す**（ffmpeg で JARVIS 非経由でも -91.0 dB を実測）。
優先順位に内蔵を残したままだと、外部マイクを抜いた瞬間に無音デバイスへ落ち、
利用者からは「待つが返答がない」としか見えない。
クラムシェル状態は ``ioreg -k AppleClamshellState`` から決定的に取れるので、
推測ではなくこれを使う。

どう適用するか
--------------
``sd.InputStream`` は ``device=`` を渡していない（upstream 実装）ため、
``sd.default.device`` を**プロセスローカルに**設定すれば開く先を誘導できる。
ユーザーのシステム既定入力は変更しない。上流のコードも変更しない。
"""
from __future__ import annotations

import re
import subprocess
import unicodedata
from typing import Any, Iterable, Mapping

# 小さいほど優先。
TIER_EXTERNAL = 1    # ジャック・USB の実マイク
TIER_WIRELESS = 2    # Bluetooth・Continuity（iPhone をマイクにする）
TIER_BUILTIN = 3     # 内蔵マイク。クラムシェル中は使えない
TIER_EXCLUDED = 99   # 仮想ドライバ等。選んではいけない

# 仮想オーディオドライバ。実マイクではないので常に除外する。
# これらを候補に残すと、実マイクが無い時に「無音だが開ける」デバイスを掴む。
_VIRTUAL_PATTERNS = (
    "nomachine", "blackhole", "loopback", "soundflower", "vb-cable",
    "vb cable", "zoomaudiodevice", "aggregate", "multi-output",
    "teams audio", "krisp", "obs virtual", "sunshine",
)

# 内蔵マイク。「…のマイク」は Continuity も同じ形なので、機種名で判別する。
_BUILTIN_PATTERNS = ("macbook", "imac", "mac mini", "mac studio",
                     "built-in", "builtin", "内蔵")

# 無線。既知のブランド名 + Continuity の「…のマイク」形。
_WIRELESS_PATTERNS = ("airpods", "beats", "bose", "jabra", "sony wh", "wh-",
                      "wf-", "galaxy buds", "pixel buds", "soundcore",
                      "shokz", "aftershokz")

_EXTERNAL_PATTERNS = ("外部", "external", "line in", "line-in")

_CONTINUITY_RE = re.compile(r"の(マイク|マイクロフォン)$")
_CLAMSHELL_RE = re.compile(r'"AppleClamshellState"\s*=\s*(\w+)')


def _normalize(name: str) -> str:
    """比較用に正規化する。

    実機のデバイス名には LEFT-TO-RIGHT MARK (U+200E) が前置されることがある
    （実測: ``'\\u200eClayのマイク'``）。制御文字を落とさないと前方一致が壊れる。
    """
    if not name:
        return ""
    cleaned = "".join(ch for ch in name
                      if unicodedata.category(ch) not in ("Cf", "Cc"))
    return cleaned.strip().lower()


def classify(name: str) -> int:
    """デバイス名から tier を決める。名前だけで判断し、I/O はしない。"""
    n = _normalize(name)
    if not n:
        return TIER_EXCLUDED
    if any(p in n for p in _VIRTUAL_PATTERNS):
        return TIER_EXCLUDED
    # 内蔵の判定を Continuity より先に行う。
    # 「MacBook Proのマイク」も「Clayのマイク」も同じ語尾なので順序が意味を持つ。
    if any(p in n for p in _BUILTIN_PATTERNS):
        return TIER_BUILTIN
    if any(p in n for p in _WIRELESS_PATTERNS):
        return TIER_WIRELESS
    if _CONTINUITY_RE.search(n):
        return TIER_WIRELESS
    if any(p in n for p in _EXTERNAL_PATTERNS):
        return TIER_EXTERNAL
    # 未知の名前は実マイクとみなす。USB マイクが最も可能性が高く、
    # 仮想ドライバは上の除外リストで先に落としている。
    return TIER_EXTERNAL


def parse_clamshell(ioreg_output: str | None) -> bool | None:
    """``ioreg -k AppleClamshellState`` の出力から蓋の状態を読む。

    取得できなかった場合は ``None``。**False と混同してはいけない**:
    不明を「開いている」と扱うと内蔵マイクを候補から外せず、
    不明を「閉じている」と扱うと蓋が開いている機体で内蔵マイクを失う。
    """
    if not ioreg_output:
        return None
    m = _CLAMSHELL_RE.search(ioreg_output)
    if not m:
        return None
    value = m.group(1).strip().lower()
    if value in ("yes", "true", "1"):
        return True
    if value in ("no", "false", "0"):
        return False
    return None


def read_clamshell_state(timeout: float = 2.0) -> bool | None:
    """実機の蓋の状態。取得できなければ ``None``。"""
    try:
        out = subprocess.run(
            ["/usr/sbin/ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"],
            capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_clamshell(out.stdout)


def candidates(devices: Iterable[Mapping[str, Any]],
               *, clamshell_closed: bool | None) -> list[dict[str, Any]]:
    """選択可能なデバイスを優先順位順（同 tier 内は index 昇順）で返す。"""
    out: list[dict[str, Any]] = []
    for dev in devices or ():
        if int(dev.get("max_input_channels") or 0) <= 0:
            continue
        name = str(dev.get("name") or "")
        tier = classify(name)
        if tier == TIER_EXCLUDED:
            continue
        # 蓋が閉じている間、内蔵マイクは列挙されても全ゼロを返す。
        # 不明（None）のときは残す — 蓋が開いている機体で回帰させないため。
        if tier == TIER_BUILTIN and clamshell_closed is True:
            continue
        out.append({"index": int(dev.get("index")), "name": name, "tier": tier})
    out.sort(key=lambda d: (d["tier"], d["index"]))
    return out


def select(devices: Iterable[Mapping[str, Any]],
           *, clamshell_closed: bool | None) -> dict[str, Any] | None:
    """優先順位で1台選ぶ。候補が無ければ ``None``。"""
    ranked = candidates(devices, clamshell_closed=clamshell_closed)
    return ranked[0] if ranked else None


def enumerate_inputs(sd) -> list[dict[str, Any]]:
    """sounddevice から入力デバイスを列挙する。"""
    out: list[dict[str, Any]] = []
    try:
        for index, dev in enumerate(sd.query_devices()):
            if int(dev.get("max_input_channels") or 0) <= 0:
                continue
            out.append({"index": index, "name": dev.get("name") or "",
                        "max_input_channels": int(dev["max_input_channels"])})
    except Exception:
        return []
    return out
