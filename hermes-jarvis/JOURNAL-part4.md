# Loop Journal — part 4 (S024 →) · hands-free build

---
STEP_ID=S024
TIME=2026-08-29T15:50+0900
PHASE=1 Exact-version capability check (tag v2026.8.27 / 5fc308a7 only, not main)
ACTION=confirm each requested capability exists in the pinned checkout
CHANGE=none
VERIFY=voice.beep_enabled PRESENT (default True, gate at hermes_cli/voice.py:270)
       voice.beep_volume  PRESENT (default 0.3, tools/voice_mode.py:459)
       voice.record_key   PRESENT (default "ctrl+b")
       voice continuous   PRESENT (start_continuous/stop_continuous, _continuous_* state)
       wake_word.input_device PRESENT (default null)
       wake re-arm        PRESENT (pause()/resume(), cli.py _start_wake_watchdog)
       AudioRecorder InputStream lifecycle PRESENT (persistent, no device=)
RESULT=PASS — all seven exist; nothing had to be taken from main
NEXT=S025

---
STEP_ID=S025
TIME=2026-08-29T15:52+0900
PHASE=2 Beep
ACTION=trace every beep call site before touching config
BEFORE=start 880Hz single / stop 660Hz double audible
VERIFY(pre)=all 6 call sites go through hermes_cli/voice.py:_play_beep, which is gated by
       _beeps_enabled() -> voice.beep_enabled. tools/voice_mode.play_beep is reached only
       from that one gated wrapper. So the config key is sufficient — NO PATCH NEEDED.
CHANGE=config.yaml voice.beep_enabled=false, voice.beep_volume=0.05 (belt-and-braces)
       backup config.yaml.pre-beep-20260829-*
VERIFY=_beeps_enabled() -> False ; _get_beep_volume() -> 0.05 ; structure issues 0
NOTE=voice.thinking_sound is a SEPARATE gate (still True) — left alone, it is not a
     record start/stop beep.
RESULT=PASS
ROLLBACK=restore config.yaml.pre-beep-*
NEXT=S026

---
STEP_ID=S026
TIME=2026-08-29T15:58+0900
PHASE=4 Does the wake listener follow a default-device change?
ACTION=drive the production WakeWordDetector, pause, change the process-local
       sd.default.device (faithful stand-in for System Settings -> Sound -> Input), resume
CHANGE=none to the system (sd.default is process-local; verified afterwards that the
       system default was still 外部マイク)
VERIFY=start : opening microphone device=Clayのマイク  default_rate=48000 capture_rate=48000
       resume: opening microphone device=NoMachine Microphone Adapter default_rate=44100
               capture_rate=44100 engine_rate=16000
       followed = True
RESULT=PASS — wake path already re-resolves device AND rate on every pause/resume.
       NO PATCH NEEDED for Phase 4. input_device stays null.
NEXT=S027

---
STEP_ID=S027
TIME=2026-08-29T16:05+0900
PHASE=3 AudioRecorder dynamic device — PATCH_NEEDED=YES
ACTION=reported cause/file/diff/compat/rollback, then applied
BEFORE=checkout pristine at tag (git status empty)
CHANGE=tools/voice_mode.py only, +60 -4 (see PATCHES.md P1)
       _default_input_identity / _identity_label added; _stream_identity added;
       _ensure_stream compares identity and reopens only on a real change;
       start() log now names the device.
       PortAudio index deliberately NOT part of the identity and NOT persisted.
VERIFY=no-change -> same stream object reused (True)  [no regression]
       change    -> reopened (True), live rate 44100 == self._sample_rate 44100 (True)
       upstream tests test_voice_max_recording_seconds + test_audio_playback_guard: 7 passed
RESULT=PASS
ROLLBACK=git checkout -- tools/voice_mode.py  (editable install, takes effect immediately)
NEXT=S028

