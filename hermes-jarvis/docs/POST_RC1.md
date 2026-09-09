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

---

## 変更6: 読み上げ前のサニタイズ（所見#10）と Output チェック

### 問題1: ツール呼び出しの生 JSON を 33 秒間読み上げた
2026-09-09 17:26 実測:
```
state=SPEAKING clarify{questions:[{choices:[<|"|>症状の詳細を教えてください<|"|>,…
PLAYBACK_DURATION=32992ms

state=SPEAKING clarify{questions:[{choices:[<|"|>はい<|"|>,<|"|>いいえ<|"|>],que…
PLAYBACK_DURATION=23421ms
```

原因は2段:
1. `jarvis-dispatch` の `LOCAL_TOOL|WEB|LOCAL` 分岐は **C8（読み上げ整形）を通らない**。
   C8 は `esac` の後にあり、`CODEX` / `CLAUDE` のフォールスルーにしか適用されない。
   よって hermes の戻り値がそのまま TTS に渡る。
2. `<|"|>` は hermes の Python ソースに**存在しない**。つまり gemma4:e2b が
   **テキスト形式の壊れたツール呼び出しを出力**し、hermes がそれを回答として返した。

handoff §7 は「production で表示しないもの: heard_text / expanded response /
raw tool args」と規定している。**読み上げも同じ扱いにすべき**だった。

音が出ていなかったので今まで無害だったが、出力を直すと顕在化する（実際に顕在化した）。

### 対策: bin/jarvis_speech.py
読み上げ直前に決定的なサニタイズを行う（モデルを使わない）。

- モデル特殊トークン `<|…|>`、`name{…}` 形、`questions:[` / `choices:[` を検出
- clarify の場合は `question` フィールド、無ければ最初の引用文字列を取り出す
- 取り出せなければ短い定型文に落とす
- **形に関係なく読み上げ長の上限（180 文字）を適用する**。
  パターンの網羅ではなく上限で止めるのが確実。33 秒読み上げを二度と起こさない

実測での効果:
```
110字 clarify{...}  → 「どちらについて聞きたいですか」(14字)   19秒 → 2秒
 84字 clarify{...}  → 「続けますか」(5字)                      14秒 → 1秒
 24字 通常の応答     → 素通し（変更なし）
 44字 秘書の応答     → 素通し（変更なし）
```

配線先は `LOCAL_TOOL|WEB|LOCAL` 分岐と C8 の両方。全経路で上限が効く。

### 問題2: 出力先が分からず「音が出ない」の切り分けに時間がかかった
TTS は mp3 を生成し `PLAYBACK_DURATION` も記録されるのに音が出なかった。
既定出力が DisplayLink ドック（`Realtek USB2.0 Audio`）で、そこに何も繋がって
いなかったため。**JARVIS は「再生した」しか知らず、出力先を知る手段が無かった。**

### 対策: jarvis-status の Output チェック
```
Output ✓ MacBook Proのスピーカー / 音量 100
Output ! MacBook Proのスピーカー / ミュート中です
Output ! MacBook Proのスピーカー / 音量が 0 です
Output ! Realtek USB2.0 Audio — 機器が接続されていないと無音になります。
         音が聞こえない場合は出力先を確認してください
```
ドック/HDMI/USB は「繋がっていなければ無音」になりやすいので注記する（断定はしない。
ドックにスピーカーを繋いでいる構成も普通にあるため）。

これで入力（`Device`）と出力（`Output`）が 1 画面に揃った。

tests: `test_jarvis_speech.py` 13件（新規）、`test_jarvis_status.py` 74 → 79件。

### 残: Capture チェックの穴
`TURN=4`（17:29:32）は `silence_cb_fired=True` / `PEAK_RMS=1801` だが
`FRAMES=751`（8 秒の無発話タイムアウト長）で `STT=112ms` の後 `GATE` 以降が全て `-`。
**「音は入ったが言葉として成立しなかった」ターン**を `Capture ✓` と判定してしまう。
STT が走ったのに GATE 以降が未実行で終わったターンの可視化は未実装。

