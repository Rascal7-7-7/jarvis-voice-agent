# REAL_LOGIN_WARM_VALIDATION — 再起動後にやること

対象: JARVIS_DAILY_RC1_20260902 (freeze中 / production source 変更禁止)
前提スナップショット: prelogout-snapshot-20260908-192326.txt (再起動前の状態)

## 手順（再起動後）
1. login したら JARVIS 系を**手動起動しない**
2. `jarvis-status` で 6プロセス確認: runtime / watchdog / HUD / Hotkey / Ollama / Gateway
3. `startup warm success` と `model resident` を確認する
4. **3 が確認できてから** Cmd+Shift+J → 「ハロー」と短く発話
   （warm 完了前に話すと Known Gap #4 EARLY_TURN_DURING_WARM が混入し測定が汚れる）
5. 先に PA 1/1 と PEAK_RMS>0 を確認。無音なら測定せず切り分けへ
6. 初回turn の backend / first audio latency を測定

## 判定
| 指標 | PRE_FIX | 期待 |
|---|---|---|
| backend | 17,866 ms | 概ね 4〜6 s |
| first audio | 26,124 ms | 概ね 8〜10 s |
主判定 = cold-start 約14秒のペナルティが消えたか。
PASS → REAL_LOGIN_WARM_VALIDATION = PASS / OLLAMA_STARTUP_WARM_RACE = CLOSED → FREEZE 解除

## 再起動前に判明していた所見（未パッチ・分類待ち）
1. stream replacement 後の deaf stream → 全ターン PEAK_RMS=0。runtime 再起動で回復 → BACKLOG (Known Gap #7)
2. ログは "shared stream replaced" を記録するがカウンタ replacements が 0 のまま → BACKLOG (計器不整合)
3. watchdog は無音キャプチャを検知できない（対象は LISTENING stuck と persistent ERROR のみ）→ BACKLOG
4. HUD の launchd application job が無い（app 本体は存在）→ **再起動後に自動復活するかで判定**
   ※ 判定情報が失われるので、再起動後に HUD を手動起動しないこと

## 再起動前の実測値（比較用）
- runtime PID 91207 / 起動 2026-09-07 21:23:12 / launchd local.jarvis.runtime 最終終了 -9 (SIGKILL)
- Audio PA 1/1 / repl 0 / err 0（回復済み）
- capture TURN 4/5/6 PEAK_RMS = 1446 / 1593 / 1576（音声正常）
- jarvis-status = FAIL（HUD not running 起因）
- RC1 manifest 25/25 OK / jarvis_runtime.py SHA256 = cc212b9923e189359040d9374f09efa75a9ca6fac635e5c8aab2e6be5c08ab4e
