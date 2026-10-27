import Foundation

/// Which processes have files open on a mounted volume — the reason an eject fails.
public enum DiskUsers {
    public struct Process: Hashable, Sendable {
        public let pid: Int32
        public let command: String
    }

    /// `lsof <mountpoint>` lists every open file on that file system. -F pc gives
    /// machine-readable "p<pid>" / "c<command>" lines.
    public static func processes(using volume: URL) -> [Process] {
        let task = Foundation.Process()
        task.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        task.arguments = ["-F", "pc", "--", volume.path]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = FileHandle.nullDevice
        do {
            try task.run()
        } catch {
            return []
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        task.waitUntilExit()
        let ownPID = ProcessInfo.processInfo.processIdentifier
        return parse(String(decoding: data, as: UTF8.self)).filter { $0.pid != ownPID }
    }

    static func parse(_ output: String) -> [Process] {
        var result: [Process] = []
        var pid: Int32?
        for line in output.split(separator: "\n") {
            guard let tag = line.first else { continue }
            let value = String(line.dropFirst())
            switch tag {
            case "p": pid = Int32(value)
            case "c":
                if let pid, !result.contains(where: { $0.pid == pid }) {
                    result.append(Process(pid: pid, command: value))
                }
            default: continue
            }
        }
        return result
    }
}
