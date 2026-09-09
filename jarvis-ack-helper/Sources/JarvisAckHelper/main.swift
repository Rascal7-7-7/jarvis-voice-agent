// Persistent ACK playback helper — PROTOTYPE. Not integrated with anything.
//
// Why this exists: `afplay` costs ~882 ms per invocation on this machine,
// measured, and that cost is per-invocation rather than per-sample — 50 ms of
// silence and 300 ms of silence both take ~882 ms more than their duration.
// Keeping the output device warm does not help (tested: 8.5 ms difference), so
// the cost is the work afplay repeats each time: spawn, dyld, open, decode,
// build an audio queue. A process that has already done all of that should not
// pay any of it.
//
// Scope is deliberately tiny. It plays ONE file, whose path is fixed at compile
// time, in response to one of three literal commands on stdin. It has no
// network, opens no microphone, executes nothing, and accepts no path, URL or
// argument from its input. There is no command that takes a parameter.

import AVFoundation
import Foundation

// The asset is a constant. Nothing on stdin can change what gets played.
let assetPath = ("~/AI-Lab/jarvis-voice-final-benchmark/outputs/wake-ack/"
                 + "ACK_A_RUNTIME_P060.wav") as NSString
let assetURL = URL(fileURLWithPath: assetPath.expandingTildeInPath)

/// Monotonic milliseconds. `Date` would be wall-clock and could step under NTP
/// mid-measurement; this is measuring latency, so it has to be monotonic.
@inline(__always) func nowMs() -> Double {
    Double(DispatchTime.now().uptimeNanoseconds) / 1_000_000.0
}

/// Where replies go. stdout by default; a socket connection swaps it for the
/// duration of the command it is serving, so the same command handlers work on
/// either channel without knowing which one they are on.
nonisolated(unsafe) var emitSink: ((String) -> Void)? = nil

func emit(_ s: String) {
    if let sink = emitSink { sink(s); return }
    print(s)
    fflush(stdout)
}

final class AckPlayer: NSObject, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private var playing = false
    var isPlaying: Bool { playing }
    private var triggerMs: Double = 0
    private var startedMs: Double = 0
    private var seq = 0

    /// Load and decode once, at startup, so PLAY does no file I/O.
    func preload() -> Bool {
        do {
            let p = try AVAudioPlayer(contentsOf: assetURL)
            p.delegate = self
            // Allocates the audio hardware and buffers now rather than on the
            // first play — the whole point of the helper.
            let prepared = p.prepareToPlay()
            player = p
            emit("READY duration_ms=\(Int(p.duration * 1000)) "
                 + "prepared=\(prepared) format=\(p.format.sampleRate)Hz "
                 + "channels=\(p.format.channelCount)")
            return prepared
        } catch {
            emit("ERROR preload_failed \(error.localizedDescription)")
            return false
        }
    }

    /// Phase 1 policy: ignore a PLAY that arrives while one is running.
    /// Not a queue — queued acknowledgements would stack up behind a user who
    /// says the wake word twice, and "はい はい" is worse than one dropped ACK.
    func play(triggerReceivedMs: Double) {
        guard let p = player else {
            emit("PLAY_REJECTED reason=not_loaded"); return
        }
        if playing {
            emit("PLAY_IGNORED reason=already_playing"); return
        }
        seq += 1
        triggerMs = triggerReceivedMs

        let callMs = nowMs()
        p.currentTime = 0
        let ok = p.play()
        let returnedMs = nowMs()

        if !ok {
            emit("PLAY_FAILED seq=\(seq)"); return
        }
        playing = true
        startedMs = returnedMs
        // `play()` returning is the closest observable proxy for first audio
        // available through AVAudioPlayer; the remaining latency is the output
        // device's own IO buffer, which this API does not expose. Reported as
        // such rather than as a true first-sample timestamp.
        emit(String(format: "STARTED seq=%d trigger_to_call=%.2f "
                    + "call_to_return=%.2f trigger_to_return=%.2f",
                    seq, callMs - triggerMs, returnedMs - callMs,
                    returnedMs - triggerMs))
        // Re-prepare for the next one while nothing is waiting on it.
        _ = p.prepareToPlay()
    }

    func audioPlayerDidFinishPlaying(_ p: AVAudioPlayer, successfully flag: Bool) {
        let endMs = nowMs()
        playing = false
        emit(String(format: "ENDED seq=%d ok=%@ trigger_to_end=%.2f "
                    + "started_to_end=%.2f",
                    seq, flag ? "true" : "false",
                    endMs - triggerMs, endMs - startedMs))
        _ = p.prepareToPlay()
    }

    func audioPlayerDecodeErrorDidOccur(_ p: AVAudioPlayer, error: Error?) {
        playing = false
        emit("ERROR decode \(error?.localizedDescription ?? "unknown")")
    }
}