---

## 変更7: 案C の潜在バグ修正 — 出力スロットに触らない

### 何が問題だったか
案C の初版は入力デバイスを選ぶ際にこう書いていた:
```python
current = sd.default.device
output = current[1] if isinstance(current, (list, tuple)) else None
sd.default.device = (chosen["index"], output)
```
出力側を「保存」しているつもりだが、**デバイスの抜き差しで PortAudio の index は
振り直される**。保存した出力 index が別デバイスを指し得る。

macOS では出力に sounddevice を使わない（TCC の kTCCServiceMediaLibrary プロンプト
回避。`voice_mode._sounddevice_output_allowed()` が Darwin で False）ため
**現状は無害**だが、潜在バグなので直した。

### 対策
`_set_input_device(sd, index)` を追加し、**入力スロットだけ**を設定する。
`sd.default.device[0] = index` の要素代入が可能であることを実測で確認した。

出力は JARVIS の管轄外である。`afplay` にデバイス指定オプションが無く
（実測: 音量・時間・レートのみ）、システム既定にしか出せない。

tests: `test_audio_devices.py` に回帰防止 2件を追加（17 → 19件）。

---

## 音声出力が途絶えていた件（2026-09-09）— 原因は切り分け手順の副作用

### 経緯
TTS は mp3 を生成し `PLAYBACK_DURATION` も記録されるのに音が出ない状態が続いた。

原因は**内蔵マイクの digital silence を切り分けるためにジャックから機器を抜いた**こと。
```
21:0x  外部マイク / 外部ヘッドフォン がデバイス一覧から消滅
       既定入力 = MacBook Proのマイク
       既定出力 = Realtek USB2.0 Audio（DisplayLink ドック）へ移動
```
ヘッドセットを挿し直しても **macOS は既定出力を自動で戻さない**
（入力は `外部マイク` に戻ったのに、出力は戻らなかった）。
それ以前は一度もジャックを抜いていなかったため、出力は最初からヘッドフォンに
向いており維持されていた。

さらに手動で `MacBook Proのスピーカー` に切り替えても、
**DisplayLink ドックが既定を奪い返す**（2 回発生。ディスプレイのスリープ復帰時に
USB オーディオが再認識されるため）。

### 解決
既定出力を `外部ヘッドフォン` に設定 → `afplay` で確認 → JARVIS の応答音声も到達。

### 教訓 / 未対応
- **ジャックを抜く切り分けは出力経路も動かす。** 手順に「出力先も移る」旨を明記すべきだった
- `jarvis-status` の `Output` チェックはこの状況を正しく警告していた
  （`Realtek USB2.0 Audio — 機器が接続されていないと無音になります`）
- **恒久対策は未実装。** ドックが既定を奪い返す問題は残っている。
  `afplay` にデバイス指定が無く `osascript` でもデバイスは変えられないため、
  出力側の優先順位付けには `switchaudio-osx` 等の外部ツールが必要。導入は未承認。

---

## 変更8: 出力デバイスの固定（システム既定に依存しない）

### 問題
`afplay` に**デバイス指定オプションが無い**（実測: 音量・時間・レートのみ）ため、
JARVIS はシステム既定出力にしか出せなかった。そして既定は安定しない:

- 内蔵マイクの切り分けでジャックを抜いた際に既定出力が DisplayLink ドックへ移り、
  挿し直しても macOS は戻さなかった
- 手動で戻してもディスプレイのスリープ復帰で**ドックが既定を奪い返す**（2 回発生）
- 結果、応答音声が数時間にわたって無音だった

### 解決の鍵（2つとも実測で確認）
1. **`ffmpeg` の audiotoolbox 出力は `-audio_device_index` を持つ。**
   実機検証: 既定=イヤホンの状態で index 8（本体スピーカー）を指定したら
   **本体から鳴った**。システム既定を無視して指定先へ流せる。
