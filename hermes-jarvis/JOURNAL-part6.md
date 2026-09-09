# Loop Journal — part 6 · always-on runtime

---
STEP_ID=S044
PHASE=0 cron drift gate
ACTION=read-only forensics on jobs.json / state.db / logs / 0.20.6 cron source
FINDING=the record was never "disabled with no reason". It reads
        state='paused', paused_at='2026-08-29T15:48:35.094214', paused_reason=None,
        which is exactly what pause_job(job_id, reason=None) writes.
ELIMINATED=automatic pause (jobs.py:3475 and scheduler.py both write a reason AND
             logger.error — neither present; zero pause/resume log lines exist)
           repeat exhaustion (that path needs times>0; this job has times=None)
           hermes console (writes "paused from hermes console")
           agent cronjob tool (state.db: 0 cronjob tool calls EVER; only
             read_file / web_search / terminal appear)
           gateway HTTP API (GET /api/jobs -> 404 on 8644; not exposed)
           `hermes pause` (global emergency stop; does not write a per-job paused_at)
           me (0 cron commands in any readable history; no agent session at 15:48 —
             the sessions in 15:00-17:10 start at 16:04, 16:39, 16:43, all AFTER)
DATA_LOSS=NONE. 63 entries (same as the 07:48 backup), every field of the job
        intact, automation-x-video.sh present, one resume_job call restores it.
RESOLUTION=asked the user. Confirmed: paused intentionally. NOT restored.
RESULT=RESOLVED — no action taken
NEXT=S045

---
STEP_ID=S045
PHASE=1 revalidation
CHANGE=none
VERIFY=hermes 0.20.6 · gateway pid 41543 uptime 14h19m · state.db schema 26 ok
       codex config sha 5ac879a7… unchanged · claude-work settings unchanged
       voice_mode.py patch still the only tree modification
       hashes recorded for all five jarvis scripts
NOTED_NOT_MINE=a llama-server appeared on 127.0.0.1:57938 between sessions.
       Not started by me; recorded for honesty, not touched.
RESULT=PASS

---
STEP_ID=S046
PHASE=4 TCC — done BEFORE choosing an architecture, because it decides it
ACTION=bin/tcc_mic_probe.py from a temporary LaunchAgent, terminal run as control
WHY_THIS_WAY=macOS hands an unpermitted process digital silence, not an error, so
       "it works in Terminal" proves nothing about launchd. The probe counts
       distinct int16 sample values; <=1 means silence.
VERIFY=terminal      peak 2143 rms 301.5 distinct 3040  REAL_AUDIO
       launchd (x3)  peak 2675/3090/2498  distinct 3781/4137/3965  REAL_AUDIO
       ppid=1 and XPC_SERVICE_NAME confirm genuine launchd spawns
RESULT=PASS — the Microphone grant follows the BINARY, not the parent.
       No .app helper, no bundle identity, no TCC database edit, no privilege
       escalation, no Full Disk Access. That whole design branch is unnecessary.
CLEANUP=probe agent unloaded and its plist moved out of ~/Library/LaunchAgents
NEXT=S047

---
STEP_ID=S047
PHASE=3 background method
VERIFY=gateway hosts no wake listener. tui_gateway/server.py:16229 does, but only
       when a connected transport calls wake.start and that transport owns it —
       not headless. cli.py needs the interactive TUI.
DECISION=BACKGROUND_METHOD=C, dedicated runtime under launchd. PATCH_REQUIRED=NO —
       tools/ is not forked; detector, recorder, STT and TTS are all upstream API.
RESULT=PASS

---
STEP_ID=S048
PHASE=5 bin/jarvis_runtime.py — first attempt FAILED
CHANGE=NEW FILE
VERIFY=exited immediately: "Wake-word microphone is already owned."
ROOT_CAUSE=tools/wake_word.py takes a MACHINE-WIDE flock on
       ~/.hermes/runtime/wake-word.lock. lsof showed the holder: pid 43976 =
       the user's own `hermes chat`. Under KeepAlive, exiting here is a crash loop.
RESULT=FAIL
NEXT=S049 (fix, not retry)

---
STEP_ID=S049
PHASE=5 runtime waits for the lease
CHANGE=start_listening wrapped in a retry with backoff (5s -> 60s cap) on
       WakeWordInUse, instead of exit 75
VERIFY=launchd pid constant across three 8 s samples while the interactive session
       held the lease -> waiting, not crash-looping
       clean SIGTERM exit; state published throughout