/// The complete command set. Note that no case reads a parameter: the verb IS
/// the whole message, which is what makes "input cannot choose the asset" a
/// structural property rather than a validation rule.
func handle(_ raw: String, receivedMs: Double) {
    switch raw.trimmingCharacters(in: .whitespaces).uppercased() {
    case "PLAY":
        DispatchQueue.main.async { ack.play(triggerReceivedMs: receivedMs) }
    case "PING":
        emit(String(format: "PONG at=%.2f", receivedMs))
    case "STATUS":
        emit("STATUS \(ack.isPlaying ? "PLAYING" : "IDLE")")
    case "QUIT":
        emit("BYE")
        exit(0)
    case "":
        return
    default:
        emit("UNKNOWN_COMMAND")
    }
}

// nonisolated: `handle` runs on the stdin queue and on socket connections.
// All MUTATION of AckPlayer happens on the main queue (play() is dispatched
// there, and the AVAudioPlayer delegate calls back there), so the only
// cross-thread access is STATUS reading `isPlaying` — a stale answer there is
// harmless, and the alternative would be a lock on the hot path for a
// diagnostic command.
nonisolated(unsafe) let ack = AckPlayer()
emit("BOOT asset=\(assetURL.lastPathComponent)")
guard FileManager.default.fileExists(atPath: assetURL.path) else {
    emit("ERROR asset_missing \(assetURL.path)")
    exit(2)
}
guard ack.preload() else { exit(3) }

// Optional AF_UNIX control channel, chosen with --socket. The path selects
// where to listen and nothing else; the asset stays a compile-time constant.
var controlSocket: ControlSocket? = nil
if let i = CommandLine.arguments.firstIndex(of: "--socket"),
   i + 1 < CommandLine.arguments.count {
    let sock = ControlSocket(path: CommandLine.arguments[i + 1])
    do {
        try sock.listen { line, reply in
            // Playback events belong to whoever asked for them, so the sink is
            // pointed at this connection while the command runs.
            emitSink = reply
            handle(line, receivedMs: nowMs())
        }
        controlSocket = sock
        emit("LISTENING socket=\(CommandLine.arguments[i + 1])")
    } catch {
        emit("ERROR socket \(error)")
        exit(4)
    }
}
_ = controlSocket

// stdin, read on a background queue so playback callbacks keep the main
// run loop. Only three literal commands are recognised; anything else is
// reported and discarded. There is no branch that takes a parameter.
let stdinQueue = DispatchQueue(label: "ack.stdin")
stdinQueue.async {
    while let line = readLine(strippingNewline: true) {
        handle(line, receivedMs: nowMs())
    }
    // stdin closed. That ends the process ONLY when stdin is the control
    // channel. Under launchd stdin is usually /dev/null and closes at once, so
    // exiting here would kill a socket-driven helper before it served anyone.
    if controlSocket == nil {
        emit("EOF")
        exit(0)
    }
    emit("STDIN_CLOSED socket_channel_active")
}

RunLoop.main.run()