2. **JARVIS は自前の `_speak()`（`jarvis_runtime.py:787`）から
   `play_audio_file(p)` を呼んでいる。** 上流を触らずに差し替えられる。

### 実装: bin/audio_output.py
```
優先順位（UID で照合）
  1 BuiltInHeadphoneOutputDevice   イヤホン/ジャック
  2 BuiltInSpeakerDevice           内蔵スピーカー（クラムシェルでも鳴ると実測確認）
除外 9件
  AppleUSBAudioEngine:DisplayLink…      ドック
  AppleUSBAudioEngine:Generic:USB…      Realtek
  430F0024-…（HDMI モニタ ×2）
  NMAudioDevice_UID / NMAudioMicDevice_UID   NoMachine 仮想
  BuiltInHeadphoneInputDevice / BuiltInMicrophoneDevice   入力専用
```

### 設計判断
**index ではなく UID で照合する。** `ffmpeg` の index はデバイスの抜き差しで
振り直されるが UID は不変。これは入力側で index 保存が潜在バグになった反省でもある
（変更7 参照）。再生のたびに UID → index を解決する。

**未知のデバイスは「選ばない」— 入力側とは逆にした。**
入力（`audio_devices`）では未知の名前を実マイクとみなした。USB マイクの可能性が
最も高く、仮想ドライバは除外リストで落とせるため。
出力には「開けるが音が出ない機器」（繋がっていないドック、スピーカー無しの HDMI
モニタ）が**実在する**。それを選ぶと今日と同じ無音事故になる。
同じ枠組みでも、間違えたときの損害が非対称なので意図的に分けた。
USB ヘッドセット等は `JARVIS_OUTPUT_UID` で明示指定する。

**失敗したら `afplay`（システム既定）へフォールバックする。**
鳴らないより既定で鳴る方がよい。

### レイテンシ
懸念していた起動オーバーヘッドは**存在しなかった**（3回平均）:
```
ffmpeg(固定)  1871 ms
afplay(既定)  2292 ms   ← 既存の方が遅い
```

### 実機検証（2026-09-09 18:2x）
```
システム既定 = Realtek USB2.0 Audio          （ドック）
JARVIS 固定  = BuiltInHeadphoneOutputDevice   （イヤホン）
→ ユーザーがイヤホンから応答音声を聴取
```
**システム既定がドックのまま、JARVIS の音声だけがイヤホンへ流れた。**
他のアプリの音は既定に従うので、JARVIS だけが固定される。

### jarvis-status
```
Output ✓ BuiltInHeadphoneOutputDevice（JARVIS が固定）/ 音量 88
```
固定できている場合は「既定が怪しい」警告を出さない（固定されていれば既定が
奪われても影響しないため）。ミュートと音量 0 は固定の有無に関係なく警告する。

tests: `test_audio_output.py` 14件（新規）、`test_jarvis_status.py` 79 → 82件。

### 今日のレイテンシ推移（1ターン / TOTAL_TO_FIRST_AUDIO）
```
39,966 ms   capture が 30 秒上限に張り付き（所見#9 修正前）
10,635 ms   適応しきい値の導入後
 7,927 ms   本変更後の実測
```

---

## 変更9: Turn チェック — Capture の穴を塞ぐ

### 問題
`Capture` チェックは capture 単体の健全性しか見ていない。2026-09-09 の `TURN=4` は
`silence_cb_fired=True` / `PEAK_RMS=1801` だったので `Capture ✓` を出したが、
timeline は `STT=112ms` の後 `GATE` 以降が全て `-` で応答音声は再生されていない。

**「音は入ったが言葉として成立しなかった」ターンを ✓ と判定していた。**
利用者から見ると「status は ✓ なのに返答がない」状態になる。

### 対策
決定的な signal は `PLAYBACK_DURATION`。これが `-` なら利用者は何も聞いていない。
「再生まで到達したか」を一次判定にし、`STT` の有無で原因を切り分ける。

