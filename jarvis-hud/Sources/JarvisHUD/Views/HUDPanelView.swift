import SwiftUI

/// The COMPACT panel: an orb, the state, and one line of detail.
///
/// Phase 2A adds the expanded panel alongside this, not instead of it. Compact
/// stays the default because this thing floats over the user's work all day,
/// and most turns are over in a few seconds.
///
/// Display only: CONFIRMATION_REQUIRED is text with NO Allow/Deny buttons.
/// Giving the viewer a way to answer would make the channel bidirectional,
/// which is the one property that makes this safe to run.
struct HUDPanelView: View {
    let state: JarvisState
    let liveness: Liveness
    let status: ProviderStatus
    var route: RouteResolution = RouteResolution(phase: nil, source: .none)
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var expanded: Bool {
        state.phase != .idle && state.phase != .offline
    }

    /// A HUD-side read problem is shown as its own message, never as the
    /// runtime's ERROR state -- reporting a fault in a healthy JARVIS because
    /// the viewer could not parse a file would be a lie.
    private var note: (text: String, isHUDFault: Bool)? {
        if let m = status.message { return (m, true) }
        if case .stale(let reason) = liveness { return (reason, false) }
        return nil
    }

    var body: some View {
        HStack(spacing: expanded ? 14 : 0) {
            OrbView(phase: state.phase, reduceMotion: reduceMotion)
                .frame(width: expanded ? 76 : 54, height: expanded ? 76 : 54)

            if expanded {
                VStack(alignment: .leading, spacing: 3) {
                    Text("JARVIS")
                        .font(.system(size: 10, weight: .semibold))
                        .tracking(2.4)
                        .foregroundStyle(.secondary)

                    // Route states show the route name; everything else shows
                    // its own label. Text always, never colour alone.
                    Text(state.phase == .unknown
                         ? "Unknown (\(state.rawState))"
                         : state.phase.label)
                        .font(.system(size: 15, weight: .medium, design: .rounded))
                        .foregroundStyle(.primary)

                    if !state.displayDetail.isEmpty {
                        // Plain Text. Not markdown, not a link, not a path.
                        Text(verbatim: state.displayDetail)
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    if let note {
                        Text(verbatim: note.text)
                            .font(.system(size: 10))
                            .foregroundStyle(note.isHUDFault ? .yellow : .orange)
                            .lineLimit(2)
                    }

                    // The route survives into SPEAKING, where the state alone
                    // no longer says where the work went.
                    if route.label != nil, state.phase != .idle {
                        RouteBadgeView(route: route).padding(.top, 2)
                    }
                }
                .frame(width: 230, alignment: .leading)
                .transition(.opacity)
            }
        }
        .padding(.horizontal, expanded ? 16 : 10)
        .padding(.vertical, expanded ? 14 : 10)
        .background {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(.ultraThinMaterial)
                .overlay {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(.white.opacity(0.10), lineWidth: 1)
                }
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.22), value: expanded)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("JARVIS \(state.phase.label)"))
        .accessibilityValue(Text(state.displayDetail.isEmpty
                                 ? "no detail" : state.displayDetail))
    }
}
