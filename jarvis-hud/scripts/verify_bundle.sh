#!/bin/zsh
# Verify the staged bundle. Read-only: inspects dist/, changes nothing.
#
# These are the checks that cannot be written as Swift unit tests, because they
# are about the shipped artifact rather than the code: what ended up in the
# Info.plist, which architecture was linked, whether the release binary still
# carries the debug machinery, and what the executable is allowed to reach.
set -uo pipefail

ROOT="${0:A:h:h}"
APP="$ROOT/dist/JARVIS HUD.app"
PLIST="$APP/Contents/Info.plist"
EXE="$APP/Contents/MacOS/JarvisHUD"

pass=0; fail=0
# `((n++))` returns the PRE-increment value, so at 0 it exits non-zero and a
# `cond && ok ... || bad ...` chain fires BOTH branches. Pre-increment, and
# return 0 explicitly, so these are always safe on the left of `||`.
ok()   { print -r -- "  PASS  $1"; ((++pass)); return 0 }
bad()  { print -r -- "  FAIL  $1"; ((++fail)); return 0 }
check() { [[ "$2" == "$3" ]] && ok "$1 = $2" || bad "$1 = $2 (expected $3)" }

print -r -- "bundle: $APP"
[[ -d "$APP" ]] || { print -r -- "not staged; run scripts/build_app.sh"; exit 1 }

print -r -- ""; print -r -- "== structure =="
for f in Contents/Info.plist Contents/MacOS/JarvisHUD Contents/PkgInfo; do
  [[ -e "$APP/$f" ]] && ok "$f present" || bad "$f missing"
done
[[ -d "$APP/Contents/Resources" ]] && ok "Contents/Resources present" \
                                   || bad "Contents/Resources missing"

print -r -- ""; print -r -- "== Info.plist =="
plutil -lint "$PLIST" >/dev/null 2>&1 && ok "plist is valid" || bad "plist is malformed"
pb() { /usr/libexec/PlistBuddy -c "Print :$1" "$PLIST" 2>/dev/null }
check "CFBundleIdentifier"         "$(pb CFBundleIdentifier)"         "local.jarvis.hud"
check "CFBundleExecutable"         "$(pb CFBundleExecutable)"         "JarvisHUD"
check "CFBundlePackageType"        "$(pb CFBundlePackageType)"        "APPL"
check "LSUIElement"                "$(pb LSUIElement)"                "true"
for k in CFBundleName CFBundleDisplayName CFBundleShortVersionString CFBundleVersion; do
  v=$(pb $k); [[ -n "$v" ]] && ok "$k = $v" || bad "$k is empty"
done
# The executable named in the plist must be the one that exists.
[[ -x "$APP/Contents/MacOS/$(pb CFBundleExecutable)" ]] \
  && ok "CFBundleExecutable resolves to an executable file" \
  || bad "CFBundleExecutable does not resolve"

print -r -- ""; print -r -- "== architecture =="
check "arch" "$(lipo -archs "$EXE" 2>/dev/null)" "arm64"

print -r -- ""; print -r -- "== signing =="
if codesign --verify --deep --strict "$APP" 2>/dev/null; then
  ok "codesign --verify --deep --strict"
else
  bad "codesign verification failed"
fi
sig=$(codesign -dv "$APP" 2>&1 | grep -c "Signature=adhoc")
check "ad-hoc signature" "$sig" "1"
ent=$(codesign -d --entitlements - "$APP" 2>/dev/null | grep -c "<key>")
check "entitlement count" "$ent" "0"

print -r -- ""; print -r -- "== no debug machinery in the release binary =="
# Phase 2A additions: the mock transcript, the mock reply and the future-only
# types must not exist in a release binary. The types are guarded at file scope,
# so absence here is the mechanical proof of that.
for s in JARVIS_HUD_MOCK JARVIS_HUD_PIN_STATE JARVIS_HUD_SEQUENCE \
         MockStateProvider "Mock State" "Mock Sequence" "Hostile Input" happyPath \
         FutureHUDContext LatencyBreakdown LatencyStripView \
         sequenceLocalFast sequenceCodex sequenceClaude \
         "Pythonとは何" "Pythonは読みやすさ" "NOT IN BACKEND" \
         "files inspected" "Auto-expand policy"; do
  n=$(strings -a "$EXE" | grep -cF -- "$s")
  [[ "$n" == "0" ]] && ok "absent: $s" || bad "present ($n): $s"
done

print -r -- ""; print -r -- "== no process, network or web surface =="
for s in NSTask "Process(" URLSession WKWebView NSAppleScript posix_spawn \
         "system(" popen "/bin/sh" "/usr/bin/"; do
  n=$(strings -a "$EXE" | grep -cF -- "$s")
  [[ "$n" == "0" ]] && ok "absent: $s" || bad "present ($n): $s"
done
for fw in Network CFNetwork WebKit AppKitScripting; do
  otool -L "$EXE" | grep -q "$fw" && bad "links $fw" || ok "does not link $fw"
done

print -r -- ""; print -r -- "== production state is read-only =="
# The only production path the binary may know is the state file itself.
if strings -a "$EXE" | grep -q "jarvis_state.json"; then
  ok "knows the state file path"
else
  bad "state file path missing from the binary"
fi
for s in "jarvis_runtime" "launchctl" "kickstart" ".tmp"; do
  n=$(strings -a "$EXE" | grep -cF -- "$s")
  [[ "$n" == "0" ]] && ok "absent: $s" || bad "present ($n): $s"
done

print -r -- ""; print -r -- "$pass passed, $fail failed"
exit $(( fail > 0 ))
