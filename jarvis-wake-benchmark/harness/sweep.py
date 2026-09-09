#!/usr/bin/env python3
"""sensitivity（閾値）と confirmation_frames をオフラインで掃引する。

背景（2026-09-09 実測）
----------------------
本番設定は `sensitivity: 0.35 / confirmation_frames: 1`。
openWakeWord では `self._threshold = _sensitivity(cfg)` なので **閾値 = 0.35**。

    Samantha「Hey Jarvis」  0.9470   FIRE
    Kyoko「ヘイ ジャービス」 0.3282   閾値の 0.02 下で不発
    Kyoko「ヘイジャービス」  0.2687   不発

日本語発音が閾値の直下に張り付いており、発話ごとのゆらぎで境界を跨ぐ。
これが「時々反応するが大抵しない」の正体。

さらに `confirmation_frames: 1` は誤検出対策を無効化している。上流のコメント:

    _DEFAULT_CONFIRMATION_FRAMES = 3 … This is the **primary lever** against
    unintended triggers on ambient talk

つまり現状は recall も precision も悪い。閾値だけ下げると room noise で暴発する
（先行調査が detector-origin の誤発火を実測している）。

このツールは production と同じ判定ロジック（N 連続フレームが閾値超）を再現し、
正例（TTS）の recall と負例（実環境音）の誤発火率を同時に測る。
**ユーザーの発話を必要としない。**

read-only。config も production コードも変更しない。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import wave

MODEL = "hey_jarvis"
ENGINE_RATE = 16000
ENGINE_FRAME = 1280
FRAME_SECONDS = ENGINE_FRAME / ENGINE_RATE   # 0.08 s


def load(path: str):
    import numpy as np
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    if rate != ENGINE_RATE:
        factor = rate // ENGINE_RATE
        usable = (data.size // factor) * factor
        # production の _resample_audio_frame と同じ box average。
        # naive な間引きは 8-16kHz を音声帯へ折り返す（先行調査が記録）。
        data = (data[:usable].astype("float64")
                .reshape(-1, factor).mean(axis=1)).astype("int16")
    return data


def frame_scores(path: str) -> list[float]:
    """1 ファイルのフレーム毎スコア列。モデルは呼び出しごとに作り直す
    （openWakeWord は内部に状態を持つので使い回すと汚れる）。"""
    from openwakeword.model import Model
    pcm = load(path)
    model = Model(wakeword_models=[MODEL])
    out: list[float] = []
    n = (pcm.size // ENGINE_FRAME) * ENGINE_FRAME
    for i in range(0, n, ENGINE_FRAME):
        preds = model.predict(pcm[i:i + ENGINE_FRAME])
        out.append(float(preds.get(MODEL, 0.0)))
    return out


def fires(scores: list[float], threshold: float, frames: int) -> int:
    """production と同じ判定: N 連続で閾値超なら発火。発火回数を返す。

    発火後は連続カウンタを 0 に戻す（cooldown はここでは扱わない。
    _FIRE_COOLDOWN_SECONDS = 2.0 は実行時の抑制で、判定ロジックではない）。
    """
    run = count = 0
    for s in scores:
        if s >= threshold:
            run += 1
            if run >= frames:
                count += 1
                run = 0
        else:
            run = 0
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pos", default=None, help="正例ディレクトリ")
    ap.add_argument("--neg", default=None, help="負例ディレクトリ")
    ap.add_argument("--thresholds", default="0.10,0.15,0.20,0.25,0.30,0.35,0.40")
    ap.add_argument("--frames", default="1,2,3,4")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    pos_dir = args.pos or os.path.join(here, "corpus", "pos")
    neg_dir = args.neg or os.path.join(here, "corpus", "neg")

    pos = sorted(glob.glob(os.path.join(pos_dir, "*.wav")))
    neg = sorted(glob.glob(os.path.join(neg_dir, "*.wav")))
    if not pos:
        print(f"正例がありません: {pos_dir}", file=sys.stderr)
        return 66

    print(f"正例 {len(pos)} 件 / 負例 {len(neg)} 件 を採点します…", file=sys.stderr)
    pos_scores = {p: frame_scores(p) for p in pos}
    neg_scores = {p: frame_scores(p) for p in neg}
    neg_seconds = sum(len(s) for s in neg_scores.values()) * FRAME_SECONDS

    thresholds = [float(x) for x in args.thresholds.split(",")]
    frames_list = [int(x) for x in args.frames.split(",")]

    print(f"\n負例の総時間: {neg_seconds:.1f} 秒 "
          f"({neg_seconds/3600:.3f} 時間)")
    print("\nrecall = 正例のうち発火した割合 / FP/h = 負例での誤発火（毎時換算）")
    print(f"\n{'frames':>6} {'thr':>6} {'recall':>8} {'FP/h':>8}   判定")
    print("-" * 52)

    best = []
    for frames in frames_list:
        for thr in thresholds:
            hit = sum(1 for s in pos_scores.values() if fires(s, thr, frames))
            recall = hit / len(pos) * 100
            fp = sum(fires(s, thr, frames) for s in neg_scores.values())
            fp_per_hour = fp / (neg_seconds / 3600) if neg_seconds else 0.0
            mark = ""
            if recall >= 90 and fp_per_hour <= 0.5:
                mark = "◎ 候補"
                best.append((frames, thr, recall, fp_per_hour))
            elif recall >= 90:
                mark = "recall◯ FP多い"
            elif fp_per_hour <= 0.5:
                mark = "FP◯ recall不足"
            cur = " ← 現行" if (frames == 1 and abs(thr - 0.35) < 1e-9) else ""
            print(f"{frames:>6} {thr:>6.2f} {recall:>7.1f}% {fp_per_hour:>8.2f}   {mark}{cur}")
        print()

    if best:
        # recall 優先、同点なら FP が少ない方、さらに同点なら frames が小さい方
        best.sort(key=lambda b: (-b[2], b[3], b[0]))
        f, t, r, fp = best[0]
        print(f"推奨: confirmation_frames={f} / sensitivity={t:.2f} "
              f"(recall {r:.1f}% / FP {fp:.2f}/h)")
    else:
        print("recall≥90% かつ FP≤0.5/h を満たす組み合わせはありません。")
        print("→ 閾値調整では解けない。別エンジン（sherpa KWS 等）の検討が必要。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
