# Wake word — 決着（2026-09-09）

`WAKE_WORD_RELIABILITY` を測定で決着させた。**閾値調整でもエンジン交換でも解けない。**
原因は英語音響モデルと日本語発音の不一致で、残る手段は日本語カスタムモデルのみ。

先行 `WAKE_WORD_DISCOVERY.md` は「捕捉経路を直すのが前提。それで閉じるかもしれない」で
終わっていた。本日その前提を潰し、accent が**主因**であることを確定させた。

## 本番の実効設定（初めて確認した）

`~/.hermes/config.yaml`:
```yaml
wake_word:
  provider: openwakeword
  phrase: jarvis                # 表示名のみ。モデルは hey_jarvis
  sensitivity: 0.35             # openWakeWord ではこれが**そのまま閾値**
  confirmation_frames: 1        # 連続フレーム要件が実質無効
  openwakeword: { model: hey_jarvis }
```

`wake_word.py:550` が `self._threshold = _sensitivity(cfg)`。つまり閾値 = 0.35。
先行調査は 0.35 を「threshold」として測っていたが、それが**設定値そのもの**だと
確認したのは今回が初めて。

`threshold = 0.05 + 0.4 * _sensitivity(cfg)`（:725）は **sherpa 用**であり
openWakeWord には無関係。混同しないこと。

## 捕捉経路は無罪（先行調査の前提を潰した）

```
TTS "Hey Jarvis" を本体スピーカーから流し外部マイクで捕捉 → score 0.9990 FIRE
帯域: 0-8kHz 99.82 % / 8-16kHz 0.16 %
環境音のみ: クリップ 0 件
```

**注意**: 先に「外部マイクは 8-16kHz に 20% を持つので歪んでいる」と判断したのは誤り。
それは**環境ノイズのスペクトル**（ファン・キーボードは高域が多い）で、発話クリップの
帯域比と比較してはいけない。先行調査も `NOISE_OPEN` の静寂時に 9.67% を記録していた。
同じ誤りを繰り返した。

**また、再生+録音による through-path 測定は信頼できない。** 同じ英語音声が
0.9990 と 0.0253 に分かれた（再生と録音のタイミング依存 + 音響結合の不安定さ）。
**ファイル直接採点だけが決定的。**

## openWakeWord の掃引（正例30 / 負例53秒）

`harness/sweep.py`。production と同じ判定（N 連続フレームが閾値超）を再現。

| frames | thr 0.35 の recall |
|---|---|
| 1（現行） | **23.3%** |
| 2 | 3.3% |
| 3（コード既定） | 3.3% |
| 4 | 0% |

閾値を下げても頭打ち: `frames=1, thr=0.10` で **60.0%**。
**recall≥90% かつ FP≤0.5/h を満たす組み合わせは存在しない。**

発音別の内訳:
```
日本語TTS n=21  max 0.3983 / 中央 0.1274 / min 0.0562
                thr0.35 で 2/21 (10%)   thr0.10 で 12/21 (57%)
英語TTS   n=9   max 0.9460 / 中央 0.4364
                thr0.35 で 5/9          thr0.10 で 7/9
```

**日本語の中央値 0.1274 は閾値 0.35 の約 1/3。** 最高値 0.3983 が閾値をわずかに
超えるだけで、発話ごとのゆらぎが境界を跨ぐ。これが「時々反応するが大抵しない」の正体。

`confirmation_frames: 1` は誤検出対策を切っているのではなく、**切らないと 1 回も
反応しない**状態だった（frames=2 で recall 3.3%）。

## sherpa KWS は使えない

`sherpa-onnx-kws-zipformer-gigaspeech-3.3M`（**英語**コーパス）を取得して実測した。
`sherpa_onnx 1.13.4` は入っていたが `pypinyin` / `sentencepiece` が無く、
**本番で一度も動いていなかった**ことが分かった。

open-vocab なのでカタカナは通らないがローマ字は通る:
```
'HEY JARVIS'   → ▁HE Y ▁JA R VI S
'HEI JAABISU'  → ▁HE I ▁JA A B IS U
'ヘイ ジャービス' → 失敗（トークン表に無い）
```

日本語 TTS 21 件に対する recall:
| キーワード | recall |
|---|---|
| `HEI JAABISU` / `HEY JAABISU` / `HAY JABISU` / `HEI JABISU` / `HEY JAVISU` / `HEI JAA BI SU`（各単独） | **0.0%** |
| 上記+英語綴りを 5 種併用 | **4.8%** |

英語音響モデルは日本語話者の音を英語音素列として認識できない。綴りでは解決しない。
（英語クリップでは全閾値で確実に発火するので、実装は正しく動いている）

**ハーネスの初版バグ**: `input_finished()` と無音パディングを省くと発話が最後の
フレームに残って検出されず、英語ですら recall 0% になる。末尾を必ず流し切ること。

## 決着

```
WAKE_WORD_RELIABILITY = WONT_FIX_ENGLISH_ONLY
```

- 日常利用は `Cmd+Shift+J`（本日実測 7.9 秒 / Physical Hotkey 10/10 PASS）
- 「Hey Jarvis」を**英語寄りに発音**すれば現行設定で通る（英語 TTS 0.9470）
- 設定変更は行わない。`sensitivity: 0.35 / confirmation_frames: 1` を維持
  （下げても 60% 止まりで、誤検出リスクだけが増える）

## 将来やるなら（ユーザー意向: あだ名で起動したい）

日本語カスタムモデルを作る。**あだ名を使えるのはこの経路だけ**の利点がある
（`hey_jarvis` は固定モデルなので語を選べない）。

先行調査が調べた前提:
- **openWakeWord custom training** — 正例は Piper TTS の**合成**（数千件。人間が
  数千回録るのではない）。ただし Piper の speaker-embedding generator は
  **英語のみ**で、日本語音声は別途調達が必要
- **Porcupine** — 個人アカウントのカスタムモデルは **x86_64** とされ arm64 Mac では
  不可の可能性（**未検証**）。built-in `jarvis` は arm64 で動く
- 学習環境: この Mac に NVIDIA GPU は無いが、**ユーザーは Linux + RTX 5070 Ti を持つ**
  （Piper は CUDA で動く）。そちらで学習し、モデルだけ Mac へ持ち込む形が現実的

必要な材料:
1. あだ名の決定（英語音素で表現しやすい語ほど有利）
2. 日本語 TTS 音声の調達（Piper 日本語ボイス or 他の TTS）
3. 5070 Ti 機での学習環境
4. `harness/sweep.py` での recall/FP 検証（**本日作ったものがそのまま使える**）

## 再現手順

```bash
V=<venv>   # sherpa 用は pypinyin + sentencepiece が必要
H=~/AI-Lab/jarvis-wake-benchmark/harness

# 単発の採点＋帯域分析
$V/bin/python $H/wake_probe.py --score-only some.wav
$V/bin/python $H/wake_probe.py --device 2 --seconds 3     # 録音して採点

# openWakeWord の掃引
~/.hermes-venv/bin/python $H/sweep.py

# sherpa の掃引
$V/bin/python $H/sherpa_sweep.py --model-dir <model> --phrases "HEY JARVIS,..."
```

コーパス: `harness/corpus/pos`（TTS 30 件）/ `corpus/neg`（実環境音 60 秒）。
負例は 53 秒しかないので **FP/h の分解能は粗い**（1 回誤発火 = 68/h）。
0.00 は「0.1/h と区別できない」という意味で、ゼロの保証ではない。
本格判定には数時間の負例が必要（openWakeWord 公式は 11.3 時間の検証セット）。