RESULT=PASS

---
STEP_ID=S050
PHASE=5 stop the CLI competing for the lease
ACTION=verify BEFORE changing: does the CLI actually consult wake_surface_enabled?
VERIFY(pre)=cli.py:15280 `if not wake_surface_enabled("cli"): return` — yes it does
CHANGE=config.yaml wake_word.surface: auto -> gui
       backup config.yaml.pre-alwayson-20260829-224053
VERIFY=wake_surface_enabled('cli') -> False; ('gui') -> True
       UNCHANGED: enabled/model/input_device/sensitivity(0.35)/confirmation_frames(1)
       UNCHANGED: beep/auto_tts/barge_in/record_key
NOTE=Ctrl+B push-to-talk is a separate mechanism and still works in the CLI.
RESULT=PASS

---
STEP_ID=S051
PHASE=5/10 LaunchAgent local.jarvis.runtime
CHANGE=NEW ~/Library/LaunchAgents/local.jarvis.runtime.plist
       RunAtLoad · KeepAlive.SuccessfulExit=false · ThrottleInterval 30
       env = PATH / VIRTUAL_ENV / HERMES_HOME / JARVIS_WORKDIR only, no secrets
       venv bin deliberately NOT first in PATH (it would shadow pyenv's python3
       for automation scripts a delegation might run)
VERIFY=plutil OK; loaded; pid 98134; three agents total; opens no listener
RESULT=PASS

---
STEP_ID=S052
PHASE=9 resource
VERIFY=runtime 0.0% CPU / 34.9 MB RSS (waiting state)
       gateway 0.1% / 112.7 MB · ollama 0.0% / 72.0 MB · total ~220 MB
       gemma4:e2b resident 7.23 GB at sample time (keep_alive; unloads on its own)
       trade recorded: resident = 7.23 GB idle vs +16.1 s cold load on the next turn
BATTERY=NOT MEASURED — powermetrics needs elevated privileges, which are prohibited,
       and the listener has not yet run for a sustained period
RESULT=PARTIAL

---
STEP_ID=S053
PHASE=11 HUD research
VERIFY=hermes-hudui  1.8k stars / 157 commits / MIT / verified against v0.20.3
         schema v26 (our exact schema) / loopback-only / reads ~/.hermes directly
         BUT has execution features (Update hermes, plugin enable/disable)
       jarvis_ai     144 stars / 7 commits / MIT / Iron-Man HUD + approval cards +
         wake state, BUT needs ElevenLabs (paid) and a Hermes API listener on 8642
         which is currently not exposed (GET /api/jobs -> 404)
       hermes-control-interface  DISQUALIFIED — ships a browser terminal
FINDING=all of them monitor HERMES. None knows JARVIS's own wake/route/delegation
       states — which is exactly the PHASE 13 list, and which jarvis_runtime.py
       already publishes to logs/jarvis_state.json.
RECOMMENDATION=build a minimal viewer over that file; borrow jarvis_ai's approval
       card interaction. Not implemented — HUD comes after always-on passes.
RESULT=PASS (research)

---
STEP_ID=S054
PHASE=blocked
STATUS=the runtime cannot take the wake lease while the interactive `hermes chat`
       (pid 43976) holds it. Everything downstream of that — background wake,
       dynamic mic in background, sleep/wake, login E2E, listener-running resource
       figures — is unverified and is reported as such, not as PASS.

---
STEP_ID=S055
PHASE=latency 1-8 measurement BEFORE any change
ACTION=decomposed the 22:59 turn from the log, then reproduced each component
FINDING=four of the brief's premises did not survive measurement:
       whisper is ALREADY cached (module-global + lock, idle-unload disabled);
       the wake phrase did NOT leak (speech sits at 11.74-12.46s, first 11.74s
         peaks at 62);
       the 15.65s capture was the recorder doing exactly what it was told
         (11.74s of _max_wait + 0.72s speech + 3.01s silence_duration);
       hermes CLI startup is 0.16s, not the 11.6s reported earlier — that
         earlier figure was an agent turn misread as startup, and is corrected.
MY_OWN_ERROR=I measured an ambient noise floor of p50=368 vs threshold 200 and
       called it the cause. The actual recording's floor is 47. Different
       moment, not that turn. Retracted in LATENCY.md.
UNEXPLAINED=the 13.92s decode. Same file, same call, now 0.75s; decode is flat
       across 2/5/10/15.5s inputs. Removed from the hot path by prewarming
       rather than explained, and recorded as NOT EXPLAINED.
