import Foundation
import Testing
@testable import JarvisHUD

/// Writes to a TEMP directory only. The production state file is never opened
/// by these tests, let alone written.
private func makeTempDir() throws -> URL {
    let dir = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("jarvis-hud-test-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir
}

/// Reproduces exactly what the runtime does: write `.tmp`, then `os.replace()`.
/// In Swift that is `replaceItemAt`, which is the same rename-over semantics and
/// therefore swaps the inode the same way.
private func atomicWrite(_ json: String, to file: URL) throws {
    let tmp = file.deletingLastPathComponent()
        .appendingPathComponent("\(file.lastPathComponent).tmp")
    try Data(json.utf8).write(to: tmp)
    _ = try FileManager.default.replaceItemAt(file, withItemAt: tmp)
}

private func stateJSON(_ state: String, detail: String = "d", pid: Int = 1) -> String {
    #"{"state":"\#(state)","since":\#(Date().timeIntervalSince1970),"detail":"\#(detail)","pid":\#(pid)}"#
}

@Test("initial read happens without waiting for a filesystem event")
@MainActor
func initialLoadReadsImmediately() throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }
    let file = dir.appendingPathComponent("jarvis_state.json")
    try atomicWrite(stateJSON("LISTENING", detail: "capturing utterance"), to: file)

    let p = FileStateProvider(fileURL: file)
    p.readNow()                       // exactly what start() does first
    #expect(p.displayed.phase == .listening)
    #expect(p.displayed.detail == "capturing utterance")
    #expect(p.status == .ok)
}

@Test("the inode really does change on atomic replace")
@MainActor
func atomicReplaceSwapsInode() throws {
    // If this ever stops being true, watching the file directly would be safe
    // and the directory-watch design could be simplified. It is asserted rather
    // than assumed.
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }
    let file = dir.appendingPathComponent("jarvis_state.json")
    try atomicWrite(stateJSON("IDLE"), to: file)

    func inode(_ u: URL) throws -> UInt64 {
        let a = try FileManager.default.attributesOfItem(atPath: u.path)
        return (a[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0
    }
    let before = try inode(file)
    try atomicWrite(stateJSON("THINKING"), to: file)
    let after = try inode(file)
    #expect(before != after, "atomic replace did not swap the inode")
}

@Test("provider keeps reading correctly across 120 atomic replaces")
@MainActor
func survivesRepeatedAtomicReplace() throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }
    let file = dir.appendingPathComponent("jarvis_state.json")
    try atomicWrite(stateJSON("IDLE"), to: file)

    let p = FileStateProvider(fileURL: file)
    p.readNow()
    #expect(p.displayed.phase == .idle)

    // The event path is exercised separately in the CLI stress test; here the
    // point is that no descriptor goes stale across many replacements, which is
    // what a file-level watcher would get wrong.
    let cycle: [JarvisPhase] = [.listening, .thinking, .routing, .localFast,
                                .speaking, .idle]
    for i in 0..<120 {
        let phase = cycle[i % cycle.count]
        try atomicWrite(stateJSON(phase.rawValue, detail: "n=\(i)"), to: file)
        p.readNow()
        #expect(p.status == .ok, "read \(i) failed")
        // urgent phases bypass the presenter, so only they are guaranteed to be
        // showing immediately; the rest may be held by the dwell window.
        if phase.isUrgent {
            #expect(p.displayed.phase == phase, "read \(i) showed the wrong state")
        }
    }
    // Whatever the presenter is showing, the final read must have succeeded.
    #expect(p.status == .ok)
}

@Test("an oversized file is refused as a HUD fault, not a runtime ERROR")
@MainActor
func oversizedFileIsHUDFault() throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }
    let file = dir.appendingPathComponent("jarvis_state.json")
    try atomicWrite(stateJSON("IDLE"), to: file)

    let p = FileStateProvider(fileURL: file)
    p.readNow()
    #expect(p.displayed.phase == .idle)

    let huge = String(repeating: "x", count: JarvisState.maxFileBytes + 10)
    try atomicWrite(#"{"state":"IDLE","detail":"\#(huge)"}"#, to: file)
    p.readNow()

    // The important part: the displayed state is untouched and the runtime is
    // NOT reported as being in ERROR.
    #expect(p.displayed.phase == .idle)
    #expect(p.displayed.phase != .error)
    if case .dataError = p.status {} else { Issue.record("expected a dataError") }
    #expect(p.status.message?.hasPrefix("HUD DATA ERROR") == true)
}

@Test("malformed JSON keeps the last good state and reports a HUD fault")
@MainActor
func malformedKeepsLastGoodState() throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }
    let file = dir.appendingPathComponent("jarvis_state.json")
    try atomicWrite(stateJSON("SPEAKING", detail: "こんにちは"), to: file)

    let p = FileStateProvider(fileURL: file)
    p.readNow()
    #expect(p.displayed.phase == .speaking)

    try atomicWrite("{ not json at all", to: file)
    p.readNow()
    #expect(p.displayed.phase == .speaking, "a bad read blanked the HUD")
    #expect(p.status.isHUDFault)
    #expect(p.displayed.phase != .error)
}

@Test("a missing file is 'unavailable', which is not a runtime ERROR")
@MainActor
func missingFileIsUnavailable() throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }
    let file = dir.appendingPathComponent("does_not_exist.json")

    let p = FileStateProvider(fileURL: file)
    p.readNow()
    if case .unavailable = p.status {} else { Issue.record("expected unavailable") }
    #expect(p.displayed.phase != .error)
}

@Test("an unknown state from the file renders as UNKNOWN, not a crash")
@MainActor
func unknownStateFromFile() throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }
    let file = dir.appendingPathComponent("jarvis_state.json")
    try atomicWrite(stateJSON("SOME_STATE_FROM_THE_FUTURE"), to: file)

    let p = FileStateProvider(fileURL: file)
    p.readNow()
    #expect(p.displayed.phase == .unknown)
    #expect(p.displayed.rawState == "SOME_STATE_FROM_THE_FUTURE")
    #expect(p.status == .ok)      // the file was fine; this build just doesn't know the state
}

@Test("a dead pid is stale; the running test process is live")
@MainActor
func pidLiveness() throws {
    // pid 1 (launchd) always exists. It may be owned by root, which makes
    // kill(1, 0) return EPERM -- and EPERM still means "the process exists".
    #expect(Liveness.processIsRunning(1))
    #expect(Liveness.processIsRunning(Int(ProcessInfo.processInfo.processIdentifier)))
    #expect(!Liveness.processIsRunning(0))
    #expect(!Liveness.processIsRunning(-5))
    // A pid far above the system maximum will not be in use.
    #expect(!Liveness.processIsRunning(999_999))
}

@Test("liveness ignores file age entirely")
@MainActor
func agedFileIsStillLive() throws {
    // A long-idle JARVIS legitimately leaves the file untouched for hours.
    // Age must not be able to force an OFFLINE verdict on its own.
    let verdict = Liveness.evaluate(pid: 1, age: nil, processExists: { _ in true })
    if case .live = verdict {} else { Issue.record("a live pid should be .live regardless of age") }
}
