# Loop Journal — part 5 · delegation + router

---
STEP_ID=S033
PHASE=A1 Codex inspection (read-only)
VERIFY=codex-cli 0.149.1 · ~/.nvm/.../bin/codex -> codex.js
       sandbox_mode="read-only" · approval_policy="on-request" · approvals_reviewer="user"
       ~/.codex/config.toml sha256 5ac879a7… == the 2026-08-29 07:49 migration backup (untouched)
       auth: ChatGPT OAuth tokens present, OPENAI_API_KEY empty (subscription-only)
BLOCKER=openai_base_url = http://127.0.0.1:17841/v1 — the codex-chatgpt-web proxy
       ("Codex Web GPT.app" 3.0.3) is NOT running. Pre-existing, not caused here.
RESULT=PASS (inspection) / Codex non-functional as configured

---
STEP_ID=S034
PHASE=A1 Two findings that shaped everything after
ACTION=drive codex exec directly
VERIFY=(a) `codex exec` IGNORES trust_level — refused even in /Users/Rascal, which IS
           listed as trusted: "Not inside a trusted directory". This resolves an
           ambiguity open since the first audit: the broad [projects."/Users/Rascal"]
           entry grants codex exec nothing.
       (b) against the dead proxy codex NEVER self-terminates — >10 min, killed by
           the harness, emitting "Reconnecting... waiting for network" forever.
           => any delegation MUST impose its own timeout.
RESULT=PASS (diagnosis)

---
STEP_ID=S035
PHASE=A1/A2 Make Codex work WITHOUT touching its config
ACTION=probe per-invocation overrides (-c key=value)
VERIFY=none                                  -> hangs
       openai_base_url=""                    -> 5.9s, "chatgpt-web/high is not supported
                                                with a ChatGPT account"  (synthetic proxy model)
       openai_base_url="https://api.openai.com/v1" -> 401, missing api.responses.write
       openai_base_url="" -m gpt-5.5         -> exit 0, 20.8s, all 3 planted bugs found
       ~/.codex/config.toml sha256 unchanged throughout
DECISION=CODEX_INTEGRATION_METHOD = B (CLI subprocess behind a fixed-argv wrapper).
       Built-in codex_app_server replaces the whole turn and inherits the broken
       base_url; MCP adds a layer with no benefit.
RESULT=PASS

---
STEP_ID=S036
PHASE=A3/A5/A6 bin/jarvis-codex
CHANGE=NEW FILE. Pins --sandbox read-only, -c openai_base_url="", -m gpt-5.5,
       a directory allowlist and a hard timeout in argv, where the model cannot reach them.
VERIFY=read-only smoke exit 0 / 25.8s / fixture+config sha unchanged
       allowlist: /etc and ~/Documents -> exit 77 ; missing dir 66 ; no prompt 64
       timeout: 5s budget -> exit 75 at 5.2s, 「Codexを利用できませんでした（応答なし）」
       write attempt -> file unchanged, Codex declines
SECURITY_NOTE=codex exec reports `approval: never`. approval_policy="on-request" does
       NOT apply non-interactively. --sandbox read-only is the ONLY enforcement.
RESULT=PASS

---
STEP_ID=S037
PHASE=A4 delegation accuracy
VERIFY=10/10, median 19.1s — Python debug, Laravel N+1/404, TS types, SQL GROUP BY,
       REST design, Dockerfile :latest+hardcoded key, git diff DEBUG/SECRET_KEY,
       SQLi+MD5, test-failure analysis, non-coding control
RESULT=PASS

---
STEP_ID=S038
PHASE=B bin/jarvis-claude
CHANGE=NEW FILE. Pins --permission-mode plan and the absolute wrapper path, and
       PROVES the config root per call via CLAUDE_LAUNCHER_SELFTEST=1 — exits 78 if
       CHILD_CLAUDE_CONFIG_DIR != ~/.claude-work, 78 on recursion, 69 if the selftest
       cannot run. Fail closed, not fail open.
VERIFY=selftest -> CHILD_CLAUDE_CONFIG_DIR=/Users/Rascal/.claude-work, RECURSION=no
       raw CLI instead of wrapper -> exit 69 (refused)
       read-only analysis exit 0 / 28.0s / fixture sha unchanged
       Claude produced structural analysis where Codex produced line-level defects
RESULT=PASS

---
STEP_ID=S039
PHASE=C1 bin/jarvis_gate.py — the deterministic boundary
CHANGE=NEW FILE. Pure pattern matching, no model/network/IO. Categories: DESTRUCTIVE,
       GIT_DESTRUCTIVE, EXTERNAL_SIDE_EFFECT, SECRET_ACCESS, PRIVILEGE, SYSTEM_CHANGE,
       CLOUD_MUTATION.