RESULT=PASS (measurement)
NEXT=S056

---
STEP_ID=S056
PHASE=latency changes
CHANGE=bin/jarvis_runtime.py + bin/jarvis-dispatch only
       backups .pre-latency-20260829-231308
       sha256 before b99e0431…/aa898e15…  after 24fa267e…/54f0d0bc…
  1 Timeline: 15 monotonic marks -> one `timeline …ms` line + a `marks` line.
    GATE/ROUTER split from route()'s own latency/llm_latency, not timed twice.
  2 warm at startup (whisper via the public transcribe path on a tone; ollama)
  3 warm on wake — the model load overlaps the user speaking
  4 recorder per-instance: _silence_duration 3.0->1.2, _max_wait 15.0->8.0.
    _silence_threshold LEFT AT 200 — that is voice sensitivity, and it was
    measured innocent. config.yaml not written; hermes chat unaffected.
  5 route passed to jarvis-dispatch instead of the whole router running twice
VERIFY=py_compile + zsh -n OK
       warm at startup: ollama 1.91s, whisper 5.71s cached=True name='small',
         both AFTER state=IDLE, so wake works while they finish
       inherited CONFIRMATION_REQUIRED still refuses to dispatch
       unknown inherited label still falls through to the safe branch
       gate 4/4 dangerous caught, 2/2 benign clean, threshold still 200
       routes: WEB/WEB/CONFIRMATION_REQUIRED/CONFIRMATION_REQUIRED/CODEX/CLAUDE
NOT_VERIFIED=no real end-to-end voice turn has run since the change. Every
       AFTER number is a component measurement. Marked as such in the report.
COST=idle RSS 34.9 MB -> ~336 MB (whisper now held in-process)
RESULT=PASS
NEXT=S057

---
STEP_ID=S057
PHASE=latency 9 targets
MET=STT 0.75-0.90s (<2s) · ROUTER 2.3-3.9s (<2.5s at the low end) · GATE ~0ms
NOT_MET=LOCAL 7-11s against a <4s target, and therefore TOTAL against <8s.
CAUSE=measured, not guessed: `hermes prompt-size` reports 22,867 B of system
       prompt (11,968 B of it the skills index) plus 41,316 B of tool schemas
       across 20 tools — ~64 KB, ~16k tokens of prompt processing per turn on
       gemma4:e2b. `user` CPU is 1.6s of a 10s turn; the rest is Ollama.
OPTIONS_MEASURED_NOT_TAKEN=
       `-t clarify`  7.0-8.0s, but strips file/terminal/web/delegation from LOCAL
       direct Ollama, no agent  2.8-3.5s, meets the target, but has no tools and
         answered "本日は2024年5月16日" — it buys the number with correctness
GATEWAY=checked per the brief: 127.0.0.1:8644 answers /health and nothing else.
       No agent endpoint; exposing one means enabling the API server platform =
       a new listener = approval item. It would not have helped anyway
       (startup is 0.16s; the 64 KB is per turn, not per process).
DECISION=left to the user. Both options change what LOCAL can DO, which is a
       capability question, not an optimization.
RESULT=PARTIAL — reported, not worked around

---
STEP_ID=S058
PHASE=capability-scoped LOCAL — investigation before building
FINDING=the date is in the AGENT'S SYSTEM PROMPT, not behind a tool. Measured:
       `hermes -t '' -z "今日は何日ですか"` -> "現在の日付は2026年8月30日です".
       So LOCAL_TIME needs the agent with ZERO tools, not a time tool. This is
       the whole exercise in one case: LOCAL_FAST gets the date wrong because it
       lacks the PROMPT, not because it lacks a TOOL.
LEVERS=`-t/--toolsets` works. --ignore-rules (10.60s) and --ignore-user-config
       (10.04s) change nothing vs 10.19s baseline; --safe-mode is SLOWER
       (29.16s); --skills ADDS preloaded skills rather than suppressing the
       index; `hermes skills opt-out` is global config and would change
       `hermes chat` too, so not used. The 11,968 B skills index is therefore
       NOT removable per invocation for LOCAL_TOOL. Recorded, not implied.
TRAP=`hermes -t __none__ -z ...` exits 0 with an EMPTY answer in 0.56s. Profile
       toolset names are validated at import and an empty agent answer triggers
       a full-agent retry, so a typo cannot become silence at the speaker.
RESULT=PASS

