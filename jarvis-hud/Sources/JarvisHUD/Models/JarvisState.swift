import Foundation

/// The states `jarvis_runtime.py` actually publishes.
///
/// Taken from every `set_state()` call site in the runtime, including
/// `set_state(route, "working")` — which is why the six route labels appear
/// here as states in their own right rather than as a separate `route` field.
/// The runtime has no `route` key; the route IS the state once dispatch starts.
public enum JarvisPhase: String, CaseIterable, Sendable {
    case offline = "OFFLINE"
    case idle = "IDLE"
    case listening = "LISTENING"
    case thinking = "THINKING"
    case routing = "ROUTING"
    case localFast = "LOCAL_FAST"
    case localTool = "LOCAL_TOOL"
    case local = "LOCAL"
    case web = "WEB"
    case codex = "CODEX"
    case claude = "CLAUDE"
    case confirmationRequired = "CONFIRMATION_REQUIRED"
    case speaking = "SPEAKING"
    case error = "ERROR"

    /// Anything the runtime emits that this build does not know about. A HUD
    /// that crashes on an unrecognised string is a HUD that breaks the first
    /// time the backend gains a state.
    case unknown = "UNKNOWN"

    /// The six states that are really route labels.
    public var isRoute: Bool {
        switch self {
        case .localFast, .localTool, .local, .web, .codex, .claude: return true
        default: return false
        }
    }

    /// Only these three animate. Everything else draws once and schedules
    /// nothing, which is what keeps idle CPU at zero.
    public var isAnimated: Bool {
        switch self {
        case .listening, .thinking, .speaking: return true
        default: return false
        }
    }

    /// States that must never be held back by presentation smoothing. A stale
    /// frame hiding "the mic is open" or "something failed" is worse than a
    /// flicker.
    public var isUrgent: Bool {
        switch self {
        case .listening, .speaking, .error, .offline, .confirmationRequired:
            return true
        default:
            return false
        }
    }

    /// Short label under the JARVIS title. Never colour alone — every state is
    /// identifiable from text, per the accessibility requirement.
    public var label: String {
        switch self {
        case .offline: return "Offline"
        case .idle: return "Idle"
        case .listening: return "Listening"
        case .thinking: return "Thinking"
        case .routing: return "Routing"
        case .localFast: return "LOCAL_FAST"
        case .localTool: return "LOCAL_TOOL"
        case .local: return "LOCAL"
        case .web: return "WEB"
        case .codex: return "CODEX"
        case .claude: return "CLAUDE"
        case .confirmationRequired: return "Confirmation required"
        case .speaking: return "Speaking"
        case .error: return "Error"
        case .unknown: return "Unknown"
        }
    }

    /// Menu-bar glyph. SF Symbols only, no colour dependency.
    public var symbolName: String {
        switch self {
        case .offline: return "moon.zzz"
        case .idle: return "circle"
        case .listening: return "waveform.circle"
        case .thinking: return "circle.dotted"
        case .routing: return "arrow.triangle.branch"
        case .localFast, .localTool, .local: return "cpu"
        case .web: return "globe"
        case .codex, .claude: return "chevron.left.forwardslash.chevron.right"
        case .confirmationRequired: return "exclamationmark.shield"
        case .speaking: return "waveform"
        case .error: return "exclamationmark.triangle"
        case .unknown: return "questionmark.circle"
        }
    }
}

/// One snapshot of `jarvis_state.json`.
///
/// Decoding is deliberately tolerant. The file is written by a trusted process
/// today, but a viewer that assumes well-formed input is a viewer that dies the
/// first time a write is truncated or a field changes type.
public struct JarvisState: Equatable, Sendable {
    public static let maxRawStateLength = 64
    public static let maxDetailDisplayLength = 120
    public static let maxFileBytes = 64 * 1024

    public let phase: JarvisPhase
    /// What the file literally said, kept so an unknown state can be shown
    /// rather than silently swallowed.
    public let rawState: String
    public let since: Date?
    public let detail: String
    public let pid: Int?

    // --- schema v2. All optional: a v1 payload has none of them, and that is
    // --- normal rather than an error. ---

    public let schemaVersion: SchemaVersion
    /// Positive integer during a turn, nil outside one. Only meaningful with
    /// `pid` -- see TurnIdentity.
    public let turnId: Int?
    /// The route the runtime actually dispatched to, as published. nil until
    /// dispatch, and nil again at IDLE. Only the six known labels survive
    /// decoding; anything else becomes nil so a badge can never show free text.
    public let route: JarvisPhase?
    /// Per-stage timings. nil on a v1 payload, or when the object is malformed.
    public let latency: LatencyMetrics?

