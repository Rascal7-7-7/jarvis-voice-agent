import Foundation
import Testing
@testable import JarvisHUD

// MARK: - route tracking

@Test("the route survives into SPEAKING, where the state no longer carries it")
func routeSurvivesTheTurn() {
    var t = RouteTracker()
    for phase in [JarvisPhase.listening, .thinking, .routing, .codex] {
        t.observe(phase)
    }
    #expect(t.label == "CODEX")
    t.observe(.speaking)
    #expect(t.label == "CODEX", "the route was lost once dispatch ended")
}

@Test("returning to IDLE forgets the route so it cannot bleed into the next turn")
func routeClearsAtIdle() {
    var t = RouteTracker()
    t.observe(.claude)
    #expect(t.label == "CLAUDE")
    t.observe(.speaking)
    t.observe(.idle)
    #expect(t.current == nil)
    #expect(t.label == nil)
    // A second turn that never dispatches must show no route at all.
    t.observe(.listening)
    t.observe(.thinking)
    #expect(t.label == nil, "a stale route reappeared on a later turn")
}

@Test("OFFLINE also clears the route")
func routeClearsAtOffline() {
    var t = RouteTracker()
    t.observe(.web)
    t.observe(.offline)
    #expect(t.current == nil)
}

@Test("every route state has a text label, never colour alone")
func everyRouteHasWords() {
    for phase in JarvisPhase.allCases where phase.isRoute {
        let t = RouteTracker().observing(phase)
        #expect(t.label?.isEmpty == false, "\(phase.rawValue) has no label")
    }
    // And non-route states must NOT produce one.
    for phase in [JarvisPhase.idle, .listening, .thinking, .speaking, .error] {
        #expect(RouteTracker().observing(phase).label == nil)
    }
}

// MARK: - disclosure policy

private func drive(_ phases: [JarvisPhase], policy: DisclosurePolicy,
                   collapse: CollapseDelay = .short,
                   start: TimeInterval = 1000,
                   step: TimeInterval = 1) -> [Bool] {
    var s = DisclosureState()
    var t = start
    return phases.map { phase in
        s.update(phase: phase, policy: policy, collapse: collapse, now: t)
        t += step
        return s.isExpanded
    }
}

@Test("MANUAL_ONLY never opens the panel by itself")
func manualOnlyNeverAutoExpands() {
    let seen = drive([.idle, .listening, .thinking, .routing, .codex,
                      .speaking, .idle], policy: .manualOnly)
    #expect(seen.allSatisfy { $0 == false })
}

@Test("TURN_ACTIVITY opens from LISTENING and closes after the idle delay")
func turnActivityOpensForTheWholeTurn() {
    var s = DisclosureState()
    var t: TimeInterval = 1000
    // Time advances BEFORE the update, because that is the real order: the
    // collapse timer fires later and asks again. Advancing afterwards would
    // mean every observation saw zero elapsed time.
    func at(_ dt: TimeInterval, _ p: JarvisPhase) {
        t += dt
        s.update(phase: p, policy: .turnActivity, collapse: .short, now: t)
    }
    at(0, .idle);      #expect(!s.isExpanded, "idle opened a closed panel")
    at(1, .listening); #expect(s.isExpanded)
    at(1, .thinking);  #expect(s.isExpanded)
    at(1, .codex);     #expect(s.isExpanded)
    at(1, .speaking);  #expect(s.isExpanded)
    at(1, .idle);      #expect(s.isExpanded, "collapsed the instant idle began")
    at(1, .idle);      #expect(s.isExpanded, "collapsed before the 2s delay")
    at(2, .idle);      #expect(!s.isExpanded, "did not collapse after the delay")
}

@Test("ROUTE_ONLY stays shut for a turn that never dispatches")
func routeOnlyIgnoresAShortTurn() {
    let seen = drive([.idle, .listening, .thinking, .idle], policy: .routeOnly)
    #expect(seen.allSatisfy { $0 == false })
}

@Test("ROUTE_ONLY opens on the route and stays open through SPEAKING")
func routeOnlyOpensOnDispatch() {
    var s = DisclosureState()
    var t: TimeInterval = 1000
    func at(_ dt: TimeInterval, _ p: JarvisPhase) {
        t += dt
        s.update(phase: p, policy: .routeOnly, collapse: .short, now: t)
    }
    at(0, .listening); #expect(!s.isExpanded)
    at(1, .routing);   #expect(!s.isExpanded)
    at(1, .codex);     #expect(s.isExpanded)
    at(1, .speaking);  #expect(s.isExpanded, "closed as soon as the reply began")
    at(1, .idle);      #expect(s.isExpanded, "collapsed instantly at idle")
    at(3, .idle);      #expect(!s.isExpanded, "never collapsed")
}

@Test("ROUTE_ONLY opens for ERROR and CONFIRMATION_REQUIRED even without a route")
func routeOnlyOpensForUrgentStates() {
    var s = DisclosureState()
    s.update(phase: .error, policy: .routeOnly, collapse: .short, now: 1000)
    #expect(s.isExpanded)

    var s2 = DisclosureState()
    s2.update(phase: .confirmationRequired, policy: .routeOnly,
              collapse: .short, now: 1000)
    #expect(s2.isExpanded)
}

