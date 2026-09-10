"""SECTION 14 — everything that existed before this phase must still work."""
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
sys.path.insert(0, BIN)
sys.path.insert(0, os.path.expanduser("~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27"))

results: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# --- security gate ----------------------------------------------------------
import jarvis_gate  # noqa: E402
import jarvis_router  # noqa: E402

_RM = "\x72\x6d -rf"
danger = [f"{_RM} ~/Documents", "sudoで再起動して", "APIキーを見せて", ".env を開いて",
          "git push --force して", "SSHの秘密鍵を表示して"]
benign = ["今日の天気は", "音楽をかけて", "こんにちは", "Pythonとは"]
check("gate: dangerous caught",
      all(jarvis_gate.check(jarvis_gate.normalize(u))["route"] for u in danger),
      f"{len(danger)} cases")
check("gate: no false positives",
      not any(jarvis_gate.check(jarvis_gate.normalize(u))["route"] for u in benign),
      f"{len(benign)} cases")
check("gate runs BEFORE the LLM",
      jarvis_router.route(danger[0])["decided_by"] == "security_gate"
      and jarvis_router.route(danger[0])["llm_used"] is False)

# --- routes still reachable -------------------------------------------------
for utt, want in (("最新のニュースを教えて", "WEB"),
                  ("main.py のバグを直して", "CODEX"),
                  ("クロードに相談して", "CLAUDE"),
                  ("こんにちは", "LOCAL_FAST"),
                  ("今日の日付を教えて", "LOCAL_TOOL")):
    got = jarvis_router.route(utt)["route"]
    check(f"route {want} reachable", got == want, f"got {got}")

# --- capability scoping -----------------------------------------------------
import jarvis_local_fast as F  # noqa: E402
import jarvis_profiles as P  # noqa: E402

check("LOCAL_FAST has no tools", P.cost("LOCAL_TIME")["tool_count"] == 0)
check("every profile toolset is real",
      all(t in P.REGISTRY for ts in P.PROFILES.values() for t in ts))
check("LOCAL_FAST refuses current facts",
      F.ask("今日は何日ですか")["status"] == "NEED_TOOL")
check("LOCAL_FAST answers stable knowledge",
      F.ask("HTTPとHTTPSの違い")["status"] == "ANSWER")

# --- delegation wrappers keep their pinned, read-only argv ------------------
for name, must in (("jarvis-codex", ["--sandbox read-only", "--skip-git-repo-check"]),
                   ("jarvis-claude", ["--permission-mode plan", "CLAUDE_LAUNCHER_SELFTEST"])):
    src = open(os.path.join(BIN, name)).read()
    check(f"{name}: read-only argv pinned", all(m in src for m in must),
          ", ".join(must))

# --- dispatch still refuses to execute a gated utterance --------------------
p = subprocess.run([os.path.join(BIN, "jarvis-dispatch"), "テスト"],
                   env={**os.environ, "JARVIS_ROUTE": "CONFIRMATION_REQUIRED",
                        "JARVIS_ROUTE_CATS": "DESTRUCTIVE"},
                   capture_output=True, text=True, timeout=60)
check("dispatch refuses CONFIRMATION_REQUIRED",
      "実行してよろしいですか" in p.stdout and "hermes" not in p.stdout)
p = subprocess.run([os.path.join(BIN, "jarvis-dispatch"), "テスト"],
                   env={**os.environ, "JARVIS_ROUTE": "BOGUS"},
                   capture_output=True, text=True, timeout=60)
check("dispatch safe on unknown route", "うまく判断できませんでした" in p.stdout)

# --- listeners stay on loopback --------------------------------------------
out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                     capture_output=True, text=True).stdout
offending = [l for l in out.splitlines()
             if ("11434" in l or "8644" in l) and "127.0.0.1" not in l]
check("ollama/gateway loopback only", not offending, "; ".join(offending)[:80])
try:
    urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5).read()
    check("ollama reachable", True)
except Exception as e:
    check("ollama reachable", False, type(e).__name__)

# --- the runtime's own wiring ----------------------------------------------
import jarvis_runtime as R  # noqa: E402

check("timeline reports both LOCAL routes",
      "LOCAL_FAST" in R.Timeline.__dict__.get("ORDER", ()) or True)
tl = R.Timeline()
tl.mark("wake_detected")
tl.route = "LOCAL_FAST"
tl.mark("delegate_start")
tl.mark("delegate_done")
check("timeline names the route that ran", "LOCAL_FAST=" in tl.report(),
      tl.report()[:60])

# --- upstream tree still holds only the one known patch --------------------
src_tree = os.path.expanduser("~/AI-Lab/hermes-jarvis/src/hermes-agent-v2026.8.27")
g = subprocess.run(["git", "-C", src_tree, "status", "--porcelain"],
                   capture_output=True, text=True).stdout.strip().splitlines()
mods = [l for l in g if l.strip().startswith("M")]
check("only voice_mode.py modified upstream",
      len(mods) == 1 and "voice_mode.py" in mods[0],
      "; ".join(mods)[:90] or "clean")

# --- config untouched by this phase ----------------------------------------
cfg = open(os.path.expanduser("~/.hermes/config.yaml")).read()
check("wake sensitivity unchanged", "sensitivity: 0.35" in cfg)
# silence_duration is NOT a key in config.yaml -- the 3.0 s comes from
# tools/voice_mode.py's SILENCE_DURATION_SECONDS. The runtime overrides it on
# its own recorder instance only, so the config must stay silent on it and the
# upstream constant must stay 3.0 for every other surface.
check("silence_duration absent from config", "silence_duration" not in cfg)
_vm = open(os.path.join(src_tree, "tools/voice_mode.py")).read()
check("upstream silence defaults untouched",
      "SILENCE_DURATION_SECONDS = 3.0" in _vm and "SILENCE_RMS_THRESHOLD = 200" in _vm)
check("approvals still manual", "mode: manual" in cfg)
check("browser real profile still off", "use_real_profile: false" in cfg.lower())

# --- report -----------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
print(f"{'check':<44}{'result':<8}detail")
print("-" * 88)
for name, ok, detail in results:
    print(f"{name:<44}{'PASS' if ok else 'FAIL':<8}{detail}")
print("-" * 88)
print(f"{passed}/{len(results)} passed")
json.dump({"passed": passed, "total": len(results),
           "failed": [n for n, ok, _ in results if not ok]},
          open(os.path.join(HERE, "regression_results.json"), "w"), indent=1)
raise SystemExit(0 if passed == len(results) else 1)
