import CryptoKit
import Foundation

public struct CopyProgress: Sendable {
    public var filesDone = 0
    public var filesTotal = 0
    public var bytesDone: Int64 = 0
    public var bytesTotal: Int64 = 0
    public var startedAt: Date

    /// Speed isn't shown until this much time has passed; the first second is noisy.
    static let warmUp: TimeInterval = 2

    public init(filesTotal: Int = 0, bytesTotal: Int64 = 0, startedAt: Date = Date()) {
        self.filesTotal = filesTotal
        self.bytesTotal = bytesTotal
        self.startedAt = startedAt
    }

    public var fraction: Double { bytesTotal > 0 ? Double(bytesDone) / Double(bytesTotal) : 0 }

    /// Average over the whole copy. The read-back check is inside the elapsed time,
    /// so this is the real end-to-end rate, not just the write speed.
    public func bytesPerSecond(at now: Date = Date()) -> Double? {
        let elapsed = now.timeIntervalSince(startedAt)
        guard elapsed >= Self.warmUp, bytesDone > 0 else { return nil }
        return Double(bytesDone) / elapsed
    }

    public func secondsRemaining(at now: Date = Date()) -> TimeInterval? {
        bytesPerSecond(at: now).map { Double(bytesTotal - bytesDone) / $0 }
    }

    /// Several cards copying at once read as one job in the menu bar.
    public static func combined(_ all: [CopyProgress]) -> CopyProgress? {
        guard let first = all.first else { return nil }
        var total = CopyProgress(startedAt: all.map(\.startedAt).min() ?? first.startedAt)
        for p in all {
            total.filesDone += p.filesDone
            total.filesTotal += p.filesTotal
            total.bytesDone += p.bytesDone
            total.bytesTotal += p.bytesTotal
        }
        return total
    }
}

public struct CopiedFile: Codable, Sendable {
    public let source: URL
    public let destination: URL
    public let size: Int64
    public let sha256: String
}

public enum CopyError: Error, LocalizedError {
    case verifyFailed(URL)
    case readFailed(URL)

    public var errorDescription: String? {
        switch self {
        case .verifyFailed(let url): "Copy of \(url.lastPathComponent) didn't match the card. It was removed; try again."
        case .readFailed(let url): "Couldn't read \(url.lastPathComponent) from the card."
        }
    }
}

/// Copies card files to the drive and proves each copy byte-for-byte.
///
/// Each file is hashed while it streams to a hidden `.part` file, then the written file is
/// read back from the drive (bypassing the memory cache) and hashed again. Only a matching
/// copy is renamed into place, so an interrupted or failed copy never looks finished.
/// Existing files are never overwritten. A clip edited on the drive (rotated in Finder: same
/// shot, re-saved without Sony's metadata) keeps its place; the card's untouched clip goes
/// in Originals/ beside it.
///
/// Speed: the card is read on its own thread while the drive writes, and each file's
/// read-back check runs while the next file copies, so neither device waits on the other.
public enum Copier {
    static let chunkBytes = 8 << 20
    /// Chunks read ahead of the writer. Bounds memory to ~32 MB per copy.
    static let readAhead = 4

