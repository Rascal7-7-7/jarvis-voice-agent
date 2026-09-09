# REAL_LOGIN_WARM_VALIDATION 試行2（logout/login 経路）  2026-09-08 20:58

## 判定: REAL_LOGIN_WARM = **PASS**（主判定 = cold-start ペナルティ消滅を確認）

### warm の raw evidence
```
20:53:43       ollama serve (PID 18913) と runtime (PID 18907) が同時起動 ← §9 の race を実際に通過
20:53:47.837   ollama startup readiness: waiting attempt=1
20:54:11.324   ollama "Listening on 127.0.0.1:11434"   ← プロセス起動から 28.3s で bind
20:54:22.841   ollama startup readiness: ready after=35.0s attempts=9   ← budget 45s 内・余裕 10s
20:56:04.629   ollama warm (startup): gemma4:e2b ready in 101.79s keep_alive=60m
```
- 6/6 プロセス launchd 自動起動（手動起動なし）。HUD も自動起動。
- Audio PA 1/1 / repl 0 / err 0（replacement 発生せず）device='外部マイク'
- jarvis-status = HEALTHY（全項目グリーン）/ resident: vram 7,231,063,981 expires +60m
- 負荷: LA 163 → 42（login 直後のスパイク）。reboot 時の 290 より穏やか。
- warm 101.79s の内訳 = readiness 待ち 35s + モデルロード約67s（高負荷のため。アイドル時は 17.96s）
- **wake listener は warm を待たず armed**（§9 設計通り）。

### 初回turn の raw evidence（TURN=1 / 20:57:01）
```
WAKE=259ms  CAPTURE=30016ms  STT=3414ms  GATE=1ms  ROUTER=2625ms
LOCAL_FAST=3048ms  TTS_GENERATION=581ms  PLAYBACK_DURATION=5473ms
TOTAL_TO_FIRST_AUDIO=39966ms
capture TURN=1 silence_cb_fired=False FRAMES=2813 PEAK_RMS=32768 wav=True
```
TURN=2（20:57:54）も同様: CAPTURE=30016ms / silence_cb_fired=False / FRAMES=2813 / PEAK_RMS=32139
/ STT=1602ms / ROUTER=3029ms / LOCAL_FAST=3521ms / TOTAL_TO_FIRST_AUDIO=39116ms

### 主判定の根拠（cold-start ペナルティは消えた）
- turn 経路に**モデルロードが存在しない**（warm 完了後に resident 状態で実行）
- PRE_FIX backend 17,866ms → 実測 ROUTER 2,625ms + LOCAL_FAST 3,048ms = **5,673ms**
- PRE_FIX の約14秒 cold-start ペナルティは観測されない → **PASS**

### ただし first audio の数値は clean ではない（再測定が必要）
TOTAL_TO_FIRST_AUDIO=39,966ms のうち **30,016ms が CAPTURE** であり、
これは warm/cold とは無関係な入力ゲインの問題（下記の新規所見#9）。
30s は capture の上限であり、`silence_cb_fired=False` = 無音検出が一度も発火していない。

## 新規所見 #9（本 run で判明・BLOCKER 級の実用障害）
**外部マイクの入力音量が 100 で、無発話でも常時クリッピングしている。**
```
ffmpeg -f avfoundation -i ":2" -t 2 -af volumedetect   # ":2" = 外部マイク・無発話
mean_volume: -13.7 dB / max_volume: 0.0 dB / histogram_0db: 303
osascript: input volume: 100   （内蔵マイク運用時は 35）
```
→ 常時フルスケール入力のため silence 検出が発火せず、**毎ターン capture が 30s 上限まで走る**。
→ 1ターンあたり約 +26s。体感「少し長い」の正体はこれ。
→ 20:41 の 外部マイク turn は PEAK_RMS=20502 / FRAMES=345 / silence_cb_fired=True で正常だった。
   つまりデバイス固有ではなく**入力音量設定の問題**。
→ 併せて所見#8 の系列: JARVIS は clipping も `silence_cb_fired=False` も利用者に通知しない。
   どちらも既にログには出ており、HUD へ出す価値がある。

## 次アクション
1. 入力音量を下げる（30〜40 目安）: システム設定 → サウンド → 入力、または
   `osascript -e 'set volume input volume 35'`
2. `osascript -e 'get volume settings'` で反映確認
3. Cmd+Shift+J → 「ハロー」を1回 → CAPTURE が 30016ms から短縮され
   `silence_cb_fired=True` になるか、clean な TOTAL_TO_FIRST_AUDIO を取得
   ※ runtime 再起動は不要（入力音量はデバイス束縛とは別レイヤ）

## ステータス更新
- REAL_LOGIN_WARM_VALIDATION = **PASS**
- OLLAMA_STARTUP_WARM_RACE = **CLOSED**
- 所見#6（45s budget 不足）= **BACKLOG**（通常の logout/login では 35.0s で足りている。
  不足したのは cold reboot + LA 290 の極端条件のみ）
- → **FREEZE 解除の条件を満たした**
