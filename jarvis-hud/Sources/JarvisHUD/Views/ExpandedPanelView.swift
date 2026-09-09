import SwiftUI

/// The expanded panel: what JARVIS is doing, and where it sent the work.
///
/// Two sections, and the split is the important part. The PRODUCTION section
/// shows only what `jarvis_state.json` actually contains — state, detail, pid,
/// elapsed, plus the route remembered from the runtime's own earlier states.
/// The FUTURE section is `#if DEBUG` and shows fields the backend does not
/// publish; a release build cannot render them because the type does not exist
/// in it.
///
/// Still viewer-only. CONFIRMATION_REQUIRED is text. There is no Allow, no
/// Deny, no retry, no control of any kind — the channel stays one-way, which is
/// the property that makes running this alongside a voice assistant safe.
struct ExpandedPanelView: View {
    let state: JarvisState
    let liveness: Liveness
    let status: ProviderStatus
    let route: RouteResolution
    #if DEBUG
    let context: FutureHUDContext?
    #endif
    let reduceMotion: Bool

    private var note: (text: String, isHUDFault: Bool)? {
        if let m = status.message { return (m, true) }
        if case .stale(let reason) = liveness { return (reason, false) }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider().opacity(0.18).padding(.vertical, 10)
            activity

            #if DEBUG
            if let context, hasFutureContent(context) {
                Divider().opacity(0.18).padding(.vertical, 10)
                futureSection(context)
            }
            #endif

            if state.phase == .confirmationRequired {
                Divider().opacity(0.18).padding(.vertical, 10)
                confirmationNotice
            }

            Divider().opacity(0.18).padding(.vertical, 10)
            diagnostics
        }
        .padding(16)
        .frame(width: 440, alignment: .leading)
        .background(panelBackground)
        .accessibilityElement(children: .contain)
    }

    // MARK: - production sections

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            OrbView(phase: state.phase, reduceMotion: reduceMotion)
                .frame(width: 46, height: 46)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text("JARVIS")
                    .font(.system(size: 10, weight: .semibold))
                    .tracking(2.6)
                    .foregroundStyle(.secondary)
                Text(state.phase == .unknown
                     ? "Unknown (\(state.rawState))"
                     : state.phase.label)
                    .font(.system(size: 17, weight: .medium, design: .rounded))
            }

            Spacer(minLength: 8)

            if route.label != nil {
                RouteBadgeView(route: route)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("JARVIS \(state.phase.label)"))
        .accessibilityValue(Text(route.label ?? "no route"))
    }

    private var activity: some View {
        VStack(alignment: .leading, spacing: 5) {
            sectionLabel("Activity")
            // Plain text. Never markdown, never a link, never a path.
            Text(verbatim: state.displayDetail.isEmpty
                 ? "—" : state.displayDetail)
                .font(.system(size: 13))
                .foregroundStyle(state.displayDetail.isEmpty ? .tertiary : .primary)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)

            if let note {
                Label {
                    Text(verbatim: note.text).font(.system(size: 11))
                } icon: {
                    Image(systemName: note.isHUDFault
                          ? "exclamationmark.circle" : "clock.badge.questionmark")
                }
                .foregroundStyle(note.isHUDFault ? .yellow : .orange)
                .lineLimit(2)
                .padding(.top, 2)
            }
        }
    }

    /// Read-only, and labelled as such. Phase 2A deliberately ships no way to
    /// answer this from the HUD; approval stays with the deterministic gate.
    private var confirmationNotice: some View {
        VStack(alignment: .leading, spacing: 5) {
            sectionLabel("Confirmation required")
            Text("A protected action is waiting. Respond to JARVIS directly — "
                 + "this panel cannot approve or deny.")
                .font(.system(size: 12))
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var diagnostics: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 16) {
                diagnostic("State", state.rawState)
                diagnostic("Elapsed", elapsedText)
                diagnostic("Turn", state.turnId.map(String.init) ?? "—")
                diagnostic("PID", state.pid.map(String.init) ?? "—")
                diagnostic("Schema", state.schemaVersion.label)
                Spacer(minLength: 0)
            }
            // Real timings from the state file. Absent stages show as an em
            // dash, never as 0 -- and `Router 0 ms` is a real reading on the
            // greeting fast path, so zero must render as zero.
            if let latency = state.latency, !latency.isEmpty {
                ProductionLatencyView(latency: latency)
            }
        }
    }

    private var elapsedText: String {
        let s = state.elapsed()
        if s < 1 { return "<1s" }
        if s < 60 { return "\(Int(s))s" }
        if s < 3600 { return "\(Int(s / 60))m" }
        return "\(Int(s / 3600))h"
    }

    // MARK: - future-only section (DEBUG)

    #if DEBUG
    private func hasFutureContent(_ c: FutureHUDContext) -> Bool {
        c.heardText != nil || c.responsePreview != nil
            || c.toolSummary != nil || c.latency != nil
    }

    @ViewBuilder
    private func futureSection(_ c: FutureHUDContext) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "flask")
                Text("NOT IN BACKEND — MOCK ONLY")
                    .font(.system(size: 9, weight: .semibold))
                    .tracking(1.2)
            }
            .foregroundStyle(.tertiary)

            if let heard = c.displayHeardText {
                labelled("Heard", heard)
            }
            if let reply = c.displayResponsePreview {
                labelled("Response", reply, lineLimit: 4)
            }
            if let tool = c.displayToolSummary {
                labelled("Activity", tool)
            }
            if let latency = c.latency {
                LatencyStripView(latency: latency)
            }
        }
    }

    private func labelled(_ title: String, _ value: String,
                          lineLimit: Int = 2) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            sectionLabel(title)
            Text(verbatim: value)              // verbatim: markup stays inert
                .font(.system(size: 12))
                .lineLimit(lineLimit)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
    #endif

    // MARK: - shared bits

    private func sectionLabel(_ s: String) -> some View {
        Text(s.uppercased())
            .font(.system(size: 9, weight: .semibold))
            .tracking(1.2)
            .foregroundStyle(.tertiary)
    }

    private func diagnostic(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title.uppercased())
                .font(.system(size: 8, weight: .semibold))
                .tracking(1.0)
                .foregroundStyle(.tertiary)
            Text(verbatim: value)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
        }
    }

    private var panelBackground: some View {
        RoundedRectangle(cornerRadius: 20, style: .continuous)
            .fill(.ultraThinMaterial)
            .overlay {
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .stroke(.white.opacity(0.10), lineWidth: 1)
            }
    }
}

