import CryptoKit
import Foundation

/// Answers "is this card file already somewhere on the drive?" by content, never by name.
///
/// Files are first grouped by exact byte size, then compared by a quick fingerprint
/// (SHA-256 of the first and last 1 MB). That makes the count appear in seconds even on
/// a 200 GB card. The full-file check happens when copying (see `Copier`).
public final class DriveIndex {
    static let sampleBytes = 1 << 20

    private var bySize: [Int64: [URL]] = [:]
    private var fingerprints: [URL: Data] = [:]

    public init(root: URL) {
        let keys: [URLResourceKey] = [.isRegularFileKey, .fileSizeKey]
        guard let walker = FileManager.default.enumerator(
            at: root, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles]
        ) else { return }
        for case let url as URL in walker {
            guard let values = try? url.resourceValues(forKeys: Set(keys)),
                  values.isRegularFile == true,
                  let size = values.fileSize, size > 0
            else { continue }
            bySize[Int64(size), default: []].append(url)
        }
    }

    public func contains(_ file: MediaFile) -> Bool {
        guard let candidates = bySize[file.size],
              let wanted = Self.fingerprint(file.url, size: file.size)
        else { return false }
        return candidates.contains { fingerprint(of: $0, size: file.size) == wanted }
    }

    /// Records a file that was just copied, so a re-scan doesn't need to re-walk the drive.
    public func add(_ url: URL, size: Int64) {
        bySize[size, default: []].append(url)
    }

    public func missing(from files: [MediaFile]) -> [MediaFile] {
        files.filter { !contains($0) }
    }

    private func fingerprint(of url: URL, size: Int64) -> Data? {
        if let cached = fingerprints[url] { return cached }
        let value = Self.fingerprint(url, size: size)
        fingerprints[url] = value
        return value
    }

    static func fingerprint(_ url: URL, size: Int64) -> Data? {
        guard let handle = ByteReader.open(url) else { return nil }  // a card file, or one on an iPhone
        defer { try? handle.close() }
        var hasher = SHA256()
        do {
            hasher.update(data: try handle.read(upToCount: sampleBytes) ?? Data())
            if size > Int64(sampleBytes) {
                try handle.seek(toOffset: UInt64(max(Int64(sampleBytes), size - Int64(sampleBytes))))
                hasher.update(data: try handle.read(upToCount: sampleBytes) ?? Data())  // the last 1 MB
            }
        } catch {
            return nil
        }
        return Data(hasher.finalize())
    }
}
