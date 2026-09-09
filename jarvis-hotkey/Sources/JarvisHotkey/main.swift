// A global Cmd+Shift+J that sends ACTIVATE to the JARVIS runtime.
//
// WHY RegisterEventHotKey. It is the only global hotkey API on macOS that needs
// no TCC grant. CGEventTap requires Input Monitoring and
// NSEvent.addGlobalMonitorForEvents requires Accessibility; both hand the holder
// every keystroke on the machine. Taking that for a reliability fallback would
// be a bad trade, and this API tells us about exactly one key combination and
// nothing else.
//
// WHY Cmd+Shift+J. Checked against the live system rather than assumed:
// Option+Space is bound to 前の入力ソース (symbolichotkeys id 60, enabled) and is
// the IME switch this user presses constantly; Shift+Option+Space is id 156;
// Cmd+Space and bare Space are Finder search and Spotlight. No symbolichotkeys
// entry uses keycode 38.
//
// WHAT IT SENDS. The eight bytes ACTIVATE, over AF_UNIX. No command text, no
// arguments, no paths. The spoken request is captured by the microphone exactly
// as after a wake word.

import AppKit
import Carbon.HIToolbox
import Darwin
import Foundation
import ServiceManagement

let socketPath = NSString(string: "~/.hermes/runtime/jarvis-activate.sock")
    .expandingTildeInPath
let payload = Array("ACTIVATE".utf8)

// kVK_ANSI_J. cmdKey and shiftKey are Carbon's modifier bits.
let hotKeyCode: UInt32 = UInt32(kVK_ANSI_J)
let hotKeyModifiers: UInt32 = UInt32(cmdKey | shiftKey)

/// Connect, write, close. Failures are reported and never fatal: a runtime that
/// is down or restarting must not take the hotkey listener with it.
func sendActivate() {
    let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
    if fd < 0 {
        FileHandle.standardError.write("jarvis-hotkey: socket() failed\n".data(using: .utf8)!)
        return
    }
    defer { Darwin.close(fd) }

    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    let bytes = Array(socketPath.utf8)
    // sun_path is 104 bytes on Darwin and must stay NUL-terminated.
    guard bytes.count < MemoryLayout.size(ofValue: addr.sun_path) else {
        FileHandle.standardError.write("jarvis-hotkey: socket path too long\n".data(using: .utf8)!)
        return
    }
    // withUnsafeMutableBytes over the whole struct: taking a pointer to
    // sun_path while addr is also being written is overlapping access.
    withUnsafeMutableBytes(of: &addr.sun_path) { raw in
        raw.copyBytes(from: bytes)
    }

    let size = socklen_t(MemoryLayout<sockaddr_un>.size)
    let connected = withUnsafePointer(to: &addr) { p -> Int32 in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
            Darwin.connect(fd, sa, size)
        }
    }
    if connected != 0 {
        FileHandle.standardError.write(
            "jarvis-hotkey: runtime socket unavailable\n".data(using: .utf8)!)
        return
    }
    _ = payload.withUnsafeBufferPointer { buf in
        Darwin.write(fd, buf.baseAddress, buf.count)
    }
    FileHandle.standardOutput.write("ACTIVATE sent\n".data(using: .utf8)!)
}

// LOGIN ITEM CONTROL.
//
// SMAppService.mainApp registers the CALLING bundle, so this has to live inside
// the app rather than in an installer script. It is behind explicit flags: the
// app never registers itself as a side effect of being launched, because
// something that quietly adds itself to Login Items is a thing to distrust.
//
// The status is printed rather than interpreted. `.requiresApproval` means macOS
// has accepted the registration and is waiting for the user to switch it on in
// System Settings; that is the user's decision to make and is not something to
// work around.
func describe(_ status: SMAppService.Status) -> String {
    switch status {
    case .notRegistered:     return "notRegistered"
    case .enabled:           return "enabled"
    case .requiresApproval:  return "requiresApproval"
    case .notFound:          return "notFound"
    @unknown default:        return "unknown(\(status.rawValue))"
    }
}

