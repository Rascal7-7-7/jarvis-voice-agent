# Loop Journal — part 2 (S012 →)

Part 1 is in `JOURNAL.md` (S001–S011). Split only because the host's
dangerous-command hook blocks Bash commands whose *text* contains strings like
`rm -rf` / `sudo` — these entries quote deny-rule patterns verbatim, so they are
written with the file tool instead of a shell heredoc.

---
STEP_ID=S012
TIME=2026-08-29T08:08+0900
PHASE=7 Regression — live cron proof
ACTION=wait for real cron jobs to fire under 0.20.6
BEFORE=cron parity proven only by comparing listings
CHANGE=none
VERIFY=automation:monitoring:health-check  08:00:44  last_status=ok
       automation:factory:publish-x        08:02:14  last_status=ok
       automation:factory:publish-note     08:07:28  last_status=ok
       all last_error=None; next_run_at rescheduled correctly
       gateway start count = 2 (both intentional); no crash loop
RESULT=PASS
ROLLBACK_AVAILABLE=YES
NEXT=S013

---
STEP_ID=S013
TIME=2026-08-29T08:10+0900
PHASE=8/9/10 Voice + STT/TTS verification WITHOUT a microphone
ACTION=synthesize Japanese audio with `say`, feed it to faster-whisper; benchmark both TTS paths
BEFORE=voice stack installed but never exercised
CHANGE=none (measurement only)
VERIFY=TTS short clause, 3 runs each:
         edge / ja-JP-NanamiNeural  ~570 ms
         say  / Kyoko               ~1060 ms
       STT faster-whisper small int8, language=ja:
         "こんにちは"              -> "こんにちは"              EXACT  0.87s
         "今日の日付を教えて"       -> "今日の日付を教えて"       EXACT  0.67s
         "パイソンについて説明して"  -> "バイソンについて説明して"  1 char 0.72s
RESULT=PASS
ROLLBACK_AVAILABLE=N/A
NEXT=S014

---
STEP_ID=S014
TIME=2026-08-29T08:12+0900
PHASE=11 Wake word verification WITHOUT a microphone
ACTION=download openwakeword hey_jarvis; score synthesized positives and negatives
BEFORE=no wake word models present
CHANGE=ADDITIVE — openwakeword resources/models (~8 MB: hey_jarvis_v0.1.{onnx,tflite},
       melspectrogram, embedding_model, silero_vad). No API key. Fully on-device.
VERIFY=threshold 0.6
         "Hey Jarvis"                       peak 0.859 -> DETECT
         "hey jarvis, what is the weather"  peak 0.999 -> DETECT
         english non-wake sentence          peak 0.000 -> no trigger
         japanese conversation              peak 0.000 -> no trigger
RESULT=PASS (synthetic only; real ambient false-positive rate NOT VERIFIED)
ROLLBACK_AVAILABLE=YES
NEXT=S015

---
STEP_ID=S015
TIME=2026-08-29T08:13+0900
PHASE=9/10/11 config
ACTION=append stt/tts/voice/wake_word blocks to ~/.hermes/config.yaml as TEXT (not a YAML
       round-trip), so the existing comment blocks survive byte-for-byte
BEFORE=config.yaml 1430 B, 3 top-level keys, sha256 6658cb14…
CHANGE=config.yaml 1430 -> 3571 B; backup config.yaml.pre-voice-20260829-*
       stt.language=ja, stt.local.model=small
       tts edge/ja-JP-NanamiNeural + say-ja local command provider
       voice.auto_tts=true, barge_in=true, stop_phrases += ストップ / 止めて
       wake_word DISABLED, provider=openwakeword, model=hey_jarvis
VERIFY=validate_config_structure -> 0 issues; every value reads back correctly;
       original 3 blocks still at lines 1 / 8 / 23
RESULT=PASS
ROLLBACK_AVAILABLE=YES
NEXT=S016

---
STEP_ID=S016
TIME=2026-08-29T08:14+0900
PHASE=12 Confirmation gate
ACTION=append approvals/browser/security blocks; classification-test 23 commands (nothing executed)
BEFORE=approvals defaults (mode=smart), no deny rules
CHANGE=approvals.mode=manual, 40 deny globs, browser+security pinned;
       backup config.yaml.pre-approvals-20260829-*
VERIFY=**first run found a REAL GAP**: `git push origin main` -> allow, no prompt.
       Hermes ships only force-push in DANGEROUS_PATTERNS, and config exposes no
       "ask me first" tier — only deny (hard block) or command_allowlist (auto-approve).
       Closed by adding "*git push *", "*gh repo delete*", "*npm publish*", "*docker push *".
       Re-test: BLOCKED 14 | PROMPT 1 | allow 8 | benign unprompted 8/8 | LEAKED: NONE
       Obfuscation defeated: quote-split sudo and backslash-split rm both BLOCKED.
SELF_CORRECTION=my first test called bool() on detect_dangerous_command(), which returns a
       TUPLE and is therefore always truthy — it made every benign command look like it
       would prompt. Fixed by reading raw[0]. The gate was fine; the test was wrong.
RESULT=PASS
ROLLBACK_AVAILABLE=YES
NEXT=S017

---
STEP_ID=S017
TIME=2026-08-29T08:16+0900
PHASE=17 Local LLM — unblocker for Phases 13-16/19
ACTION=establish an LLM brain with no credential. `hermes status` shows Model:(not set) and
       every API key / OAuth provider unset, so agent-driven phases cannot run otherwise.
BEFORE=Ollama installed, stopped, 0 models
CHANGE=`ollama serve` bound to 127.0.0.1:11434 (verified loopback-only); pulled qwen3:4b (2.5 GB)
VERIFY=throughput 76–78 tok/s
       TOOL CALLING: PASS — get_weather({"city":"東京"}) and ({"city":"大阪"}) both correct,
         Japanese arguments extracted correctly
       VOICE BRAIN: FAIL — qwen3:4b is a thinking model. "こんにちは" burns 300+ thinking
         tokens and returns EMPTY content even at num_predict=600 (done_reason=length, 8.1 s).
         `think:false` does not fix it; it relocates English chain-of-thought into `content`.
       -> disqualified as a voice brain BY MEASUREMENT; retained as the tool-calling brain
RESULT=PARTIAL (tool calling PASS, voice latency FAIL)
ROLLBACK_AVAILABLE=YES (models are removable; nothing depends on them)
NEXT=S018
