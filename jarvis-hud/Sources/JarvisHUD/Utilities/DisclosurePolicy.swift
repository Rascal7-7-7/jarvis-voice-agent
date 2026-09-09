import Foundation

/// When the expanded panel opens and closes.
///
/// Pure and testable, because "the HUD blew itself open in the middle of
/// something" and "the HUD never opened when it mattered" are both behaviours
/// worth pinning down before they are wired to a window.
///
/// Three candidates are implemented rather than one, because which is right is
/// a judgement about how intrusive a floating panel is allowed to be, and that
/// is better made after watching all three than argued in advance.
public enum DisclosurePolicy: String, CaseIterable, Sendable {
    /// Never opens itself. The user opens it from the menu bar or by clicking
    /// the orb, and it stays as they left it.
    case manualOnly = "MANUAL_ONLY"

    /// Opens for the whole turn, from LISTENING, and closes after IDLE.
    case turnActivity = "TURN_ACTIVITY"

    /// Opens only once dispatch has happened and there is a route to show.
    /// Quietest of the three: a two-second LOCAL_FAST turn never opens a panel.
    case routeOnly = "ROUTE_ONLY"
}

/// How long to wait after returning to IDLE before collapsing.
public enum CollapseDelay: String, CaseIterable, Sendable {
    case immediate = "IMMEDIATE"
    case short = "2S"
    case medium = "5S"
    case manualOnly = "MANUAL_ONLY"

    public var seconds: TimeInterval? {
        switch self {
        case .immediate: return 0
        case .short: return 2
        case .medium: return 5
        case .manualOnly: return nil       // never collapses on its own
        }
    }
}

/// The disclosure state machine.
///
/// Holds no timers. It is asked "what should be showing, given this phase at
/// this moment", and the caller decides how often to ask. A view model that
/// owns a repeating timer to animate a panel that is not moving is exactly the
/// idle CPU this project has spent effort avoiding.
public struct DisclosureState: Equatable, Sendable {
    public private(set) var isExpanded: Bool
    /// Set by the user; overrides the policy until the next turn begins.
    public private(set) var manualOverride: Bool?
    /// When IDLE was entered, for the collapse delay. Monotonic-ish: the caller
    /// supplies `now`, so tests do not wait and the clock cannot surprise us.
    public private(set) var idleSince: TimeInterval?
    private var lastPhase: JarvisPhase?

    public init(isExpanded: Bool = false) {
        self.isExpanded = isExpanded
    }

    /// Fold in the current phase. Returns self for chaining in tests.
    @discardableResult
    public mutating func update(phase: JarvisPhase,
                                policy: DisclosurePolicy,
                                collapse: CollapseDelay,
                                now: TimeInterval) -> DisclosureState {
        let phaseChanged = phase != lastPhase
        let wasIdleOrOffline = lastPhase == .idle || lastPhase == .offline
        lastPhase = phase

        // A new turn clears a manual choice: the user's "hide it" applied to the
        // turn they hid it during, not to every turn afterwards. Without this,
        // one dismissal would silently disable the panel forever.
        if phaseChanged, wasIdleOrOffline, phase != .idle, phase != .offline {
            manualOverride = nil
        }

        if phase == .idle || phase == .offline {
            if idleSince == nil { idleSince = now }
        } else {
            idleSince = nil
        }

        if let manual = manualOverride {
            isExpanded = manual
            return self
        }

        switch policy {
        case .manualOnly:
            isExpanded = false

        case .turnActivity:
            if phase == .idle || phase == .offline {
                // KEEP an open panel open until the delay elapses -- never
                // open a closed one. Returning to rest is not a reason to show
                // anything, and `!collapseElapsed` alone said otherwise.
                isExpanded = isExpanded && !collapseElapsed(now: now, collapse: collapse)
            } else {
                isExpanded = true
            }

        case .routeOnly:
            if phase.isRoute || phase == .confirmationRequired || phase == .error {
                isExpanded = true
            } else if phase == .speaking {
                // Deliberately no assignment: whatever the route set stays put
                // through the reply, so a panel that opened for CODEX does not
                // slam shut the moment JARVIS starts talking about the result.
                break
            } else if phase == .idle || phase == .offline {
                isExpanded = isExpanded && !collapseElapsed(now: now, collapse: collapse)
            } else {
                isExpanded = false
            }
        }
        return self
    }

    private func collapseElapsed(now: TimeInterval, collapse: CollapseDelay) -> Bool {
        guard let seconds = collapse.seconds else { return false }  // manual: never
        guard let idleSince else { return false }
        return now - idleSince >= seconds
    }

    /// The user asked for a specific state. Sticks until the next turn starts.
    public mutating func setManual(expanded: Bool) {
        manualOverride = expanded
        isExpanded = expanded
    }

    public mutating func toggleManual() {
        setManual(expanded: !isExpanded)
    }
}
