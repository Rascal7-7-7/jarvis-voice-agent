# FREEZE 解除  2026-09-09 15:50:39

ユーザーが明示的に解除を許可。

## 解除の根拠
- REAL_LOGIN_WARM_VALIDATION = **PASS**
  （logout/login 経路で ollama bind 28.3s / readiness ready after=35.0s attempts=9・budget 45s 内）
- OLLAMA_STARTUP_WARM_RACE = **CLOSED**
- 主判定: backend 17,866ms → 5,673ms。cold-start 約14秒のペナルティ消滅を確認
- 副産物: watchdog persistent ERROR 回復を実機観測（LIVE VERIFIED へ昇格）

## 解除時点の状態
- RC1 manifest: 25/25 OK（改変なし）
- jarvis-status: HEALTHY
- git 管理: 開始済み（JARVIS_project / private）。巻き戻し手段を獲得
- 権限: Claude auto モード / Codex approval_policy=never + sandbox=workspace-write

## 着手順（合意済み）
1. 所見#8 の可視化 ← ここから
2. マイク案C（優先順位付きデバイス選択・再起動経由）
3. JARVIS 音声 → 秘書の接続（jarvis_gate 経由・router 宛先 SECRETARY を追加）

## production source 変更時のルール（git 化後）
- 変更したら RC1 manifest を更新する（同一性の権威は依然 manifest）
- src/ 配下（wake_word.py / voice_mode.py）を変更した場合は
  deploy/pinned-upstream/ の参照コピーと SHA も更新する
- TDD（RED→GREEN→REFACTOR）を守る。jarvis-status は 61 tests が既にある