---
STEP_ID=S028
TIME=2026-08-29T16:12+0900
PHASE=5 Enable wake word
CHANGE=config.yaml wake_word.enabled=true, wake_word.input_device=null (explicit)
       backup config.yaml.pre-wake-enable-20260829-*
       voice.record_key left at ctrl+b as the manual fallback
VERIFY=structure issues 0; listener starts and reports
       "opening microphone device=外部マイク ... capture_rate=48000 engine_rate=16000"
       audio_silent=False (mic is live)
       cli.py wiring confirmed: _on_wake_word -> pause_listening -> single-utterance
       capture -> _start_wake_watchdog resumes when idle
RESULT=PASS (wiring); acoustic detection see S029
NEXT=S029

---
STEP_ID=S029
TIME=2026-08-29T16:18+0900
PHASE=8-A Acoustic wake test — INCONCLUSIVE BY TEST RIG
ACTION=play "Hey Jarvis" through the speakers, listen on the real default mic
VERIFY=first run 1 fire in 2 positives; a 11-trial repeat gave 0/11.
       Negatives (English sentence, Japanese sentence): 0 false fires throughout.
       Quantified the loss by scoring what the mic actually captured:
           clean synthesised file  peak_score = 0.960   (threshold 0.6)
           speaker->room->mic      peak_score = 0.111 / 0.298 / 0.330
           captured peak 1590-1747, rms 181-213  -> LEVEL IS FINE
       => the loss is spectral/reverb in the loopback rig, not the detector or the mic.
RESULT=CANNOT VALIDATE WITHOUT A HUMAN SPEAKING. Reported as such, not as a pass.
NEXT=S030

---
STEP_ID=S030
TIME=2026-08-29T16:22+0900
PHASE=8 Input gain — measured, then REVERTED
ACTION=swept input gain 35/50/65/80/95 measuring ambient vs speech against threshold 200
VERIFY=35 -> ambient 827 (outlier; a separate measurement gave 116) speech 226
       50 -> ambient 141  speech 1345   <- only clean separation
       65 -> ambient 205  speech 306
       80 -> ambient 3232 speech 7848
       95 -> ambient 8012 speech 32003
       At gain 50, ambient over 5x4s windows: median 61-85 (fine) but peak crossed 200
       in 3/5 windows. has_spoken needs 0.3s sustained, so transients do not confirm speech.
DECISION=REVERTED to the original 35. The user's own successful live run was at 35, and
       my sweep was too noisy (the 35 ambient reading contradicted a separate measurement)
       to justify overriding a known-good value. Data reported for the user to decide.
RESULT=no net change (35 -> 50 -> 35)
NEXT=S031

---
STEP_ID=S031
TIME=2026-08-29T16:26+0900
PHASE=6 Stop phrase
ACTION=verify voice.stop_phrases against the production is_voice_stop_phrase()
VERIFY='stop' True | 'ストップ' True | '止めて' True | 'Stop' True
       'ストップ。' False | '止めてください' False | '今日の天気は' False | 'stop it' False
       => exact match, ASCII case-insensitive. Trailing 。 does NOT match, but the measured
          faster-whisper output carries no trailing punctuation, so this is currently moot.
       cli.py consumes it at two capture sites (14795, 15082).
RESULT=PASS
NEXT=S032

---
STEP_ID=S032
TIME=2026-08-29T16:28+0900
PHASE=7 Bare "Jarvis" — research only, nothing changed
VERIFY=0.20.6 _build_engine supports porcupine | sherpa/sherpa-onnx/kws/open | openwakeword
       sherpa = "free, no API key, open vocabulary — detects ANY typed phrase with no
       training: set wake_word.phrase and it is tokenized at runtime against a small
       streaming zipformer (~13 MB English model, one-time download)"
       porcupine has a built-in "jarvis" keyword but needs PORCUPINE_ACCESS_KEY (line 789)
       custom openWakeWord needs a trained .onnx
RESULT=RECOMMEND sherpa for bare "Jarvis". Not applied — user asked for a recommendation only.
