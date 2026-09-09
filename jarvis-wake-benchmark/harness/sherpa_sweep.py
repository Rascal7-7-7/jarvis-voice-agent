#!/usr/bin/env python3
"""sherpa KWS を openWakeWord と同じコーパスで測る。

なぜ sherpa を試すか（2026-09-09 実測）
--------------------------------------
openWakeWord の `hey_jarvis` は固定モデルで、日本語発音の中央値が 0.1274。
閾値 0.35 では recall 23.3%、0.10 まで下げても 57%。閾値調整では解けない。

sherpa KWS は **open-vocab**（キーワードを実行時にトークン化する）。
カタカナはトークン表に無いが、**ローマ字なら通る**:

    'HEY JARVIS'   → ▁HE Y ▁JA R VI S
    'HEI JAABISU'  → ▁HE I ▁JA A B IS U   ← 日本語音をローマ字で表現できる

これは openWakeWord では原理的にできない（モデルが固定）。

production と同じ設定経路を使う:
    threshold = 0.05 + 0.4 * sensitivity     （wake_word.py:725）
    keywords_threshold に渡す
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import tempfile
import wave

SAMPLE_RATE = 16000
FRAME = 1280
FRAME_SECONDS = FRAME / SAMPLE_RATE


def load(path: str):
    import numpy as np
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    if rate != SAMPLE_RATE:
        factor = rate // SAMPLE_RATE
        usable = (data.size // factor) * factor
        data = (data[:usable].astype("float64")
                .reshape(-1, factor).mean(axis=1)).astype("int16")
    return data


def make_keywords_file(model_dir: str, phrases: list[str], threshold: float) -> str:
    from sherpa_onnx import text2token
    usable, toks = [], []
    for p in phrases:
        try:
            t = text2token([p.upper()], tokens=f"{model_dir}/tokens.txt",
                           tokens_type="bpe", bpe_model=f"{model_dir}/bpe.model")
            if t and t[0]:
                usable.append(p); toks.append(t[0])
        except Exception:
            pass
    if not usable:
        raise RuntimeError("トークン化できるキーワードがありません")
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                     encoding="utf-8")
    for p, t in zip(usable, toks):
        fh.write(" ".join(t) + f" @{p.upper().replace(' ', '_')}\n")
    fh.close()
    return fh.name


def spotter(model_dir: str, keywords_file: str, threshold: float):
    import sherpa_onnx
    def pick(pattern: str) -> str:
        hits = sorted(p for p in glob.glob(os.path.join(model_dir, pattern))
                      if "int8" not in p)
        if not hits:
            raise RuntimeError(f"model file missing: {pattern}")
        return hits[0]
    return sherpa_onnx.KeywordSpotter(
        tokens=os.path.join(model_dir, "tokens.txt"),
        encoder=pick("encoder-*.onnx"),
        decoder=pick("decoder-*.onnx"),
        joiner=pick("joiner-*.onnx"),
        keywords_file=keywords_file,
        keywords_threshold=threshold,
        num_threads=1,
    )


def count_fires(sp, pcm) -> int:
    """発火回数。**末尾を必ず流し切る。**

    2026-09-09 実測: `input_finished()` と無音パディングを省くと、
    発話が最後のフレームに残ったまま検出されずに終わり、
    英語クリップですら recall 0% になった（初版のバグ）。
    """
    import numpy as np
    stream = sp.create_stream()
    hits = 0
    n = (pcm.size // FRAME) * FRAME
    for i in range(0, n, FRAME):
        stream.accept_waveform(SAMPLE_RATE,
                               np.asarray(pcm[i:i + FRAME], dtype=np.float32) / 32768.0)
        while sp.is_ready(stream):
            sp.decode_stream(stream)
            r = sp.get_result(stream)
            if r:
                hits += 1
                sp.reset_stream(stream)
    # 末尾の残りを 0.5 秒の無音で押し出してから閉じる
    stream.accept_waveform(SAMPLE_RATE, np.zeros(SAMPLE_RATE // 2, dtype=np.float32))
    stream.input_finished()
    while sp.is_ready(stream):
        sp.decode_stream(stream)
        r = sp.get_result(stream)
        if r:
            hits += 1
            sp.reset_stream(stream)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--phrases", required=True,
                    help="カンマ区切り。例: HEY JARVIS,HEI JAABISU")
    ap.add_argument("--sensitivities", default="0.3,0.4,0.5,0.6,0.7,0.8")
    ap.add_argument("--pos", default=None)
    ap.add_argument("--neg", default=None)
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    pos = sorted(glob.glob(os.path.join(args.pos or os.path.join(here, "corpus", "pos"), "*.wav")))
    neg = sorted(glob.glob(os.path.join(args.neg or os.path.join(here, "corpus", "neg"), "*.wav")))
    if not pos:
        print("正例がありません", file=sys.stderr); return 66

    phrases = [p.strip() for p in args.phrases.split(",") if p.strip()]
    pos_pcm = {p: load(p) for p in pos}
    neg_pcm = {p: load(p) for p in neg}
    neg_seconds = sum(x.size for x in neg_pcm.values()) / SAMPLE_RATE

    print(f"キーワード: {phrases}")
    print(f"正例 {len(pos)} 件 / 負例 {neg_seconds:.1f} 秒\n")
    print(f"{'sens':>6} {'thr':>6} {'recall':>8} {'FP/h':>8}   判定")
    print("-" * 52)

    best = []
    for s in [float(x) for x in args.sensitivities.split(",")]:
        thr = 0.05 + 0.4 * s          # production と同じ写像
        kwf = make_keywords_file(args.model_dir, phrases, thr)
        sp = spotter(args.model_dir, kwf, thr)
        hit = sum(1 for pcm in pos_pcm.values() if count_fires(sp, pcm))
        recall = hit / len(pos) * 100
        fp = sum(count_fires(sp, pcm) for pcm in neg_pcm.values())
        fph = fp / (neg_seconds / 3600) if neg_seconds else 0.0
        mark = "◎ 候補" if (recall >= 90 and fph <= 0.5) else \
               ("recall◯ FP多い" if recall >= 90 else
                ("FP◯ recall不足" if fph <= 0.5 else ""))
        if recall >= 90 and fph <= 0.5:
            best.append((s, thr, recall, fph))
        print(f"{s:>6.2f} {thr:>6.3f} {recall:>7.1f}% {fph:>8.2f}   {mark}")
        os.unlink(kwf)

    if best:
        best.sort(key=lambda b: (-b[2], b[3]))
        s, t, r, f = best[0]
        print(f"\n推奨: sensitivity={s:.2f} (threshold {t:.3f}) "
              f"recall {r:.1f}% / FP {f:.2f}/h")
    else:
        print("\nrecall≥90% かつ FP≤0.5/h を満たす設定はありません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
