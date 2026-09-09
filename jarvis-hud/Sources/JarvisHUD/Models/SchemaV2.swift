import Foundation

/// The state-file format version the HUD is reading.
///
/// A file with no `version` is v1 — that is what everything written before the
/// Minimal V2 runtime looks like, and it is a normal thing to encounter rather
/// than an error. A version the HUD does not know about is also not an error:
/// the four v1 fields are read, the rest is ignored, and the HUD keeps drawing.
/// Refusing to render because the backend moved ahead would make the viewer the
/// fragile half of a pair that is deliberately deployed separately.

// MARK: - JSON value helpers

/// Is this JSON value a boolean?
///
/// `raw is Bool` does NOT answer that for JSONSerialization output.
/// `JSONSerialization` returns numbers as `NSNumber`, and an `NSNumber`
/// holding 0 or 1 bridges to `Bool` successfully -- so `is Bool` is true for
/// the integers 0 and 1. Using it as a guard silently rejected `router: 0`
/// (a real reading on the greeting fast path) and `turn_id: 1` (every first
/// turn). CoreFoundation's type id is the only reliable discriminator.
func isJSONBoolean(_ raw: Any?) -> Bool {
    guard let raw else { return false }
    return CFGetTypeID(raw as CFTypeRef) == CFBooleanGetTypeID()
}

/// The state-file format version the HUD is reading.
public enum SchemaVersion: Equatable, Sendable {
    case v1                       // no version key
    case v2
    case future(Int)              // newer than this build understands

    public init(rawValue: Any?) {
        guard !isJSONBoolean(rawValue), let n = rawValue as? Int else {
            self = .v1            // absent, or not an integer
            return
        }
        switch n {
        case ..<2: self = .v1
        case 2: self = .v2
        default: self = .future(n)
        }
    }

    /// Shown in diagnostics only. Never a reason to change what renders.
    public var label: String {
        switch self {
        case .v1: return "v1"
        case .v2: return "v2"
        case .future(let n): return "v\(n) (newer)"
        }
    }

    /// True when the payload may carry turn_id / route / latency_ms. A future
    /// version still might, so it is included.
    public var mayCarryTurnMetadata: Bool { self != .v1 }
}

/// Per-stage timings, as published. All optional, because a turn fills them in
/// as it goes and a v1 payload has none of them.
///
/// `nil` means "not measured yet". `0` means "measured, and it was zero" —
/// `router` really is 0 ms on the greeting fast path, where the gate settles the
/// turn and the LLM never runs. Code that treats 0 as missing would erase a
/// real measurement, so nothing here does.
public struct LatencyMetrics: Equatable, Sendable {
    /// Defence in depth against a malformed writer. The runtime already
    /// publishes null above an hour; this must agree rather than invent a
    /// second threshold.
    public static let maxPlausibleMs = 3_600_000

    public let wakeToCapture: Int?
    public let captureDuration: Int?
    public let stt: Int?
    public let router: Int?
    public let backend: Int?

    public init(wakeToCapture: Int? = nil, captureDuration: Int? = nil,
                stt: Int? = nil, router: Int? = nil, backend: Int? = nil) {
        self.wakeToCapture = wakeToCapture
        self.captureDuration = captureDuration
        self.stt = stt
        self.router = router
        self.backend = backend
    }

    public var isEmpty: Bool {
        wakeToCapture == nil && captureDuration == nil && stt == nil
            && router == nil && backend == nil
    }

    /// UI order, with labels separate from the schema keys.
    ///
    /// `captureDuration` is labelled "Capture" rather than being folded into a
    /// row of latencies with the rest: it is the user talking, not overhead, and
    /// a 30-second recording must not read as 30 seconds of lag.
    public var stages: [(label: String, ms: Int?)] {
        [("Wake→Capture", wakeToCapture),
         ("Capture", captureDuration),
         ("STT", stt),
         ("Router", router),
         ("Backend", backend)]
    }

    /// Sum of the stages that have been measured, for a "so far" figure.
    /// Deliberately excludes `captureDuration` — see above.
    public var measuredOverheadMs: Int? {
        let parts = [wakeToCapture, stt, router, backend].compactMap { $0 }
        return parts.isEmpty ? nil : parts.reduce(0, +)
    }

    /// One value from the JSON. Anything that is not a plausible, non-negative
    /// integer becomes nil rather than being shown.
    static func value(_ raw: Any?) -> Int? {
        // Booleans first: `true` must not arrive as 1 ms.
        if isJSONBoolean(raw) { return nil }
        guard let n = raw as? Int, n >= 0, n <= maxPlausibleMs else { return nil }
        return n
    }

    static func decode(_ raw: Any?) -> LatencyMetrics? {
        guard let d = raw as? [String: Any] else { return nil }
        return LatencyMetrics(
            wakeToCapture: value(d["wake_to_capture"]),
            captureDuration: value(d["capture_duration"]),
            stt: value(d["stt"]),
            router: value(d["router"]),
            backend: value(d["backend"]))
    }
}

/// Which turn a payload belongs to.
///
/// `turn_id` restarts at 1 when the runtime restarts, so it is only meaningful
/// with the pid: 42 → 1 means a new process, not a counter going backwards.
/// That is why identity is the pair and never the id alone.
public struct TurnIdentity: Equatable, Sendable {
    public let pid: Int?
    public let turnId: Int?

    public init(pid: Int?, turnId: Int?) {
        self.pid = pid
        self.turnId = turnId
    }

    public var isActiveTurn: Bool { turnId != nil }

    /// True when this payload belongs to a different turn than `other` —
    /// including "a different process, same turn number".
    public func differsFrom(_ other: TurnIdentity) -> Bool {
        pid != other.pid || turnId != other.turnId
    }

    static func turnId(_ raw: Any?) -> Int? {
        if isJSONBoolean(raw) { return nil }
        guard let n = raw as? Int, n > 0 else { return nil }   // 0 and -1 invalid
        return n
    }
}
