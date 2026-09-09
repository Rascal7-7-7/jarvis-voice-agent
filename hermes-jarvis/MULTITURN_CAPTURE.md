# MULTITURN_CAPTURE — the second-turn failure, 2026-08-30

## Symptom

Turn 1 worked. Every turn after it captured nothing and ended on the 30 s
ceiling:

```
turn 1   CAPTURE=5177ms   transcript='こんにちは'    PASS
turn 2   CAPTURE=30005ms  state=IDLE no speech
turn 3   CAPTURE=30005ms  state=IDLE no speech
```

The wake listener itself was healthy — it re-armed in 113 ms and detected the
second and third wake words. The failure was entirely downstream of wake, in
command capture.

## Root cause

`AudioRecorder` keeps **one InputStream for the life of the process**, and
`_ensure_stream()` returns early whenever it already holds a stream on the same
device:

```python
if self._stream is not None:
    if identity == self._stream_identity:
        return  # already alive on the same device
```

It asks whether the device changed. It never asks whether the stream it is
holding is **still running**. Resuming the wake listener reopens the same input
device and leaves the recorder's stream stopped, so from turn 2 onward `start()`
was handed a dead handle: same object, `active=False`, zero callbacks, zero
frames, and a capture that could only end on the ceiling.

## Reproduced deterministically, with no human and no speech

The decisive question was never "was there speech" but "did the stream deliver
frames at all" — the callback appends a chunk for every buffer while recording,
so a healthy stream is non-zero even in a silent room. That makes the bug
testable without a microphone performance.

`tests/repro_second_turn.py` drives the runtime's exact sequence
(`pause_listening → start → stop → resume_listening`) three times:

| turn | RECORDER | STREAM | active | FRAMES | PEAK_RMS |
|---:|---|---|---|---:|---:|
| 1 | 0x112bd19a0 | 0x112c06660 | **True** | 563 | 85 |
| 2 | 0x112bd19a0 | 0x112c06660 | **False** | **0** | 0 |
| 3 | 0x112bd19a0 | 0x112c06660 | **False** | **0** | 0 |

Same recorder, same stream object, and the only thing that differs is one
boolean. The recorder was never recreated and its settings were never reset —
`start()` resets the per-turn VAD state but leaves `_silence_duration` and
`_max_wait` alone, so those persisted correctly.

## A second, independent bug found on the way

`_tune_recorder()` was called inside the `if rec is None` branch, so it ran
**only on the first turn**. That is why the `recorder tuned:` line was missing
from turns 2+, which is what pointed at the area in the first place.

On its own this one was latent rather than fatal: the instance kept the tuned
values anyway. It is fixed regardless — the tuning must not depend on
construction happening this turn, because after this fix the recorder sometimes
*is* rebuilt.

## The fix

`_prepare_recorder_for_turn()` in `bin/jarvis_runtime.py`, run **every** turn:

```
stale stream?  -> close it, so upstream's _ensure_stream() rebuilds on start()
no recorder?   -> construct one
always         -> apply the runtime tuning
always         -> log the full object/stream/device/settings state
```

`_close_stream_with_timeout()` is upstream's own method and carries the 3 s
guard for the CoreAudio close hang that motivated keeping the stream open in
the first place — so this uses the sanctioned escape hatch rather than working
around it. No upstream file was modified; `tools/voice_mode.py` still carries
only the dynamic-mic patch.

Device-following is untouched: `_ensure_stream()` still compares the OS default
input identity and reopens on change, and no numeric PortAudio index is stored.

## Verified — both directions

The test exercises the **shipped** function, not a copy of the fix.

```
FIXED    turn 1 FRAMES=563   turn 2 FRAMES=563   turn 3 FRAMES=563   zero-frame turns: none
UNFIXED  turn 1 FRAMES=563   turn 2 FRAMES=0     turn 3 FRAMES=0     zero-frame turns: [2, 3]
```

Stream object ids differ per turn under the fix, confirming the rebuild. The
unfixed path is kept runnable so the failure stays reproducible on demand.

## Handoff window (no fixed delay added)

Measured from `prepare` to the first callback landing:

| turn | prepare | start() | to 1st frame | total |
|---:|---:|---:|---:|---:|
| 1 | 2 ms | 79 ms | 10 ms | **90 ms** |
| 2 | 105 ms | 78 ms | 10 ms | **193 ms** |
| 3 | 109 ms | 41 ms | 10 ms | **159 ms** |

The stale-stream close costs about 105 ms on turns 2+. The total window in
which command audio would be lost is 90–193 ms — smaller than the ~279 ms
wake→capture handoff already being paid. No arbitrary delay was added, and none
is needed at this size.

`||PaMacCore (AUHAL)|| Error on line 2523: err='-50'` appears on stream
open/close. Recorded because it is real output, not hidden: streams opened and
frames flowed on every turn regardless.

## Regression after the fix

| | |
|---|---|
| routes | 100/100 · dangerous 10/10 · current-fact leak 0/31 · fastpath 51 % |
| greetings | 61/61 |
| general regression | 26/26 |
| LOCAL_FAST quality | 24 runs, 0 defects, median 3.78 s (max 5.69 s) |
| runtime | `ProcessType=Interactive`, PRI 97, whisper warm 1.46 s |
| residency | `ollama ps` → `UNTIL 59 minutes from now` |

## Snapshot and rollback

```
PRE_FIX_SNAPSHOT = bin/*.pre-2ndturn-20260830-014553
                   logs/local.jarvis.runtime.plist.pre-2ndturn-20260830-014553
PRE_FIX_SHA256   = f266966f0277cdb79b7ceb953531d199cd47e0ae7cf950e5a6fe94497bebd8cd  jarvis_runtime.py
                   59d5ec3df0be2b8802016e500478396d3816441298790c7d7ed7701976b90741  jarvis_router.py
                   f6e3357c81fd31bb17169507a464a9d89d10dd638ee0628807f7eac2f3788966  jarvis-dispatch
                   b89bcc3c6a5a1a671d6a912f3c3cb3aa9323f19756bb3cc399b46e6fa19af539  jarvis_local_fast.py
                   e5978a2d4af62962cbd6b7fec8d50457b86188285310e5d42b2b3dcf21212a93  jarvis_profiles.py
                   0a921de5ae30752972da0c41d0b1740e284732f1f592d89c5471ace0b43bf879  local.jarvis.runtime.plist
```

```sh
cd ~/AI-Lab/hermes-jarvis/bin
cp jarvis_runtime.py.pre-2ndturn-20260830-014553 jarvis_runtime.py
launchctl bootout gui/$(id -u)/local.jarvis.runtime
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.jarvis.runtime.plist
```

Only `jarvis_runtime.py` changed this phase; the other snapshots are taken so
the whole set is restorable together. No config, plist, upstream source, model
or Ollama setting was changed.