```
Turn ✓ 応答まで到達 / TOTAL_TO_FIRST_AUDIO=7927ms
Turn ! 音声は取得できましたが応答に到達しませんでした（STT=112ms の後で停止）
Turn ! 発話が認識されませんでした（STT が実行されていません）
```

判定は**最後の timeline 行**に対して行う（過去の失敗で永久に WARN が残らないよう、
Capture チェックと同じ方針）。

これで音声経路の全段が 1 画面で追える:
```
Device  入力デバイス（優先順位で固定）
Capture 音が取れたか
Turn    応答まで到達したか
Output  出力デバイス（UID で固定）
```

tests: `test_jarvis_status.py` 82 → 88件。

---

## 変更10: Power チェック — CLAMSHELL_RECOVERY の前提を可視化し、優先度を訂正

### 私の推奨が間違っていた
`CLAMSHELL_SPECIFIC_RECOVERY` を「ユーザーの日常構成そのものだから優先度が高い」
と判断したが、**逆だった**。クラムシェル + AC 運用は**スリープを能動的に封じている**。

2026-09-09 の実測:
```
pmset -g custom  AC Power: sleep 0 / displaysleep 0     ← システムスリープ無効
pmset -g assertions  Amphetamine: PreventUserIdleSystemSleep 21h40m 保持
pmset -g log     Total Sleep/Wakes since boot at 2026-09-08 19:38:41 +0900 :0
                 ← この runtime を起動した再起動以来、スリープ 0 回
最後の実スリープ  2026-09-07 17:10（9/8 の再起動より前）
```

したがってこの gap は**現構成では到達しない**。優先度を上げるのではなく下げるべきだった。
`known_gaps.json` の status を `UNVERIFIED` → `UNREACHABLE_IN_CURRENT_CONFIG` に変更し、
経緯（低→高→低）と根拠を note に残した。

### 対策: Power チェック
この前提は不可視だった。可視化しないと、次に検証しようとした人が
「なぜ再現しないのか」を一から調べ直す。

```
Power ✓ システムスリープ無効 / 抑止中: Amphetamine, coreaudiod
        （CLAMSHELL_SPECIFIC_RECOVERY は現構成では検証不能）
```

**FAIL は出さない。** スリープしないこと自体は異常ではなく、文脈情報である。

### 実装中に自分で入れた欠陥を2つ潰した
**1. 誤った回数を表示した。** 初版は正規表現 `[^:]*` が**タイムスタンプのコロンで止まり**、
`Total Sleep/Wakes since boot at 2026-09-08 19:38:41 +0900 :0` の `19:38` から
`38` を回数として拾って「起動以来のスリープ 38回」と表示した（実際は 0）。
→ **回数の取得自体をやめた**。取得手段が `pmset -g log` しかなく費用に見合わない。
取れない値を無理に出すより出さない方が正しい。

**2. jarvis-status を 2s -> 4.34s に悪化させた。**
`pmset -g log` が 72,000 行を吐いて **2.99 秒**かかる（`custom` と `assertions` は各 0.01 秒）。
診断を速く回すためのコマンドが遅くなるのは本末転倒。`pmset -g log` を使わない実装に変更し、
**1.13 秒**（元の 2 秒より速い）になった。

### known_gaps.json の更新
`REAL_LOGIN_WARM_VALIDATION` を削除（本日 PASS）。4件 → 3件。
```
WAKE_WORD_RELIABILITY  = TUNING_PENDING                 （手つかず）
CLAMSHELL_RECOVERY     = UNREACHABLE_IN_CURRENT_CONFIG  （本日再分類）
EARLY_TURN_DURING_WARM = UNVERIFIED
```
`EARLY_TURN_DURING_WARM` は本日の warm 検証中に窓（readiness 35s + モデルロード）が
あったが、レイテンシ測定を汚さないため意図的にターンを止めたので未観測のまま。

tests: `test_jarvis_status.py` 88 → 94件。