---
STEP_ID=S059
PHASE=implementation
CHANGE=NEW bin/jarvis_local_fast.py · NEW bin/jarvis_profiles.py
       MOD bin/jarvis_router.py · bin/jarvis-dispatch · bin/jarvis_runtime.py
       backups .pre-capscope-20260830-000501
SECURITY_ORDER=unchanged. gate is step 1, LLM is step 4. split_local() is
       deterministic and one-way (FAST -> TOOL only); even the 0 ms greeting
       path is routed through it, so nothing can talk its way into FAST.
       approvals.deny 40 globs, jarvis_gate, Codex read-only, Claude read-only:
       all untouched and re-verified.
THREE_LAYERS=current-fact containment: (1) regex demotion on the utterance,
       (2) NEED_TOOL in the FAST system prompt, (3) answer-shape re-check on the
       way out. Only layer 2 is a model.
BUGS_FOUND_AND_FIXED=
       `status` is a READ-ONLY variable in zsh — assigning it killed the whole
         LOCAL_FAST branch silently. Renamed.
       passing the FAST verdict back as JSON through zsh brace-expanded "{...}",
         turning every verdict into NEED_TOOL. Now returns plain text + exit code.
RESULT=PASS

---
STEP_ID=S060
PHASE=measurement
ROUTE_ACCURACY=100.0% (100/100)   target >= 95%
DANGEROUS_ACCURACY=100.0% (10/10) target = 100%
CURRENT_FACT_LEAK=0.0% (0/31)     target = 0%
NEED_TOOL_FALLBACK=100% (5/5)
FASTPATH=51% · LLM=49%
LOCAL_FAST_QUALITY=24 runs, 0 defects, median 3.64s (min 2.94 max 4.40),
       median answer 60 chars. No empty/echo/reasoning-leak/markdown/over-length.
TOOL_SCOPING=no profile lost accuracy vs the full agent; LOCAL_SYSTEM was more
       often CORRECT than full. LOCAL_TIME 10.25s vs full 11.00s;
       LOCAL_FILES_READ 7.52s vs full 22.62s; LOCAL_SYSTEM 21-28s vs full
       11-12s BUT full was fast because it gave up without executing.
MODEL_DEFECT_NOT_OURS=gemma4:e2b renders tool calls as prose
       ("terminal command: df -h /", "session_search(query=...)") at ANY toolset
       size, INCLUDING the full 20-tool agent. Not caused by scoping. Reported.
LIMITATION=現在時刻 (clock time) is answered by neither full nor scoped — the
       system prompt carries the date, not the time, and no tool fetched it
       reliably. 2/2 WRONG on both variants.
EVIDENCE_LIMIT=the A/B table is 2 reps and verdicts differ BETWEEN reps. Latency
       is usable; a reliability claim is NOT established and is not made.
RESULT=PASS

---
STEP_ID=S061
PHASE=regression + open items
VERIFY=26/26 PASS — gate before LLM, all 5 routes reachable, Codex/Claude
       read-only argv still pinned, dispatch refuses CONFIRMATION_REQUIRED and
       unknown labels, ollama/gateway loopback-only, upstream tree still shows
       ONLY voice_mode.py modified, wake sensitivity 0.35 unchanged,
       approvals manual, browser real profile off.
CORRECTED=my regression test asserted "silence_duration: 3.0" is in config.yaml.
       It is NOT a config key at all — the 3.0 comes from voice_mode.py's
       SILENCE_DURATION_SECONDS. Test fixed to assert the true invariant
       (absent from config, upstream constant still 3.0). Config verified
       byte-identical to the pre-alwayson backup in the voice section.
CONFLICT_TO_FLAG=section 12 states _max_wait=15s / silence_duration~3s and says
       do not change them THIS phase. The PREVIOUS phase set 1.2s / 8.0s on the
       runtime's own recorder instance (config untouched). Nothing was changed
       this phase; the discrepancy is surfaced for the user rather than
       silently kept or silently reverted.
RSS=508 MB idle (previous phase sampled 336 MB; figure varies between starts)
NOT_DONE=section 13 real-voice E2E — HUMAN_APPROVAL_REQUIRED, 3 utterances.
       No HUD work.
RESULT=PASS

---
STEP_ID=S062
PHASE=1 contention hypothesis — REFUTED
METHOD=same whisper model, same audio (the ACTUAL recording from the slow turn),
       three Ollama states, two passes in reversed order
