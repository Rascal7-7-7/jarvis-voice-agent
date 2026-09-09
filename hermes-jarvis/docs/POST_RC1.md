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

---

## 変更2: 適応的な無音しきい値（所見#9 の解決）

### 問題
`SILENCE_RMS_THRESHOLD = 200`（upstream 固定値）は「静かなデバイス」を暗黙の前提にしていた。
silence callback は `rms <= threshold` が `SILENCE_DURATION_SECONDS = 3.0` 秒連続で発火するが、

| デバイス | 無発話時のノイズフロア | 200 との比 | 発火 |
|---|---|---|---|
| 内蔵マイク | PEAK_RMS 1,446〜1,764 | 発火する範囲 | ✅ |
| 外部マイク（ジャック） | **mean RMS 2,663（実測 2,965）** | **13倍** | ❌ 原理的に不可能 |

結果、外部マイク運用では 8 ターン連続で `silence_cb_fired=False` となり、
capture が毎回 30 秒上限まで走っていた（1 ターン +26 秒）。
200 まで下げるには -22.5 dB の減衰が必要で、入力音量調整では実用的に届かない。

**クリッピングは原因ではなかった**（PEAK_RMS 2,571〜24,997 の非飽和でも発火せず）。
当初の帰属は誤りで、真因はノイズフロアがしきい値を常に上回っていたこと。

### 対策
`bin/shared_audio.py` に `NoiseFloorTracker` を追加。

```
threshold = clamp(noise_floor * 2.5, 200, 8000)
```

- 共有ストリームはターン間も idle フレームを見ている（`_on_idle_frame`）ので、
  ノイズフロアは追加の録音なしで測れる。4 チャンクに 1 回だけ RMS を取り callback を軽く保つ
- 平均ではなく**中央値**。idle 中に wake word 発話が混ざってもフロアが引き上げられない
- 下限 200 は upstream 値と一致。**静かなデバイスでは挙動が変わらない**
- 上限 8,000 は安全弁（上げ過ぎると発話ごと無音扱いになる）
- 適用は `ensure_healthy_for_turn`（ターン前フック）で毎ターン
- `counters()` に `noise_floor` / `silence_threshold` を追加。capture ログ行に出る

**upstream は無変更。** `_silence_threshold` は `AudioRecorder` のインスタンス属性なので、
`src/` 配下の `voice_mode.py` を触らずに外から設定できた
（`deploy/pinned-upstream/` との乖離も発生しない）。

### 実測結果（2026-09-09 16:09）
```
silence threshold 200 -> 7412 (noise floor 2965)
capture TURN=1 silence_cb_fired=True FRAMES=385 PEAK_RMS=9708
WAKE=260 CAPTURE=4111 STT=856 GATE=0 ROUTER=2280 LOCAL_FAST=2380 TTS=721
TOTAL_TO_FIRST_AUDIO=10635
```

| 指標 | 前 | 後 | 差分 |
|---|---|---|---|
| `silence_cb_fired` | False | **True** | — |
| `CAPTURE` | 30,016 ms | **4,111 ms** | **-86%** |
| `TOTAL_TO_FIRST_AUDIO` | 39,966 ms | **10,635 ms** | **-73%** |

§13 の first audio 期待レンジ（概ね 8〜10 秒）に到達。

tests: `tests/test_noise_floor.py` 11 件（ハードウェア不要なので wake lease 保持中も実行可能）。
既存 179 件に影響なし。

### 所見#2 の原因（判明）
`replacements` カウンタが 0 のままだったのはバグではなく、**置換経路が 2 つある**ため。
`SharedAudioInput.ensure_healthy_for_turn` は自分の置換だけを数えており、
`voice_mode.py` 側の silence watchdog による `controlled replacement N/3` は数えていない。
案C で置換経路を触る際に整理する。

---

## 変更3: 入力デバイスの優先順位選択（案C）

