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

from collections import deque

import audio_devices
from statistics import median

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


def _set_input_device(sd, index: int) -> None:
    """``sd.default.device`` の**入力スロットだけ**を設定する。

    初版は ``sd.default.device = (index, 既存の出力index)`` としていたが、
    デバイスの抜き差しで PortAudio の index は振り直されるため、
    「保存した出力 index」が別デバイスを指し得る。出力は触らないのが正しい。

    macOS では出力に sounddevice を使わない（TCC 回避。voice_mode の
    ``_sounddevice_output_allowed`` 参照）ので現状は無害だが、潜在バグなので直す。
    """
    sd.default.device[0] = int(index)


# --------------------------------------------------------- noise floor
#
# なぜ必要か（2026-09-09 実測）:
#   upstream の SILENCE_RMS_THRESHOLD は 200 固定。しかし外部マイク（ジャック）の
#   無発話時ノイズフロアは mean RMS ≈ 2,663 で、しきい値の 13 倍あった。
#   silence callback は「rms <= threshold が 3 秒連続」で発火するため、
#   この構成では原理的に一度も発火せず、capture が毎回 30 秒上限まで走っていた
#   （1 ターン +26 秒）。内蔵マイクは PEAK_RMS 1,446〜1,764 で発火していたので、
#   固定しきい値は「静かなデバイス」を暗黙の前提にしていたことになる。
#
# 測り方:
#   共有ストリームはターン間も idle フレームを見ている（_on_idle_frame）。
#   そこで各チャンクの RMS を観測しておけば、ノイズフロアは追加コストなしで得られる。
#   平均ではなく**中央値**を使う: idle 中に wake word 発話が混ざってもフロアが
#   引き上げられないため。
#
# しきい値の決め方:
#   threshold = clamp(floor * MULTIPLIER, MINIMUM, MAXIMUM)
#   - MINIMUM は upstream の 200。静かなデバイスでは挙動を変えない
#   - MAXIMUM は上げ過ぎて発話ごと無音扱いになるのを防ぐ安全弁
_FLOOR_WINDOW = 100
_FLOOR_MULTIPLIER = 2.5
_FLOOR_MIN_THRESHOLD = 200      # upstream SILENCE_RMS_THRESHOLD と一致させる
_FLOOR_MAX_THRESHOLD = 8000