RESULT=UNLOADED 0.71s · COLD_CONCURRENT 0.73s (Ollama loading 17.64s, free RAM
       down to 77MB) · RESIDENT_IDLE 0.73s. Pass 2: 0.81/0.72/0.72. swapins=0.
CONCLUSION=STT is unaffected by Ollama loading OR by 7.23GB resident. The
       wake-time warm was removed for being USELESS, not for being harmful.
RESULT=PASS (hypothesis refuted, recorded as such)

---
STEP_ID=S063
PHASE=1b actual root cause — launchd QoS
EVIDENCE=plist ProcessType="Adaptive"; runtime PRI=4 vs interactive shell PRI=31
VERIFY_UNDER_LAUNCHD=probe agent at each ProcessType (ppid=1, XPC_SERVICE_NAME
       confirm genuine launchd spawns), NOT a terminal measurement:
         Interactive PRI=31  warm 0.68/0.67/0.67s
         Adaptive    PRI=4   warm 12.80/11.95/12.90s
         Background  PRI=4   warm 12.49/11.81/12.72s
       19x. "Adaptive" performed identically to "Background" on this machine.
CHANGE=plist ProcessType Adaptive -> Interactive
       backup logs/local.jarvis.runtime.plist.pre-qos-20260830-010703
CORROBORATION=startup whisper warm 7.19s -> 1.38s, same process, same work
RESOLVES=the 13.92s decode logged as NOT EXPLAINED in the latency phase. Same
       cause. LATENCY.md amended rather than rewritten.
RESULT=PASS

---
STEP_ID=S064
PHASE=2 model residency
FINDING=keep_alive is accepted PER REQUEST on the native /api/chat endpoint —
       verified: one call with "60m" -> `ollama ps` UNTIL "59 minutes from now".
       /v1 (OpenAI-compat) has no such field, which is why warm moved endpoints.
CHOICE=60m, renewed after every turn. Renewal is REQUIRED, not decorative: the
       router and LOCAL_FAST both use /v1, which resets the window to the server
       default 10m, so residency would otherwise survive exactly one turn.
       Renewal runs on a thread AFTER the wake listener is re-armed.
NOT_TOUCHED=OLLAMA_KEEP_ALIVE=10m in local.ollama.serve.plist
RESULT=PASS

---
STEP_ID=S065
PHASE=4/5 TTS timeline + rearm
PROBLEM=speak_text() synthesizes AND plays in one blocking call and returns at
       playback END, so the only mark it can produce described the wrong event —
       TTS=8282ms for a synthesis that finished in 729ms.
CHANGE=_speak() composes the SAME upstream pieces in the same order
       (prepare_spoken_text -> text_to_speech_tool -> play_audio_file), falling
       back to speak_text whole if any piece is missing.
MARKS=tts_request_start · tts_audio_ready · playback_start · first_audio_played
       · playback_end, and TOTAL_TO_FIRST_AUDIO now ends at first audio, not at
       playback end.
VERIFY=TTS_GENERATION=500ms PLAYBACK_START_DELAY=0ms PLAYBACK_DURATION=2755ms
       TOTAL_TO_FIRST_AUDIO=552ms; first_audio != playback_end confirmed
HONEST_LIMIT=playback_start is when the player is handed the file; the gap to
       the first sample is inside afplay and not observable here. Reported as a
       LOWER BOUND rather than as exact.
STREAMING=not available — tts_streaming._REGISTRY holds only elevenlabs/openai/
       gemini/xai. edge has no streamer, and the TTS provider is a regression
       item, so no change.
RESULT=PASS

---
STEP_ID=S066
PHASE=6 barge-in
BARGE_IN_SUPPORTED=YES but only as interactive-CLI internals (cli.py
       _voice_barge_monitor / _voice_barge_capture / _voice_submit_barge_utterance).
       No reusable API in tools/. stop_playback() DOES exist in voice_mode.py.
BARGE_IN_ENABLED=NO
WHY=using it from the background runtime is a reimplementation (noise-floor
       calibration, grace window, echo handling), not a wiring job — and it
       requires holding the wake mic OPEN during playback. Upstream itself hit
       the self-voice problem: cli.py:15091 "Dropping playback-phase barge
       transcript as TTS echo". The brief said not to force it in that case.
RESULT=PASS (investigated, declined with evidence)

---
STEP_ID=S067
PHASE=7 greeting normalization
CAUSE=faster-whisper returned 'こんにちわ' for 「こんにちは」 — phonetically
       identical and an ordinary spelling — which missed the fast path and cost
       a 2.379s router round trip to conclude the obvious.