### 問題
upstream は「OS の既定入力」をそのまま開く。macOS の既定は
「最後に接続したものが勝つ」なので、固定優先順位にならない。
さらに `follow_default_device = False`（CoreAudio デッドロック回避）のため
稼働中は追従しない ＝ **起動時の選択が実質的に唯一の機会**。

そこにクラムシェル運用が重なる。蓋を閉じている間、内蔵マイクは
**列挙されるが全サンプル 0 を返す**（ffmpeg で JARVIS 非経由でも -91.0 dB を実測）。
優先順位に内蔵を残すと、外部マイクを抜いた瞬間に無音デバイスへ落ちる。

### 対策
`bin/audio_devices.py`（新規）で優先順位選択を行う。

```
TIER_EXTERNAL = 1   ジャック・USB の実マイク
TIER_WIRELESS = 2   Bluetooth・Continuity（iPhone をマイクにする）
TIER_BUILTIN  = 3   内蔵マイク（クラムシェル中は候補から外す）
TIER_EXCLUDED = 99  仮想ドライバ（NoMachine / BlackHole / Loopback / Soundflower 等）
```

適用方法: `sd.default.device` を**プロセスローカルに**設定する。
upstream の `sd.InputStream(...)` は `device=` を渡していないため、これで開く先が決まる。
**ユーザーのシステム既定入力は変更しない。upstream も無変更。**

### 設計上の判断
1. **クラムシェルは推測せずシステムから取る。** `ioreg -r -k AppleClamshellState`。
   ただし**取得失敗（None）を False と混同しない**。不明を「開いている」と扱えば
   内蔵マイクを外せず、「閉じている」と扱えば蓋が開いた機体で内蔵マイクを失う。
2. **内蔵と Continuity の判別順序が意味を持つ。**
   `MacBook Proのマイク` と `Clayのマイク` は同じ語尾なので、機種名の判定を先に置く。
   実機のデバイス名には U+200E (LRM) が前置されるため制御文字を正規化する。
3. **稼働中のストリームは差し替えない。** 切り替えは runtime 再起動で行う。
   watchdog の persistent ERROR 回復は 2026-09-08 21:07 に実機観測済みで、
   束縛先デバイス消失 → ERROR → 15s grace → kickstart -k → 新 PID →
   新デバイスへ再束縛、が動くことを確認している。
4. **未知のデバイス名は TIER_EXTERNAL にする。** USB マイクの可能性が最も高く、
   仮想ドライバは除外リストで先に落としている。

### 実機結果（2026-09-09 16:52）
```
clamshell_closed = True
候補: tier=1 [5] '外部マイク' / tier=2 [2] 'Clayのマイク'
除外: tier=3 [7] 'MacBook Proのマイク'（クラムシェル）
      tier=99 [9][10] NoMachine（仮想）

16:52:12.893  input device = '外部マイク' (tier 1, index 5, clamshell_closed=True)
16:52:12.944  stream open  PA 1/1
Device  ✓ 外部マイク
```

### jarvis-status の Device チェック
```
Device ✓ 外部マイク
Device ! 優先デバイスが '外部マイク' に変わりました（束縛中は 'MacBook Proのマイク'）。
         切り替えには runtime の再起動が必要です
Device ! 束縛中 '外部マイク' / 選択可能な候補がありません
         （クラムシェル中に外部マイクを抜いた等）
```
所見#8 の教訓に従い「何が起きていて、どうすれば直るか」まで出す。

`counters()` に `bound_device` / `preferred_device` / `device_change_pending` を追加。

tests: `test_audio_devices.py` 17件（新規）、`test_jarvis_status.py` 69 → 74件。

### 残: 自動切り替えは未実装
優先デバイスの変化は**検知して通知するだけ**で、自動再起動はしていない。
利用中に勝手に再起動されるのを避けたため。信頼が積めた段階で
`device_change_pending` を watchdog の再起動条件に加えるのが次の一手。

---

## 変更4: 置換の数え漏れを修正（所見#2）

置換経路は**3つ**あり、以前は 2 だけが自分で `replacements` を加算していた。
ログに `controlled replacement 1/3` が出ていてもカウンタが 0 のままだったのはこれが理由。

