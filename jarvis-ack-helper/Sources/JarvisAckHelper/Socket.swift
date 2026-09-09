// Unix domain socket control channel. PROTOTYPE.
//
// POSIX sockets rather than Network.framework, deliberately: linking
// Network.framework would put networking symbols in a binary whose whole claim
// is that it has none. AF_UNIX cannot leave the machine.
//
// The socket path is the ONE thing that comes from argv, and only so the
// prototype can run out of an isolated directory. It selects where to listen,
// never what to play — the asset remains a compile-time constant. Production
// should hardcode the path too.

import Foundation

enum SocketError: Error, CustomStringConvertible {
    case create(Int32), bind(Int32), listen(Int32)
    case unsafeDirectory(String)

    var description: String {
        switch self {
        case .create(let e):  return "socket() failed errno=\(e)"
        case .bind(let e):    return "bind() failed errno=\(e)"
        case .listen(let e):  return "listen() failed errno=\(e)"
        case .unsafeDirectory(let why): return "unsafe socket directory: \(why)"
        }
    }
}

/// Line-oriented AF_UNIX listener. One connection served at a time, which is
/// all a single-client control channel needs.
final class ControlSocket: @unchecked Sendable {
    private let path: String
    private var fd: Int32 = -1
    private let queue = DispatchQueue(label: "ack.socket")

    init(path: String) { self.path = path }

    /// Refuse to bind inside a directory anyone else can write.
    ///
    /// A fixed socket path in a shared directory can be pre-created by another
    /// user, who then receives whatever the client sends. There is nothing
    /// secret in PLAY, but a hijacked control channel is still a hijacked
    /// control channel, and failing closed here costs nothing.
    private func checkDirectory() throws {
        let dir = (path as NSString).deletingLastPathComponent
        var st = stat()
        guard stat(dir, &st) == 0 else {
            throw SocketError.unsafeDirectory("cannot stat \(dir)")
        }
        guard st.st_uid == getuid() else {
            throw SocketError.unsafeDirectory("\(dir) is owned by uid \(st.st_uid)")
        }
        // No group or other write bits.
        if st.st_mode & UInt16(S_IWGRP | S_IWOTH) != 0 {
            throw SocketError.unsafeDirectory(
                String(format: "%@ is mode %o", dir, st.st_mode & 0o777))
        }
    }

    /// Remove a leftover socket, but only one we own in a directory we own.
    private func removeStale() throws {
        var st = stat()
        guard stat(path, &st) == 0 else { return }        // nothing there
        guard st.st_mode & S_IFMT == S_IFSOCK else {
            throw SocketError.unsafeDirectory("\(path) exists and is not a socket")
        }
        guard st.st_uid == getuid() else {
            throw SocketError.unsafeDirectory("\(path) is owned by uid \(st.st_uid)")
        }
        unlink(path)
    }

    func listen(handler: @escaping @Sendable (String, @escaping @Sendable (String) -> Void) -> Void)
        throws {
        try checkDirectory()
        try removeStale()

        fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw SocketError.create(errno) }

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8)
        // sun_path is a fixed-size C array; its capacity is a property of the
        // type, so take it before touching the field. Reading
        // MemoryLayout.size(ofValue:) on `addr.sun_path` inside a
        // withUnsafeMutablePointer to that same field is an overlapping access.
        let capacity = MemoryLayout.size(ofValue: addr.sun_path)
        guard bytes.count < capacity else {
            throw SocketError.unsafeDirectory("socket path too long")
        }
        withUnsafeMutablePointer(to: &addr.sun_path) { p in
            p.withMemoryRebound(to: CChar.self, capacity: capacity) { dst in
                for (i, b) in bytes.enumerated() { dst[i] = CChar(bitPattern: b) }
                dst[bytes.count] = 0
            }
        }

        // 0600 on the socket itself: set the mask before bind, because
        // chmod-after-bind leaves a window where the socket is permissive.
        let oldMask = umask(0o077)
        let bound = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        umask(oldMask)
        guard bound == 0 else {
            let e = errno; close(fd); throw SocketError.bind(e)
        }
        guard Darwin.listen(fd, 4) == 0 else {
            let e = errno; close(fd); throw SocketError.listen(e)
        }

        queue.async { [weak self] in self?.acceptLoop(handler: handler) }
    }

    private func acceptLoop(
        handler: @escaping @Sendable (String, @escaping @Sendable (String) -> Void) -> Void) {
        while true {
            let client = accept(fd, nil, nil)
            if client < 0 {
                if errno == EINTR { continue }
                return
            }
            serve(client, handler: handler)
            close(client)
        }
    }

    private func serve(
        _ client: Int32,
        handler: @escaping @Sendable (String, @escaping @Sendable (String) -> Void) -> Void) {
        // Fixed, small read buffer. The longest verb is 6 bytes; anything
        // longer than this is not a command this helper has.
        let maxLine = 16
        var pending = [UInt8]()
        var buf = [UInt8](repeating: 0, count: 64)

        let reply: @Sendable (String) -> Void = { line in
            let out = Array((line + "\n").utf8)
            _ = out.withUnsafeBufferPointer { p in
                write(client, p.baseAddress, p.count)
            }
        }

        while true {
            let n = read(client, &buf, buf.count)
            if n <= 0 { return }
            for byte in buf[0..<n] {
                if byte == UInt8(ascii: "\n") {
                    let line = String(decoding: pending, as: UTF8.self)
                    pending.removeAll(keepingCapacity: true)
                    handler(line, reply)
                } else if pending.count < maxLine {
                    pending.append(byte)
                } else {
                    // Over-long line: drop it rather than parse it.
                    pending.removeAll(keepingCapacity: true)
                    pending.append(0)      // poison so it cannot match a verb
                }
            }
        }
    }

    func shutdown() {
        if fd >= 0 { close(fd) }
        unlink(path)
    }
}
