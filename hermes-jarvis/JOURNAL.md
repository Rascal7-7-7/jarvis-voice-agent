# Loop Journal — Hermes JARVIS Build

---
STEP_ID=S001
TIME=2026-08-29T07:37+0900
PHASE=1 Baseline Revalidation
ACTION=read-only re-audit (os/hw/mem/disk/hermes/python/gateway/launchd/ports/config/state.db/skills/cron/mcp/codex/claude/ollama/stt/tts/procs)
BEFORE=n/a
CHANGE=none
VERIFY=hermes 0.15.2 / gateway PID 38523 running / 127.0.0.1:8644 / state.db schema_version=13, sessions=3523, messages=10563 / skills=34 / cron=63 / hermes MCP=0 / ollama stopped, 0 models / voice deps missing in 3.12.3 env
RESULT=PASS
ROLLBACK_AVAILABLE=N/A (no change)
NEXT=S002

---
STEP_ID=S002
TIME=2026-08-29T07:40+0900
PHASE=2 Migration Compatibility Investigation
ACTION=PyPI/GitHub release analysis + source diff of state schema + config version logic
BEFORE=assumed "0.20.6 installable via pip"
CHANGE=none (source fetched to scratchpad only)
VERIFY=PyPI max=0.19.0 (no wake word). GitHub tag v2026.8.27 = 0.20.6.
       SCHEMA_VERSION 13 -> 26; migrations v16/v18/v20/v22/v23/v25 apply; all additive
       except v25 which rewrites sessions.system_prompt (one-way).
       _config_version effective 0 (key absent) vs 0.20 auto-migration floor v12.
       Node 26 is an installer/heal/upgrade requirement; claude+codex live in node v24.14.1.
RESULT=PASS
ROLLBACK_AVAILABLE=N/A (no change)
NEXT=S003

---
STEP_ID=S003
TIME=2026-08-29T07:48+0900
PHASE=3 Backup / Rollback Design
ACTION=create backup root; sqlite3 .backup snapshots; rsync ~/.hermes; capture meta; cache 0.15.2 wheel; checksums; manifest
BEFORE=no rollback point existed
CHANGE=ADDITIVE ONLY - created ~/AI-Lab/backups/hermes-jarvis-migration-20260829-074823 (241M)
       and ~/AI-Lab/hermes-jarvis/ (docs)
VERIFY=integrity_check ok (both DBs); snapshot counts == live counts (3523/10563);
       skills 34==34; scripts 68==68; 18283 files hashed; wheel cached (11.3MB)
RESULT=PASS
ROLLBACK_AVAILABLE=YES
NEXT=S004

---
STEP_ID=S004
TIME=2026-08-29T07:50+0900
PHASE=3 Rollback Test
ACTION=restore backup into isolated HERMES_HOME and drive it with the 0.15.2 binary
BEFORE=backup unproven
CHANGE=none to production (scratchpad only)
VERIFY=integrity ok / schema_version=13 / 3523/10563 /
       HERMES_HOME=<tmp> hermes sessions list rc=0 with real rows /
       hermes skills list rc=0
       SIDE FINDING: HERMES_HOME env var works -> this is the Phase 5 dry-run mechanism
RESULT=PASS
ROLLBACK_AVAILABLE=YES
NEXT=S005 (Phase 4 security preflight)

---
STEP_ID=S005
TIME=2026-08-29T07:52+0900
PHASE=4 Security Preflight
ACTION=assess codex trust scope / browser isolation / secrets inheritance
BEFORE=H-1 assumed "home-wide write access"
CHANGE=none
VERIFY=codex sandbox_mode=read-only bounds the trust entry -> risk lower than assumed; DEFERRED to human.
       Chrome 5.4G + Brave 2.9G real profiles exist; CDP 9222 closed; codex node_repl has chrome backend.
       Hermes gateway launchd env = PATH/VIRTUAL_ENV/HERMES_HOME ONLY; 0 secret names in plist;
       no ~/.hermes/.env; PAT/BRAVE key not defined in any shell rc -> secrets isolation ALREADY satisfied.
RESULT=PASS (1 item HUMAN_APPROVAL_REQUIRED, non-blocking)
ROLLBACK_AVAILABLE=N/A
NEXT=S006

---
STEP_ID=S006
TIME=2026-08-29T07:53+0900
PHASE=5 Dry Run - install attempt 1
ACTION=uv pip install "hermes-agent[...] @ git+...@v2026.8.27"
BEFORE=empty ~/.hermes-venv
CHANGE=none persisted (build failed)
VERIFY=RuntimeError: "Building wheels or sdists for hermes-agent is not supported."
RESULT=FAIL
ROLLBACK_AVAILABLE=N/A (nothing installed)
NEXT=S007 (different method, not a retry of the same command)

