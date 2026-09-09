// Proves the directory watch survives atomic replacement.
//
// A watcher attached to the state FILE would receive the first event and then
// go permanently deaf, because os.replace() swaps the inode and leaves the old
// descriptor pointing at an orphan. This drives the same rename-over pattern
// the runtime uses and checks that events keep arriving and the FINAL value is
// readable.
//
// Operates entirely inside a temp directory passed as argv[1]. The production
// state file is not referenced anywhere in this file.
import Foundation

let dir = CommandLine.arguments[1]
let file = dir + "/jarvis_state.json"
let replacements = 60

let fd = open(dir, O_EVTONLY)
guard fd >= 0 else {
    print("could not watch \(dir)")
    exit(1)
}

final class Box: @unchecked Sendable {
    var events = 0
    var lastDetail = ""
}
let box = Box()

let src = DispatchSource.makeFileSystemObjectSource(
    fileDescriptor: fd, eventMask: [.write, .rename, .delete], queue: .main)
src.setEventHandler {
    box.events += 1
    // Open fresh every time -- never hold a descriptor on the file.
    guard let d = FileManager.default.contents(atPath: file),
          let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
          let s = o["detail"] as? String else { return }
    box.lastDetail = s
}
src.resume()

DispatchQueue.global().async {
    for i in 1...replacements {
        let json = #"{"state":"THINKING","since":1.0,"detail":"n=\#(i)","pid":1}"#
        try? json.write(toFile: file + ".tmp", atomically: false, encoding: .utf8)
        _ = rename(file + ".tmp", file)     // exactly what os.replace() does
        usleep(15_000)
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
        let ok = box.events > 0 && box.lastDetail == "n=\(replacements)"
        print("replacements : \(replacements)")
        print("events fired : \(box.events)")
        print("last detail  : \(box.lastDetail)")
        print(ok
              ? "PASS — watcher stayed live across every inode swap"
              : "FAIL — events stopped arriving or the final value was missed")
        exit(ok ? 0 : 1)
    }
}

RunLoop.main.run()
