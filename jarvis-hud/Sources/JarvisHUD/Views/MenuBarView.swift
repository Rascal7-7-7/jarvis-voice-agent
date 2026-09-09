import SwiftUI

/// Menu bar contents.
///
/// The Mock State submenu is a PHASE 1A TEST AFFORDANCE. It exists so all
/// fourteen states — including the four the runtime has never yet emitted — can
/// be inspected without waiting for them to occur naturally. It is gated behind
/// `#if DEBUG` so a release build cannot ship a menu that fakes system state.
struct MenuBarView: View {
    @Bindable var controller: AppDelegate

    var body: some View {
        Text("JARVIS — \(controller.snapshot.phase.label)")
        if !controller.snapshot.displayDetail.isEmpty {
            Text(verbatim: controller.snapshot.displayDetail)
        }
        if let m = controller.status.message { Text(verbatim: m) }
        Divider()

        Button(controller.panelVisible ? "Hide HUD" : "Show HUD") {
            controller.setPanel(visible: !controller.panelVisible)
        }
        Button(controller.isExpanded ? "Collapse" : "Show Expanded") {
            controller.setExpanded(!controller.isExpanded)
        }
        if let r = controller.route.label {
            Text("Route: \(r) (\(controller.route.source.rawValue))")
        }

        #if DEBUG
        // Comparing the three disclosure rules needs to happen on real
        // transitions, so the switch is here rather than in a config file --
        // and it is DEBUG-only, because a release build should ship one
        // decided behaviour, not a menu of them.
        Divider()
        Menu("Auto-expand policy") {
            ForEach(DisclosurePolicy.allCases, id: \.rawValue) { p in
                Button((controller.policy == p ? "● " : "  ") + p.rawValue) {
                    controller.policy = p
                }
            }
        }
        Menu("Collapse delay") {
            ForEach(CollapseDelay.allCases, id: \.rawValue) { d in
                Button((controller.collapseDelay == d ? "● " : "  ") + d.rawValue) {
                    controller.collapseDelay = d
                }
            }
        }
        #endif

        #if DEBUG
        if let provider = controller.mockProvider {
        Divider()
        Menu("Mock State") {
            ForEach(JarvisPhase.allCases, id: \.rawValue) { phase in
                Button(phase == .unknown
                       ? "UNKNOWN (unrecognised value)"
                       : phase.rawValue) {
                    provider.set(phase, detail: Self.sampleDetail(for: phase))
                }
            }
        }
        Menu("Mock Sequence") {
            Button("A — Local fast") {
                provider.runSequence(MockStateProvider.sequenceLocalFast)
            }
            Button("B — Codex") {
                provider.runSequence(MockStateProvider.sequenceCodex)
            }
            Button("C — Claude") {
                provider.runSequence(MockStateProvider.sequenceClaude)
            }
            Button("D — Error") {
                provider.runSequence(MockStateProvider.sequenceError)
            }
            Button("E — Confirmation (read-only)") {
                provider.runSequence(MockStateProvider.sequenceConfirmation)
            }
            Divider()
            // V2 payloads through the real decoder: explicit route and latency
            // appearing stage by stage, as production publishes them.
            Button("V2 — Codex turn (production schema)") {
                provider.runV2Turn()
            }
            Button("V2 — loop") { provider.runV2Turn(loop: true) }
            Divider()
            Button("Happy path (CODEX turn)") {
                provider.runSequence(MockStateProvider.happyPath)
            }
            Button("Error + gate refusal") {
                provider.runSequence(MockStateProvider.errorPath)
            }
            Button("Rapid transitions (flicker test)") {
                provider.runSequence(MockStateProvider.flickerPath)
            }
            Button("Loop sequence B (Codex)") {
                provider.runSequence(MockStateProvider.sequenceCodex, loop: true)
            }
            if provider.isRunningSequence {
                Button("Stop sequence") { provider.stopSequence() }
            }
        }
        Menu("Hostile Input") {
            Button("Oversized detail (5000 chars)") {
                provider.injectRaw(Self.raw(state: "SPEAKING",
                                            detail: String(repeating: "あ", count: 5000)))
            }
            Button("Malformed JSON") {
                provider.injectRaw(Data("{ not json".utf8))
            }
            Button("Missing fields") {
                provider.injectRaw(Data(#"{"state":"THINKING"}"#.utf8))
            }
            Button("Empty detail") {
                provider.injectRaw(Self.raw(state: "IDLE", detail: ""))
            }
            Button("since in the far future") {
                provider.injectRaw(Data(
                    #"{"state":"THINKING","since":4102444800,"detail":"future","pid":1}"#.utf8))
            }
            Button("Invalid pid (-1)") {
                provider.injectRaw(Data(
                    #"{"state":"IDLE","since":1788044091.0,"detail":"bad pid","pid":-1}"#.utf8))
            }
            Button("Control characters in detail") {
                provider.injectRaw(Self.raw(state: "ERROR",
                                            detail: "line1\nline2\u{0007}\u{001B}[31mred"))
            }
        }
        }
        #endif

        Divider()
        Button("Quit JARVIS HUD") { controller.quit() }
            .keyboardShortcut("q")
    }

    private static func raw(state: String, detail: String) -> Data {
        let obj: [String: Any] = ["state": state,
                                  "since": Date().timeIntervalSince1970,
                                  "detail": detail, "pid": 4242]
        return (try? JSONSerialization.data(withJSONObject: obj)) ?? Data()
    }

    private static func sampleDetail(for phase: JarvisPhase) -> String {
        switch phase {
        case .offline: return "wake mic owned by another surface"
        case .idle: return "waiting for wake word"
        case .listening: return "capturing utterance"
        case .thinking: return "transcribing"
        case .routing: return "CODEX via llm"
        case .confirmationRequired: return "DESTRUCTIVE"
        case .speaking: return "解析が完了しました。重要な点を三つ報告します。"
        case .error: return "mic silent; restarting listener"
        case .unknown: return "state this build does not know"
        default: return "working"
        }
    }
}
