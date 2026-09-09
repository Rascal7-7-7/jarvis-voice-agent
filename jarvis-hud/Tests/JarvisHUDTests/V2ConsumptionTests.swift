import Foundation
import Testing
@testable import JarvisHUD

private func decode(_ json: String) -> JarvisState? {
    JarvisState.decode(from: Data(json.utf8))
}

private func v2json(version: String? = "2", state: String = "SPEAKING",
                     turnId: String = "42", route: String = "\"CODEX\"",
                     latency: String = """
                     {"wake_to_capture":251,"capture_duration":3557,
                      "stt":731,"router":2546,"backend":3054}
                     """) -> String {
    let v = version.map { "\"version\": \($0)," } ?? ""
    return """
    {\(v) "state": "\(state)", "since": 1788125256.0, "detail": "d", "pid": 97559,
     "turn_id": \(turnId), "route": \(route), "latency_ms": \(latency)}
    """
}

// MARK: - schema version

@Test("a v1 payload is read as v1, with no turn metadata")
func consumptionV1Payload() throws {
    let s = try #require(decode(
        #"{"state":"IDLE","since":1788125256.0,"detail":"waiting","pid":97559}"#))
    #expect(s.schemaVersion == .v1)
    #expect(s.turnId == nil)
    #expect(s.route == nil)
    #expect(s.latency == nil)
    // The v1 fields are unaffected.
    #expect(s.phase == .idle)
    #expect(s.detail == "waiting")
    #expect(s.pid == 97559)
}

@Test("a v2 payload exposes all four new fields")
func consumptionV2Payload() throws {
    let s = try #require(decode(v2json()))
    #expect(s.schemaVersion == .v2)
    #expect(s.turnId == 42)
    #expect(s.route == .codex)
    #expect(s.latency?.stt == 731)
    #expect(s.latency?.backend == 3054)
}

@Test("a version from the future keeps the fields it recognises")
func consumptionFutureVersion() throws {
    let json = """
    {"version": 3, "state": "SPEAKING", "since": 1788125256.0, "detail": "d",
     "pid": 97559, "turn_id": 7, "route": "CLAUDE",
     "latency_ms": {"stt": 800},
     "some_v3_field": {"nested": [1, 2, 3]}}
    """
    let s = try #require(decode(json))
    #expect(s.schemaVersion == .future(3))
    #expect(s.turnId == 7)
    #expect(s.route == .claude)
    #expect(s.latency?.stt == 800)
    #expect(s.phase == .speaking)
}

@Test("an unsupported version is never a reason to stop rendering")
func futureVersionStillRenders() throws {
    for v in ["3", "99", "\"two\"", "null", "-1", "{\"a\":1}"] {
        let s = try #require(decode(v2json(version: v)),
                             "version \(v) made the decoder give up")
        #expect(s.phase == .speaking)
        #expect(s.pid == 97559)
    }
}

// MARK: - turn identity

@Test("turn_id accepts positive integers only")
func turnIdValidation() throws {
    #expect(try #require(decode(v2json(turnId: "1"))).turnId == 1)
    for bad in ["0", "-1", "\"42\"", "true", "null", "3.5", "[]"] {
        let s = try #require(decode(v2json(turnId: bad)))
        #expect(s.turnId == nil, "turn_id \(bad) was accepted")
    }
}

@Test("turn identity is the pid and the id together, never the id alone")
func turnIdentityIsAPair() {
    let a = TurnIdentity(pid: 100, turnId: 42)
    let sameTurnNewProcess = TurnIdentity(pid: 200, turnId: 42)
    let sameProcessNextTurn = TurnIdentity(pid: 100, turnId: 43)

    #expect(a.differsFrom(sameTurnNewProcess),
            "a restart with the same turn number read as the same turn")
    #expect(a.differsFrom(sameProcessNextTurn))
    #expect(!a.differsFrom(TurnIdentity(pid: 100, turnId: 42)))
    #expect(!TurnIdentity(pid: 1, turnId: nil).isActiveTurn)
}

// MARK: - route

@Test("only the six dispatch labels are accepted as a route")
func routeValidation() throws {
    for token in ["LOCAL_FAST", "LOCAL_TOOL", "LOCAL", "WEB", "CODEX", "CLAUDE"] {
        let s = try #require(decode(v2json(route: "\"\(token)\"")))
        #expect(s.route?.rawValue == token)
    }
}

