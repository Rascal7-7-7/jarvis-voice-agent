import Foundation
import Testing
@testable import JarvisHUD

/// Can this HUD read a state file written by a NEWER runtime?
///
/// It has to, because the two ship separately: the runtime is restarted by a
/// watchdog and the HUD is launched by hand, so a v2 runtime will run against
/// this HUD build. The decoder reads four keys and ignores the rest, which
/// makes the answer yes by construction — but "by construction" was never
/// tested, and a schema change is exactly the wrong moment to be relying on an
/// untested property.
///
/// No HUD logic is changed by this file. It only pins behaviour that already
/// exists, so a future refactor cannot quietly break forward compatibility.

/// The Phase 2B Minimal V2 payload, plus a field from a version after that.
private let v2Payload = """
{
  "version": 2,
  "state": "SPEAKING",
  "since": 1234,
  "detail": "test",
  "pid": 123,
  "turn_id": 42,
  "route": "CODEX",
  "latency_ms": {
    "wake_to_capture": 251,
    "capture_duration": 3200,
    "stt": 800,
    "router": 2500,
    "backend": 3100
  },
  "future_unknown_field": "ignored"
}
"""

@Test("a v2 payload decodes, and the four v1 fields are correct")
func v2PayloadDecodesOnAV1Reader() throws {
    let state = try #require(JarvisState.decode(from: Data(v2Payload.utf8)))
    #expect(state.phase == .speaking)
    #expect(state.rawState == "SPEAKING")
    #expect(state.detail == "test")
    #expect(state.pid == 123)
    #expect(state.since == Date(timeIntervalSince1970: 1234))
}

@Test("the v1 fields are identical whether or not the v2 fields are present")
func v1FieldsAreUnaffectedByV2Fields() throws {
    let minimal = #"{"state":"SPEAKING","since":1234,"detail":"test","pid":123}"#
    let plain = try #require(JarvisState.decode(from: Data(minimal.utf8)))
    let rich = try #require(JarvisState.decode(from: Data(v2Payload.utf8)))

    // This assertion used to be `plain == rich`: before Phase 2B the decoder
    // ignored the v2 keys entirely, so the two states were equal. Consuming
    // those fields is exactly the change that makes them differ, so the
    // comparison narrows to the four fields that must NOT be affected.
    #expect(plain.phase == rich.phase)
    #expect(plain.rawState == rich.rawState)
    #expect(plain.since == rich.since)
    #expect(plain.detail == rich.detail)
    #expect(plain.pid == rich.pid)

    // And the difference is confined to the new fields.
    #expect(plain.schemaVersion == .v1)
    #expect(rich.schemaVersion == .v2)
    #expect(plain.turnId == nil)
    #expect(rich.turnId == 42)

    // The unknown future key is still ignored by both.
    #expect(rich.route == .codex)
}

@Test("a v1 payload with no version still decodes")
func v1PayloadStillDecodes() throws {
    let v1 = #"{"state":"IDLE","since":1234,"detail":"waiting","pid":7}"#
    let state = try #require(JarvisState.decode(from: Data(v1.utf8)))
    #expect(state.phase == .idle)
    #expect(state.pid == 7)
}

@Test("a version from the future is not a reason to refuse the file")
func unknownVersionDoesNotBlank() throws {
    for version in ["3", "99", "\"two\"", "null", "-1"] {
        let json = """
        {"version": \(version), "state": "THINKING", "since": 1234,
         "detail": "transcribing", "pid": 5}
        """
        let state = try #require(JarvisState.decode(from: Data(json.utf8)),
                                 "version \(version) made the decoder give up")
        #expect(state.phase == .thinking)
    }
}

@Test("a hostile shape in a new field cannot break the known fields")
func hostileNewFieldsAreIgnored() throws {
    // If a future field arrives as the wrong type, or enormous, the four fields
    // this build actually uses must still come through.
    let json = """
    {"state": "ERROR", "since": 1234, "detail": "mic silent", "pid": 9,
     "turn_id": "not-a-number",
     "route": {"nested": "object"},
     "latency_ms": [1, 2, 3],
     "version": {"unexpected": true},
     "padding": "\(String(repeating: "x", count: 2000))"}
    """
    let state = try #require(JarvisState.decode(from: Data(json.utf8)))
    #expect(state.phase == .error)
    #expect(state.detail == "mic silent")
    #expect(state.pid == 9)
}

@Test("the decoder's key surface is the v2 schema, and nothing beyond it")
func decoderSurfaceIsTheV2Schema() {
    // Written when the decoder read only the four v1 keys, and updated when
    // Phase 2B taught it the four v2 ones -- which is what this guard is for:
    // it forced the change to be noticed rather than passing silently. Anything
    // NOT listed here is still ignored, which is what keeps a future schema
    // from breaking this build.
    let source = #filePath
        .replacingOccurrences(of: "Tests/JarvisHUDTests/SchemaCompatibilityTests.swift",
                              with: "Sources/JarvisHUD/Models/JarvisState.swift")
    guard let text = try? String(contentsOfFile: source, encoding: .utf8) else {
        Issue.record("could not read JarvisState.swift at \(source)")
        return
    }
    let keys = text.components(separatedBy: "dict[\"").dropFirst()
        .compactMap { $0.components(separatedBy: "\"").first }
    #expect(Set(keys) == ["state", "since", "detail", "pid",
                          "version", "turn_id", "route", "latency_ms"],
            "the decoder now reads \(Set(keys).sorted())")
}