let args = CommandLine.arguments
if args.contains("--login-item-status") {
    print("bundle: \(Bundle.main.bundlePath)")
    print("status: \(describe(SMAppService.mainApp.status))")
    exit(0)
}
if args.contains("--register-login-item") {
    print("bundle: \(Bundle.main.bundlePath)")
    print("status_before: \(describe(SMAppService.mainApp.status))")
    do {
        try SMAppService.mainApp.register()
        print("register: ok")
    } catch {
        print("register: FAILED \(error)")
        print("status_after: \(describe(SMAppService.mainApp.status))")
        exit(1)
    }
    print("status_after: \(describe(SMAppService.mainApp.status))")
    exit(0)
}
if args.contains("--unregister-login-item") {
    do {
        try SMAppService.mainApp.unregister()
        print("unregister: ok")
    } catch {
        print("unregister: FAILED \(error)")
        exit(1)
    }
    print("status_after: \(describe(SMAppService.mainApp.status))")
    exit(0)
}

// ONE INSTANCE, ENFORCED BY THE FILESYSTEM.
//
// Two helpers both holding Cmd+Shift+J would send two ACTIVATEs per press. The
// runtime would refuse the second as busy, so no double turn -- but the count
// would be wrong and the cause would be invisible from either side. An
// exclusive, non-blocking flock is the check: it is released by the kernel when
// the process dies, so a crashed helper leaves nothing to clean up, unlike a
// pidfile that has to be validated.
//
// Checked BEFORE registering the hotkey: losing the race must not mean briefly
// stealing the key from the instance that already has it.
let lockPath = NSString(string: "~/.hermes/runtime/jarvis-hotkey.lock")
    .expandingTildeInPath
do {
    try FileManager.default.createDirectory(
        atPath: (lockPath as NSString).deletingLastPathComponent,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700])
} catch {
    // A pre-existing directory is fine; anything else surfaces at open().
}
let lockFD = open(lockPath, O_CREAT | O_WRONLY, 0o600)
if lockFD < 0 {
    FileHandle.standardError.write(
        "jarvis-hotkey: cannot open \(lockPath)\n".data(using: .utf8)!)
    exit(1)
}
if flock(lockFD, LOCK_EX | LOCK_NB) != 0 {
    FileHandle.standardError.write(
        "jarvis-hotkey: another instance already holds the hotkey; exiting\n"
            .data(using: .utf8)!)
    exit(2)
}
// The descriptor is deliberately never closed: the lock lives as long as the
// process does.

// REGISTER WITH THE WINDOW SERVER FIRST.
//
// RegisterEventHotKey needs the process to be a registered application, and a
// bare command-line executable is not one: the first build of this helper armed
// the hotkey without error and then never received a single key event, because
// nothing was routing Carbon events to it. Creating the shared NSApplication
// and setting an accessory activation policy makes it a real (if invisible)
// app -- no Dock tile, no menu bar, no window -- which is what the event target
// needs to exist. .accessory rather than .prohibited: a prohibited process is
// still excluded from the paths hot keys travel.
let app = NSApplication.shared
app.setActivationPolicy(.accessory)

var hotKeyRef: EventHotKeyRef?
var hotKeyID = EventHotKeyID(signature: OSType(0x4A56_5253), id: 1)   // 'JVRS'

var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                         eventKind: UInt32(kEventHotKeyPressed))

let handler: EventHandlerUPP = { _, _, _ in
    sendActivate()
    return noErr
}

var handlerRef: EventHandlerRef?
let installed = InstallEventHandler(GetEventDispatcherTarget(), handler,
                                    1, &spec, nil, &handlerRef)
guard installed == noErr else {
    FileHandle.standardError.write(
        "jarvis-hotkey: InstallEventHandler failed (\(installed))\n".data(using: .utf8)!)
    exit(1)
}

let registered = RegisterEventHotKey(hotKeyCode, hotKeyModifiers, hotKeyID,
                                     GetEventDispatcherTarget(), 0, &hotKeyRef)
guard registered == noErr else {
    // -9868 is eventHotKeyExistsErr: something else already owns it.
    let msg = "jarvis-hotkey: RegisterEventHotKey failed (\(registered)) — "
        + "the combination may already be taken\n"
    FileHandle.standardError.write(msg.data(using: .utf8)!)
    exit(1)
}

FileHandle.standardOutput.write(
    "jarvis-hotkey: Cmd+Shift+J armed -> \(socketPath)\n".data(using: .utf8)!)
// app.run(), not CFRunLoopRun(): the AppKit loop is the one that pumps the
// Carbon event dispatcher this handler is installed on.
app.run()