---
STEP_ID=S007
TIME=2026-08-29T07:55+0900
PHASE=5 Dry Run - install attempt 2 (editable, upstream-sanctioned)
ACTION=git clone --depth 1 --branch v2026.8.27 -> uv pip install -e ".[voice,wake,mcp,cron,edge-tts]"
BEFORE=empty venv
CHANGE=ADDITIVE - ~/.hermes-venv (511MB) + src/hermes-agent-v2026.8.27 (259MB)
VERIFY=sha 5fc308a70719a83cccdbba4c0e39c23f5a8239d5 == tag v2026.8.27; pyproject version=0.20.6;
       venv reports hermes-agent 0.20.6; all voice+wake deps import; arm64 native;
       onnxruntime providers include CoreMLExecutionProvider
RESULT=PASS
ROLLBACK_AVAILABLE=YES (production untouched)
NEXT=S008

---
STEP_ID=S008
TIME=2026-08-29T07:58+0900
PHASE=5 Dry Run - migration against an isolated copy
ACTION=HERMES_HOME=<scratch copy> hermes sessions list  (+ skills/cron/mcp/doctor/gateway)
BEFORE=copy at schema_version 13, 3523/10563, 3 inline system_prompts
CHANGE=only the scratch copy
VERIFY=schema 13->26; integrity ok; 3523/10563 UNCHANGED; 7 new tables; 3 inline sysprompts -> 0 inline
       + 3 rows in system_prompts (lossless); v23 FTS opt-in NOT executed (flag only);
       config.yaml byte-identical to backup; cron listing byte-identical between 0.15.2 and 0.20.6;
       gateway listened on 127.0.0.1:8645 then released cleanly. Production stayed PID 38523 / 8644 / schema 13.
RESULT=PASS
ROLLBACK_AVAILABLE=YES
NEXT=S009

---
STEP_ID=S009
TIME=2026-08-29T08:03+0900
PHASE=6 Production Upgrade - atomic switch
ACTION=launchctl unload; plist ProgramArguments[0] pyenv-python -> ~/.hermes-venv/bin/python,
       VIRTUAL_ENV -> ~/.hermes-venv; PATH DELIBERATELY UNCHANGED; launchctl load
BEFORE=FINAL_BACKUP verified (18283/18283 checksums OK; fresh snapshot hash identical to 07:48 = production untouched)
CHANGE=2 plist values; inline plist backup .pre-0.20.6-20260829-080329 taken
VERIFY=plutil OK; gateway PID 29735 running venv python; 127.0.0.1:8644 LISTEN; webhook connected;
       cron ticker started; kanban dispatcher embedded
RESULT=PASS
ROLLBACK_AVAILABLE=YES (revert 2 plist values)
NEXT=S010

---
STEP_ID=S010
TIME=2026-08-29T08:04+0900
PHASE=6 Production Upgrade - deliberate DB migration
ACTION=0.20.6 defers state.db migration (lazy). Rather than let a cron agent trigger it unsupervised,
       stop gateway -> run the exact call validated in dry-run -> verify -> start gateway
BEFORE=0.20.6 code running against schema_version 13 DB (mixed state)
CHANGE=state.db migrated 13 -> 26
VERIFY=schema_version=26; sessions=3523 messages=10563 (unchanged); integrity_check=ok;
       7 new tables; inline system_prompt 0; system_prompts rows 3
       TOTAL DOWNTIME 08:04:23 -> 08:04:25 = 2 seconds
RESULT=PASS
ROLLBACK_AVAILABLE=YES (restore hermes-final/state.db + revert plist)
NEXT=S011

---
STEP_ID=S011
TIME=2026-08-29T08:05+0900
PHASE=6 Production Upgrade - command resolution
ACTION=ln -s ~/.hermes-venv/bin/hermes ~/.local/bin/hermes
BEFORE=`hermes` resolved via pyenv shims to 0.15.2; 0 scripts actually invoke `hermes`
       (only hit was a comment in automation-learn-ai-practices.sh)
CHANGE=one symlink in ~/.local/bin (first in PATH)
VERIFY=command -v hermes -> ~/.local/bin/hermes; hermes --version -> v0.20.6 (2026.8.27);
       pyenv 0.15.2 still intact as rollback target
       PATH NOT modified, so venv python/python3 do NOT shadow pyenv for the 68 automation scripts
RESULT=PASS
ROLLBACK_AVAILABLE=YES (remove one symlink)
NEXT=S012 (Phase 7 regression + live cron verification)