@Test("anything else becomes nil rather than reaching the badge")
func hostileRouteIsRefused() throws {
    // CONFIRMATION_REQUIRED is a valid JarvisPhase but is NOT a route: it is a
    // gate verdict, and nothing was dispatched for it.
    for bad in ["\"DROP TABLE\"", "\"codex\"", "\"\"", "true", "null", "42",
                "{\"a\":1}", "[\"CODEX\"]", "\"CONFIRMATION_REQUIRED\"",
                "\"IDLE\"", "\"SPEAKING\""] {
        let s = try #require(decode(v2json(route: bad)))
        #expect(s.route == nil, "route \(bad) was accepted")
    }
}

@Test("the published route wins over the inferred one")
func publishedRouteWins() {
    var tracker = RouteTracker()
    tracker.observe(.claude)                      // inference says CLAUDE
    let r = RouteResolution.resolve(published: .codex, tracker: tracker)
    #expect(r.phase == .codex)
    #expect(r.source == .authoritative)
    #expect(r.label == "CODEX")
}

@Test("with no published route the tracker is the fallback")
func trackerFallback() {
    var tracker = RouteTracker()
    tracker.observe(.claude)
    let r = RouteResolution.resolve(published: nil, tracker: tracker)
    #expect(r.phase == .claude)
    #expect(r.source == .inferred)
}

@Test("an unusable published route falls back rather than showing nothing")
func unknownPublishedFallsBack() throws {
    var tracker = RouteTracker()
    tracker.observe(.web)
    // A payload whose route did not survive decoding.
    let s = try #require(decode(v2json(route: "\"GPT5\"")))
    #expect(s.route == nil)
    let r = RouteResolution.resolve(published: s.route, tracker: tracker)
    #expect(r.phase == .web)
    #expect(r.source == .inferred)
}

@Test("no route from either source shows nothing at all")
func noRouteAtAll() {
    let r = RouteResolution.resolve(published: nil, tracker: RouteTracker())
    #expect(r.phase == nil)
    #expect(r.label == nil)
    #expect(r.source == .none)
}

@Test("a published/inferred disagreement is detected but not rendered as an error")
func mismatchIsDiagnosticOnly() {
    var tracker = RouteTracker()
    tracker.observe(.claude)
    #expect(RouteResolution.mismatch(published: .codex, tracker: tracker))
    // The published value still wins -- the viewer keeps drawing something sane.
    #expect(RouteResolution.resolve(published: .codex, tracker: tracker).phase
            == .codex)
    #expect(!RouteResolution.mismatch(published: .claude, tracker: tracker))
    #expect(!RouteResolution.mismatch(published: nil, tracker: tracker))
}

@Test("the tracker still clears at IDLE, so the v1 fallback stays correct")
func trackerClearsAtIdle() {
    var tracker = RouteTracker()
    tracker.observe(.codex)
    tracker.observe(.speaking)
    #expect(tracker.current == .codex)
    tracker.observe(.idle)
    #expect(RouteResolution.resolve(published: nil, tracker: tracker).phase == nil)
}

// MARK: - latency

