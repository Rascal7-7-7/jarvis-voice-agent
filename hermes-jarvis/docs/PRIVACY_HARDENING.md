# Privacy hardening — log and state file permissions

Applied 2026-08-31. Scope is permissions only: no logging policy changed, no
data deleted, no schema touched.

## What was wrong

The state file and the runtime log carry what the user said and what JARVIS
replied, and both were readable by other local accounts.

```
logs/                    0755
logs/jarvis_state.json   0644     reply, first 60 chars  (set_state SPEAKING)
logs/jarvis_runtime.log  0644     30 transcripts         (transcript=%r)
logs/runtime.out.log     0644     the same 30 transcripts (launchd stdout)
/Users/Rascal            0750  owner Rascal  group staff
```

This machine has three local accounts: `Rascal`, `userA`, `userB`. `userB` is in
group `staff`, and `/Users/Rascal` is `drwxr-x---` with group `staff` — so
`userB` could traverse in and read both files **with no privilege escalation and
no prompt**. (`userA` is not in `staff` and could not.)

Observed live during the audit, before the change:

```json
{"state": "SPEAKING",
 "detail": "申し訳ありませんが、私はその内容について応答することができません。", …}
```

This was a pre-existing condition, not something a recent phase introduced. It
predates the HUD.

## What changed

Three edits to `bin/jarvis_runtime.py`, nothing else.

**1. The log directory is tightened on every start.**

```python
STATE_FILE_MODE = 0o600
LOG_FILE_MODE = 0o600
LOG_DIR_MODE = 0o700

os.makedirs(LOG_DIR, exist_ok=True)
os.chmod(LOG_DIR, LOG_DIR_MODE)
```

The directory mode is the load-bearing control. At 0700 nobody else can
traverse into `logs/` at all, whatever mode the individual files end up with —
which matters for the files this runtime does not create, like launchd's
`runtime.out.log`.

**2. `set_state()` sets the mode on the TEMP file.**

```python
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
             STATE_FILE_MODE)
os.fchmod(fd, STATE_FILE_MODE)
```

This is the part a plain `chmod 600 jarvis_state.json` would not have achieved.
Publication is `write tmp` → `os.replace()`, which swaps the inode — so the
published file's mode comes from the temp file, and a mode set on the live file
is discarded by the very next publish. There is a test that demonstrates
exactly this (`test_d_chmod_on_the_live_file_would_not_have_worked`).

`fchmod` as well as the open mode, because `os.open`'s mode argument is masked
by the process umask and this must not depend on what umask launchd supplied.

`O_NOFOLLOW` so the temp path cannot be pre-created as a symlink aimed
elsewhere. The path is a constant rather than input, but a file being written
with tightened permissions should not be redirectable.

**3. The log file is created at 0600 before `FileHandler` opens it.**

```python
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, LOG_FILE_MODE)
os.fchmod(fd, LOG_FILE_MODE)
os.close(fd)
```

`FileHandler` would create it at the umask default, leaving a window — however
brief — where a log of transcripts was world-readable. Pre-creating covers every
path that yields a new file: first run, a deleted log, and the size-based
rotation immediately above it. `O_CREAT|O_APPEND` leaves existing content alone,
so a log written before this change is tightened on the next start rather than
lost.

Atomicity is unchanged: still `write tmp` → `os.replace()`, never an in-place
truncate.

## Files, and why each one

Existing files were changed by hand once; new ones are handled by the code above.

| file | mode | why |
|---|---|---|
| `logs/` | 0700 | traversal control; covers everything below |
| `jarvis_state.json` | 0600 | reply text |
| `jarvis_runtime.log` | 0600 | transcripts and replies |
| `runtime.out.log` | 0600 | launchd stdout — **the same 30 transcripts again** |
| `runtime.err.log` | 0600 | 4 tracebacks, 18 filesystem paths |
| `ollama.log`, `ollama.error.log` | left at 0644 | checked: 0 transcripts, 0 Japanese text |
| `tccprobe*.log` | left at 0644 | checked: no user content |
| `jarvis_runtime.lock` | left at 0644 | contains a pid |

The last three groups were left deliberately rather than swept up, per "do not
chmod files without checking their contents". They are protected by the 0700
directory. Note that `runtime.out.log` and `runtime.err.log` are created by
launchd, not by this runtime — if they are deleted, launchd recreates them at
its own umask. The directory mode is what makes that safe.