class NoiseFloorTracker:
    """idle フレームの RMS からノイズフロアを推定し、無音しきい値を出す。

    audio callback スレッドから ``observe`` が呼ばれるので、1 回あたりの仕事は
    deque への append だけに留める。中央値の計算は ``floor`` 参照時（ターン前、
    1 ターン 1 回）にだけ行う。
    """

    def __init__(self, window: int = _FLOOR_WINDOW) -> None:
        self._samples: deque[int] = deque(maxlen=max(1, int(window)))

    def observe(self, rms) -> None:
        """1 チャンク分の RMS を記録する。異常値は黙って捨てる。"""
        try:
            value = int(rms)
        except (TypeError, ValueError):
            return
        if value < 0:
            return
        self._samples.append(value)

    @property
    def floor(self) -> int | None:
        if not self._samples:
            return None
        return int(median(self._samples))

    def threshold(self,
                  minimum: int = _FLOOR_MIN_THRESHOLD,
                  multiplier: float = _FLOOR_MULTIPLIER,
                  maximum: int = _FLOOR_MAX_THRESHOLD) -> int:
        floor = self.floor
        if floor is None:
            return minimum
        return max(minimum, min(maximum, int(floor * multiplier)))


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
        # ノイズフロア追跡。idle フレームから測るので追加の録音は不要。
        self._floor = NoiseFloorTracker()
        self._floor_stride = 4      # 4 チャンクに 1 回だけ測る（callback を軽く保つ）
        self._floor_tick = 0
        self.silence_threshold = _FLOOR_MIN_THRESHOLD
        # 案C: デバイス優先順位。bound は実際に開いた先、preferred は今選ぶべき先。
        self.bound_device: str | None = None
        self.preferred_device: str | None = None
        self.device_change_pending = False
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

    def select_input_device(self, sd) -> dict | None:
        """優先順位（外部 > 無線 > 内蔵）で入力デバイスを選び、開く先を誘導する。

        ``sd.default.device`` を**プロセスローカルに**設定する。upstream の
        ``sd.InputStream(...)`` は ``device=`` を渡していないので、これで開く先が
        決まる。ユーザーのシステム既定入力は変更しない。

        クラムシェル中は内蔵マイクを候補から外す（列挙されるが全ゼロを返すため）。
        候補が無ければ何もせず None を返し、OS 既定に委ねる。
        """
        clamshell = audio_devices.read_clamshell_state()
        chosen = audio_devices.select(audio_devices.enumerate_inputs(sd),
                                      clamshell_closed=clamshell)
        if chosen is None:
            logger.warning("shared audio: 選択可能な入力デバイスがありません "
                           "(clamshell_closed=%s)。OS 既定に委ねます", clamshell)
            self.preferred_device = None
            return None
        self.preferred_device = chosen["name"]
        try:
            # 入力スロットだけを触る。出力は macOS では afplay がシステム既定を
            # 使うので JARVIS の管轄外であり、index を保存すると抜き差しで
            # 別デバイスを指す危険がある。
            _set_input_device(sd, chosen["index"])
        except Exception:
            logger.exception("shared audio: sd.default.device の設定に失敗しました")
            return chosen
        logger.info("shared audio: input device = %r (tier %d, index %d, "
                    "clamshell_closed=%s)",
                    chosen["name"], chosen["tier"], chosen["index"], clamshell)
        return chosen

    def refresh_preferred_device(self, sd) -> bool:
        """今選ぶべきデバイスを再評価する。束縛先と食い違えば True。

        ここでストリームを差し替えることはしない。差し替えは CoreAudio の
        close→reopen デッドロックを踏む唯一の経路であり、稼働中の健全な
        ストリームに対して行ってはいけない（open_once のコメント参照）。
        切り替えは runtime の再起動で行う。
        """
        clamshell = audio_devices.read_clamshell_state()
        chosen = audio_devices.select(audio_devices.enumerate_inputs(sd),
                                      clamshell_closed=clamshell)
        self.preferred_device = chosen["name"] if chosen else None
        pending = bool(self.preferred_device and self.bound_device
                       and self.preferred_device != self.bound_device)
        if pending and not self.device_change_pending:
            logger.warning(
                "shared audio: 優先デバイスが変わりました %r -> %r。"
                "切り替えには runtime の再起動が必要です",
                self.bound_device, self.preferred_device)
        self.device_change_pending = pending
        return pending

    def open_once(self) -> bool:
        """Open and start the one physical stream. Called once, at startup.

        Deliberately drives ``AudioRecorder``'s own stream creation rather than
        building a stream here, so device resolution, sample-rate selection and
        the device-identity fingerprint all stay upstream's.
        """
        sd, _np = self._vm._import_audio()
        # 案C: 開く前に優先順位でデバイスを決める。upstream は「OS 既定」を開くが、
        # macOS の既定は「最後に接続したものが勝つ」なので固定優先順位にならない。
        self.select_input_device(sd)
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
        self.bound_device = self._vm._identity_label(
            self._rec._stream_identity).split("@")[0].strip("'\"")
        active = self.stream_active
        logger.info("shared audio: stream open=%s active=%s device=%s rate=%d "
                    "PA_OPEN_COUNT=%d PA_START_COUNT=%d",
                    self._rec._stream is not None, active,
                    self._vm._identity_label(self._rec._stream_identity),
                    self._rec._sample_rate,
                    self.pa_open_count, self.pa_start_count)
        return active

    def _note_stream(self) -> None:
        """Count stream constructions by watching the object change.

        置換の計数もここで行う（2026-09-09 の修正）。置換経路は3つあり、
        以前は 2 だけが自分で ``replacements`` を加算していたため、
        ログに ``controlled replacement 1/3`` が出ていてもカウンタは 0 のままだった。

          1. ``open_once``                    初回オープン
          2. ``ensure_healthy_for_turn``      死んだストリームの復旧
          3. ``jarvis_runtime`` の無音 watchdog  ``open_once`` を呼び直す

        オブジェクトの入れ替わりを見ているのはこのメソッドだけなので、
        どの経路から来ても必ず通る。ここに集約すれば数え漏れが起きない。
        """
        st = getattr(self._rec, "_stream", None)
        if st is not None and st is not self._stream_obj:
            first_open = self._stream_obj is None
            self._stream_obj = st
            self.pa_open_count += 1
            self.pa_start_count += 1
            if not first_open:
                self.replacements += 1

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

            # ノイズフロアの観測。callback を軽く保つため間引く。
            # ここは「発話していない区間」なので、得られる RMS はほぼ環境ノイズ。
            self._floor_tick += 1
            if self._floor_tick % self._floor_stride == 0:
                self._floor.observe(
                    np.sqrt(np.mean(frame.astype(np.float32) ** 2)))

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
        # 適応的な無音しきい値をターンごとに適用する。
        # upstream の固定値 200 は静かなデバイスを前提にしており、ノイズフロアが
        # それを上回るデバイス（実測: 外部マイクで RMS 2,663）では silence callback が
        # 一度も発火せず capture が 30 秒上限まで走る。
        # _silence_threshold は AudioRecorder のインスタンス属性なので、
        # upstream の voice_mode.py を変更せずに外から設定できる。
        self.apply_silence_threshold()
        try:
            sd, _np = self._vm._import_audio()
            self.refresh_preferred_device(sd)
        except Exception:
            logger.debug("shared audio: 優先デバイスの再評価に失敗しました")

        if self.stream_active:
            return True

        logger.warning("shared audio: stream is not active before TURN=%d — "
                       "attempting one controlled replacement", turn)
        # replacements は _note_stream() が数える（経路によらず一箇所に集約）
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

    def apply_silence_threshold(self) -> int:
        """測ったノイズフロアから無音しきい値を決め、recorder へ設定する。

        戻り値は実際に設定した値。recorder が属性を持たない場合（テストの
        フェイク等）は設定を諦め、値だけ返す。
        """
        threshold = self._floor.threshold()
        self.silence_threshold = threshold
        floor = self._floor.floor
        try:
            previous = getattr(self._rec, "_silence_threshold", None)
            self._rec._silence_threshold = threshold
        except Exception:
            logger.debug("shared audio: could not set _silence_threshold")
            return threshold
        if previous != threshold:
            logger.info("shared audio: silence threshold %s -> %d "
                        "(noise floor %s)", previous, threshold, floor)
        return threshold

    def counters(self) -> dict:
        return {
            "PA_OPEN_COUNT": self.pa_open_count,
            "PA_START_COUNT": self.pa_start_count,
            "stream_active": self.stream_active,
            "idle_chunks": self.idle_chunks,
            "idle_frames": self.idle_frames,
            "noise_floor": self._floor.floor,
            "silence_threshold": self.silence_threshold,
            "bound_device": self.bound_device,
            "preferred_device": self.preferred_device,
            "device_change_pending": self.device_change_pending,
            "engine_frames_fed": self.engine_frames_fed,
            "feed_errors": self.feed_errors,
            "replacements": self.replacements,
            "wake_feed_enabled": self._wake_feed,
            "wake_feed_gated_frames": self.wake_feed_gated_frames,
            "gate_engagements": self.gate_engagements,
        }