CHANGE=_FAST_SAFE widened for SPEECH spelling variants only. Widening a
       GREETING is safe in a way widening "stable knowledge" is not: a greeting
       has no object, so there is nothing for it to be current-fact about. Every
       match still passes through split_local().
VERIFY=61/61 — 51 must-fast variants, 10 must-NOT (greeting + real task, and
       greeting + current-fact). Zero greeting-path mis-fires, zero leaks.
       Full suites re-run: routes 100/100, dangerous 10/10, current-fact 0/31,
       regression 26/26.
RESULT=PASS

---
STEP_ID=S068
PHASE=8 real E2E
STATUS=HUMAN_APPROVAL_REQUIRED — not run. No HUD work.
NOTE_ON_ROLLBACK=no .pre-qos snapshot of jarvis_runtime.py / jarvis_router.py
       was taken before this phase's edits. Stated plainly in RUNTIME_QOS.md
       rather than documenting a path that does not exist; nearest snapshots are
       .pre-capscope (coarser by one phase). post-qos-20260830-011913 taken now
       for future phases.

---
STEP_ID=S069
PHASE=second-turn capture regression
SNAPSHOT_FIRST=yes, before touching anything (the previous phase missed this)
       PRE_FIX_SNAPSHOT=bin/*.pre-2ndturn-20260830-014553
       jarvis_runtime.py f266966f… router 59d5ec3d… dispatch f6e3357c…
       local_fast b89bcc3c… profiles e5978a2d… plist 0a921de5…
ROOT_CAUSE=AudioRecorder keeps ONE InputStream for the process lifetime, and
       _ensure_stream() returns early whenever it holds a stream on the same
       DEVICE. It never checks whether that stream is still RUNNING. Resuming
       the wake listener reopens the same input device and leaves the
       recorder's stream stopped, so turns 2+ got a dead handle: same object,
       active=False, zero callbacks, zero frames, capture ending only on the
       30 s ceiling.
REPRO=deterministic, no human, no speech. The callback appends a chunk per
       buffer while recording, so a healthy stream is non-zero even in a silent
       room -- "frames" separates a dead stream from a quiet room.
         turn 1  STREAM=0x112c06660 active=True   FRAMES=563
         turn 2  STREAM=0x112c06660 active=False  FRAMES=0
         turn 3  STREAM=0x112c06660 active=False  FRAMES=0
SECOND_BUG=_tune_recorder() was inside the `if rec is None` branch, so it ran
       only on turn 1 -- that is why the "recorder tuned" line was missing from
       later turns, which is what located the area. Latent on its own (the
       instance kept the values; start() resets VAD state but not
       _silence_duration/_max_wait), fixed regardless because the recorder can
       now be rebuilt mid-session.
FIX=_prepare_recorder_for_turn() every turn: close a stale stream so upstream's
       own _ensure_stream() rebuilds it on start(); construct if absent; apply
       tuning ALWAYS; log RECORDER_OBJECT/STREAM_OBJECT/STREAM_ACTIVE/DEVICE/
       RATE/MAX_WAIT/SILENCE_DURATION/THRESHOLD before and after.
       _close_stream_with_timeout() is UPSTREAM's method and carries the 3 s
       guard for the CoreAudio close hang that motivated the keep-open design,
       so this uses the sanctioned escape hatch. No upstream file modified.
VERIFY=the test exercises the SHIPPED function, not a copy:
         FIXED    563/563/563 frames, stream object differs per turn, none dead
         UNFIXED  563/0/0, zero-frame turns [2,3] -- still reproducible
HANDOFF=prepare+start+first frame = 90ms (turn 1) / 193ms / 159ms. The stale
       close costs ~105ms on turns 2+. Smaller than the ~279ms wake->capture
       handoff already paid, so NO fixed delay was added.
NOTED=||PaMacCore (AUHAL)|| err=-50 on stream open/close. Recorded rather than
       hidden; streams opened and frames flowed on every turn regardless.
REGRESSION=routes 100/100 · dangerous 10/10 · current-fact 0/31 · greetings
       61/61 · general 26/26 · LOCAL_FAST 24 runs 0 defects median 3.78s
       (max 5.69s, up from 4.40s -- variance, reported not hidden)
       ProcessType=Interactive PRI=97 · whisper warm 1.46s · ollama UNTIL 59m
NOT_DONE=PHASE 8 real 3-turn human test — HUMAN_APPROVAL_REQUIRED. No HUD work.
RESULT=PASS
