import Foundation
import Observation
import OSLog

/// Reads `jarvis_state.json`, read-only, and never writes anything.
///
/// WHY THE DIRECTORY IS WATCHED AND NOT THE FILE.
/// The runtime publishes with `tmp` + `os.replace()`. That swaps the inode: the
/// path then points at a *new* file, while a descriptor opened on the old one
/// stays valid and stops receiving updates forever. A watcher attached to the
/// file would go quiet after the very first state change and look like a frozen
/// HUD rather than a broken watcher.
///
/// So the containing directory is watched instead. A rename into it fires
/// `.write` on the directory, and the file is then opened fresh, read, decoded
/// and closed each time. No descriptor on the state file is held between reads.
///
/// The file is opened `O_RDONLY` and nothing in this type can open it for
/// writing — there is no write path here to get wrong.
@MainActor
@Observable
public final class FileStateProvider: StateProviding {
    public private(set) var displayed: JarvisState = .placeholder
    public private(set) var liveness: Liveness = .unknown
    public private(set) var status: ProviderStatus = .unavailable("not started")

    /// Retained for future diagnostics; deliberately NOT used to decide OFFLINE.
    /// A long-idle JARVIS legitimately leaves this file untouched for hours.
    public private(set) var lastFileAge: TimeInterval?
    public private(set) var readCount: Int = 0
    private var lastLoggedRaw = ""
    private var lastLoggedDetail = ""

    private let fileURL: URL
    private let directoryURL: URL
    private var dirFD: CInt = -1
    private var source: DispatchSourceFileSystemObject?
    private var presenter = StatePresenter()
    private var flushTask: Task<Void, Never>?
    private var coalesceTask: Task<Void, Never>?
    private var livenessTask: Task<Void, Never>?

    /// Events arrive in bursts during a turn; collapse them briefly so one
    /// state change is not read several times.
    private let coalesceWindow = Duration.milliseconds(40)

    /// Diagnostics via os_log. Chosen over a log FILE deliberately: the HUD's
    /// security contract says it writes nothing outside its own preferences,
    /// and os_log keeps that true while still making received states auditable
    /// with `log show --predicate 'subsystem == "local.jarvis.hud"'`.
    private let log = Logger(subsystem: "local.jarvis.hud", category: "state")

    public init(fileURL: URL) {
        self.fileURL = fileURL
        self.directoryURL = fileURL.deletingLastPathComponent()
    }

    public static var productionURL: URL {
        URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("AI-Lab/hermes-jarvis/logs/jarvis_state.json")
    }

    // MARK: - lifecycle

    public func start() {
        // Read once immediately. Waiting for a filesystem event would leave the
        // HUD blank until JARVIS happened to change state, which for an idle
        // assistant could be hours.
        readNow()
        startWatching()
        startLivenessTicker()
    }

    public func stop() {
        source?.cancel()
        source = nil
        if dirFD >= 0 { close(dirFD); dirFD = -1 }
        flushTask?.cancel()
        coalesceTask?.cancel()
        livenessTask?.cancel()
    }

    // No deinit closing dirFD: it is MainActor-isolated and cannot be touched
    // from a nonisolated deinit. The descriptor is closed by stop() and by the
    // dispatch source's cancel handler, which is the only owner that can.

