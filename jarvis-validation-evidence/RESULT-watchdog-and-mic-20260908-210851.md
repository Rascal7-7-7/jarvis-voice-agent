# watchdog persistent-ERROR 回復の LIVE 検証 + 内蔵マイク故障の確定  2026-09-08 21:08

## 1. watchdog persistent ERROR recovery = **LIVE VERIFIED**（handoff §8 はテストのみだった）
契機: ユーザーがジャックから機器を抜き、runtime の束縛先 `外部マイク` が消滅した。
```
21:07:19  runtime state=ERROR "mic silent; replacing input stream"
21:07:21  ERROR shared stream replacement failed
21:07:24  state=ERROR "input stream replacement failed"
21:07:39  state=ERROR 継続（15s 経過）
21:07:39  watchdog restart_decision cause=PERSISTENT_ERROR watched_s=15.0 listening_s=15.0
21:07:46  watchdog restart_result ok=true old_pid=18907 new_pid=23821 restart_return=0
21:07:51  state=IDLE pid=23821 "listening on 'MacBook Proのマイク'@48000Hz"
```
→ ERROR grace 15s / `launchctl kickstart -k` / 新 PID / IDLE 復帰、すべて §8 の記述通り。
→ 併せて「**再起動時には新しい既定入力デバイスへ再束縛される**」ことも実証
   （`follow_default_device=False` の回復手順が実際に機能する裏付け）。

## 2. 内蔵マイク（MacBook Proのマイク）の故障を確定
以下すべてを経ても `mean_volume/max_volume = -91.0 dB`（完全無音・全ゼロ）:
- full reboot（19:38）
- logout/login（20:53）
- coreaudiod 再起動（login 時 20:53:43）
- **ジャックから機器を抜いた状態**（21:0x / `外部マイク`・`外部ヘッドフォン` がデバイス一覧から消滅、
  既定入力 = MacBook Proのマイク、input volume 54）
測定は ffmpeg avfoundation で JARVIS を介さず実施。frame は届き値が全ゼロ。

→ **ジャック占有起因という前回の仮説も否定された。**
→ 内蔵マイクは OS/ハードウェアレベルで無音。**JARVIS 外部の問題**であり RC1 とは無関係。
→ 本日 18:03 までは同一デバイス名で PEAK_RMS 1576 と正常だったため、18:03〜19:38 の間に発生。
→ 追調査候補: Apple Diagnostics / NoMachine の仮想オーディオドライバ（`NoMachine Audio Adapter`,
  `NoMachine Microphone Adapter` がインストール済み。daemon は非稼働）/ ハードウェア点検。

## 3. 現在の状態
runtime PID 23821 が内蔵マイク（無音）に束縛され、
ERROR「mic silent」→ 置換 → IDLE を往復（21:07:56 / 21:08:01）。
jarvis-status = DEGRADED。音声入力は事実上使用不能。

## 4. 未確認の観察（要確認・低優先）
watchdog ログの SPEAKING イベントの `detail` に応答本文の断片が入っている:
`{"state": "SPEAKING", "detail": "こんにちは。何…"}`
handoff §7 は「production で表示しないもの: heard_text / expanded response / raw tool args」
と規定。state detail 経由で応答テキストが watchdog ログへ落ちているのは意図的か要確認。
heard_text ではないため §17 の禁止事項には触れていない可能性が高い。

## 5. 次アクション
内蔵マイクが復旧しない限り JARVIS の音声入力は成立しない。当面の実用復帰手順:
1. ジャックに機器を戻す（`外部マイク` が再出現）
2. **input volume は現在 54**（クリッピング時は 100 だった）→ 所見#9 が同時に解消する可能性が高い
3. runtime 再起動で `外部マイク` へ再束縛
4. Cmd+Shift+J → clean な first audio 測定（CAPTURE が 30s 上限に張り付かなくなるか）

## 訂正（4回目）: 内蔵マイクは「故障」ではなく **clamshell による正常な使用不可**  2026-09-09

ユーザー申告: **現在 Mac を閉じて外部モニターで使用中（クラムシェル運用）**。
→ 蓋を閉じている間、MacBook 内蔵マイクは利用できない。デバイスは列挙されるが全ゼロ。
→ 上記「2. 内蔵マイクの故障を確定」は**誤り**。raw evidence は残すが結論を差し替える。

### 全 evidence がこれで整合する
| 時刻 | 実測 | 説明 |
|---|---|---|
| 16:58-18:03 | PEAK_RMS 1446-1764 / silence_cb_fired=True | 蓋が開いていた |
| 18:03〜19:38 の間 | — | **クラムシェル運用へ移行** |
| 19:38 reboot 後 | -91.0 dB | 蓋が閉じたまま |
| 20:53 logout/login 後 | -91.0 dB | 同上 |
| 21:0x ジャック抜き後 | -91.0 dB | 同上（ジャック占有仮説も無効だった理由） |

Apple Diagnostics / NoMachine ドライバ / ハードウェア修理の追調査案は**すべて不要**。

### 重要な帰結: 日常運用構成が RC1 の検証構成と違う
- ユーザーの通常構成は **クラムシェル＝内蔵マイク使用不可**。
  したがって daily use は**外部マイク（ジャック / USB / Continuity `‎Clayのマイク`）が必須**。
- RC1 のベースライン計測（PEAK_RMS 1500 前後・silence_cb_fired=True）は
  **内蔵マイク前提**であり、実際の日常構成とは異なる。
- 所見#9（クリッピングで silence 検出が発火せず毎ターン 30s）は
  外部マイク運用における**恒常的な問題**として扱う必要がある。単発の設定ミスではない。

### Known Gap の優先度が上がる
`CLAMSHELL_SPECIFIC_RECOVERY = UNVERIFIED`（handoff §8 / §15 #2）は
「まだ観測されていない未検証項目」ではなく、**ユーザーの日常構成そのもの**。
FREEZE 明けの優先度を上げるべき。

### 次アクション（変更）
1. 外部マイクを接続（ジャック / USB / Continuity のいずれか）
2. input volume は現在 **54**（クリッピング時は 100）→ 所見#9 の再確認
3. runtime 再起動で外部マイクへ再束縛
4. Cmd+Shift+J → clean な first audio 測定
