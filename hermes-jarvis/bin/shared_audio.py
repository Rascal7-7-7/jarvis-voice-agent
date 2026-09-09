"""One physical input stream, shared by the wake detector and the recorder.

WHAT THIS REMOVES. Until now every turn ran this sequence:

    wake listener closes its InputStream
    AudioRecorder opens a new InputStream
    Pa_StartStream                        <-- deadlocked, 4 times in production

The stack was always the same: the recorder's thread blocked in
``HALB_IOThread::StartAndWaitForState`` waiting on a guard mutex held by the
process's own ``com.apple.audio.IOThread.client``, itself parked in
``mach_msg2_trap``. A fresh process opened the same device in ~85 ms while the
wedged one stayed stuck for minutes, and ``coreaudiod`` was healthy throughout,
so the damage was confined to this process's HAL client state -- unrecoverable
from inside it. Hangs 3 and 4 happened on a recorder's very first stream, so
close/reopen was not required; starting a stream at all was.

HOW. ``AudioRecorder`` already keeps ONE stream alive for its whole life --
upstream's own ``_ensure_stream`` docstring says that is deliberately "to avoid
the CoreAudio bug where closing and re-opening an InputStream hangs
indefinitely on macOS". The missing piece was that the wake listener still
opened and closed the same device around every turn, which both restarted the
CoreAudio dance and left the recorder's stream stopped (the second-turn
zero-frames bug). So:

  * the recorder owns the single stream, opened and started once at startup;
  * the wake detector runs in upstream's ``external_audio`` mode and opens
    nothing -- it is fed 16 kHz frames through ``wake_word.feed_audio``;
  * a turn changes only which consumer receives frames. No stream operation.

Ownership is exclusive by construction rather than by agreement: the recorder's
callback feeds the wake detector ONLY while ``_recording`` is false, so during a
turn the wake engine cannot see a frame even in principle.

The one upstream change this needs is a tap point for those idle frames --
``AudioRecorder.set_idle_frame_listener``. Everything about capture (silence
threshold, dip tolerance, minimum speech duration, max wait, the hard recording
cap, frame accounting) is untouched upstream code, driven exactly as before.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger("jarvis")

# Frames the wake engine wants: 1280 samples at 16 kHz (80 ms). The physical
# stream runs at the device rate (48 kHz here), so each idle chunk is resampled
# with wake_word's own resampler -- the same 48k->16k step the wake listener
# performed internally when it owned a stream. Nothing new is introduced.
_ENGINE_RATE = 16000
# openWakeWord scores one frame of this many 16 kHz samples (~80 ms) at a time.
_ENGINE_FRAME = 1280


class SharedAudioInput:
    """Owns the single physical stream and routes its frames.

    The physical stream belongs to the ``AudioRecorder``; this class owns the
    policy around it. Nothing else may open, start, stop or close a stream.
    """

    def __init__(self, vm, ww, recorder, owner):
        self._vm = vm
        self._ww = ww
        self._rec = recorder
        self._owner = owner

        self._lock = threading.Lock()
        self._stream_obj = None          # identity of the live stream object
        # Resampled 16 kHz samples waiting to make up whole engine frames.
        # REQUIRED, not an optimisation: wake_word.feed() zero-PADS anything
        # shorter than frame_length, so handing it a partial frame does not
        # delay that audio -- it destroys it, splicing silence into the middle
        # of the phrase. The recorder's blocksize is PortAudio's default and is
        # typically far smaller than 1280 samples after downsampling, so
        # without this every frame the detector saw was mostly zeros and no
        # phrase could ever score.
        self._pending = None
        # Wake feed gate. When closed, the physical callback, the copy, the
        # aggregation and the resample all still run -- only the handoff to the
        # detector is skipped. The stream is never stopped, so PA_OPEN_COUNT and
        # PA_START_COUNT cannot move.
        #
        # WHAT THIS IS AND IS NOT. Upstream already drains the detector's queue
        # at the top of every arm ("Drain any stale frames from a previous arm"
        # in wake_word._run), and pause()/resume() go through start(), so a
        # backlog accumulated during a turn is discarded before any inference
        # runs. This gate is therefore NOT the fix for a live self-echo bug --
        # it is defence in depth for a property currently guaranteed by another
        # module's internals, plus the counter that makes "gated during
        # SPEAKING" an observable fact instead of an inference from two
        # codebases agreeing.
        self._wake_feed = True
        self.wake_feed_gated_frames = 0  # engine frames withheld while gated
        self.gate_engagements = 0
        self.pa_open_count = 0           # stream constructions observed
        self.pa_start_count = 0          # each construction is start()ed once
        self.idle_frames = 0
        self.idle_chunks = 0
        self.engine_frames_fed = 0
        self.feed_errors = 0
        self.replacements = 0

    # ---- wake feed gate ----------------------------------------------------

    @property
    def wake_feed_enabled(self) -> bool:
        return self._wake_feed

    def gate_wake_feed(self) -> None:
        """Stop handing frames to the detector. Does NOT touch the stream."""
        with self._lock:
            if self._wake_feed:
                self.gate_engagements += 1
            self._wake_feed = False

    def ungate_wake_feed(self) -> None:
        """Resume handing frames to the detector.

        ``_pending`` is dropped rather than carried across: it holds up to one
        capture frame of audio from while the gate was closed, and splicing that
        onto the first frame after re-arming would put the tail of JARVIS's own
        reply at the start of what the detector scores next.
        """
        with self._lock:
            self._wake_feed = True
            self._pending = None

    # ---- stream lifecycle (startup and recovery ONLY) ----------------------

    def open_once(self) -> bool:
        """Open and start the one physical stream. Called once, at startup.

        Deliberately drives ``AudioRecorder``'s own stream creation rather than
        building a stream here, so device resolution, sample-rate selection and
        the device-identity fingerprint all stay upstream's.
        """
        sd, _np = self._vm._import_audio()
        # Device-following off: a healthy stream is kept even if the OS default
        # changes. Replacing a working stream would mean another open/start
        # against CoreAudio, which is precisely the call that deadlocks -- so
        # replacement is reserved for a stream that has actually failed
        # (ensure_healthy_for_turn). The cost is that switching the preferred
        # microphone while both devices are present is no longer picked up at
        # the next turn; it takes a runtime restart.
        self._rec.follow_default_device = False
        self._rec._sample_rate = self._vm._default_input_samplerate(sd)
        # Private, but this file already lives at that level of intimacy with
        # AudioRecorder, and the alternative -- start() then stop() -- would
        # write a spurious WAV.
        self._rec._ensure_stream()
        self._note_stream()
        active = self.stream_active
        logger.info("shared audio: stream open=%s active=%s device=%s rate=%d "
                    "PA_OPEN_COUNT=%d PA_START_COUNT=%d",
                    self._rec._stream is not None, active,
                    self._vm._identity_label(self._rec._stream_identity),
                    self._rec._sample_rate,
                    self.pa_open_count, self.pa_start_count)
        return active

    def _note_stream(self) -> None:
        """Count stream constructions by watching the object change."""
        st = getattr(self._rec, "_stream", None)
        if st is not None and st is not self._stream_obj:
            self._stream_obj = st
            self.pa_open_count += 1
            self.pa_start_count += 1

    @property
    def stream_active(self) -> bool:
        st = getattr(self._rec, "_stream", None)
        return bool(st is not None and getattr(st, "active", False))

    # ---- frame routing -----------------------------------------------------

    def attach_wake(self) -> None:
        """Route idle frames to the wake detector."""
        self._rec.set_idle_frame_listener(self._on_idle_frame)

    def detach(self) -> None:
        self._rec.set_idle_frame_listener(None)

    def _on_idle_frame(self, indata) -> None:
        """Audio callback thread. Must stay cheap.

        openWakeWord inference is NOT run here: ``feed_audio`` drops the frame
        into upstream's bounded queue (maxsize 64, drop-oldest on overflow) and
        the detector's own thread does the inference. So the work on this thread
        is one resample and one enqueue, and a slow model can never starve the
        stream.
        """
        try:
            _sd, np = self._vm._import_audio()
            # COPY. `indata` is PortAudio's buffer and is reused as soon as this
            # callback returns, so anything retained past it -- the _pending
            # remainder, or a block handed to the detector's queue for another
            # thread to read -- must own its memory. Upstream's own recorder
            # callback copies for exactly this reason. Kept as a view, the audio
            # the engine scored was whatever had since been written over it:
            # max score 0.0009 on a phrase that scores 0.4944 from a file.
            frame = indata[:, 0] if getattr(indata, "ndim", 1) == 2 else indata
            frame = np.array(frame, dtype=np.int16, copy=True)
            n = int(len(frame))
            if n == 0:
                return
            self.idle_chunks += 1
            self.idle_frames += n

            rate = int(self._rec._sample_rate or _ENGINE_RATE)
            # Buffer the RAW capture-rate audio and resample a whole capture
            # frame at a time -- 3840 -> 1280 at 48 kHz, an exact 3:1. This is
            # the granularity wake_word itself used when it owned the stream
            # (blocksize=capture_frame_length, one resample per block), and
            # matching it matters: resampling each 512-sample callback chunk
            # independently means 512 * 16000/48000 = 170.67 output samples,
            # rounded, with the averaging bins restarting at every chunk
            # boundary ~94 times a second. Measured effect on a phrase that
            # scores 0.4944 from a file: max score 0.0104, nothing detected.
            capture_frame = max(1, int(round(
                _ENGINE_FRAME * rate / _ENGINE_RATE)))

            pending = frame if self._pending is None else np.concatenate(
                (self._pending, frame))
            usable = (len(pending) // capture_frame) * capture_frame
            for i in range(0, usable, capture_frame):
                block = pending[i:i + capture_frame]
                if rate != _ENGINE_RATE:
                    block = self._ww._resample_audio_frame(np, block,
                                                           _ENGINE_FRAME)
                else:
                    block = np.asarray(block, dtype=np.int16)
                # THE GATE. Everything above still ran -- the copy, the
                # aggregation, the resample -- so the only thing withheld is
                # the handoff. Read without the lock on purpose: this is the
                # audio callback thread and a bool read is atomic, whereas
                # taking a lock here would put the gate's own contention on the
                # hot path the shared stream exists to keep clear.
                if not self._wake_feed:
                    self.wake_feed_gated_frames += 1
                    continue
                self._ww.feed_audio(owner=self._owner, pcm_int16=block)
                self.engine_frames_fed += 1
            self._pending = pending[usable:] if len(pending) > usable else None
        except Exception:
            self.feed_errors += 1
            if self.feed_errors <= 3:
                logger.exception("shared audio: feeding the wake detector failed")

    # ---- failure handling --------------------------------------------------

    def ensure_healthy_for_turn(self, turn: int) -> bool:
        """Check the stream before a turn. Replaces it only if it is dead.

        A normal turn does nothing here. Replacement is the RECOVERING path and
        is the one place `Pa_StartStream` can still be reached, so it is taken
        only when the stream is genuinely not running -- never on a schedule,
        and never merely because the OS default device changed.

        If a replacement does deadlock, this call will not return. That is
        deliberate: the external watchdog restarts the process after 45 s in
        LISTENING, and abandoning a thread blocked inside a CoreAudio guard
        mutex would leak it and make the next open worse.
        """
        if self.stream_active:
            return True

        logger.warning("shared audio: stream is not active before TURN=%d — "
                       "attempting one controlled replacement", turn)
        self.replacements += 1
        try:
            self._rec._close_stream_with_timeout()
        except Exception:
            logger.exception("shared audio: closing the dead stream failed")
        try:
            sd, _np = self._vm._import_audio()
            self._rec._sample_rate = self._vm._default_input_samplerate(sd)
            self._rec._ensure_stream()
            self._note_stream()
        except Exception:
            logger.exception("shared audio: stream replacement failed")
            return False
        ok = self.stream_active
        logger.warning("shared audio: replacement %s (PA_OPEN_COUNT=%d)",
                       "succeeded" if ok else "FAILED", self.pa_open_count)
        return ok

    def counters(self) -> dict:
        return {
            "PA_OPEN_COUNT": self.pa_open_count,
            "PA_START_COUNT": self.pa_start_count,
            "stream_active": self.stream_active,
            "idle_chunks": self.idle_chunks,
            "idle_frames": self.idle_frames,
            "engine_frames_fed": self.engine_frames_fed,
            "feed_errors": self.feed_errors,
            "replacements": self.replacements,
            "wake_feed_enabled": self._wake_feed,
            "wake_feed_gated_frames": self.wake_feed_gated_frames,
            "gate_engagements": self.gate_engagements,
        }