    private func startWatching() {
        dirFD = open(directoryURL.path, O_EVTONLY)
        guard dirFD >= 0 else {
            status = .unavailable("cannot watch \(directoryURL.lastPathComponent)")
            return
        }
        let src = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: dirFD,
            eventMask: [.write, .rename, .delete],
            queue: .main)
        src.setEventHandler { [weak self] in self?.scheduleRead() }
        src.setCancelHandler { [weak self] in
            guard let self, self.dirFD >= 0 else { return }
            close(self.dirFD)
            self.dirFD = -1
        }
        src.resume()
        source = src
    }

    private func scheduleRead() {
        coalesceTask?.cancel()
        coalesceTask = Task { [weak self] in
            try? await Task.sleep(for: self?.coalesceWindow ?? .milliseconds(40))
            guard !Task.isCancelled else { return }
            self?.readNow()
        }
    }

    // MARK: - reading

    /// One complete read: stat, size-check, open read-only, read, close, decode.
    public func readNow() {
        readCount += 1

        let fm = FileManager.default
        guard let attrs = try? fm.attributesOfItem(atPath: fileURL.path) else {
            status = .unavailable("state file not found")
            return
        }
        let size = (attrs[.size] as? NSNumber)?.intValue ?? 0
        if let mtime = attrs[.modificationDate] as? Date {
            lastFileAge = max(0, Date().timeIntervalSince(mtime))
        }
        guard size > 0 else {
            status = .dataError("empty file")
            return
        }
        guard size <= JarvisState.maxFileBytes else {
            // Refuse without reading. Do NOT surface this as a runtime ERROR.
            status = .dataError("file is \(size) bytes, over the \(JarvisState.maxFileBytes) cap")
            return
        }

        // O_RDONLY. There is no code path in this type that opens for writing.
        let fd = open(fileURL.path, O_RDONLY)
        guard fd >= 0 else {
            status = .dataError("cannot open state file")
            return
        }
        defer { close(fd) }

        var buffer = [UInt8](repeating: 0, count: size)
        let n = buffer.withUnsafeMutableBytes { read(fd, $0.baseAddress, size) }
        guard n > 0 else {
            status = .dataError("read returned \(n)")
            return
        }
        let data = Data(buffer[0..<n])

        guard let parsed = JarvisState.decode(from: data) else {
            // Keep showing the last good state. A momentary bad read must not
            // blank a HUD that was working a frame ago.
            status = .dataError("unparseable state payload")
            return
        }

        status = .ok
        // Log the state as RECEIVED from the file, before the presentation
        // stabilizer can hold it back -- so the record shows what the provider
        // actually saw, not what happened to be on screen.
        if parsed.rawState != lastLoggedRaw || parsed.detail != lastLoggedDetail {
            log.info("provider received state=\(parsed.rawState, privacy: .public) detail=\(parsed.detail, privacy: .public)")
            #if DEBUG
            // os_log from a non-bundled SwiftPM binary does not reliably reach
            // `log show`, so the debug build also prints. Release keeps only
            // os_log and writes nothing.
            let ts = ISO8601DateFormatter().string(from: Date())
            print("[\(ts)] PROVIDER_RECEIVED state=\(parsed.rawState) detail=\(parsed.detail)")
            fflush(stdout)
            #endif
            lastLoggedRaw = parsed.rawState
            lastLoggedDetail = parsed.detail
        }
        apply(parsed)
    }

    private func apply(_ incoming: JarvisState) {
        let before = displayed.rawState
        displayed = presenter.accept(incoming)
        if displayed.rawState != before {
            log.info("hud presented state=\(self.displayed.rawState, privacy: .public)")
            #if DEBUG
            print("[\(ISO8601DateFormatter().string(from: Date()))] HUD_PRESENTED state=\(displayed.rawState)")
            fflush(stdout)
            #endif
        }
        updateLiveness(for: incoming)
        scheduleFlushIfNeeded()
    }

    private func scheduleFlushIfNeeded() {
        flushTask?.cancel()
        guard let delay = presenter.nextFlushDelay() else { return }
        flushTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard !Task.isCancelled, let self else { return }
            if let released = self.presenter.flush() { self.displayed = released }
            self.scheduleFlushIfNeeded()
        }
    }

    // MARK: - liveness

    /// PID existence is the primary signal, NOT file freshness.
    ///
    /// An idle JARVIS can go hours without touching this file; treating that as
    /// OFFLINE would be wrong every night. File age is recorded but not used to
    /// decide.
    private func updateLiveness(for state: JarvisState) {
        liveness = Liveness.evaluate(
            pid: state.pid,
            age: nil,                       // deliberately not consulted
            processExists: Liveness.processIsRunning)
    }

    /// The runtime dying produces no filesystem event, so nothing would wake the
    /// watcher. A slow tick notices it. 5 s is frequent enough to be useful and
    /// far too slow to register as CPU load.
    private func startLivenessTicker() {
        livenessTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard !Task.isCancelled, let self else { return }
                let before = self.liveness
                self.updateLiveness(for: self.displayed)
                if case .stale = self.liveness, case .live = before {
                    // The process is gone. Show OFFLINE rather than leaving a
                    // stale "THINKING" on screen forever.
                    let off = JarvisState(phase: .offline,
                                          rawState: JarvisPhase.offline.rawValue,
                                          since: Date(),
                                          detail: "runtime is not running",
                                          pid: self.displayed.pid)
                    self.displayed = self.presenter.accept(off)
                }
            }
        }
    }
}