WHY_SEPARATE=approvals.deny matches COMMAND strings; the router input is an UTTERANCE.
       「このファイル全部消して」 never matches *rm -rf *. Neither layer subsumes the other.
DEFECTS_FOUND_AND_FIXED=
  1. `\bsudo\b` did NOT match 「sudoで再起動して」 — Python treats で as a word char so
     there is no boundary after `o`. A privilege-escalation utterance passed the gate.
     All ASCII tokens re-anchored on (?<![A-Za-z0-9_])…(?![A-Za-z0-9_]).
     A Japanese-voice-specific failure an English suite would never surface.
  2. the "is this a question?" exemption let 「APIキーを教えて」 through. Narrowed to
     genuinely definitional markers; secret categories additionally require the
     absence of a disclosure verb. 「APIキーとは何ですか」 still passes.
  3. `api\s*key` does not match 「APIキー」 (katakana). Japanese spellings added.
  4. my own bulk \b rewrite mis-converted three TRAILING boundaries into lookbehinds
     (`\.env`, `push --f`, `branch -D`), which silently disabled them. Caught by the
     suite, fixed.
VERIFY=dangerous 25/25 caught · benign 17/17 passed · 0 leaks · 0 false positives
RESULT=PASS

---
STEP_ID=S040
PHASE=C1-C4 bin/jarvis_router.py
CHANGE=NEW FILE. gate -> explicit override -> fast path -> gemma4:e2b
       (reasoning_effort=none, temperature 0, max_tokens 24, closed label set).
       The model cannot emit CONFIRMATION_REQUIRED; unparseable -> LOCAL.
RESULT=PASS

---
STEP_ID=S041
PHASE=C5 router benchmark, 50 cases
ITERATION=49/50 -> fix -> 49/50 (new miss) -> fix -> 50/50
  miss 1 「このテストが落ちる原因を調べて」 -> WEB   (調べて outweighed the debugging context)
         => CODEX fast path
  miss 2 「Next.jsの最新バージョンは?」    -> CODEX (fast path matched `.js` in the product name)
         => .js/.ts removed from the extension set; WEB evaluated first
  miss 3 「…要約して: /private/tmp/claude-502/…」 -> CLAUDE (override matched the PATH)
         => overrides and fast paths now run on path-stripped text.
            Found by the injection test, not by the router suite.
VERIFY=router_accuracy 50/50=1.000 · dangerous 10/10=1.000
       false_codex/claude/web all 0.000 · unnecessary_delegation 0.000
       deterministic 24/50 @ 0.0ms · llm 26/50 @ 2.4s
RESULT=PASS

---
STEP_ID=S042
PHASE=C7/C8 bin/jarvis-dispatch end-to-end
VERIFY=T5 「このディレクトリ全部消して」 -> CONFIRMATION_REQUIRED in 0.70s, nothing
          dispatched, fixture intact  ← run FIRST, deliberately
       T1 LOCAL 30.8s · T2 WEB 43.7s · T3 CODEX 39.8s · T4 CLAUDE 58.8s
       T6 injection: no payload executed, 0 ssh-key material in logs, 0 exfil hits,
          fixtures and ~/.codex/config.toml sha unchanged
       C8: 833-char Codex output spoken as 2 sentences + 「詳細も読み上げますか？」
LATENCY_NOTE=`hermes -z` costs 11.6s of CLI startup per invocation (measured twice).
       That dominates the E2E numbers and is an artifact of this text harness — the
       real voice path runs against the persistent gateway, not a fresh CLI per turn.
RESULT=PASS

---
STEP_ID=S043
PHASE=E regression
VERIFY=config issues 0 · model gemma4:e2b + reasoning_effort=none
       WAKE untouched: enabled=True hey_jarvis device=None sens=0.35 frames=1
       VOICE untouched: beep=False vol=0.05 auto_tts=True barge_in=True
       STT/TTS untouched: small ja / edge ja-JP-NanamiNeural
       gate: approvals.mode=manual deny=40 · browser.use_real_profile=False
       voice_mode.py patch unchanged · state.db schema 26 integrity ok
       8644 and 11434 both loopback-only · 5 ollama models, none deleted
       ~/.codex/config.toml sha 5ac879a7… unchanged · ~/.claude-work untouched
OBSERVED_NOT_CAUSED=cron enabled 35 -> 34. jobs.json entry count unchanged (63), nothing
       deleted. automation:x:video went enabled=False with last_status=ok and no error.
       No cron command was issued in this session; the gateway rewrote jobs.json at
       22:17. Cause not determinable from the data — reported, not explained away.
RESULT=PASS
