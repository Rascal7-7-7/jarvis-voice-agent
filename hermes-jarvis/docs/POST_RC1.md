# RC1 以降の変更 — manifest の FAILED は正常です

## 読む前に

```bash
cd ~ && shasum -a 256 -c ~/AI-Lab/hermes-jarvis/docs/JARVIS_DAILY_RC1_SHA256SUMS.txt
```
が **FAILED を報告するのは異常ではありません**。RC1 は 2026-09-02 の凍結点であり、
FREEZE は 2026-09-09 に解除済み。以降の変更は git が同一性を担保します。

**RC1 manifest は書き換えません。** 凍結点の歴史的記録として保持します
（後の解釈に合わせて過去の証拠を書き換えない、という運用方針）。

## RC1 からの差分

| ファイル | RC1 の SHA256 | 現在の SHA256 | 変更理由 |
|---|---|---|---|
| `tools/jarvis_status.py` | `...`（manifest 参照） | `1eade457e21e7327866ff31db17f329d99ffb72e095ad4bd8519be46c3c8c387` | Capture チェックの組み込み |
| `tools/jarvis_status_checks.py` | `...`（manifest 参照） | `20c892c75b259662c06e257f0ef147a0210219b8df7d70b4ccffb193bdd9054c` | `evaluate_capture_health` 追加 |

期待される照合結果: **23/25 OK・2 FAILED**（上記2件）。
これ以外が FAILED になった場合は調査対象です。

## 変更内容: Capture チェック（所見#8 の可視化）

2026-09-08/09 の検証で、音声が通らない・遅い状態が**ログには出ているのに
status/HUD には出ていない**ため、切り分けに数時間を要した。3つの失敗モードを可視化した。

| 表示 | 条件 |
|---|---|
| `PEAK_RMS=0 — マイクが無音です（Nターン連続）` | `PEAK_RMS == 0` |
| `PEAK_RMS=N — 入力がクリッピングしています` | `PEAK_RMS >= 32000` |
| `silence_cb_fired=False / FRAMES=N — 無音検出が発火せず capture が上限まで走りました` | `silence_cb_fired == False` |

判定は**最後の capture** に対して行う（過去の事故で永久に WARN が残らないため）。
無音が連続している場合は本数を出す（クラムシェルのような持続的故障は本数が状況を語る）。

tests: `tests/test_jarvis_status.py` に 8 件追加 → **61 → 69 passed**。
他の主要スイート（startup_warm 11 / renew_policy 26 / wake_feed_gate 11）も維持。

## 所見#9 の訂正

当初「クリッピングにより無音検出が発火しない」と帰属したが、**不正確だった**。
2026-09-09 の実測（外部マイク・8ターン連続）:

```
TURN=1..8  silence_cb_fired=False  FRAMES=2813前後（30s上限）
PEAK_RMS=22545 / 23529 / 24997 / 20759 / 13429 / 19173 / 2571 / 3775
```

**PEAK_RMS は 2,571〜24,997 で飽和していない**（クリッピング閾値 32000 未満）。
つまりクリッピングの有無に関係なく、**この外部マイクでは silence callback が発火しない**。

候補: デバイスのノイズフロアが silence 判定の閾値を常に上回っている。
`Recording too quiet (peak RMS=0 < 200)` の 200 は「捨てる」閾値で、silence callback の
閾値とは別。次の調査は silence 判定のしきい値と当該デバイスのノイズフロアの比較。

**実用インパクト: 1ターンあたり +26 秒**（capture 30s 上限に毎回張り付く）。
