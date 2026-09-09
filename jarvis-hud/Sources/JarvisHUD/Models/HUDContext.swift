import Foundation

/// The route a turn was dispatched to, remembered across the turn.
///
/// The runtime has no `route` field — the route IS the state once dispatch
/// starts (`set_state(route, "working")`). So by the time JARVIS is SPEAKING,
/// the route it used is no longer in the state file at all.
///
/// This remembers it. That is derivation, not invention: every value here was
/// published by the runtime moments earlier. It resets at IDLE and OFFLINE so
/// a route can never bleed from one turn into the next.
public struct RouteTracker: Equatable, Sendable {
    public private(set) var current: JarvisPhase?

    public init(current: JarvisPhase? = nil) { self.current = current }

    /// Fold in a newly observed phase.
    public mutating func observe(_ phase: JarvisPhase) {
        switch phase {
        case .idle, .offline:
            current = nil                      // turn is over; forget it
        case _ where phase.isRoute:
            current = phase
        default:
            break                              // LISTENING/THINKING/SPEAKING keep it
        }
    }

    public func observing(_ phase: JarvisPhase) -> RouteTracker {
        var copy = self
        copy.observe(phase)
        return copy
    }

    /// Forget everything. Used when the turn identity changes underneath us --
    /// a new process, or a new turn -- so an inferred route cannot survive into
    /// a turn it did not belong to.
    public mutating func reset() { current = nil }

    /// Display text. Always words — a badge distinguished only by colour is
    /// unreadable to a large fraction of users and invisible to VoiceOver.
    public var label: String? {
        switch current {
        case .localFast: return "LOCAL FAST"
        case .localTool: return "LOCAL TOOL"
        case .local:     return "LOCAL"
        case .web:       return "WEB"
        case .codex:     return "CODEX"
        case .claude:    return "CLAUDE"
        default:         return nil
        }
    }

    public var symbolName: String? { current?.symbolName }
}

/// Which route to show, and where it came from.
///
/// The runtime now publishes the route it actually dispatched to, so inference
/// is no longer necessary -- but the tracker stays. The two halves ship
/// separately: this HUD will meet v1 runtimes, and a viewer that needs a
/// matching backend version is a viewer that breaks on the wrong ordering of
/// two independent updates.
public struct RouteResolution: Equatable, Sendable {
    public enum Source: String, Sendable {
        /// Published by the runtime. What actually happened.
        case authoritative = "published"
        /// Inferred by watching state transitions. Correct, but derived.
        case inferred = "inferred"
        case none = "none"
    }

    public let phase: JarvisPhase?
    public let source: Source

    public var label: String? {
        guard let phase else { return nil }
        return RouteTracker(current: phase).label
    }
    public var symbolName: String? { phase?.symbolName }

    /// Priority: published route, then inferred, then nothing.
    public static func resolve(published: JarvisPhase?,
                               tracker: RouteTracker) -> RouteResolution {
        if let published, published.isRoute {
            return RouteResolution(phase: published, source: .authoritative)
        }
        if let inferred = tracker.current, inferred.isRoute {
            return RouteResolution(phase: inferred, source: .inferred)
        }
        return RouteResolution(phase: nil, source: .none)
    }

    /// A published route that disagrees with the inferred one.
    ///
    /// Only possible if the writer has a bug. The published value still wins --
    /// it is the authoritative one -- and this is recorded for diagnostics
    /// rather than rendered as an alarm. A viewer's job when the backend
    /// misbehaves is to keep drawing something sane, not to shout.
    public static func mismatch(published: JarvisPhase?,
                                tracker: RouteTracker) -> Bool {
        guard let published, published.isRoute,
              let inferred = tracker.current, inferred.isRoute else { return false }
        return published != inferred
    }
}

#if DEBUG
/// Fields the HUD could show but the backend does NOT publish today.
///
/// DEBUG-only, at file scope, so this type does not exist in a release build.
/// That is the point: the expanded panel must not be able to display a
/// transcript, a reply or a latency breakdown in a shipped binary, because
/// `jarvis_state.json` contains none of those and inventing them would be
/// worse than showing nothing.
///
/// Whether any of this becomes real is a Phase 2B decision about the BACKEND,
/// taken on its merits. Building the view first must not become the argument
/// for changing the schema.
public struct FutureHUDContext: Equatable, Sendable {
    /// Caps chosen per field. The transcript is one utterance; a reply can be a
    /// paragraph. Neither is ever shown in full — an unbounded string on a
    /// floating panel is a layout bug waiting for a long answer.
    public static let maxHeardText = 300
    public static let maxResponsePreview = 1000
    public static let maxToolSummary = 120

    public let heardText: String?
    public let responsePreview: String?
    public let toolSummary: String?
    public let latency: LatencyBreakdown?

    public init(heardText: String? = nil, responsePreview: String? = nil,
                toolSummary: String? = nil, latency: LatencyBreakdown? = nil) {
        self.heardText = heardText
        self.responsePreview = responsePreview
        self.toolSummary = toolSummary
        self.latency = latency
    }

    public var displayHeardText: String? {
        heardText.map { Self.sanitise($0, cap: Self.maxHeardText) }
    }
    public var displayResponsePreview: String? {
        responsePreview.map { Self.sanitise($0, cap: Self.maxResponsePreview) }
    }
    public var displayToolSummary: String? {
        toolSummary.map { Self.sanitise($0, cap: Self.maxToolSummary) }
    }

    /// Same treatment `JarvisState.displayDetail` gets: control characters out,
    /// newlines flattened, length capped. Rendered with `Text(verbatim:)` so
    /// markdown, links and paths stay inert characters.
    static func sanitise(_ s: String, cap: Int) -> String {
        let cleaned = s
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\r", with: " ")
            .unicodeScalars
            .filter { !CharacterSet.controlCharacters.contains($0) }
            .reduce(into: "") { $0.unicodeScalars.append($1) }
        if cleaned.count <= cap { return cleaned }
        return String(cleaned.prefix(cap - 1)) + "\u{2026}"
    }
}

/// Where a turn spent its time. Mock-only in Phase 2A.
public struct LatencyBreakdown: Equatable, Sendable {
    public let stt: TimeInterval
    public let router: TimeInterval
    public let backend: TimeInterval
    public let tts: TimeInterval

    public init(stt: TimeInterval, router: TimeInterval,
                backend: TimeInterval, tts: TimeInterval) {
        self.stt = stt
        self.router = router
        self.backend = backend
        self.tts = tts
    }

    public var total: TimeInterval { stt + router + backend + tts }

    /// Ordered stages, with each one's share of the total. The reason to draw
    /// this at all is "where am I waiting" — so proportion is the payload, and
    /// four numbers do not need a chart.
    public var stages: [(name: String, seconds: TimeInterval, fraction: Double)] {
        let t = max(total, 0.0001)
        return [("STT", stt, stt / t), ("Router", router, router / t),
                ("Backend", backend, backend / t), ("TTS", tts, tts / t)]
    }
}
#endif