    /// Returns one record per verified copy, in order. On cancel or error, files already
    /// copied stay copied (and verified); the error is rethrown with the partial list lost,
    /// which is fine because the next scan finds them by content anyway.
    public static func copy(
        _ files: [MediaFile],
        plan: ShootPlan,
        onProgress: (CopyProgress) -> Void
    ) throws -> [CopiedFile] {
        var progress = CopyProgress(filesTotal: files.count, bytesTotal: files.reduce(0) { $0 + $1.size })
        var copied: [CopiedFile] = []
        var checking: PendingCheck?   // previous file, being read back while this one copies
        defer { _ = try? checking?.finish() }   // cancelled/failed: still keep a good previous copy
        onProgress(progress)

        func finishChecking() throws {
            guard let check = checking else { return }
            checking = nil
            copied.append(try check.finish())
            progress.filesDone += 1
            onProgress(progress)
        }

        for file in files {
            try Task.checkCancellation()
            let folder = plan.destinationFolder(for: file)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            // The file still being checked hasn't been renamed yet, so its name is reserved.
            let named = folder.appendingPathComponent(file.url.lastPathComponent)
            let target = isEdit(named, of: file) ? folder.appendingPathComponent(originalsFolder, isDirectory: true) : folder
            if target != folder { try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true) }
            let dest = uniqueDestination(for: file.url.lastPathComponent, in: target, reserved: checking?.dest)
            let written = try write(file, toPartFor: dest) { bytes in
                progress.bytesDone += bytes
                onProgress(progress)
            }
            try finishChecking()
            checking = written
        }
        try finishChecking()
        return copied
    }

    /// Where the card's untouched clip goes when the drive holds an edit of it.
    static let originalsFolder = "Originals"

    /// Whether `existing` is `file` edited on the drive: a clip of the same name shot at the
    /// same second (a rotate keeps the file's created date). Only reached when the bytes
    /// differ, since clips already on the drive aren't copied again. A different clip that
    /// shares the name was shot at another time, so it still gets " (2)".
    static func isEdit(_ existing: URL, of file: MediaFile) -> Bool {
        guard file.kind == .video, file.created != .distantPast,
              let created = (try? existing.resourceValues(forKeys: [.creationDateKey]))?.creationDate else { return false }
        return abs(created.timeIntervalSince(file.created)) < 1
    }

    /// Streams `file` into `.<name>.part` next to `dest` and starts its read-back check.
    static func write(_ file: MediaFile, toPartFor dest: URL, onBytes: (Int64) -> Void) throws -> PendingCheck {
        let part = dest.deletingLastPathComponent().appendingPathComponent(".\(dest.lastPathComponent).part")
        let fm = FileManager.default
        try? fm.removeItem(at: part)  // stale partial from an earlier interrupted run; ours, never user media
        guard fm.createFile(atPath: part.path, contents: nil) else { throw CopyError.readFailed(part) }
        do {
            let sourceHash = try streamCopy(from: file.url, to: part, onBytes: onBytes)
            return PendingCheck(file: file, part: part, dest: dest, sourceHash: sourceHash)
        } catch {
            try? fm.removeItem(at: part)
            throw error
        }
    }

    private static func streamCopy(from source: URL, to part: URL, onBytes: (Int64) -> Void) throws -> Data {
        guard let input = ByteReader.open(source) else { throw CopyError.readFailed(source) }
        let output = try FileHandle(forWritingTo: part)
        // Written data goes straight to the drive, so the read-back really reads the drive.
        _ = fcntl(output.fileDescriptor, F_NOCACHE, 1)
        let pipe = ChunkPipe(depth: readAhead)
        Thread { pipe.fill(from: input) }.start()
        defer {
            pipe.stop()   // reader must be finished before its handle closes
            try? input.close()
            try? output.close()
        }

        var hasher = SHA256()
        while let chunk = try pipe.next() {
            try Task.checkCancellation()
            hasher.update(data: chunk)
            try output.write(contentsOf: chunk)
            onBytes(Int64(chunk.count))
        }
        try output.synchronize()
        return Data(hasher.finalize())
    }

    /// SHA-256 of a file as it is on disk, not as macOS remembers it.
    static func hash(of url: URL) throws -> Data {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        _ = fcntl(handle.fileDescriptor, F_NOCACHE, 1)
        var hasher = SHA256()
        while let chunk = try readChunk(handle) {
            hasher.update(data: chunk)
        }
        return Data(hasher.finalize())
    }

    /// One chunk, or nil at the end. The pool releases Foundation's buffer right away;
    /// without it a background loop keeps every chunk until the copy ends (GBs of RAM).
    static func readChunk(_ handle: ByteSource) throws -> Data? {
        try autoreleasepool {
            guard let chunk = try handle.read(upToCount: chunkBytes), !chunk.isEmpty else { return nil }
            return chunk
        }
    }

    /// C0001.MP4 -> C0001 (2).MP4 if a different file already has that name.
    static func uniqueDestination(for name: String, in folder: URL, reserved: URL? = nil) -> URL {
        let base = (name as NSString).deletingPathExtension
        let ext = (name as NSString).pathExtension
        var candidate = folder.appendingPathComponent(name)
        var n = 2
        while FileManager.default.fileExists(atPath: candidate.path) || candidate == reserved {
            candidate = folder.appendingPathComponent(ext.isEmpty ? "\(base) (\(n))" : "\(base) (\(n)).\(ext)")
            n += 1
        }
        return candidate
    }
}

/// Hands chunks from a reader thread to the writer, at most `depth` chunks ahead.
final class ChunkPipe: @unchecked Sendable {
    private let depth: Int
    private let lock = NSCondition()
    private var queue: [Data] = []
    private var finished = false
    private var stopped = false
    private var error: Error?
    private let done = DispatchSemaphore(value: 0)

    init(depth: Int) { self.depth = depth }

    /// Runs on the reader thread until the file ends, a read fails, or `stop()`.
    func fill(from handle: ByteSource) {
        defer {
            lock.lock(); finished = true; lock.broadcast(); lock.unlock()
            done.signal()
        }
        do {
            while let chunk = try Copier.readChunk(handle) {
                lock.lock()
                while queue.count >= depth && !stopped { lock.wait() }
                if stopped { lock.unlock(); return }
                queue.append(chunk)
                lock.broadcast()
                lock.unlock()
            }
        } catch {
            lock.lock(); self.error = error; lock.unlock()
        }
    }

    /// Next chunk in order; nil at the end of the file.
    func next() throws -> Data? {
        lock.lock()
        defer { lock.unlock() }
        while queue.isEmpty && !finished { lock.wait() }
        if !queue.isEmpty {
            let chunk = queue.removeFirst()
            lock.broadcast()
            return chunk
        }
        if let error { throw error }
        return nil
    }

    /// Stops the reader early (or confirms it finished) and waits for it to exit.
    func stop() {
        lock.lock(); stopped = true; lock.broadcast(); lock.unlock()
        done.wait()
    }
}

/// A written `.part` file being read back on a background thread. `finish()` renames it
/// into place only if it matches what was read from the card.
final class PendingCheck: @unchecked Sendable {
    let file: MediaFile
    let part: URL
    let dest: URL
    private let sourceHash: Data
    private var result: Result<Data, Error>?
    private let done = DispatchSemaphore(value: 0)

    init(file: MediaFile, part: URL, dest: URL, sourceHash: Data) {
        self.file = file
        self.part = part
        self.dest = dest
        self.sourceHash = sourceHash
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            result = Result { try Copier.hash(of: part) }
            done.signal()
        }
    }

    func finish() throws -> CopiedFile {
        done.wait()
        let fm = FileManager.default
        do {
            guard try result?.get() == sourceHash else { throw CopyError.verifyFailed(file.url) }
            try fm.moveItem(at: part, to: dest)
        } catch {
            try? fm.removeItem(at: part)
            throw error
        }
        try? fm.setAttributes([.creationDate: file.created, .modificationDate: file.created], ofItemAtPath: dest.path)
        return CopiedFile(source: file.url, destination: dest, size: file.size,
                          sha256: sourceHash.map { String(format: "%02x", $0) }.joined())
    }
}
