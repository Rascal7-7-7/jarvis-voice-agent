// DEBUG-ONLY. The entire file is guarded so the type does not exist in a
// release build at all.
//
// Every use site was already behind `#if DEBUG`, so this was unreachable in a
// release binary -- but the type metadata and this file's path still landed in
// it, which `strings` finds. A shipped artifact should not carry the machinery
// for faking system state, reachable or not: the guarantee is easier to trust
// when it is "not present" than when it is "present but unused".
#if DEBUG
import Foundation
import Observation

/// The only state source in Phase 1A.
///
/// Nothing here reads the production file. The runtime is not connected, and
/// this provider cannot reach it even by accident: it holds no path, opens no
/// file, and starts no process.
@MainActor
@Observable
public final class MockStateProvider: StateProviding {
    public private(set) var displayed: JarvisState = .placeholder
    public private(set) var liveness: Liveness = .unknown
    public private(set) var status: ProviderStatus = .ok
    public private(set) var isRunningSequence: Bool = false

    private var presenter = StatePresenter()
    private var flushTask: Task<Void, Never>?
    private var sequenceTask: Task<Void, Never>?

    public init() {
        set(.idle, detail: "waiting for wake word")
    }

    // MARK: - manual control (menu bar, Phase 1A only)

    public func set(_ phase: JarvisPhase, detail: String = "") {
        let raw = phase == .unknown ? "SOME_FUTURE_STATE" : phase.rawValue
        push(JarvisState(phase: phase, rawState: raw, since: Date(),
                         detail: detail, pid: 4242))
    }

    /// Feed a raw JSON payload — the path a hostile or malformed file would
    /// take once production is connected. Exercised now so the parser is proven
    /// before it is ever pointed at a real file.
    public func injectRaw(_ data: Data) {
        if let s = JarvisState.decode(from: data) {
            push(s)
        } else {
            // Undecodable input must not blank the HUD. The last good state
            // stays, and this is reported as a HUD-side fault -- NOT as the
            // runtime's own ERROR state.
            status = .dataError("unparseable state payload")
        }
    }

    private func push(_ incoming: JarvisState) {
        status = .ok
        displayed = presenter.accept(incoming)
        liveness = Liveness.evaluate(
            pid: incoming.pid,
            age: incoming.since.map { Date().timeIntervalSince($0) },
            processExists: { _ in true })   // mock: always "running"
        scheduleFlushIfNeeded()
    }

