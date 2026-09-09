#!/usr/bin/env python3
"""捕捉経路ごとに wake word の通り具合を測る。

なぜこれが必要か
----------------
`docs/WAKE_WORD_DISCOVERY.md` の結論:

    The engine is not the failing component. Fixing the capture path is the
    prerequisite -- the same voice, captured cleanly, already scores 0.9975.

そこで未解決だったのが「同じ経路が断続的に歪むのか / 別の経路が恒常的に歪むのか」。
2026-09-09 の測定で後者を支持する材料が出た:

    MY_HEY_JARVIS（成功 0.9975）  0-8kHz 99.41%  8-16kHz  0.57%
    外部マイク（環境音のみ）        0-8kHz 76.31%  8-16kHz 20.18%

つまり**ジャック経路は無発話でも 8kHz 超に 20% を持つ**。成功クリップの 35倍。
クリッピングは 0 件だったのでゲインではなくデバイス固有の性質。

このツールは device を変えて同じ発話を測り、どの経路なら wake が通るかを出す。
ユーザーの発話を無駄にしないため、1回の録音から score と帯域の両方を取る。

読み取り専用。production の設定もモデルも変更しない。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import wave

MODEL = "hey_jarvis"
THRESHOLD = 0.35          # production と同じ
ENGINE_RATE = 16000
ENGINE_FRAME = 1280


def list_input_devices() -> list[tuple[int, str]]:
    """ffmpeg avfoundation の音声入力デバイス。"""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "avfoundation",
         "-list_devices", "true", "-i", ""],
        capture_output=True, text=True, errors="replace")
    out: list[tuple[int, str]] = []
    seen_audio = False
    for line in (p.stderr or "").splitlines():
        if "audio devices" in line:
            seen_audio = True
            continue
        if not seen_audio:
            continue
        if "] [" in line and "]" in line:
            try:
                idx = int(line.split("[", 2)[2].split("]")[0])
            except (IndexError, ValueError):
                continue
            name = line.split("]", 3)[-1].strip()
            out.append((idx, name))
    return out


def record(device: int, seconds: float, path: str) -> bool:
    """48 kHz mono int16 で録る。production の捕捉レートに合わせる。"""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-f", "avfoundation", "-i", f":{device}", "-t", str(seconds),
         "-ac", "1", "-ar", "48000", path],
        capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        sys.stderr.write((p.stderr or "")[:300] + "\n")
    return p.returncode == 0


def load_wav(path: str):
    import numpy as np
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    return data, rate


def band_energy(data, rate: int) -> dict:
    """先行調査と同じ 0-8k / 8-16k / 16-24k の比。"""
    import numpy as np
    x = data.astype(np.float64)
    if x.size == 0:
        return {}
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
    freq = np.fft.rfftfreq(x.size, 1 / rate)
    total = spec.sum() or 1.0
    out = {}
    for lo, hi in ((0, 8000), (8000, 16000), (16000, 24000)):
        m = (freq >= lo) & (freq < hi)
        out[f"{lo//1000}-{hi//1000}kHz"] = float(spec[m].sum() / total * 100)
    return out


def resample_like_production(np, data, rate: int):
    """production と同じ box average で 48k -> 16k にする。

    naive な 3:1 間引きは 8-16 kHz を音声帯へ折り返すので、
    先行調査が「自分の resampler が spectra を汚していた」と記録している。
    ここは production の `_resample_audio_frame` と同じ挙動にする。
    """
    if rate == ENGINE_RATE:
        return data.astype(np.int16)
    factor = rate // ENGINE_RATE
    usable = (data.size // factor) * factor
    if usable == 0:
        return np.zeros(0, dtype=np.int16)
    return (data[:usable].astype(np.float64)
            .reshape(-1, factor).mean(axis=1)).astype(np.int16)


def score(path: str) -> dict:
    """本番モデルで最大スコアを出す。フレーム位相を4つずらして最大を取る。"""
    import numpy as np
    from openwakeword.model import Model

    data, rate = load_wav(path)
    pcm = resample_like_production(np, data, rate)
    best = 0.0
    for offset in range(0, ENGINE_FRAME, ENGINE_FRAME // 4):
        model = Model(wakeword_models=[MODEL])
        buf = pcm[offset:]
        n = (buf.size // ENGINE_FRAME) * ENGINE_FRAME
        for i in range(0, n, ENGINE_FRAME):
            preds = model.predict(buf[i:i + ENGINE_FRAME])
            best = max(best, float(preds.get(MODEL, 0.0)))
    return {"score": best, "fire": best >= THRESHOLD}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", type=int, help="avfoundation の音声デバイス index")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--out", default="/tmp/wake_probe.wav")
    ap.add_argument("--list", action="store_true", help="デバイス一覧のみ")
    ap.add_argument("--score-only", metavar="WAV", help="録音せず既存 WAV を採点")
    args = ap.parse_args()

    if args.list:
        for idx, name in list_input_devices():
            print(f"  [{idx}] {name}")
        return 0

    import numpy as np

    if args.score_only:
        path = args.score_only
    else:
        if args.device is None:
            ap.error("--device か --score-only が必要です")
        print(f"録音 {args.seconds}s device=[{args.device}] … 話してください")
        if not record(args.device, args.seconds, args.out):
            print("録音に失敗しました")
            return 70
        path = args.out

    data, rate = load_wav(path)
    peak = int(np.abs(data).max()) if data.size else 0
    rms = int(np.sqrt((data.astype(np.float64) ** 2).mean())) if data.size else 0
    clipped = int((np.abs(data) >= 32700).sum())
    bands = band_energy(data, rate)
    result = score(path)

    print(f"\nfile   : {path}")
    print(f"rate   : {rate} Hz  samples {data.size}")
    print(f"level  : peak {peak}  rms {rms}  clipped {clipped}"
          f" ({clipped/max(data.size,1)*100:.3f}%)")
    for k, v in bands.items():
        print(f"band   : {k:>10} {v:6.2f} %")
    verdict = "FIRE" if result["fire"] else "no fire"
    print(f"score  : {result['score']:.4f}  (threshold {THRESHOLD})  → {verdict}")
    print("\n参考（docs/WAKE_WORD_DISCOVERY.md の実測）")
    print("  成功クリップ 0.9975 : 0-8kHz 99.41 %  8-16kHz  0.57 %")
    print("  失敗クリップ 0.0000 : 0-8kHz 36.96 %  8-16kHz 58.14 %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
