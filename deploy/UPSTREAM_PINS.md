# 上流 pin と、git 管理外に置いた凍結対象

## 公開リポジトリでの扱い（2026-09-12）

`deploy/pinned-upstream/` に置いていた上流 hermes-agent のコピー2ファイル
（`tools/voice_mode.py` / `tools/wake_word.py`）は、**この公開リポジトリには含めていない**。

理由: 上流のライセンス表記を確認できなかったため。素性の分からない第三者の
コードを再配布しない。参照している箇所の説明は本ファイルに残してあるので、
どこに依存しているかは追える。

## src/hermes-agent-v2026.8.27（git 管理外）

`hermes-jarvis/src/hermes-agent-v2026.8.27/` は上流 hermes-agent の pin 済みコピー。
**それ自体が git リポジトリ**（`.git` 配下に 50MB 超の pack を持つ・全体 317MB）なので、
JARVIS_project では追跡しない（ネストリポジトリ化を避けるため）。

ただし RC1 manifest はこの配下の **2 ファイル**を凍結対象に含んでいる:

| ファイル | 役割 |
|---|---|
| `src/hermes-agent-v2026.8.27/tools/wake_word.py` | wake word 検出 |
| `src/hermes-agent-v2026.8.27/tools/voice_mode.py` | AudioRecorder / device 解決・`follow_default_device` |

この2ファイルの同一性は **`docs/JARVIS_DAILY_RC1_SHA256SUMS.txt` が唯一の担保**。
変更する場合は manifest を更新し、変更前のコピーを `hermes-jarvis/backups/` に退避すること
（git の巻き戻しが使えないため）。

## ビルド済み .app バイナリ（git 管理外）

RC1 manifest は以下を凍結対象に含むが、バイナリは git に入れない方針:

| ファイル | ソースの所在 |
|---|---|
| `~/Applications/JARVIS HUD.app/Contents/MacOS/JarvisHUD` | `jarvis-hud/Sources/` |
| `~/Applications/JARVIS Hotkey.app/Contents/MacOS/JarvisHotkey` | `jarvis-hotkey/Sources/` |

ソースを追跡し、バイナリの同一性は SHA manifest 側で担保する。
再ビルド手順は各プロジェクトの `scripts/` を参照。

## LaunchAgents

`deploy/LaunchAgents/*.plist` は `~/Library/LaunchAgents/` の**コピー**。
実体を移動していない（launchd の登録を壊さないため）。
反映するには実体側へコピーし直して `launchctl bootout` → `bootstrap` が必要。

## 参照コピー（ロールバック用）

ネスト git のため `src/` 配下は git add できない（実測: git が無言で拒否）。
そこで RC1 凍結対象の2ファイルを **参照コピー**として `deploy/pinned-upstream/tools/` に置く。

**実体は `hermes-jarvis/src/hermes-agent-v2026.8.27/tools/` 側が権威。**
コピーは巻き戻し用のスナップショットであり、実行時に読まれることはない。

作成時点（2026-09-09 15:32:07）の SHA256:

```
0d39b668217e92f8f5189dd45b40b9f99ce950c8a898316b7d50b091b201b399  wake_word.py
7cfc1e4d251a20fb3681d6054545da6237ec9bb91cd9413d43771de7aece1410  voice_mode.py
```

実体を変更したらこのコピーと SHA も更新すること。乖離検知:

```bash
diff hermes-jarvis/src/hermes-agent-v2026.8.27/tools/voice_mode.py \
     deploy/pinned-upstream/tools/voice_mode.py
```