| 経路 | 場所 | 修正前 |
|---|---|---|
| 初回オープン | `shared_audio.open_once` | 対象外 |
| 死んだストリームの復旧 | `shared_audio.ensure_healthy_for_turn` | 加算していた |
| **無音 watchdog** | **`jarvis_runtime.py:1230`** | **加算していなかった** |

第3経路は `detector.audio_silent` を契機に走り、`shared.open_once()` を呼び直す。
`open_once` は `_note_stream()` を通るので `pa_open_count` は増えるが、
`replacements` は `ensure_healthy_for_turn` 側にしか無かった。

**対策**: 数える場所を `_note_stream()` に集約した。ストリームオブジェクトの
入れ替わりを見ているのはこのメソッドだけなので、どの経路から来ても必ず通る。
`ensure_healthy_for_turn` の明示加算は二重計上になるため撤去した。

**副産物**: 無音起因の置換は `open_once()` を呼ぶため、案C のデバイス優先順位
再選択も自動で走るようになった。無音デバイスに張り付いたまま置換を繰り返す事故が減る。

tests: `test_replacement_counter.py` 5件（新規）。

---

## 変更5: 音声から秘書を呼ぶ経路（SECRETARY 宛先）

### 到達経路は決定的なキーワードだけ
`DELEGATION_SECURITY.md` の中心原則は「**LLM Router ≠ security boundary**」。
router は gemma4:e2b で `dangerous_action_accuracy 0.75` と実測されている
（「gitで強制プッシュして」を CODEX と分類した）。したがって:

- **`SECRETARY` を `jarvis_router.LABELS` に入れない。**
  LABELS は LLM 出力の検証に使われるので、入れなければモデルはこの宛先を発明できない
- 到達は `_OVERRIDES` の決定的パターン `(秘書|ひしょ)` のみ
- `jarvis_gate.check()` は override より前に走る（`route()` の順序）

実測での確認:
```
秘書、状況を教えて            → SECRETARY  explicit_override  llm_used=False
秘書、このファイル全部消して   → CONFIRMATION_REQUIRED  security_gate  ← gate が勝つ
LABELS に SECRETARY           → False
```
この契約は `test_jarvis_secretary.py` でテストとして固定した。

### 音声からは書き込まない（v1 の境界）
JARVIS の委譲は `--permission-mode plan` / `--sandbox read-only` が argv に
ピン留めされ、「Write delegation. Not enabled.」と明記されている。
一方 `secretary dispatch --headless` は書き込む。直結すると
**JARVIS が実質的な書き込み経路を獲得**するので、v1 では:

- 読み取り系（状況・一覧・レポート）は即実行する
- dispatch は**キュー登録のみ**。実行はテキスト側の明示操作に委ねる

音声は「指示を取りこぼさず捕まえる」ところまでを担い、実行の引き金は人が引く。

### 実装中に踏んだバグ
1. `jarvis-dispatch` の変数名は `$PY`。`$VENV_PY` は存在しない（`zsh -n` で検出）
2. `subprocess` の strict デコードで `UnicodeDecodeError`。呼び先はシェルスクリプトで
   不正バイトが混ざり得るため `errors="replace"` にした。読み上げ文を作るために
   呼んでいるのだから、1 バイトの欠けで全体を落としてはいけない
3. **LC_ALL の強制が新しい障害を作った。** `survey.sh` が
   `note?: unbound variable` で落ちた。`${note:+$note・}` の**パラメータ展開の中に
   多バイト文字**があり、ロケール次第で変数名ごと壊れる。ASCII 区切りに置き換え、
   4 種のロケール（en_US.UTF-8 / C / ja_JP.UTF-8 / 未設定）で検証した
4. 読み上げ品質: `survey.sh` はコミット 0 件のリポジトリに `idle_days=999` を入れる。
   これは番兵値なので「999日放置」と読み上げるのは事実と違う。日数のあるものと分けた

tests: `test_jarvis_secretary.py` 19件（新規）。