    public init(phase: JarvisPhase, rawState: String, since: Date?,
                detail: String, pid: Int?,
                schemaVersion: SchemaVersion = .v1, turnId: Int? = nil,
                route: JarvisPhase? = nil, latency: LatencyMetrics? = nil) {
        self.phase = phase
        self.rawState = rawState
        self.since = since
        self.detail = detail
        self.pid = pid
        self.schemaVersion = schemaVersion
        self.turnId = turnId
        self.route = route
        self.latency = latency
    }

    public static let placeholder = JarvisState(
        phase: .unknown, rawState: "UNKNOWN", since: nil, detail: "", pid: nil)

    public var turnIdentity: TurnIdentity {
        TurnIdentity(pid: pid, turnId: turnId)
    }

    /// Seconds since the state was entered, clamped at zero.
    ///
    /// `since` is wall-clock (`time.time()`), not monotonic, so an NTP step or
    /// a sleep/wake can put it in the future. Negative elapsed time is a clock
    /// artefact, never something to render.
    public func elapsed(now: Date = Date()) -> TimeInterval {
        guard let since else { return 0 }
        return max(0, now.timeIntervalSince(since))
    }

    /// Detail text safe to put on screen: length-capped, control characters
    /// stripped, newlines flattened. Rendered as plain text only — never as
    /// markup, a link, or a path.
    public var displayDetail: String {
        let cleaned = detail
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\r", with: " ")
            .unicodeScalars
            .filter { !CharacterSet.controlCharacters.contains($0) }
            .reduce(into: "") { $0.unicodeScalars.append($1) }
        if cleaned.count <= Self.maxDetailDisplayLength { return cleaned }
        return String(cleaned.prefix(Self.maxDetailDisplayLength - 1)) + "\u{2026}"
    }

    /// Parse a raw file. Returns nil for anything unusable, so the caller can
    /// keep showing the last good state instead of blanking the HUD.
    public static func decode(from data: Data) -> JarvisState? {
        guard data.count <= maxFileBytes else { return nil }
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let dict = object as? [String: Any] else { return nil }

        // `state` must be a string; a number or object is malformed input.
        let rawState = (dict["state"] as? String).map {
            String($0.prefix(maxRawStateLength))
        } ?? ""
        let phase = JarvisPhase(rawValue: rawState) ?? .unknown

        // `since` is a POSIX float in the runtime; accept an int too rather
        // than discarding an otherwise valid state over a type nicety.
        var since: Date?
        if let s = dict["since"] as? Double, s.isFinite, s > 0 {
            since = Date(timeIntervalSince1970: s)
        } else if let s = dict["since"] as? Int, s > 0 {
            since = Date(timeIntervalSince1970: Double(s))
        }

        let detail = dict["detail"] as? String ?? ""
        var pid: Int?
        if let p = dict["pid"] as? Int, p > 0 { pid = p }

        // An entry with no usable state string is not worth showing.
        if rawState.isEmpty { return nil }

        // Schema v2, all optional. A missing key, a wrong type or an
        // out-of-range value yields nil for that field and nothing else --
        // never a rejected payload. The four v1 fields above must survive any
        // shape the newer fields arrive in.
        let version = SchemaVersion(rawValue: dict["version"])
        let turnId = TurnIdentity.turnId(dict["turn_id"])
        let route = decodeRoute(dict["route"])
        let latency = LatencyMetrics.decode(dict["latency_ms"])

        return JarvisState(phase: phase, rawState: rawState, since: since,
                           detail: detail, pid: pid,
                           schemaVersion: version, turnId: turnId,
                           route: route, latency: latency)
    }

    /// Only the six dispatch labels are accepted as a route.
    ///
    /// CONFIRMATION_REQUIRED is excluded deliberately: it is a gate verdict, not
    /// a backend, and the runtime never publishes it here. Anything unrecognised
    /// -- lowercase, empty, a SQL fragment, an object -- becomes nil, so the
    /// route badge cannot be turned into a channel for arbitrary text.
    static func decodeRoute(_ raw: Any?) -> JarvisPhase? {
        guard let token = raw as? String,
              let phase = JarvisPhase(rawValue: token),
              phase.isRoute else { return nil }
        return phase
    }
}