The project root was NOT changed. `~/AI-Lab/hermes-jarvis` stays 0755; the
scope here is log privacy, not the repository.

## Diff scope (exact)

```
3 hunks, +54 / -2 = 56 changed lines
of which 24 are code; the rest are comments
```

Recorded here because the phase report said both "3箇所、57行" and "差分は1行のみ",
which cannot both describe the patch. The second was a mis-statement: it
described the SHA256 *listing* (one of four filenames had a changed hash), not
the diff. The patch numbers above are the measured ones.

## Verification

`tests/test_state_permissions.py`, 10 tests, all passing:

```
A  log directory is 0700 after import (starting from a deliberately wrong 0755)
B  first state file is 0600
C  mode survives 100 atomic replacements — with an inode-changed assertion, so
   a passing test cannot mean "it was never actually replaced"
D  a manual chmod 644 on the live file is corrected by the next publish
E  JSON content, key set, non-ASCII detail and atomicity unchanged; no temp left
F  a deleted log is recreated at 0600
G  a pre-existing 0644 log is tightened, and its content is preserved
+  live deployment: directory 0700, files 0600, owned by this uid
```

Live, after a runtime restart:

```
logs                    700
logs/jarvis_state.json  600
logs/jarvis_runtime.log 600
```

All three processes still work — they run as the same uid (502):

```
runtime   pid 89490  writes state
watchdog  pid 33852  read pid 89490 at 06:10:30, after hardening
HUD       pid 90805  holds logs/ open read-only (3r DIR)
```

No ACL entries on the directory or either file, so mode bits are the whole
story.

## What was NOT done

- **No logging policy change.** `logger.info("transcript=%r", transcript)` still
  runs. Whether the transcript should be logged at all is a separate decision
  with a real debuggability cost on the other side.
- **No data deleted.** The 30 existing transcripts are still there, now
  owner-only.
- **No schema change.** `reply[:60]` at SPEAKING is untouched, and was
  explicitly not expanded.
- **No LaunchAgent change.** Adding a `Umask` key would have been another way to
  handle `runtime.out.log`, but the plists are out of scope.

## Transcript retention — recommendation only, not applied

Now that the audit has established transcripts are persisted in two files:

| option | debug value | privacy | incident analysis | disk |
|---|---|---|---|---|
| A. keep as-is, 0600 | full | owner-only | full | grows to the 5 MB cap, then `.1` |
| B. transcript only at DEBUG level | none at INFO | best | **poor** — the interesting failures are the ones you did not expect | smaller |
| C. log length and a hash, not the text | partial — "STT returned 12 chars" | good | moderate | smaller |
| D. separate private log with short retention | full, briefly | good | full within the window | bounded |

**Recommendation: A now, D later if wanted.** This project has repeatedly needed
the transcript to tell "the user said nothing" from "the room was loud" from
"whisper mis-transcribed" — the 23:59 turn that transcribed a television is a
concrete example, and it would have been invisible under B or C.

D is the principled end state: transcripts in their own file with a retention
window, so debuggability is preserved for recent turns without accumulating
months of speech. It is more work than this phase, and the 0600 change already
removes the exposure that motivated it.

## Rollback

`backups/privacy-20260831-060843/jarvis_runtime.py`

```sh
B=~/AI-Lab/hermes-jarvis/backups/privacy-20260831-060843
launchctl bootout gui/$(id -u)/local.jarvis.runtime
cp "$B/jarvis_runtime.py" ~/AI-Lab/hermes-jarvis/bin/jarvis_runtime.py
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.jarvis.runtime.plist
```

**ROLLBACK_SECURITY_NOTE.** Reverting the source restores the old *writer*
behaviour: the next `set_state()` publishes a temp file at the umask default,
so `jarvis_state.json` returns to 0644 on its own. The directory stays 0700
unless separately changed — and it should stay that way. Reverting the modes as
well:

```sh
chmod 755 ~/AI-Lab/hermes-jarvis/logs        # DO NOT unless you mean it
chmod 644 ~/AI-Lab/hermes-jarvis/logs/*.log
```

restores read access for every local account in group `staff` — on this machine,
`userB`. There is no reason to do this. It is written down so that a rollback is
a decision rather than an accident.
