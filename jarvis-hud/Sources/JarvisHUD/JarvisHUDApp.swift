import AppKit
import Observation
import ServiceManagement
import SwiftUI

@main
struct JarvisHUDApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    /// Login-item control, before any UI exists.
    ///
    /// `SMAppService.mainApp` registers the CALLING bundle, so this cannot live
    /// in an installer script -- it has to be inside the app. It is behind
    /// explicit flags and never runs as a side effect of a normal launch: an app
    /// that quietly adds itself to Login Items is a thing to distrust, and this
    /// one is a viewer.
    ///
    /// Nothing about the HUD's behaviour changes. No new capability, no state
    /// write, no network. The status is printed and interpreted by a human.
    init() {
        let args = CommandLine.arguments
        guard args.contains("--login-item-status")
                || args.contains("--register-login-item")
                || args.contains("--unregister-login-item") else { return }

        func describe(_ s: SMAppService.Status) -> String {
            switch s {
            case .notRegistered:    return "notRegistered"
            case .enabled:          return "enabled"
            case .requiresApproval: return "requiresApproval"
            case .notFound:         return "notFound"
            @unknown default:       return "unknown(\(s.rawValue))"
            }
        }

        print("bundle: \(Bundle.main.bundlePath)")
        if args.contains("--login-item-status") {
            print("status: \(describe(SMAppService.mainApp.status))")
            exit(0)
        }
        print("status_before: \(describe(SMAppService.mainApp.status))")
        do {
            if args.contains("--register-login-item") {
                try SMAppService.mainApp.register()
                print("register: ok")
            } else {
                try SMAppService.mainApp.unregister()
                print("unregister: ok")
            }
        } catch {
            print("FAILED \(error)")
            print("status_after: \(describe(SMAppService.mainApp.status))")
            exit(1)
        }
        print("status_after: \(describe(SMAppService.mainApp.status))")
        exit(0)
    }

    var body: some Scene {
        MenuBarExtra {
            MenuBarView(controller: delegate)
        } label: {
            Image(systemName: delegate.currentPhase.symbolName)
        }
        .menuBarExtraStyle(.menu)
    }
}

/// Owns the panel and the state source.
///
/// A borderless non-activating NSPanel: floats above other windows, draggable,
/// never steals focus. A HUD that pulls focus mid-turn is worse than no HUD.
@MainActor
@Observable
final class AppDelegate: NSObject, NSApplicationDelegate {
    /// The production source. In DEBUG a mock can be substituted; a release
    /// build has no path that swaps it.
    private(set) var fileProvider = FileStateProvider(fileURL: FileStateProvider.productionURL)

    #if DEBUG
    private(set) var mockProvider: MockStateProvider?
    var usingMock: Bool { mockProvider != nil }
    #else
    var usingMock: Bool { false }
    #endif

    private var panel: NSPanel?
    private var host: NSHostingView<PanelRoot>?
    private(set) var panelVisible = true

    // --- Phase 2A: compact vs expanded -------------------------------------
    /// Route remembered across the turn. The runtime publishes no route field;
    /// the route IS the state during dispatch, so it has to be carried forward
    /// or it is gone by the time JARVIS is SPEAKING.
    private(set) var tracker = RouteTracker()
    /// Last turn identity seen, so a change of turn or process can clear the
    /// inferred route before it is shown against the wrong turn.
    private var lastIdentity = TurnIdentity(pid: nil, turnId: nil)

    /// What to display: the runtime's published route if there is one, else the
    /// inferred one. The tracker is kept rather than deleted -- this HUD still
    /// has to work against a v1 runtime, and the two ship separately.
    var route: RouteResolution {
        RouteResolution.resolve(published: snapshot.route, tracker: tracker)
    }

    /// A published route disagreeing with the inferred one can only be a writer
    /// bug. Counted for diagnostics; the published value still wins and nothing
    /// alarming is rendered.
    private(set) var routeMismatches = 0
    private(set) var disclosure = DisclosureState()
    /// Which auto-expand rule is in force. Phase 2A ships all three so they can
    /// be compared on real transitions rather than argued about.
    var policy: DisclosurePolicy = .routeOnly {
        didSet { refreshDisclosure() }
    }
    var collapseDelay: CollapseDelay = .short {
        didSet { refreshDisclosure() }
    }
    var isExpanded: Bool { disclosure.isExpanded }

