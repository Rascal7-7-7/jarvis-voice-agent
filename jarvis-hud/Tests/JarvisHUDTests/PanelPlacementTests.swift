import CoreGraphics
import Testing
@testable import JarvisHUD

/// The failure these guard against is nasty in a specific way: a menu-bar agent
/// has no Dock icon and no window in the Window menu, so a panel restored off
/// every screen cannot be dragged back. The user's only recovery would be
/// deleting the app's defaults from the command line.
private let size = CGSize(width: 380, height: 118)
private let builtIn = CGRect(x: 0, y: 0, width: 1728, height: 1080 - 38)
private let external = CGRect(x: 1728, y: 200, width: 2560, height: 1440)

@Test("first launch lands in the top-right of the main screen")
func firstLaunchDefault() {
    let p = PanelPlacement.resolve(saved: nil, size: size,
                                   visibleFrames: [builtIn], mainFrame: builtIn)
    #expect(p.x + size.width <= builtIn.maxX)
    #expect(p.y + size.height <= builtIn.maxY)
    // Top-right, not some other corner.
    #expect(p.x > builtIn.midX)
    #expect(p.y > builtIn.midY)
}

@Test("a saved position fully on screen is kept exactly")
func savedPositionKept() {
    let saved = CGPoint(x: 400, y: 300)
    let p = PanelPlacement.resolve(saved: saved, size: size,
                                   visibleFrames: [builtIn], mainFrame: builtIn)
    #expect(p == saved)
}

@Test("a position on a display that is gone comes back on an existing one")
func unpluggedDisplayRecovers() {
    // Saved while an external monitor was attached, restored without it.
    let saved = CGPoint(x: 3000, y: 900)
    let p = PanelPlacement.resolve(saved: saved, size: size,
                                   visibleFrames: [builtIn], mainFrame: builtIn)
    let rect = CGRect(origin: p, size: size)
    #expect(builtIn.contains(rect), "panel restored off-screen at \(p)")
}

@Test("a position hanging off the right edge is pulled fully inside")
func partiallyOffscreenIsClamped() {
    let saved = CGPoint(x: builtIn.maxX - 40, y: 500)   // 340pt hanging off
    let p = PanelPlacement.resolve(saved: saved, size: size,
                                   visibleFrames: [builtIn], mainFrame: builtIn)
    #expect(CGRect(origin: p, size: size).maxX <= builtIn.maxX)
    #expect(p.y == 500, "y should not move when only x was out of bounds")
}

@Test("a smaller screen after a resolution change still contains the panel")
func resolutionChangeIsHandled() {
    let saved = CGPoint(x: 1600, y: 1000)               // fine on a big screen
    let smaller = CGRect(x: 0, y: 0, width: 1280, height: 800)
    let p = PanelPlacement.resolve(saved: saved, size: size,
                                   visibleFrames: [smaller], mainFrame: smaller)
    #expect(smaller.contains(CGRect(origin: p, size: size)))
}

@Test("with two displays the saved one is preferred over the main one")
func savedDisplayWinsOverMain() {
    let saved = CGPoint(x: 2000, y: 800)                // on the external
    let p = PanelPlacement.resolve(saved: saved, size: size,
                                   visibleFrames: [builtIn, external],
                                   mainFrame: builtIn)
    #expect(p == saved)
    #expect(external.contains(CGRect(origin: p, size: size)))
}

@Test("a panel mostly off-screen is treated as lost, not kept")
func mostlyOffscreenIsRecovered() {
    // Only a sliver visible at the left edge: technically intersecting, but
    // not usable. Keeping it would look identical to losing it.
    let saved = CGPoint(x: builtIn.minX - size.width + 20, y: 400)
    let p = PanelPlacement.resolve(saved: saved, size: size,
                                   visibleFrames: [builtIn], mainFrame: builtIn)
    #expect(builtIn.contains(CGRect(origin: p, size: size)))
    #expect(p != saved)
}

@Test("no screens at all does not crash or produce a wild position")
func noScreens() {
    let p = PanelPlacement.resolve(saved: CGPoint(x: 900, y: 900), size: size,
                                   visibleFrames: [], mainFrame: nil)
    #expect(p == .zero)
}

@Test("a panel larger than the screen is pinned, not resized away")
func oversizedPanel() {
    let tiny = CGRect(x: 0, y: 0, width: 200, height: 100)
    let p = PanelPlacement.resolve(saved: CGPoint(x: 5000, y: 5000), size: size,
                                   visibleFrames: [tiny], mainFrame: tiny)
    #expect(p.x >= tiny.minX)
    #expect(p.y >= tiny.minY)
}

@Test("clamping is idempotent")
func clampIsIdempotent() {
    let once = PanelPlacement.clamp(CGRect(x: 9999, y: -9999, width: 380, height: 118),
                                    into: builtIn)
    let twice = PanelPlacement.clamp(once, into: builtIn)
    #expect(once == twice)
}
