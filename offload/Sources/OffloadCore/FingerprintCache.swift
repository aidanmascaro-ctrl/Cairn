import Foundation

/// Quick fingerprints (see `DriveIndex`) remembered between launches, so a card checked
/// before is checked again without re-reading every clip on it and on the drive. A file is
/// known by its path, exact size and modified time; if either of the last two changes it's
/// read again.
public final class FingerprintCache: @unchecked Sendable {
    public static let shared = FingerprintCache(location: defaultLocation)

    static var defaultLocation: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Offload/fingerprints.plist")
    }

    /// Fingerprints not used for this long are dropped when saving.
    static let keepFor: TimeInterval = 60 * 24 * 3600

    private struct Entry: Codable {
        let fingerprint: Data
        let used: Date
    }

    private let location: URL
    private let lock = NSLock()
    private var entries: [String: Entry]

    /// An unreadable file just means starting with nothing remembered.
    public init(location: URL) {
        self.location = location
        entries = (try? Data(contentsOf: location))
            .flatMap { try? PropertyListDecoder().decode([String: Entry].self, from: $0) } ?? [:]
    }

    /// The remembered fingerprint of `url`, or `compute()` (remembered for next time). Files
    /// without a modified time (an iPhone's) are always computed.
    public func fingerprint(of url: URL, size: Int64, compute: () -> Data?) -> Data? {
        var onDisk = url
        onDisk.removeAllCachedResourceValues()  // a URL keeps what it read before; ask the disk now
        guard let modified = (try? onDisk.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        else { return compute() }
        let key = "\(url.standardizedFileURL.path)|\(size)|\(Int(modified.timeIntervalSince1970))"
        if let known = lock.withLock({ entries[key] }) {
            lock.withLock { entries[key] = Entry(fingerprint: known.fingerprint, used: Date()) }
            return known.fingerprint
        }
        guard let value = compute() else { return nil }
        lock.withLock { entries[key] = Entry(fingerprint: value, used: Date()) }
        return value
    }

    /// Writes what's remembered, dropping fingerprints unused for `keepFor`. Not being able
    /// to write only costs speed next launch, so a failure is logged, not thrown.
    public func save() {
        lock.withLock {
            let cutoff = Date().addingTimeInterval(-Self.keepFor)
            entries = entries.filter { $0.value.used > cutoff }
            do {
                let encoder = PropertyListEncoder()
                encoder.outputFormat = .binary
                try FileManager.default.createDirectory(at: location.deletingLastPathComponent(),
                                                        withIntermediateDirectories: true)
                try encoder.encode(entries).write(to: location, options: .atomic)
            } catch {
                NSLog("Offload: couldn't save fingerprints to %@: %@", location.path, error.localizedDescription)
            }
        }
    }
}
