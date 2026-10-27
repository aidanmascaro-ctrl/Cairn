import CryptoKit
import Foundation

/// Every new kind of card Offload meets — camera, folder layout, file types, and anything it
/// didn't recognise — added to cards-seen.md. The brand rules in `Card` (and the reference in
/// docs/camera-cards.md) are extended from these real cards.
public enum CardLog {
    public static var defaultURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Offload/cards-seen.md")
    }

    /// Adds the card if this camera + layout isn't logged yet. Returns whether it was new.
    @discardableResult
    public static func record(volume: URL, camera: CameraInfo?, files: [MediaFile], unknown: [String: Int],
                              to log: URL = defaultURL, date: Date = Date()) -> Bool {
        let folders = layout(of: volume)
        let model = camera?.model ?? "Unknown camera"
        // Digits don't make a new layout: 100MSDCF and 101MSDCF are the same card.
        let shape = folders.map { $0.replacingOccurrences(of: "[0-9]", with: "#", options: .regularExpression) }
        let key = SHA256.hash(data: Data((model + "|" + Set(shape).sorted().joined(separator: ",")).utf8))
            .prefix(6).map { String(format: "%02x", $0) }.joined()
        let existing = (try? String(contentsOf: log, encoding: .utf8)) ?? """
            # Cards seen by Offload

            Written by Offload: one entry per new camera + card layout. "Not recognised" files
            were not copied. Fold new layouts into Card.swift and docs/camera-cards.md.

            """
        guard !existing.contains("<!-- card:\(key) -->") else { return false }

        var counts: [String: Int] = [:]
        for file in files { counts[file.url.pathExtension.uppercased(), default: 0] += 1 }
        let list = { (c: [String: Int]) in
            c.sorted { ($0.value, $1.key) > ($1.value, $0.key) }.map { "\($0.key) ×\($0.value)" }.joined(separator: ", ")
        }
        let formatter = DateFormatter()
        formatter.dateFormat = "d MMM yyyy"
        var entry = """

            ## \(formatter.string(from: date)) — \(model)
            <!-- card:\(key) -->
            - Card: \(volume.lastPathComponent)\(Card.brandHint(of: volume).map { " · folders say \($0)" } ?? "")
            - Folders: \(folders.prefix(25).map { "`\($0)`" }.joined(separator: ", "))
            - Copied: \(counts.isEmpty ? "nothing" : list(counts))

            """
        if !unknown.isEmpty { entry += "- Not recognised (not copied): \(list(unknown))\n" }
        try? FileManager.default.createDirectory(at: log.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? (existing + entry).write(to: log, atomically: true, encoding: .utf8)
        return true
    }

    /// Folders on the card, three levels deep: "DCIM/100MSDCF", "PRIVATE/M4ROOT/CLIP".
    static func layout(of volume: URL) -> [String] {
        var result: [String] = []
        func walk(_ folder: URL, _ path: String, _ depth: Int) {
            for sub in ShootLibrary.subfolders(of: folder).sorted(by: { $0.path < $1.path }) {
                let here = path.isEmpty ? sub.lastPathComponent : "\(path)/\(sub.lastPathComponent)"
                result.append(here)
                if depth < 3 { walk(sub, here, depth + 1) }
            }
        }
        walk(volume, "", 1)
        return result
    }
}
