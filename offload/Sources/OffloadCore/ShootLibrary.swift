import Foundation

/// A shoot folder already on the drive, e.g. ".../Northwind Media/261015 2 Harvest Dinner".
public struct ExistingShoot: Hashable, Sendable {
    public let url: URL
    public var client: URL { url.deletingLastPathComponent() }
    public var folderName: String { url.lastPathComponent }
    /// Folder name without the date prefix.
    public var shootName: String { ShootLibrary.splitDate(folderName).name }
    public var date: Date? { ShootLibrary.splitDate(folderName).date }
    /// Camera folder name -> clip count, from Footage/<Camera>.
    public let cameras: [String: Int]
    public var clipCount: Int { cameras.values.reduce(0, +) }
}

/// Finds clients and shoots anywhere under the chosen working folder, so a whole drive
/// can be picked and ".../untitled folder/Northwind Media/261015 Shoot" is still found.
public struct ShootLibrary: Sendable {
    public let root: URL
    public let shoots: [ExistingShoot]
    /// Clients known from the Library on other plugged-in drives (may hold no dated shoot yet).
    private let extraClients: [URL]

    /// How far below the chosen folder to look for shoots. Root/Work/Client/Shoot = 3.
    static let maxDepth = 4

    /// `known` adds the Library's confirmed clients (and their confirmed shoots, dated or
    /// not) from other drives, so a card can go straight to a client on the Lexar even
    /// when the working folder is somewhere else.
    public init(root: URL, known: [(client: URL, shoots: [URL])] = []) {
        self.root = root
        var found: [ExistingShoot] = []
        Self.collect(in: root, depth: 0, into: &found)
        var seen = Set(found.map { $0.url.standardizedFileURL })
        for (_, shoots) in known {
            for url in shoots where seen.insert(url.standardizedFileURL).inserted {
                found.append(ExistingShoot(url: url, cameras: Self.cameras(in: url)))
            }
        }
        shoots = found.sorted { ($0.date ?? .distantPast, $0.folderName) > ($1.date ?? .distantPast, $1.folderName) }
        extraClients = known.map(\.client)
    }

    /// Client folders = parents of shoots (newest shoot first), then Library clients with none yet.
    public var clients: [URL] {
        var seen = Set<URL>()
        return (shoots.map(\.client) + extraClients).filter { seen.insert($0.standardizedFileURL).inserted }
    }

    /// Short name for a client picker: "ACME", or "ACME · Lexar 4TB" when it's on another drive.
    public func shortLabel(for url: URL) -> String {
        url.standardizedFileURL.path.hasPrefix(root.standardizedFileURL.path)
            ? url.lastPathComponent : "\(url.lastPathComponent) · \(Self.volumeName(of: url))"
    }

    static func volumeName(of url: URL) -> String {
        (try? url.resourceValues(forKeys: [.volumeNameKey]).volumeName) ?? url.pathComponents.dropFirst(2).first ?? ""
    }

    public func shoots(for client: URL) -> [ExistingShoot] {
        shoots.filter { $0.client.standardizedFileURL == client.standardizedFileURL }
    }

    /// Where a brand-new client folder goes: alongside the existing clients
    /// (the folder holding the most recent shoot's client), else the chosen root.
    public var workingFolder: URL {
        shoots.first.map { $0.client.deletingLastPathComponent() } ?? root
    }

    /// "untitled folder › Northwind Media" — the folder's path below the chosen root.
    public func label(for url: URL) -> String {
        let rootPath = root.standardizedFileURL.path
        let path = url.standardizedFileURL.path
        guard path.hasPrefix(rootPath) else {
            // Another drive: "Lexar 4TB › ACME › 261013 Spring launch".
            let volume = (try? url.resourceValues(forKeys: [.volumeURLKey]).volume)?.standardizedFileURL.path ?? "/"
            let below = path.hasPrefix(volume) ? path.dropFirst(volume.count).split(separator: "/").map(String.init) : []
            return ([Self.volumeName(of: url)] + below).joined(separator: " › ")
        }
        let parts = path.dropFirst(rootPath.count).split(separator: "/")
        return parts.isEmpty ? url.lastPathComponent : parts.joined(separator: " › ")
    }

