import SwiftUI

/// The orb. Concentric rings, a soft glow, and motion ONLY while listening,
/// thinking or speaking.
///
/// Everything else is a static draw that schedules no work — an idle HUD that
/// animates is an idle HUD that costs battery for nothing.
struct OrbView: View {
    let phase: JarvisPhase
    let reduceMotion: Bool

    /// Motion is suppressed entirely under Reduce Motion; the state stays
    /// legible from the ring weight and the text label beside it.
    private var animates: Bool { phase.isAnimated && !reduceMotion }

    private var tint: Color {
        switch phase {
        case .offline: return .secondary
        case .idle: return Color(red: 0.35, green: 0.65, blue: 0.95)
        case .listening: return Color(red: 0.30, green: 0.85, blue: 0.75)
        case .thinking, .routing: return Color(red: 0.45, green: 0.60, blue: 1.00)
        case .localFast, .localTool, .local: return Color(red: 0.40, green: 0.75, blue: 0.95)
        case .web: return Color(red: 0.35, green: 0.80, blue: 0.60)
        case .codex, .claude: return Color(red: 0.65, green: 0.55, blue: 1.00)
        case .confirmationRequired: return Color(red: 0.95, green: 0.70, blue: 0.25)
        case .speaking: return Color(red: 0.35, green: 0.80, blue: 1.00)
        case .error: return Color(red: 0.95, green: 0.40, blue: 0.40)
        case .unknown: return .secondary
        }
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: animates ? 1.0 / 30.0 : nil,
                                paused: !animates)) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            Canvas { ctx, size in
                let c = CGPoint(x: size.width / 2, y: size.height / 2)
                let base = min(size.width, size.height) * 0.30

                // Outer glow — dimmed when offline so "not running" reads at a
                // glance without relying on colour alone.
                let glowR = base * (phase == .offline ? 1.15 : 1.45)
                ctx.fill(Path(ellipseIn: CGRect(x: c.x - glowR, y: c.y - glowR,
                                                width: glowR * 2, height: glowR * 2)),
                         with: .radialGradient(
                            Gradient(colors: [tint.opacity(phase == .offline ? 0.10 : 0.28),
                                              tint.opacity(0)]),
                            center: c, startRadius: base * 0.2, endRadius: glowR))

                // Concentric rings. Amplitude is zero unless animating, so the
                // same code path draws the static case.
                let pulse = animates ? sin(t * 2.2) : 0
                for (i, mult) in [1.0, 1.28, 1.56].enumerated() {
                    let wobble: Double
                    switch phase {
                    case .listening: wobble = pulse * 0.06 * Double(i + 1)
                    case .thinking, .speaking: wobble = pulse * 0.04 * Double(i + 1)
                    default: wobble = 0
                    }
                    let r = base * mult * (1 + wobble)
                    let rect = CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)
                    ctx.stroke(Path(ellipseIn: rect),
                               with: .color(tint.opacity(0.55 - Double(i) * 0.15)),
                               lineWidth: i == 0 ? 2.0 : 1.0)
                }

                // Thinking: one arc sweeping the outer ring. A rotating arc is
                // cheaper and calmer than a spinner made of many parts.
                if phase == .thinking || phase == .routing, animates {
                    let r = base * 1.56
                    var arc = Path()
                    arc.addArc(center: c, radius: r,
                               startAngle: .radians(t * 2.0),
                               endAngle: .radians(t * 2.0 + .pi * 0.5),
                               clockwise: false)
                    ctx.stroke(arc, with: .color(tint), lineWidth: 2.5)
                }

                // Speaking: a small symmetric bar pair, standing in for a
                // waveform without the cost of real audio analysis (the HUD has
                // no access to audio, and should not).
                if phase == .speaking, animates {
                    for k in -2...2 {
                        let phaseOff = Double(k) * 0.7
                        let h = base * (0.25 + 0.22 * abs(sin(t * 3.0 + phaseOff)))
                        let x = c.x + CGFloat(k) * base * 0.22
                        let rect = CGRect(x: x - 1.5, y: c.y - h, width: 3, height: h * 2)
                        ctx.fill(Path(roundedRect: rect, cornerRadius: 1.5),
                                 with: .color(tint.opacity(0.85)))
                    }
                } else {
                    // Solid core for every other state.
                    let r = base * 0.42
                    ctx.fill(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r,
                                                    width: r * 2, height: r * 2)),
                             with: .color(tint.opacity(phase == .offline ? 0.35 : 0.9)))
                }
            }
        }
        .accessibilityHidden(true)   // the text label carries the meaning
    }
}
