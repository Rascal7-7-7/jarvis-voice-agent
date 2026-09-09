import Foundation

/// Decides what the HUD SHOWS, given what the backend SAYS.
///
/// The backend is never delayed or filtered — this only smooths presentation.
/// Real turns pass through states that last milliseconds (`GATE=0ms`,
/// and a router hop can be over before a frame lands), so without a minimum
/// dwell the panel strobes.
///
/// Deliberately small: a full state machine here would be a second source of
/// truth about a pipeline that already has one.
public struct StatePresenter: Sendable {
    /// How long a non-urgent state stays on screen before it can be replaced.
    public var minimumDwell: TimeInterval = 0.30

    private(set) var shown: JarvisState?
    private(set) var shownAt: Date = .distantPast
    private(set) var pending: JarvisState?

    public init(minimumDwell: TimeInterval = 0.30) {
        self.minimumDwell = minimumDwell
    }

    /// Feed a backend state. Returns the state that should be displayed now.
    ///
    /// Urgent states are promoted immediately: holding back LISTENING would
    /// mean the mic is open with no indication, and holding back ERROR or
    /// OFFLINE would show a working HUD for a runtime that has stopped.
    public mutating func accept(_ incoming: JarvisState,
                                now: Date = Date()) -> JarvisState {
        guard let current = shown else {
            shown = incoming
            shownAt = now
            pending = nil
            return incoming
        }
        if incoming == current {
            pending = nil
            return current
        }
        if incoming.phase.isUrgent || now.timeIntervalSince(shownAt) >= minimumDwell {
            shown = incoming
            shownAt = now
            pending = nil
            return incoming
        }
        // Too soon: remember it and keep showing the current one. Only the most
        // recent pending state is kept -- a queue would replay states the user
        // has already been overtaken by.
        pending = incoming
        return current
    }

    /// Call when the dwell timer fires to release a held state.
    public mutating func flush(now: Date = Date()) -> JarvisState? {
        guard let p = pending, now.timeIntervalSince(shownAt) >= minimumDwell else {
            return nil
        }
        shown = p
        shownAt = now
        pending = nil
        return p
    }

    /// When the next flush should happen, if anything is waiting.
    public func nextFlushDelay(now: Date = Date()) -> TimeInterval? {
        guard pending != nil else { return nil }
        return max(0, minimumDwell - now.timeIntervalSince(shownAt))
    }
}

/// Is the runtime that wrote this state still alive?
///
/// Phase 1A does not watch a real PID — this exists so the logic is written and
/// tested before it is needed, and so the production connection in a later
/// phase is a wiring change rather than new untested code.
///
/// It matters because `set_state()` swallows write errors
/// (`except OSError: pass`): a file that looks fresh is not proof of a live
/// runtime, and a file that stopped updating leaves no trace of its own.
public enum Liveness: Sendable {
    case live
    case stale(reason: String)
    case unknown

    /// - Parameters:
    ///   - pid: from the state file
    ///   - age: how long since the state was written
    ///   - maxAge: beyond this, treat as stale even if the process exists
    ///   - processExists: injected so this is testable without real processes
    public static func evaluate(pid: Int?,
                                age: TimeInterval?,
                                maxAge: TimeInterval = 300,
                                processExists: (Int) -> Bool) -> Liveness {
        guard let pid else { return .unknown }
        guard pid > 0 else { return .stale(reason: "invalid pid") }
        if !processExists(pid) { return .stale(reason: "pid \(pid) not running") }
        if let age, age > maxAge {
            return .stale(reason: String(format: "no update for %.0fs", age))
        }
        return .live
    }

    /// Real check, for the production phase. `kill(pid, 0)` tests existence
    /// without sending a signal; EPERM means it exists but is owned by someone
    /// else, which still counts as running.
    public static func processIsRunning(_ pid: Int) -> Bool {
        if pid <= 0 { return false }
        if kill(pid_t(pid), 0) == 0 { return true }
        return errno == EPERM
    }
}