    #if DEBUG
    /// Fields the backend does not publish. Nil in production, and the type
    /// does not exist in a release build at all.
    var futureContext: FutureHUDContext? {
        mockProvider?.futureContext
    }
    #endif

    private var lastObservedPhase: JarvisPhase?
    private var collapseTimer: Timer?

    /// Fold the current phase into the route tracker and the disclosure state.
    ///
    /// Called on every state change rather than on a repeating timer -- the one
    /// timer here fires once, only when a collapse is actually pending.
    func refreshDisclosure() {
        let phase = snapshot.phase
        let identity = snapshot.turnIdentity

        // A new process, or a new turn, invalidates anything inferred from the
        // old one. Checked before observing, so turn N+1 never starts holding
        // turn N's route.
        if identity.differsFrom(lastIdentity), identity.isActiveTurn {
            tracker.reset()
        }
        lastIdentity = identity

        if phase != lastObservedPhase {
            tracker.observe(phase)
            lastObservedPhase = phase
        }
        if RouteResolution.mismatch(published: snapshot.route, tracker: tracker) {
            routeMismatches += 1
        }
        disclosure.update(phase: phase, policy: policy, collapse: collapseDelay,
                          now: Date().timeIntervalSinceReferenceDate)
        scheduleCollapseIfNeeded(phase: phase)
        resizePanel()
    }

    /// A pending collapse needs exactly one wake-up, at the moment it is due.
    private func scheduleCollapseIfNeeded(phase: JarvisPhase) {
        collapseTimer?.invalidate()
        collapseTimer = nil
        guard disclosure.isExpanded,
              phase == .idle || phase == .offline,
              let delay = collapseDelay.seconds, delay > 0 else { return }
        collapseTimer = Timer.scheduledTimer(withTimeInterval: delay + 0.05,
                                             repeats: false) { [weak self] _ in
            Task { @MainActor in self?.refreshDisclosure() }
        }
    }

    func toggleExpanded() {
        disclosure.toggleManual()
        resizePanel()
    }

    func setExpanded(_ expanded: Bool) {
        disclosure.setManual(expanded: expanded)
        resizePanel()
    }
    private nonisolated static let frameKey = "hud.panel.origin"

    // What the views read, whichever source is active.
    var currentPhase: JarvisPhase { snapshot.phase }

    var snapshot: JarvisState {
        #if DEBUG
        if let m = mockProvider { return m.displayed }
        #endif
        return fileProvider.displayed
    }

    var liveness: Liveness {
        #if DEBUG
        if let m = mockProvider { return m.liveness }
        #endif
        return fileProvider.liveness
    }

    var status: ProviderStatus {
        #if DEBUG
        if let m = mockProvider { return m.status }
        #endif
        return fileProvider.status
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)   // menu-bar agent, no Dock icon
        makePanel()

        #if DEBUG
        // Test affordances exist ONLY in debug builds. A release binary cannot
        // be told to fake system state, by environment variable or otherwise.
        let env = ProcessInfo.processInfo.environment
        if env["JARVIS_HUD_MOCK"] == "1" {
            let m = MockStateProvider()
            mockProvider = m
            if let pinned = env["JARVIS_HUD_PIN_STATE"],
               let phase = JarvisPhase(rawValue: pinned) {
                m.set(phase, detail: "measurement")
            } else if env["JARVIS_HUD_SEQUENCE"] == "loop" {
                m.runSequence(MockStateProvider.happyPath, loop: true)
            }
            return
        }
        #endif