@Test("latency values decode as integers, with zero distinct from missing")
func latencyDecoding() throws {
    let s = try #require(decode(v2json(latency: """
        {"wake_to_capture":251,"capture_duration":3557,"stt":731,
         "router":0,"backend":null}
        """)))
    let l = try #require(s.latency)
    #expect(l.wakeToCapture == 251)
    #expect(l.captureDuration == 3557)
    #expect(l.stt == 731)
    #expect(l.router == 0, "a measured zero was discarded")
    #expect(l.router != nil, "0 was conflated with missing")
    #expect(l.backend == nil)
}

@Test("router 0 ms renders as zero, not as pending")
func routerZeroRenders() throws {
    let l = try #require(decode(v2json(latency: #"{"router":0}"#))?.latency)
    let router = l.stages.first { $0.label == "Router" }
    #expect(router?.ms == 0)
    // The view renders nil as an em dash and a value as "N ms"; 0 must take the
    // second path.
    #expect(router?.ms.map { "\($0) ms" } == "0 ms")
}

@Test("implausible latency values are refused")
func latencyValidation() throws {
    let cases: [(String, String)] = [
        ("negative", "-1"), ("string", "\"731\""), ("bool", "true"),
        ("float", "731.5"), ("array", "[731]"), ("object", "{\"ms\":731}"),
        ("beyond an hour", "7200000"),
    ]
    for (name, value) in cases {
        let l = try #require(decode(v2json(latency: "{\"stt\":\(value)}"))?.latency)
        #expect(l.stt == nil, "\(name) latency was accepted")
    }
}

@Test("a malformed latency object yields nil, not a broken payload")
func malformedLatencyObject() throws {
    for bad in ["null", "42", "\"none\"", "[1,2,3]"] {
        let s = try #require(decode(v2json(latency: bad)))
        #expect(s.latency == nil)
        #expect(s.phase == .speaking, "the payload was damaged")
        #expect(s.turnId == 42)
    }
}

@Test("partial latency is normal: stages fill in as the turn runs")
func partialLatency() throws {
    // THINKING: capture is done, nothing after it has started.
    let thinking = try #require(decode(v2json(
        state: "THINKING",
        latency: #"{"wake_to_capture":251,"capture_duration":3557,"stt":null,"router":null,"backend":null}"#
    ))?.latency)
    #expect(thinking.wakeToCapture == 251)
    #expect(thinking.stt == nil)
    #expect(thinking.backend == nil)
    #expect(!thinking.isEmpty)

    // LISTENING: nothing yet.
    let listening = try #require(decode(v2json(
        state: "LISTENING",
        latency: #"{"wake_to_capture":null,"capture_duration":null,"stt":null,"router":null,"backend":null}"#
    ))?.latency)
    #expect(listening.isEmpty, "an all-null object should read as empty")
}

@Test("the 'so far' total excludes capture, which is the user talking")
func overheadExcludesCapture() {
    let l = LatencyMetrics(wakeToCapture: 251, captureDuration: 30000,
                           stt: 731, router: 2546, backend: 3054)
    #expect(l.measuredOverheadMs == 251 + 731 + 2546 + 3054)
    #expect(l.measuredOverheadMs != 251 + 30000 + 731 + 2546 + 3054)
    #expect(LatencyMetrics().measuredOverheadMs == nil)
}

@Test("stage labels do not present capture as latency")
func captureLabelIsHonest() {
    let labels = LatencyMetrics().stages.map(\.label)
    #expect(labels == ["Wake→Capture", "Capture", "STT", "Router", "Backend"])
}

// MARK: - stale turn protection

@Test("turn 41's route and timings cannot appear against turn 42")
func staleTurnProtection() throws {
    // Turn 41 dispatched to CODEX and has timings.
    let turn41 = try #require(decode(v2json(turnId: "41", route: "\"CODEX\"")))
    #expect(turn41.route == .codex)
    #expect(turn41.latency?.backend == 3054)

    // IDLE clears everything on the runtime side.
    let idle = try #require(decode("""
        {"version":2,"state":"IDLE","since":1788125300.0,
         "detail":"waiting for wake word","pid":97559,
         "turn_id":null,"route":null,
         "latency_ms":{"wake_to_capture":null,"capture_duration":null,
                       "stt":null,"router":null,"backend":null}}
        """))
    #expect(idle.turnId == nil)
    #expect(idle.route == nil)
    #expect(idle.latency?.isEmpty == true)

    // Turn 42 begins with nothing carried over.
    let turn42 = try #require(decode("""
        {"version":2,"state":"LISTENING","since":1788125310.0,
         "detail":"capturing utterance","pid":97559,
         "turn_id":42,"route":null,
         "latency_ms":{"wake_to_capture":null,"capture_duration":null,
                       "stt":null,"router":null,"backend":null}}
        """))
    #expect(turn42.turnId == 42)
    #expect(turn42.route == nil, "turn 41's route reached turn 42")
    #expect(turn42.latency?.isEmpty == true, "turn 41's timings reached turn 42")

    // And the inferred fallback must not resurrect it either.
    var tracker = RouteTracker()
    tracker.observe(.codex)                      // left over from turn 41
    #expect(turn41.turnIdentity.differsFrom(turn42.turnIdentity))
    tracker.reset()                              // what refreshDisclosure does
    #expect(RouteResolution.resolve(published: turn42.route,
                                    tracker: tracker).phase == nil)
}

@Test("a runtime restart mid-turn invalidates the inferred route")
func restartInvalidatesTracker() {
    let before = TurnIdentity(pid: 100, turnId: 3)
    let after = TurnIdentity(pid: 200, turnId: 3)   // same number, new process
    #expect(before.differsFrom(after))
}