@Test("MANUAL_ONLY collapse delay never collapses on its own")
func manualCollapseDelayHoldsOpen() {
    var s = DisclosureState()
    s.update(phase: .codex, policy: .routeOnly, collapse: .manualOnly, now: 1000)
    #expect(s.isExpanded)
    s.update(phase: .idle, policy: .routeOnly, collapse: .manualOnly, now: 1001)
    s.update(phase: .idle, policy: .routeOnly, collapse: .manualOnly, now: 1600)
    #expect(s.isExpanded, "collapsed despite MANUAL_ONLY")
}

@Test("a manual choice overrides the policy for that turn")
func manualOverridesPolicy() {
    var s = DisclosureState()
    s.update(phase: .codex, policy: .routeOnly, collapse: .short, now: 1000)
    #expect(s.isExpanded)
    s.setManual(expanded: false)                // user dismissed it
    s.update(phase: .speaking, policy: .routeOnly, collapse: .short, now: 1001)
    #expect(!s.isExpanded, "the policy reopened a panel the user dismissed")
}

@Test("a dismissal applies to that turn only, not to every turn after it")
func manualOverrideClearsOnTheNextTurn() {
    var s = DisclosureState()
    s.update(phase: .codex, policy: .routeOnly, collapse: .short, now: 1000)
    s.setManual(expanded: false)
    s.update(phase: .idle, policy: .routeOnly, collapse: .short, now: 1001)
    s.update(phase: .idle, policy: .routeOnly, collapse: .short, now: 1005)
    #expect(!s.isExpanded)
    // New turn begins.
    s.update(phase: .listening, policy: .routeOnly, collapse: .short, now: 1010)
    s.update(phase: .codex, policy: .routeOnly, collapse: .short, now: 1011)
    #expect(s.isExpanded, "one dismissal silently disabled the panel for good")
}

// MARK: - mock/production separation

@Test("long future text is capped, never rendered in full")
func futureTextIsCapped() {
    let huge = String(repeating: "あ", count: 5000)
    let c = FutureHUDContext(heardText: huge, responsePreview: huge,
                             toolSummary: huge)
    #expect(c.displayHeardText!.count <= FutureHUDContext.maxHeardText)
    #expect(c.displayResponsePreview!.count <= FutureHUDContext.maxResponsePreview)
    #expect(c.displayToolSummary!.count <= FutureHUDContext.maxToolSummary)
    #expect(c.displayHeardText!.hasSuffix("\u{2026}"))
}

@Test("control characters and newlines are stripped from future text")
func futureTextIsSanitised() {
    let nasty = "line one\nline\rtwo\u{0007}\u{200B}three"
    let c = FutureHUDContext(heardText: nasty)
    let out = c.displayHeardText!
    #expect(!out.contains("\n"))
    #expect(!out.contains("\r"))
    #expect(!out.contains("\u{0007}"))
}

@Test("markup in future text stays inert text, never markdown")
func markupIsNotInterpreted() {
    // The guarantee is at the view layer (Text(verbatim:)), but the sanitiser
    // must not silently strip or transform the characters either -- a link that
    // vanished is as misleading as one that was rendered.
    let md = "[click](https://example.com) **bold** `code`"
    let c = FutureHUDContext(responsePreview: md)
    #expect(c.displayResponsePreview == md)
}

@Test("an empty future context has nothing to show")
func emptyFutureContext() {
    let c = FutureHUDContext()
    #expect(c.displayHeardText == nil)
    #expect(c.displayResponsePreview == nil)
    #expect(c.displayToolSummary == nil)
    #expect(c.latency == nil)
}

@Test("the mock provider publishes no future context until a sequence runs")
@MainActor
func mockProviderStartsClean() {
    let p = MockStateProvider()
    #expect(p.futureContext == nil, "future data existed before any sequence")
    p.set(.codex, detail: "working")
    #expect(p.futureContext == nil, "a plain state change invented context")
}

@Test("every Phase 2A sequence ends back at a resting state")
@MainActor
func sequencesReturnToRest() {
    let sequences: [(String, [MockStateProvider.Step])] = [
        ("A", MockStateProvider.sequenceLocalFast),
        ("B", MockStateProvider.sequenceCodex),
        ("C", MockStateProvider.sequenceClaude),
        ("D", MockStateProvider.sequenceError),
        ("E", MockStateProvider.sequenceConfirmation),
    ]
    for (name, steps) in sequences {
        #expect(!steps.isEmpty, "sequence \(name) is empty")
        #expect(steps.last!.phase == .idle,
                "sequence \(name) does not return to IDLE")
    }
}

@Test("latency stages sum to the whole and give proportions")
func latencyStages() {
    let l = LatencyBreakdown(stt: 0.8, router: 2.5, backend: 3.0, tts: 0.6)
    #expect(abs(l.total - 6.9) < 0.0001)
    let sum = l.stages.reduce(0.0) { $0 + $1.fraction }
    #expect(abs(sum - 1.0) < 0.0001)
    #expect(l.stages.map(\.name) == ["STT", "Router", "Backend", "TTS"])
}

@Test("a zero-length breakdown does not divide by zero")
func latencyZero() {
    let l = LatencyBreakdown(stt: 0, router: 0, backend: 0, tts: 0)
    #expect(l.stages.allSatisfy { $0.fraction.isFinite })
}
