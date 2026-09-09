import Foundation

/// What the HUD knows about its own ability to read state.
///
/// Kept separate from `JarvisPhase.error` on purpose. The runtime's ERROR means
/// "JARVIS hit a problem"; these mean "the HUD could not read the file". Showing
/// the first when the second happened would report a fault in a system that is
/// working fine.
public enum ProviderStatus: Equatable, Sendable {
    /// Reading normally.
    case ok
    /// The file exists but the HUD could not use it — malformed, oversized,
    /// unreadable. The runtime may be perfectly healthy.
    case dataError(String)
    /// No file to read yet.
    case unavailable(String)

    public var isHUDFault: Bool {
        if case .ok = self { return false }
        return true
    }

    public var message: String? {
        switch self {
        case .ok: return nil
        case .dataError(let m): return "HUD DATA ERROR — \(m)"
        case .unavailable(let m): return "HUD — \(m)"
        }
    }
}

/// A source of JARVIS state. The views never learn which implementation is
/// behind it, so the Phase 1A mock tests keep working unchanged.
@MainActor
public protocol StateProviding: AnyObject {
    var displayed: JarvisState { get }
    var liveness: Liveness { get }
    var status: ProviderStatus { get }
}