        fileProvider.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        fileProvider.stop()
    }

    /// Compact size is fixed; the expanded panel is fixed in WIDTH only.
    ///
    /// Its height comes from the content, because the content genuinely varies:
    /// a release build has no future-fields section at all, and even in debug
    /// the sections present depend on the state. A fixed height left a release
    /// panel two-thirds empty -- a large translucent rectangle of nothing,
    /// floating over the user's work.
    static let compactSize = CGSize(width: 380, height: 118)
    static let expandedWidth: CGFloat = 472
    static let expandedMinHeight: CGFloat = 150
    static let expandedMaxHeight: CGFloat = 460

    /// Resize in place, keeping the panel on screen.
    ///
    /// Growing downward-right can push the panel off the edge it was parked
    /// against, and a menu-bar agent has no Dock icon to recover it from -- so
    /// the new frame goes through the same clamp first launch uses.
    private func resizePanel() {
        guard let panel else { return }
        let target = disclosure.isExpanded ? expandedFittingSize() : Self.compactSize
        guard panel.frame.size != target else { return }
        // Keep the top-left corner fixed so the panel grows downward, which is
        // where there is usually room, rather than jumping under the cursor.
        let topLeft = CGPoint(x: panel.frame.minX, y: panel.frame.maxY)
        let origin = PanelPlacement.resolve(
            saved: CGPoint(x: topLeft.x, y: topLeft.y - target.height),
            size: target,
            visibleFrames: NSScreen.screens.map(\.visibleFrame),
            mainFrame: NSScreen.main?.visibleFrame)
        panel.setFrame(NSRect(origin: origin, size: target), display: true,
                       animate: false)
    }

    /// Ask SwiftUI how tall the expanded content actually is.
    ///
    /// Clamped at both ends: a floor so a nearly-empty panel still reads as a
    /// panel, and a ceiling so a long reply cannot grow it into a window.
    private func expandedFittingSize() -> CGSize {
        guard let host else {
            return CGSize(width: Self.expandedWidth, height: Self.expandedMinHeight)
        }
        host.layoutSubtreeIfNeeded()
        let fitted = host.fittingSize.height
        let height = min(max(fitted > 0 ? fitted : Self.expandedMinHeight,
                             Self.expandedMinHeight), Self.expandedMaxHeight)
        return CGSize(width: Self.expandedWidth, height: height)
    }

    private func makePanel() {
        let host = NSHostingView(rootView: PanelRoot(controller: self))
        self.host = host
        host.frame = NSRect(origin: .zero, size: Self.compactSize)

        let p = NSPanel(contentRect: host.frame,
                        styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: false)
        p.contentView = host
        p.isFloatingPanel = true
        p.level = .statusBar
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = true
        p.hidesOnDeactivate = false
        p.isMovableByWindowBackground = true
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        p.isReleasedWhenClosed = false

        // Position comes from this app's own UserDefaults and nowhere else.
        //
        // The saved point is never trusted as-is: the display it was on may be
        // gone, or the resolution may have changed since. PanelPlacement pulls
        // it back onto a screen that exists. Getting this wrong strands the
        // panel off-screen with no Dock icon to recover it from.
        let saved = UserDefaults.standard.string(forKey: Self.frameKey)
            .map { NSPointFromString($0) }
        p.setFrameOrigin(PanelPlacement.resolve(
            saved: saved,
            size: Self.compactSize,
            visibleFrames: NSScreen.screens.map(\.visibleFrame),
            mainFrame: NSScreen.main?.visibleFrame))
        NotificationCenter.default.addObserver(
            forName: NSWindow.didMoveNotification, object: p, queue: .main) { note in
                let window = note.object as? NSWindow
                Task { @MainActor in
                    guard let w = window else { return }
                    UserDefaults.standard.set(NSStringFromPoint(w.frame.origin),
                                              forKey: Self.frameKey)
                }
            }

        p.orderFrontRegardless()
        panel = p
    }

    func setPanel(visible: Bool) {
        panelVisible = visible
        visible ? panel?.orderFrontRegardless() : panel?.orderOut(nil)
    }

    func quit() { NSApp.terminate(nil) }
}

private struct PanelRoot: View {
    @Bindable var controller: AppDelegate
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Group {
            if controller.isExpanded {
                #if DEBUG
                ExpandedPanelView(state: controller.snapshot,
                                  liveness: controller.liveness,
                                  status: controller.status,
                                  route: controller.route,
                                  context: controller.futureContext,
                                  reduceMotion: reduceMotion)
                #else
                ExpandedPanelView(state: controller.snapshot,
                                  liveness: controller.liveness,
                                  status: controller.status,
                                  route: controller.route,
                                  reduceMotion: reduceMotion)
                #endif
            } else {
                HUDPanelView(state: controller.snapshot,
                             liveness: controller.liveness,
                             status: controller.status,
                             route: controller.route)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        // Clicking the panel toggles disclosure. A viewer with one gesture is
        // still a viewer; this changes what is drawn and nothing else.
        .contentShape(Rectangle())
        .onTapGesture { controller.toggleExpanded() }
        .onChange(of: controller.snapshot.phase) { _, _ in
            controller.refreshDisclosure()
        }
    }
}