/// The route, as words. Colour is decoration; the text is the information.
struct RouteBadgeView: View {
    let route: RouteResolution

    var body: some View {
        if let label = route.label {
            HStack(spacing: 5) {
                if let symbol = route.symbolName {
                    Image(systemName: symbol).font(.system(size: 9))
                }
                Text(label)
                    .font(.system(size: 9, weight: .semibold, design: .rounded))
                    .tracking(0.8)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background {
                Capsule().fill(.white.opacity(0.08))
                    .overlay { Capsule().stroke(.white.opacity(0.14), lineWidth: 1) }
            }
            .foregroundStyle(.secondary)
            .accessibilityLabel(Text("Route \(label)"))
            .accessibilityValue(Text(route.source == .inferred
                                     ? "inferred from state transitions"
                                     : "published by the runtime"))
        }
    }
}

/// Per-stage timings from `latency_ms`. Production data, not the DEBUG mock.
///
/// Deliberately a labelled list rather than the proportional strip the mock
/// uses: these arrive incrementally, so a bar chart of a half-finished turn
/// would show shifting proportions of a total that is not known yet.
struct ProductionLatencyView: View {
    let latency: LatencyMetrics

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("TIMINGS")
                    .font(.system(size: 9, weight: .semibold)).tracking(1.2)
                Spacer()
                if let overhead = latency.measuredOverheadMs {
                    Text("\(overhead) ms so far")
                        .font(.system(size: 9, design: .monospaced))
                }
            }
            .foregroundStyle(.tertiary)

            ForEach(latency.stages, id: \.label) { stage in
                HStack(spacing: 8) {
                    Text(stage.label)
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                        .frame(width: 96, alignment: .leading)
                    // nil -> em dash. 0 -> "0 ms". They are different readings.
                    Text(stage.ms.map { "\($0) ms" } ?? "—")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(stage.ms == nil ? .tertiary : .primary)
                    Spacer(minLength: 0)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("Timings"))
        .accessibilityValue(Text(latency.stages
            .map { "\($0.label) \($0.ms.map { "\($0) milliseconds" } ?? "pending")" }
            .joined(separator: ", ")))
    }
}

#if DEBUG
/// Four stages as one proportional strip. The question it answers is "where is
/// the time going", which needs relative width and a number — not a chart.
struct LatencyStripView: View {
    let latency: LatencyBreakdown

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 0) {
                Text("LATENCY")
                    .font(.system(size: 9, weight: .semibold))
                    .tracking(1.2)
                Spacer()
                Text(String(format: "%.1fs total", latency.total))
                    .font(.system(size: 9, design: .monospaced))
            }
            .foregroundStyle(.tertiary)

            GeometryReader { geo in
                HStack(spacing: 2) {
                    ForEach(latency.stages, id: \.name) { stage in
                        RoundedRectangle(cornerRadius: 2)
                            .fill(.secondary.opacity(0.35))
                            .frame(width: max(2, geo.size.width * stage.fraction))
                    }
                }
            }
            .frame(height: 5)

            HStack(spacing: 12) {
                ForEach(latency.stages, id: \.name) { stage in
                    Text("\(stage.name) \(String(format: "%.1fs", stage.seconds))")
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("Latency breakdown"))
        .accessibilityValue(Text(latency.stages
            .map { "\($0.name) \(String(format: "%.1f", $0.seconds)) seconds" }
            .joined(separator: ", ")))
    }
}
#endif