    /// The shoot already holding the most of these drive files: where the rest of a card
    /// that was half copied belongs, whatever its dates say or wherever it was moved on the card.
    public func shootHolding(_ copies: [URL]) -> ExistingShoot? {
        let paths = copies.map(\.standardizedFileURL.path)
        let counts = shoots.map { shoot in
            let folder = shoot.url.standardizedFileURL.path + "/"
            return (shoot, paths.filter { $0.hasPrefix(folder) }.count)
        }
        guard let best = counts.max(by: { $0.1 < $1.1 }), best.1 > 0 else { return nil }
        return best.0
    }

    // MARK: Detection

    static func isShoot(_ url: URL) -> Bool {
        let fm = FileManager.default
        return splitDate(url.lastPathComponent).date != nil
            || fm.fileExists(atPath: url.appendingPathComponent("Footage").path)
            || fm.fileExists(atPath: url.appendingPathComponent(".cairn/offload.json").path)
    }

    private static func collect(in folder: URL, depth: Int, into found: inout [ExistingShoot]) {
        guard depth < maxDepth else { return }
        for child in subfolders(of: folder) {
            if isShoot(child) {
                found.append(ExistingShoot(url: child, cameras: cameras(in: child)))
            } else {
                collect(in: child, depth: depth + 1, into: &found)  // never descend into a shoot
            }
        }
    }

    static func subfolders(of folder: URL) -> [URL] {
        let items = (try? FileManager.default.contentsOfDirectory(
            at: folder, includingPropertiesForKeys: [.isDirectoryKey, .isPackageKey], options: [.skipsHiddenFiles]
        )) ?? []
        return items.filter {
            let v = try? $0.resourceValues(forKeys: [.isDirectoryKey, .isPackageKey])
            return v?.isDirectory == true && v?.isPackage != true
        }
    }

    static func cameras(in shoot: URL) -> [String: Int] {
        var result: [String: Int] = [:]
        for camera in subfolders(of: shoot.appendingPathComponent("Footage")) {
            let files = (try? FileManager.default.contentsOfDirectory(atPath: camera.path)) ?? []
            result[camera.lastPathComponent] = files.filter {
                !$0.hasPrefix(".") && Card.videoExtensions.contains(($0 as NSString).pathExtension.lowercased())
            }.count
        }
        return result
    }

    /// When a shoot's footage was shot: the earliest clip or photo in it, each by the older of
    /// its created and modified dates (a copy can reset one, rarely both). Sidecars and edits
    /// are left out. nil when the folder holds no footage.
    public static func footageDate(in shoot: URL) -> Date? {
        let keys: [URLResourceKey] = [.isRegularFileKey, .creationDateKey, .contentModificationDateKey]
        guard let walker = FileManager.default.enumerator(at: shoot, includingPropertiesForKeys: keys,
                                                          options: [.skipsHiddenFiles]) else { return nil }
        let footage = Card.videoExtensions.union(Card.photoExtensions)
        var earliest: Date?
        for case let url as URL in walker where footage.contains(url.pathExtension.lowercased()) {
            guard let v = try? url.resourceValues(forKeys: Set(keys)), v.isRegularFile == true,
                  let shot = [v.creationDate, v.contentModificationDate].compactMap({ $0 }).min() else { continue }
            earliest = min(earliest ?? shot, shot)
        }
        return earliest
    }

    /// "260729 Pod" (yyMMdd, the house style) -> (29 Jul 2026, "Pod").
    /// Also reads the older dotted "15.10.26 2 Harvest Dinner" -> (15 Oct 2026, "2 Harvest Dinner").
    public static func splitDate(_ folderName: String) -> (date: Date?, name: String) {
        let parts = folderName.split(separator: " ", maxSplits: 1)
        guard let first = parts.first else { return (nil, folderName) }
        let formatter = DateFormatter()
        formatter.isLenient = false
        if first.count == 8 {
            formatter.dateFormat = "dd.MM.yy"
        } else if first.count == 6, first.allSatisfy(\.isNumber) {
            formatter.dateFormat = "yyMMdd"
        } else {
            return (nil, folderName)
        }
        guard let date = formatter.date(from: String(first)) else { return (nil, folderName) }
        return (date, parts.count > 1 ? String(parts[1]) : "")
    }
}
