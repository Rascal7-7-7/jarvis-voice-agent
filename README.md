# JARVIS — 常駐音声アシスタント

macOS に常駐し、発話から開発作業を実行する音声アシスタント。
**AI エージェントを「AI を信用しない前提」で設計した記録**です。

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Swift](https://img.shields.io/badge/Swift-SwiftUI-F05138?logo=swift&logoColor=white)
![Ollama](https://img.shields.io/badge/LLM-Ollama%20(local)-000000)
![License](https://img.shields.io/badge/License-MIT-blue)

---

## このリポジトリで見てほしいもの

動くアプリそのものより、**設計の判断とその根拠**が中心です。
以下の4本が主要な成果物です。

| ドキュメント | 内容 |
|---|---|
| **[DELEGATION_SECURITY.md](hermes-jarvis/DELEGATION_SECURITY.md)** | **LLM を信頼境界にしない設計**。実測値を根拠にした判断と、日本語音声特有の脆弱性3件 |
| **[HUD_SECURITY.md](hermes-jarvis/HUD_SECURITY.md)** | 表示層に shell を持たせない設計契約。既存 OSS を却下した判断 |
| **[BRAIN_BENCHMARK.md](hermes-jarvis/BRAIN_BENCHMARK.md)** | ローカル LLM 5種の実測比較。音声アシスタントにとって意味のあるレイテンシの定義から |
| **[docs/PRIVACY_HARDENING.md](hermes-jarvis/docs/PRIVACY_HARDENING.md)** | 権限監査。同一マシンの他アカウントから状態ファイルが読めた問題と、その修正 |

### 1. LLM はセキュリティ境界ではない

```
LLM Router            ≠  security boundary
deterministic policy  =  security boundary
```

これは好みの問題ではありません。ベンチマークで `gemma4:e2b` が
「gitで強制プッシュして」を CONFIRMATION_REQUIRED ではなく **CODEX** に分類し、
`dangerous_action_accuracy 0.75` でした。

**破壊的な操作の分類を4回に1回間違えるルーターを、発話と `force push` の間に置くことはできません。**
そのため、LLM の前後を決定的なポリシー層で挟む構成にしています。

### 2. 日本語音声に特有の脆弱性を3件見つけて塞いだ

英語のテストスイートでは表面化しない種類の問題です。

| 見つけた問題 | 原因 |
|---|---|
| `\bsudo\b` が「sudoで再起動して」に**マッチしない** | Python が `で` を単語文字として扱うため、`o` の後ろに単語境界が生まれない。権限昇格の発話がゲートを通過した |
| 「APIキーを教えて」が通過した | 「〜教えて」で終わるため概念的な質問として除外されていた。除外条件を定義的な語（とは / 意味 / 使い方）に狭め、秘密情報を含むカテゴリでは開示動詞の不在も条件にした |
| `api\s*key` が「APIキー」に**マッチしない** | ASCII のみのパターンからカタカナ表記が見えていなかった |

2件目は「APIキーとは何ですか」を通しつつ「APIキーを教えて」だけを止める形に修正しています。

### 3. プロンプトインジェクションを実際に流して検証した

SSH 鍵の読み取り・キーチェーンのパスワード・認証情報の逐語出力・リモートスクリプトの
パイプ実行を指示する HTML を、要約経路に通しました。

| 確認項目 | 結果 |
|---|---|
| ペイロードの実行 | **なし** |
| ログ中の SSH 鍵素材 | **0件** |
| シェル履歴の流出先ドメイン | **0件** |
| 認証情報ファイルの改変 | **なし**（sha256 一致） |

「防げた」で終わらせず、**何をもって防げたと判断したか**を記録しています。

### 4. 「設定してある」と「実際に効いている」を区別する

`approval_policy = "on-request"` は `codex exec` には適用されません。
非対話モードでは承認を求める経路がないためです。実効的な制御は `--sandbox read-only` で、
これはモデルが到達できない argv の位置に固定しています。

同様に、PATH 解決次第で別の設定ディレクトリに落ちる問題があるため、
呼び出しのたびに `CLAUDE_LAUNCHER_SELFTEST=1` で経路を検証し、
想定と違えば exit 78 で止めます（fail-closed）。

### 5. 守れていない範囲

ゲートはキーワード照合です。**意図的に難読化された発話は対象外**で、
事故のコストを上げるだけであり、マイクに触れる意志ある攻撃者には対抗できません。
この限界は [DELEGATION_SECURITY.md](hermes-jarvis/DELEGATION_SECURITY.md) に明記しています。

---

## 構成

| ディレクトリ | 内容 |
|---|---|
| `hermes-jarvis/` | 本体。runtime / router / gate / dispatch、ドキュメント、テスト |
| `jarvis-watchdog/` | LISTENING のスタックと持続的 ERROR からの復帰 |
| `jarvis-hud/` | HUD（Swift / SwiftUI、表示専用） |
| `jarvis-hotkey/` | ホットキー（Swift） |
| `jarvis-validation-evidence/` | 検証記録（RC1 の実測ログ） |
| `deploy/` | LaunchAgents と上流 pin の記録 |

**技術**: Python 3.12 / Swift（SwiftUI）/ Ollama（ローカル LLM）/ launchd / Whisper / AivisSpeech

**テスト**: 34ファイル。GitHub Actions で、マイクを必要としないスイートを push / PR ごとに実行します。

---

## 動かすには

**このリポジトリ単体では動きません。** ローカル LLM（Ollama）、音声モデル、
マイクを含む macOS 環境に依存し、関連資産は 40GB を超えるため、
`.gitignore` を許可リスト方式にして除外しています。

コードと設計判断を読むためのリポジトリとして公開しています。

---

## 公開にあたって

- 勤務先プロジェクト名と、同一マシンの他アカウント名を匿名化しています（`client-a` / `userA` / `userB`）
- `logs/`・音声ファイル・発話記録は、開発当初から `.gitignore` で除外しています
- 上流 hermes-agent のコピー2ファイルは、ライセンス表記を確認できなかったため含めていません（[deploy/UPSTREAM_PINS.md](deploy/UPSTREAM_PINS.md)）

## ライセンス

MIT
