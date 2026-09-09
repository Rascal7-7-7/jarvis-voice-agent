import Foundation
import Testing
@testable import JarvisHUD

// MARK: - decoding

@Test("every state the runtime emits decodes to a known phase")
func allRuntimeStatesDecode() {
    // Exactly the vocabulary found in jarvis_runtime.py's set_state() calls,
    // including the route labels that arrive via set_state(route, "working").
    let runtimeStates = ["OFFLINE", "IDLE", "LISTENING", "THINKING", "ROUTING",
                         "LOCAL_FAST", "LOCAL_TOOL", "LOCAL", "WEB", "CODEX",
                         "CLAUDE", "CONFIRMATION_REQUIRED", "SPEAKING", "ERROR"]
    for s in runtimeStates {
        let data = Data(#"{"state":"\#(s)","since":1788044091.0,"detail":"d","pid":1}"#.utf8)
        let decoded = JarvisState.decode(from: data)
        #expect(decoded != nil, "\(s) failed to decode")
        #expect(decoded?.phase != .unknown, "\(s) decoded as unknown")
        #expect(decoded?.phase.rawValue == s)
    }
}

@Test("an unrecognised state becomes .unknown but keeps its raw text")
func unknownState() {
    let data = Data(#"{"state":"SOME_FUTURE_STATE","since":1.0,"detail":"","pid":1}"#.utf8)
    let s = JarvisState.decode(from: data)
    #expect(s?.phase == .unknown)
    #expect(s?.rawState == "SOME_FUTURE_STATE")
}

@Test("malformed input returns nil rather than throwing or blanking")
func malformedInput() {
    #expect(JarvisState.decode(from: Data("{ not json".utf8)) == nil)
    #expect(JarvisState.decode(from: Data()) == nil)
    #expect(JarvisState.decode(from: Data("[]".utf8)) == nil)
    #expect(JarvisState.decode(from: Data(#"{"state":123}"#.utf8)) == nil)
    #expect(JarvisState.decode(from: Data(#"{"detail":"no state"}"#.utf8)) == nil)
}

@Test("a file larger than the cap is refused")
func oversizedFileRefused() {
    let huge = Data(repeating: 0x20, count: JarvisState.maxFileBytes + 1)
    #expect(JarvisState.decode(from: huge) == nil)
}

@Test("missing optional fields still yield a usable state")
func missingFields() {
    let s = JarvisState.decode(from: Data(#"{"state":"THINKING"}"#.utf8))
    #expect(s?.phase == .thinking)
    #expect(s?.detail == "")
    #expect(s?.since == nil)
    #expect(s?.pid == nil)
    #expect(s?.elapsed() == 0)
}

// MARK: - display hardening

@Test("detail is truncated to the display cap")
func detailTruncation() {
    let long = String(repeating: "あ", count: 5000)
    let s = JarvisState(phase: .speaking, rawState: "SPEAKING", since: nil,
                        detail: long, pid: 1)
    #expect(s.displayDetail.count == JarvisState.maxDetailDisplayLength)
    #expect(s.displayDetail.hasSuffix("\u{2026}"))
}

@Test("a detail exactly at the cap is not truncated")
func detailAtCap() {
    let exact = String(repeating: "x", count: JarvisState.maxDetailDisplayLength)
    let s = JarvisState(phase: .idle, rawState: "IDLE", since: nil,
                        detail: exact, pid: 1)
    #expect(s.displayDetail == exact)
}

@Test("control characters and newlines are stripped from displayed detail")
func controlCharactersStripped() {
    let s = JarvisState(phase: .error, rawState: "ERROR", since: nil,
                        detail: "line1\nline2\u{0007}\u{001B}[31m", pid: 1)
    let d = s.displayDetail
    #expect(!d.contains("\n"))
    #expect(!d.contains("\u{0007}"))
    #expect(!d.contains("\u{001B}"))
    #expect(d.contains("line1 line2"))
}

@Test("an over-long raw state string is capped")
func rawStateCapped() {
    let long = String(repeating: "A", count: 500)
    let data = Data(#"{"state":"\#(long)","since":1.0,"detail":"","pid":1}"#.utf8)
    let s = JarvisState.decode(from: data)
    #expect(s?.rawState.count == JarvisState.maxRawStateLength)
    #expect(s?.phase == .unknown)
}

// MARK: - since / elapsed

@Test("a future timestamp clamps to zero instead of going negative")
func futureSinceClamps() {
    let future = Date().addingTimeInterval(60 * 60 * 24 * 365)
    let s = JarvisState(phase: .thinking, rawState: "THINKING", since: future,
                        detail: "", pid: 1)
    #expect(s.elapsed() == 0)
}

@Test("elapsed is measured from since")
func elapsedMeasured() {
    let past = Date().addingTimeInterval(-5)
    let s = JarvisState(phase: .idle, rawState: "IDLE", since: past,
                        detail: "", pid: 1)
    #expect(s.elapsed() >= 4.9 && s.elapsed() <= 5.5)
}

@Test("a non-positive since is treated as absent")
func nonPositiveSince() {
    let s = JarvisState.decode(from: Data(#"{"state":"IDLE","since":0,"pid":1}"#.utf8))
    #expect(s?.since == nil)
}

// MARK: - presenter

@Test("a non-urgent state is held for the minimum dwell")
func nonUrgentIsHeld() {
    var p = StatePresenter(minimumDwell: 0.30)
    let t0 = Date()
    let thinking = JarvisState(phase: .thinking, rawState: "THINKING",
                               since: t0, detail: "", pid: 1)
    let routing = JarvisState(phase: .routing, rawState: "ROUTING",
                              since: t0, detail: "", pid: 1)
    #expect(p.accept(thinking, now: t0).phase == .thinking)
    // 60 ms later: too soon, keep showing THINKING
    #expect(p.accept(routing, now: t0.addingTimeInterval(0.06)).phase == .thinking)
    // past the dwell: the held state is released
    #expect(p.flush(now: t0.addingTimeInterval(0.35))?.phase == .routing)
}

@Test("urgent states bypass the dwell entirely")
func urgentBypassesDwell() {
    var p = StatePresenter(minimumDwell: 0.30)
    let t0 = Date()
    let thinking = JarvisState(phase: .thinking, rawState: "THINKING",
                               since: t0, detail: "", pid: 1)
    _ = p.accept(thinking, now: t0)
    for urgent in [JarvisPhase.listening, .speaking, .error, .offline,
                   .confirmationRequired] {
        var q = StatePresenter(minimumDwell: 0.30)
        _ = q.accept(thinking, now: t0)
        let s = JarvisState(phase: urgent, rawState: urgent.rawValue,
                            since: t0, detail: "", pid: 1)
        #expect(q.accept(s, now: t0.addingTimeInterval(0.01)).phase == urgent,
                "\(urgent.rawValue) was delayed")
    }
}

@Test("rapid transitions collapse to the most recent pending state")
func rapidTransitionsCollapse() {
    var p = StatePresenter(minimumDwell: 0.30)
    let t0 = Date()
    func s(_ ph: JarvisPhase) -> JarvisState {
        JarvisState(phase: ph, rawState: ph.rawValue, since: t0, detail: "", pid: 1)
    }
    _ = p.accept(s(.thinking), now: t0)
    _ = p.accept(s(.routing), now: t0.addingTimeInterval(0.02))
    _ = p.accept(s(.localFast), now: t0.addingTimeInterval(0.04))
    _ = p.accept(s(.web), now: t0.addingTimeInterval(0.06))
    // Only the newest survives -- intermediate states the user was already
    // overtaken by are not replayed.
    #expect(p.flush(now: t0.addingTimeInterval(0.40))?.phase == .web)
}

@Test("repeating the same state does not restart the dwell")
func identicalStateIsIdempotent() {
    var p = StatePresenter(minimumDwell: 0.30)
    let t0 = Date()
    let idle = JarvisState(phase: .idle, rawState: "IDLE", since: t0,
                           detail: "waiting", pid: 1)
    #expect(p.accept(idle, now: t0).phase == .idle)
    #expect(p.accept(idle, now: t0.addingTimeInterval(0.05)).phase == .idle)
    #expect(p.nextFlushDelay(now: t0.addingTimeInterval(0.05)) == nil)
}

// MARK: - liveness

@Test("liveness classifies pid and age correctly")
func livenessRules() {
    if case .live = Liveness.evaluate(pid: 100, age: 5, processExists: { _ in true }) {}
    else { Issue.record("a live process with a fresh state should be .live") }

    if case .stale = Liveness.evaluate(pid: 100, age: 5, processExists: { _ in false }) {}
    else { Issue.record("a dead pid should be .stale") }

    if case .stale = Liveness.evaluate(pid: 100, age: 9999, processExists: { _ in true }) {}
    else { Issue.record("a very old state should be .stale") }

    if case .stale = Liveness.evaluate(pid: -1, age: 1, processExists: { _ in true }) {}
    else { Issue.record("an invalid pid should be .stale") }

    if case .unknown = Liveness.evaluate(pid: nil, age: 1, processExists: { _ in true }) {}
    else { Issue.record("a missing pid should be .unknown") }
}

// MARK: - phase classification

@Test("only listening, thinking and speaking animate")
func animationSetIsMinimal() {
    let animated = JarvisPhase.allCases.filter(\.isAnimated)
    #expect(Set(animated) == Set([.listening, .thinking, .speaking]))
}

@Test("the six route labels are recognised as routes")
func routeSet() {
    let routes = JarvisPhase.allCases.filter(\.isRoute)
    #expect(Set(routes) == Set([.localFast, .localTool, .local, .web, .codex, .claude]))
}

@Test("every phase has a non-empty label and symbol")
func everyPhaseIsRenderable() {
    for p in JarvisPhase.allCases {
        #expect(!p.label.isEmpty, "\(p.rawValue) has no label")
        #expect(!p.symbolName.isEmpty, "\(p.rawValue) has no symbol")
    }
}
