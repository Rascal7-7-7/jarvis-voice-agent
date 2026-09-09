# JARVIS_project

常駐音声アシスタント JARVIS のスタック一式。ルートは `~/AI-Lab`。

`~/AI-Lab` には 40GB 超の無関係な資産（音声ベンチマーク・GPT-SoVITS モデル・codex 実験）が
同居しているため、`.gitignore` は**許可リスト方式**（全部無視してから必要な物だけ戻す）。

## 構成

| ディレクトリ | 内容 |
|---|---|
| `hermes-jarvis/` | 本体。runtime / router / gate / dispatch、docs、tests |
| `jarvis-watchdog/` | LISTENING stuck・persistent ERROR の回復 |
| `jarvis-hud/` | HUD（Swift、viewer-only） |
| `jarvis-hotkey/` | Cmd+Shift+J のホットキー（Swift） |
| `jarvis-ack-helper/` | Wake ACK のプロトタイプ（未統合） |
| `jarvis-activate/` | 手動アクティベーション CLI |
| `jarvis-validation-evidence/` | 検証記録（RC1 の実測ログ） |
| `deploy/` | LaunchAgents のコピー、上流 pin の記録 |

## git 管理外にしているもの

`deploy/UPSTREAM_PINS.md` に理由と対処を記録。要約:

- `hermes-jarvis/src/hermes-agent-v2026.8.27/` — 上流 pin 済みコピー。**それ自体が git repo**（317MB）
- ビルド済み `.app` バイナリ — ソースを追跡し、同一性は SHA manifest で担保
- `logs/` — 発話・応答テキストを含む（privacy）
- 音声ファイル・`.build/`・`DerivedData/`

## 現在の状態

`JARVIS_DAILY_RC1_20260902` が Release Candidate。同一性の権威は
`hermes-jarvis/docs/JARVIS_DAILY_RC1_SHA256SUMS.txt`（25ファイル）。

```bash
cd ~ && shasum -a 256 -c ~/AI-Lab/hermes-jarvis/docs/JARVIS_DAILY_RC1_SHA256SUMS.txt
```

状態確認は `jarvis-status`。検証の経緯と残作業は
`jarvis-validation-evidence/` および `hermes-jarvis/docs/JARVIS_DAILY_RC1_BASELINE.md`。
