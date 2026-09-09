import CoreGraphics

/// Where the floating panel should sit, given a remembered position and the
/// screens that actually exist right now.
///
/// Pure geometry on purpose: no AppKit, no UserDefaults, no window. A HUD that
/// restores itself onto a display that has since been unplugged is invisible
/// AND unreachable — there is no Dock icon to click and no window to drag back
/// — so this is worth being able to test directly rather than by plugging
/// monitors in.
///
/// Coordinates are AppKit's: origin bottom-left, y increasing upward.
enum PanelPlacement {

    /// How much of the panel has to remain on a screen for a saved position to
    /// count as usable. A sliver at the edge of a display is not "visible" in
    /// any sense the user cares about, but demanding the whole panel would
    /// reject positions people deliberately chose.
    static let minimumVisibleFraction: CGFloat = 0.5

    /// Inset from the top-right corner used when there is nothing to restore.
    static let defaultInset = CGSize(width: 20, height: 42)

    /// Resolve the origin to use.
    ///
    /// - Parameters:
    ///   - saved: the remembered origin, or nil on first launch.
    ///   - size: the panel's size.
    ///   - visibleFrames: `visibleFrame` of every screen currently attached.
    ///   - mainFrame: `visibleFrame` of the main screen, if there is one.
    static func resolve(saved: CGPoint?,
                        size: CGSize,
                        visibleFrames: [CGRect],
                        mainFrame: CGRect?) -> CGPoint {
        let fallback = defaultOrigin(size: size,
                                     screen: mainFrame ?? visibleFrames.first)
        guard let saved else { return fallback }
        guard !visibleFrames.isEmpty else { return fallback }

        let rect = CGRect(origin: saved, size: size)

        // Still mostly on a screen that exists: keep it, but pull it fully
        // inside so a partly off-edge panel comes back whole.
        if let host = visibleFrames.first(where: { visibleFraction(of: rect, on: $0) >= minimumVisibleFraction }) {
            return clamp(rect, into: host).origin
        }

        // The display it was on is gone, or it drifted off every screen.
        // Recover onto whichever screen is closest rather than teleporting to
        // a corner, so the panel stays near where the user last put it.
        let target = nearestFrame(to: rect, among: visibleFrames) ?? mainFrame
        guard let target else { return fallback }
        return clamp(rect, into: target).origin
    }

    /// Fraction of the panel's area lying inside `screen`.
    static func visibleFraction(of rect: CGRect, on screen: CGRect) -> CGFloat {
        let area = rect.width * rect.height
        guard area > 0 else { return 0 }
        let overlap = rect.intersection(screen)
        guard !overlap.isNull else { return 0 }
        return (overlap.width * overlap.height) / area
    }

    /// Move `rect` the shortest distance needed to sit entirely within `screen`.
    ///
    /// A panel larger than the screen is pinned to the top-left rather than
    /// being resized: the caller owns the size, and silently changing it would
    /// be a surprise.
    static func clamp(_ rect: CGRect, into screen: CGRect) -> CGRect {
        var origin = rect.origin
        origin.x = min(max(origin.x, screen.minX),
                       max(screen.minX, screen.maxX - rect.width))
        origin.y = min(max(origin.y, screen.minY),
                       max(screen.minY, screen.maxY - rect.height))
        return CGRect(origin: origin, size: rect.size)
    }

    /// Top-right of the given screen, inset. The corner a HUD belongs in.
    static func defaultOrigin(size: CGSize, screen: CGRect?) -> CGPoint {
        guard let screen else { return .zero }
        return CGPoint(x: screen.maxX - size.width - defaultInset.width,
                       y: screen.maxY - size.height - defaultInset.height)
    }

    private static func nearestFrame(to rect: CGRect,
                                     among frames: [CGRect]) -> CGRect? {
        frames.min { a, b in
            distance(from: rect.center, to: a.center)
                < distance(from: rect.center, to: b.center)
        }
    }

    private static func distance(from a: CGPoint, to b: CGPoint) -> CGFloat {
        let dx = a.x - b.x, dy = a.y - b.y
        return dx * dx + dy * dy          // squared: ordering is all we need
    }
}

private extension CGRect {
    var center: CGPoint { CGPoint(x: midX, y: midY) }
}