    /// One-shot timer, only while a state is being held back. No timer runs at
    /// idle -- that is what keeps idle CPU at zero.
    private func scheduleFlushIfNeeded() {
        flushTask?.cancel()
        guard let delay = presenter.nextFlushDelay() else { return }
        flushTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard !Task.isCancelled, let self else { return }
            if let released = self.presenter.flush() {
                self.displayed = released
            }
            self.scheduleFlushIfNeeded()
        }
    }

    // MARK: - demo sequences

    public struct Step: Sendable {
        let phase: JarvisPhase
        let detail: String
        let hold: Duration
        /// Fields the backend does NOT publish. Carried per step so a sequence
        /// can show a transcript appearing after STT and a reply after the
        /// backend answers -- which is the ordering Phase 2B would have to
        /// reproduce for real if any of this is ever adopted.
        let context: FutureHUDContext?

        public init(_ phase: JarvisPhase, _ detail: String = "", _ ms: Int,
                    context: FutureHUDContext? = nil) {
            self.phase = phase
            self.detail = detail
            self.hold = .milliseconds(ms)
            self.context = context
        }
    }

    /// The mock-only context currently on show. Always nil outside a sequence,
    /// and this whole file is `#if DEBUG`, so a release build has neither the
    /// data nor the type.
    public private(set) var futureContext: FutureHUDContext?

    /// Sample content for the sequences below. Deliberately obvious as sample
    /// data rather than plausible-looking user speech.
    private enum Sample {
        static let heard = "Pythonとは何？"
        static let reply = "Pythonは読みやすさを重視した高水準の汎用プログラミング"
            + "言語です。動的型付けとガベージコレクションを備え、手続き型・"
            + "オブジェクト指向・関数型のいずれのスタイルでも記述できます。"
        static let latency = LatencyBreakdown(stt: 0.8, router: 2.5,
                                              backend: 3.0, tts: 0.6)
    }

    /// SEQUENCE A — a local fast turn, answered without delegation.
    public static let sequenceLocalFast: [Step] = [
        .init(.idle, "waiting for wake word", 900),
        .init(.listening, "capturing utterance", 1500),
        .init(.thinking, "transcribing", 900,
              context: .init(heardText: Sample.heard)),
        .init(.routing, "LOCAL_FAST via llm", 500,
              context: .init(heardText: Sample.heard)),
        .init(.localFast, "working", 1400,
              context: .init(heardText: Sample.heard,
                             toolSummary: "LOCAL FAST · resident model")),
        .init(.speaking, "Pythonは高水準で読みやすいプログラミング言語です。", 2400,
              context: .init(heardText: Sample.heard,
                             responsePreview: Sample.reply,
                             toolSummary: "LOCAL FAST · resident model",
                             latency: LatencyBreakdown(stt: 0.8, router: 2.5,
                                                       backend: 0.9, tts: 0.4))),
        .init(.idle, "waiting for wake word", 1200),
    ]

    /// SEQUENCE B — delegated to Codex.
    public static let sequenceCodex: [Step] = [
        .init(.idle, "waiting for wake word", 900),
        .init(.listening, "capturing utterance", 1500),
        .init(.thinking, "transcribing", 1000,
              context: .init(heardText: "このプロジェクトの構成を調べて")),
        .init(.routing, "CODEX via llm", 500,
              context: .init(heardText: "このプロジェクトの構成を調べて")),
        .init(.codex, "working", 2600,
              context: .init(heardText: "このプロジェクトの構成を調べて",
                             toolSummary: "CODEX · 3 files inspected")),
        .init(.speaking, "解析が完了しました。重要な点を三つ報告します。", 2600,
              context: .init(heardText: "このプロジェクトの構成を調べて",
                             responsePreview: "解析が完了しました。重要な点を三つ"
                                + "報告します。第一に、依存関係は最小限に保たれて"
                                + "います。第二に、テストカバレッジは良好です。",
                             toolSummary: "CODEX · 3 files inspected",
                             latency: Sample.latency)),
        .init(.idle, "waiting for wake word", 1200),
    ]

    /// SEQUENCE C — delegated to Claude.
    public static let sequenceClaude: [Step] = [
        .init(.idle, "waiting for wake word", 900),
        .init(.listening, "capturing utterance", 1500),
        .init(.thinking, "transcribing", 1000,
              context: .init(heardText: "この設計の問題点を指摘して")),
        .init(.routing, "CLAUDE via llm", 500,
              context: .init(heardText: "この設計の問題点を指摘して")),
        .init(.claude, "working", 3000,
              context: .init(heardText: "この設計の問題点を指摘して",
                             toolSummary: "CLAUDE · waiting for response")),
        .init(.speaking, "三点、確認したい箇所があります。", 2400,
              context: .init(heardText: "この設計の問題点を指摘して",
                             responsePreview: "三点、確認したい箇所があります。",
                             toolSummary: "CLAUDE · waiting for response",
                             latency: LatencyBreakdown(stt: 0.9, router: 2.6,
                                                       backend: 5.2, tts: 0.6))),
        .init(.idle, "waiting for wake word", 1200),
    ]

    /// SEQUENCE D — the turn fails.
    public static let sequenceError: [Step] = [
        .init(.idle, "waiting for wake word", 900),
        .init(.listening, "capturing utterance", 1400),
        .init(.thinking, "transcribing", 1000),
        .init(.error, "mic silent; restarting listener", 3000),
        .init(.idle, "listening on 外部マイク", 1200),
    ]

    /// SEQUENCE E — a protected action is waiting. Read-only in the HUD.
    public static let sequenceConfirmation: [Step] = [
        .init(.idle, "waiting for wake word", 800),
        .init(.listening, "capturing utterance", 1200),
        .init(.routing, "LOCAL_TOOL via llm", 600),
        .init(.confirmationRequired, "DESTRUCTIVE", 3200,
              context: .init(heardText: "古いログを全部消して")),
        .init(.idle, "waiting for wake word", 1000),
    ]

    /// A normal delegated turn.
    public static let happyPath: [Step] = [
        .init(.idle, "waiting for wake word", 900),
        .init(.listening, "capturing utterance", 1600),
        .init(.thinking, "transcribing", 1200),
        .init(.routing, "CODEX via llm", 400),
        .init(.codex, "working", 2200),
        .init(.speaking, "解析が完了しました。重要な点を三つ報告します。", 2600),
        .init(.idle, "waiting for wake word", 900),
    ]

    /// A turn that fails, plus the gate refusing an utterance.
    public static let errorPath: [Step] = [
        .init(.idle, "waiting for wake word", 800),
        .init(.listening, "capturing utterance", 1400),
        .init(.thinking, "transcribing", 1000),
        .init(.error, "mic silent; restarting listener", 2200),
        .init(.idle, "listening on 外部マイク", 800),
        .init(.listening, "capturing utterance", 1200),
        .init(.confirmationRequired, "DESTRUCTIVE", 2600),
        .init(.idle, "waiting for wake word", 800),
    ]

    /// Deliberately faster than the dwell window, to prove the presenter stops
    /// the panel strobing without hiding urgent states.
    public static let flickerPath: [Step] = [
        .init(.thinking, "transcribing", 60),
        .init(.routing, "LOCAL_FAST via llm", 60),
        .init(.localFast, "working", 60),
        .init(.routing, "WEB via fast_path", 60),
        .init(.web, "working", 60),
        .init(.speaking, "はい、承知しました。", 1500),
        .init(.idle, "waiting for wake word", 800),
    ]

    public func runSequence(_ steps: [Step], loop: Bool = false) {
        stopSequence()
        isRunningSequence = true
        sequenceTask = Task { [weak self] in
            repeat {
                for step in steps {
                    if Task.isCancelled { break }
                    self?.futureContext = step.context
                    self?.set(step.phase, detail: step.detail)
                    try? await Task.sleep(for: step.hold)
                }
            } while loop && !Task.isCancelled
            self?.isRunningSequence = false
        }
    }

    /// Replay a V2-shaped turn: explicit route and latency filling in stage by
    /// stage, exactly as the runtime publishes them.
    ///
    /// Goes through `injectRaw`, i.e. the real decoder, so this exercises the
    /// production path rather than constructing states directly. The production
    /// state file is never written.
    public func runV2Turn(loop: Bool = false) {
        stopSequence()
        isRunningSequence = true
        sequenceTask = Task { [weak self] in
            repeat {
                for (json, hold) in Self.v2TurnFrames {
                    if Task.isCancelled { break }
                    self?.injectRaw(Data(json.utf8))
                    try? await Task.sleep(for: .milliseconds(hold))
                }
            } while loop && !Task.isCancelled
            self?.isRunningSequence = false
        }
    }

    /// One CODEX turn as v2 payloads. `pid` is fixed and `turn_id` advances, so
    /// the turn-identity logic sees a real boundary at the end.
    static let v2TurnFrames: [(String, Int)] = {
        func frame(_ state: String, _ detail: String, turn: String,
                   route: String, latency: String) -> String {
            """
            {"version":2,"state":"\(state)","since":\(Date().timeIntervalSince1970),
             "detail":"\(detail)","pid":97559,"turn_id":\(turn),"route":\(route),
             "latency_ms":\(latency)}
            """
        }
        let none = #"{"wake_to_capture":null,"capture_duration":null,"stt":null,"router":null,"backend":null}"#
        let captured = #"{"wake_to_capture":251,"capture_duration":3557,"stt":null,"router":null,"backend":null}"#
        let routed = #"{"wake_to_capture":251,"capture_duration":3557,"stt":731,"router":2546,"backend":null}"#
        let done = #"{"wake_to_capture":251,"capture_duration":3557,"stt":731,"router":2546,"backend":3054}"#
        return [
            (frame("IDLE", "waiting for wake word", turn: "null", route: "null", latency: none), 900),
            (frame("LISTENING", "capturing utterance", turn: "42", route: "null", latency: none), 1500),
            (frame("THINKING", "transcribing", turn: "42", route: "null", latency: captured), 900),
            (frame("ROUTING", "CODEX via llm", turn: "42", route: "null", latency: captured), 500),
            (frame("CODEX", "working", turn: "42", route: "\"CODEX\"", latency: routed), 2600),
            (frame("SPEAKING", "解析が完了しました。", turn: "42", route: "\"CODEX\"", latency: done), 2600),
            (frame("IDLE", "waiting for wake word", turn: "null", route: "null", latency: none), 1500),
        ]
    }()

    public func stopSequence() {
        sequenceTask?.cancel()
        sequenceTask = nil
        isRunningSequence = false
        futureContext = nil
    }
}

#endif
