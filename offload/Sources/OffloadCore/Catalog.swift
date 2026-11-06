import CryptoKit
import Foundation

/// A drive Offload has seen, remembered while it's unplugged.
public struct CatalogDrive: Codable, Sendable, Identifiable, Equatable {
    /// Volume UUID, so renaming the drive keeps its history.
    public let id: String
    public var name: String
    /// Where the drive physically lives ("Studio desk"). Typed by the user.
    public var note: String
    public var capacity: Int64?
    public var free: Int64?
    public var lastSeen: Date
    /// On the Mac's own disk: the folder that was scanned (the working folder at the time),
    /// so it can still be re-read after the working folder moves to another drive.
    public var scanRoot: String?

    public init(id: String, name: String, note: String = "", capacity: Int64?, free: Int64?, lastSeen: Date,
                scanRoot: String? = nil) {
        self.id = id
        self.name = name
        self.note = note
        self.capacity = capacity
        self.free = free
        self.lastSeen = lastSeen
        self.scanRoot = scanRoot
    }
}

/// Whether the user has said yes or no to a folder a drive scan suggested.
public enum Review: String, Codable, Sendable {
    case pending, confirmed, ignored
}

/// A folder that might be a client: anything at the top of a drive, or the parent of
/// a dated shoot folder deeper down.
public struct CatalogClient: Codable, Sendable, Identifiable, Hashable {
    public let driveID: String
    public let path: String
    public var name: String
    /// Subfolders with a date in their name — the strongest sign this is a client.
    public var datedShoots: Int
    public var folders: Int
    public var review: Review

    public var id: String { "\(driveID)/\(path)" }

    /// Pre-ticked on the review page when it already holds a dated shoot.
    public var looksLikeClient: Bool { datedShoots > 0 }

    public init(driveID: String, path: String, name: String, datedShoots: Int, folders: Int, review: Review = .pending) {
        self.driveID = driveID
        self.path = path
        self.name = name
        self.datedShoots = datedShoots
        self.folders = folders
        self.review = review
    }
}

/// One shoot folder inside a client folder on one drive.
public struct CatalogShoot: Codable, Sendable, Identifiable, Hashable {
    public let driveID: String
    /// Path below the drive's root, e.g. "ACME/13.10.26 Spring launch".
    public let path: String
    public let clientPath: String
    public var client: String
    public var name: String
    /// yyyy-MM-dd, nil when the folder name has no date.
    public var date: String?
    public var cameras: [String]
    public var clips: Int
    public var bytes: Int64
    /// Offload copied it (has .cairn/offload.json) — logged without asking.
    public var offloaded: Bool
    public var review: Review
    /// Fingerprint of the files inside (names and sizes), so a renamed or moved folder is
    /// recognised as the same shoot. nil for an empty folder.
    public var signature: String?
    /// Footage or photos not yet in Footage/, Photos/ or Project/ — what Sort would move.
    public var unsorted: Int?

    public var id: String { "\(driveID)/\(path)" }
    public var clientID: String { "\(driveID)/\(clientPath)" }

    /// Pre-ticked on the review page: dated, organised by Offload, or copied by it.
    public var looksLikeShoot: Bool { date != nil || !cameras.isEmpty || offloaded }

    public init(driveID: String, path: String, clientPath: String, client: String, name: String, date: String?,
                cameras: [String], clips: Int, bytes: Int64, offloaded: Bool, signature: String? = nil,
                unsorted: Int? = nil, review: Review = .pending) {
        self.driveID = driveID
        self.path = path
        self.clientPath = clientPath
        self.client = client
        self.name = name
        self.date = date
        self.cameras = cameras
        self.clips = clips
        self.bytes = bytes
        self.offloaded = offloaded
        self.signature = signature
        self.unsorted = unsorted
        self.review = review
    }

    /// Everything a search can match: "acme spring launch 261013 2026-10-13 fx3 lexar".
    func searchText(driveName: String) -> String {
        [client, name, date ?? "", date.flatMap(Catalog.shortDate) ?? "", cameras.joined(separator: " "), driveName]
            .joined(separator: " ").lowercased()
    }
}

/// Every shoot on every drive, kept on the Mac so it can be searched while drives are
/// in a drawer. A drive scan suggests clients first; once a client is confirmed, its
/// shoots are suggested. Anything Offload copied itself is logged without asking.
public struct Catalog: Codable, Sendable, Equatable {
    public static let currentSchema = 2

    public var schemaVersion = currentSchema
    public var drives: [CatalogDrive] = []
    public var clients: [CatalogClient] = []
    /// Every shoot folder found under any suggested client, whatever its review state.
    public var folders: [CatalogShoot] = []

    public init() {}

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", drives, clients, folders
    }

    // MARK: What the user sees

    private var confirmedClients: Set<String> { Set(clients.filter { $0.review == .confirmed }.map(\.id)) }

    /// The catalog proper: confirmed shoots of confirmed clients, newest first.
    public var shoots: [CatalogShoot] {
        let ok = confirmedClients
        return folders.filter { $0.review == .confirmed && ok.contains($0.clientID) }
    }

    public var pendingClients: [CatalogClient] { clients.filter { $0.review == .pending } }

    /// Shoots waiting for a yes/no — only for clients already confirmed.
    public var pendingShoots: [CatalogShoot] {
        let ok = confirmedClients
        return folders.filter { $0.review == .pending && ok.contains($0.clientID) }
    }

    public var hasPending: Bool { !pendingClients.isEmpty || !pendingShoots.isEmpty }

    /// What's up for review on the given drives only: renaming or ticking folders needs the
    /// drive plugged in, so folders on drives that aren't wait until they are.
    public func pendingClients(on drives: Set<String>) -> [CatalogClient] {
        pendingClients.filter { drives.contains($0.driveID) }
    }

    public func pendingShoots(on drives: Set<String>) -> [CatalogShoot] {
        pendingShoots.filter { drives.contains($0.driveID) }
    }

    public func hasPending(on drives: Set<String>) -> Bool {
        !pendingClients(on: drives).isEmpty || !pendingShoots(on: drives).isEmpty
    }

    // MARK: Scan results

    /// Replaces what's known about `drive` with a fresh scan, keeping every earlier yes/no.
    /// A renamed or moved folder keeps its answer: a new shoot folder holding exactly the
    /// files of one that vanished is the same shoot, and a new client folder holding any of
    /// a vanished client's shoots is the same client. Other folders gone from the drive drop
    /// out. Anything Offload copied is confirmed, along with its client.
    public mutating func merge(drive: CatalogDrive, clients found: [CatalogClient], shoots: [CatalogShoot]) {
        if let i = drives.firstIndex(where: { $0.id == drive.id }) {
            let note = drives[i].note
            drives[i] = drive
            drives[i].note = note
        } else {
            drives.append(drive)
        }
        let oldClients = Dictionary(clients.map { ($0.id, $0.review) }, uniquingKeysWith: { a, _ in a })
        let oldShoots = Dictionary(folders.map { ($0.id, $0.review) }, uniquingKeysWith: { a, _ in a })
        let offloadedClients = Set(shoots.filter(\.offloaded).map(\.clientID))

        // Renames: answers of folders that vanished, looked up by what's inside them.
        let newShootIDs = Set(shoots.map(\.id))
        let vanished = folders.filter { $0.driveID == drive.id && !newShootIDs.contains($0.id) && $0.signature != nil }
        var bySignature: [String: Review] = [:]
        for (sig, group) in Dictionary(grouping: vanished, by: { $0.signature! }) where group.count == 1 {
            bySignature[sig] = group[0].review  // a signature shared by two folders proves nothing
        }
        let newClientIDs = Set(found.map(\.id))
        let vanishedClients = clients.filter { $0.driveID == drive.id && !newClientIDs.contains($0.id) }
        let oldSignatures = Dictionary(grouping: folders.filter { $0.signature != nil }, by: \.clientID)
            .mapValues { Set($0.compactMap(\.signature)) }
        let newSignatures = Dictionary(grouping: shoots.filter { $0.signature != nil }, by: \.clientID)
            .mapValues { Set($0.compactMap(\.signature)) }
        func renamedClientReview(_ id: String) -> Review? {
            guard let mine = newSignatures[id] else { return nil }
            return vanishedClients.first { !(oldSignatures[$0.id] ?? []).isDisjoint(with: mine) }?.review
        }

        clients.removeAll { $0.driveID == drive.id }
        folders.removeAll { $0.driveID == drive.id }
        clients += found.map { client in
            var client = client
            client.review = offloadedClients.contains(client.id)
                ? .confirmed : oldClients[client.id] ?? renamedClientReview(client.id) ?? .pending
            return client
        }
        folders += shoots.map { shoot in
            var shoot = shoot
            shoot.review = shoot.offloaded
                ? .confirmed : oldShoots[shoot.id] ?? shoot.signature.flatMap { bySignature[$0] } ?? .pending
            return shoot
        }
        sort()
    }

    /// Review answers: ticked ids become confirmed, the rest of `asked` ignored (never asked again).
    public mutating func answerClients(asked: Set<String>, yes: Set<String>) {
        for i in clients.indices where asked.contains(clients[i].id) {
            clients[i].review = yes.contains(clients[i].id) ? .confirmed : .ignored
        }
    }

    public mutating func answerShoots(asked: Set<String>, yes: Set<String>) {
        for i in folders.indices where asked.contains(folders[i].id) {
            folders[i].review = yes.contains(folders[i].id) ? .confirmed : .ignored
        }
    }

    public mutating func setNote(_ note: String, forDrive id: String) {
        guard let i = drives.firstIndex(where: { $0.id == id }) else { return }
        drives[i].note = note
    }

    private mutating func sort() {
        clients.sort { ($0.name.lowercased(), $0.path) < ($1.name.lowercased(), $1.path) }
        folders.sort { ($0.date ?? "", $0.name) > ($1.date ?? "", $1.name) }
    }

    // MARK: Search

    public func drive(_ id: String) -> CatalogDrive? { drives.first { $0.id == id } }

    /// Shoots matching every word typed, newest first.
    public func search(_ query: String) -> [CatalogShoot] {
        let words = query.lowercased().split(separator: " ").map(String.init)
        guard !words.isEmpty else { return shoots }
        return shoots.filter { shoot in
            let text = shoot.searchText(driveName: drive(shoot.driveID)?.name ?? "")
            return words.allSatisfy(text.contains)
        }
    }

    // MARK: Obsidian note

    /// The catalog as a Markdown note: one table per client (most recent client first),
    /// then the drives. Drives named in `mappedDrives` have a folder map note
    /// ("Drive - <name>", from the Drive Directory) and are linked to it. Shoots whose client
    /// folder is in `hiddenClients` (lowercased) are left out — see `untickedFolders(inNote:)`.
    public func markdown(updated: Date = Date(), mappedDrives: Set<String> = [],
                         hiddenClients: Set<String> = []) -> String {
        let stamp = DateFormatter()
        stamp.dateFormat = "d MMM yyyy, h:mm a"
        var lines = [
            "# Shoot catalog", "",
            "Written by Offload — \(stamp.string(from: updated)). Edits here are overwritten; drive notes are set in Offload.",
            "",
        ]
        if !mappedDrives.isEmpty {
            lines += ["Every folder on each drive: [[Drive Directory]].", ""]
        }
        func driveCell(_ name: String) -> String {
            mappedDrives.contains(name) ? "[[\(Self.mapNote(for: name))\\|\(Self.cell(name))]]" : Self.cell(name)
        }
        let shoots = shoots.filter { !hiddenClients.contains($0.client.lowercased()) }
        var clientNames: [String] = []
        for shoot in shoots where !clientNames.contains(shoot.client) { clientNames.append(shoot.client) }
        for client in clientNames {
            lines += ["## \(Self.cell(client))", "", "| Date | Shoot | Cameras | Size | Drive |", "| --- | --- | --- | --- | --- |"]
            for shoot in shoots where shoot.client == client {
                lines.append("| \(shoot.date.flatMap(Self.shortDate) ?? "—") | \(Self.cell(shoot.name)) | "
                    + "\(Self.cell(shoot.cameras.joined(separator: ", "))) | \(Self.size(shoot.bytes)) | "
                    + "\(drive(shoot.driveID).map { driveCell($0.name) } ?? "?") |")
            }
            lines.append("")
        }
        lines += ["## Drives", "", "| Drive | Where it lives | Free | Last seen |", "| --- | --- | --- | --- |"]
        let seen = DateFormatter()
        seen.dateFormat = "d MMM yyyy"
        for drive in drives.sorted(by: { $0.name < $1.name }) {
            lines.append("| \(driveCell(drive.name)) | \(Self.cell(drive.note)) | \(drive.free.map(Self.size) ?? "—") | "
                + "\(seen.string(from: drive.lastSeen)) |")
        }
        return lines.joined(separator: "\n") + "\n"
    }

    /// Folders left unticked in the Drive Directory's "Folder categories" note, lowercased.
    /// Each folder is a line like `- [ ] PERSONAL #personal — drives · size`; a tick means
    /// "holds work shoots", so unticked folders stay out of the catalog.
    public static func untickedFolders(inNote text: String) -> Set<String> {
        var names = Set<String>()
        for raw in text.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard line.hasPrefix("- [ ] ") else { continue }
            var rest = String(line.dropFirst(6))
            if let dash = rest.range(of: " — ") { rest = String(rest[..<dash.lowerBound]) }
            guard let tag = rest.range(of: " #", options: .backwards) else { continue }
            names.insert(rest[..<tag.lowerBound].lowercased())
        }
        return names
    }

    /// The Drive Directory's note name for a drive's folder map.
    public static func mapNote(for driveName: String) -> String { "Drive - \(driveName)" }

    private static func cell(_ text: String) -> String { text.replacingOccurrences(of: "|", with: "\\|") }

    public static func size(_ bytes: Int64) -> String { ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file) }

    /// "2026-10-27" -> "261027", the way shoot folders are named.
    public static func shortDate(_ iso: String) -> String? {
        let parts = iso.split(separator: "-")
        guard parts.count == 3, parts[0].count == 4 else { return nil }
        return "\(parts[0].suffix(2))\(parts[1])\(parts[2])"
    }

    // MARK: Disk

    public static var defaultLocation: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Offload/catalog.json")
    }

    /// An unreadable or older-format file is set aside (never overwritten) and the
    /// catalog starts fresh; plugging drives back in rebuilds it.
    public static func load(from url: URL = defaultLocation) -> Catalog {
        guard let data = try? Data(contentsOf: url) else { return Catalog() }
        if let catalog = try? Handoff.decoder.decode(Catalog.self, from: data), catalog.schemaVersion == currentSchema {
            return catalog
        }
        let backup = url.deletingPathExtension().appendingPathExtension("old-\(Int(Date().timeIntervalSince1970)).json")
        try? FileManager.default.moveItem(at: url, to: backup)
        NSLog("Offload: catalog not readable as schema \(currentSchema); moved to \(backup.path)")
        return Catalog()
    }

    public func save(to url: URL = defaultLocation) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Handoff.encoder.encode(self).write(to: url, options: .atomic)
    }
}

/// Reads a drive: who it is and which folders might be clients and shoots. Only lists
/// folders and file sizes; never opens or changes footage.
public enum DriveScan {
    public struct Identity: Sendable {
        public let id: String
        public let name: String
        public let capacity: Int64?
        public let free: Int64?
    }

    public struct Result: Sendable {
        public let clients: [CatalogClient]
        public let shoots: [CatalogShoot]
        public var isEmpty: Bool { clients.isEmpty }
    }

    /// Top-level folders that are never clients (Windows and recovery-tool leftovers).
    static let systemFolders: Set<String> = ["$RECYCLE.BIN", "System Volume Information", "Disk Drill"]

    public static func identity(of volume: URL) -> Identity? {
        let keys: Set<URLResourceKey> = [.volumeUUIDStringKey, .volumeNameKey, .volumeTotalCapacityKey]
        guard let v = try? volume.resourceValues(forKeys: keys), let name = v.volumeName else { return nil }
        return Identity(id: v.volumeUUIDString ?? "name:\(name)", name: name,
                        capacity: v.volumeTotalCapacity.map(Int64.init),
                        free: DriveSpace.available(at: volume))
    }

    /// Candidate clients under `root` (a whole drive, or the working folder on the Mac's
    /// own disk) and every subfolder of each as a candidate shoot. Paths are stored relative
    /// to `volume` so a drive mounted under a new name still matches.
    public static func scan(_ root: URL, volume: URL, driveID: String) -> Result {
        let rootPath = root.standardizedFileURL.path
        var candidates: [URL] = []
        var seen = Set<String>()
        let topLevel = ShootLibrary.subfolders(of: root).filter { !systemFolders.contains($0.lastPathComponent) }
        for folder in topLevel + ShootLibrary(root: root).clients {
            let path = folder.standardizedFileURL.path
            guard path != rootPath, seen.insert(path).inserted else { continue }
            candidates.append(folder)
        }

        var clients: [CatalogClient] = []
        var shoots: [CatalogShoot] = []
        for folder in candidates {
            let clientPath = relative(folder, to: volume)
            let subfolders = ShootLibrary.subfolders(of: folder)
            var dated = 0
            for sub in subfolders {
                let split = ShootLibrary.splitDate(sub.lastPathComponent)
                if split.date != nil { dated += 1 }
                let cameras = ShootLibrary.cameras(in: sub)
                let stats = contents(of: sub)
                shoots.append(CatalogShoot(
                    driveID: driveID, path: relative(sub, to: volume), clientPath: clientPath,
                    client: folder.lastPathComponent, name: split.name.isEmpty ? sub.lastPathComponent : split.name,
                    date: split.date.map(isoDate), cameras: cameras.keys.sorted(),
                    clips: stats.clips, bytes: stats.bytes,
                    offloaded: FileManager.default.fileExists(atPath: sub.appendingPathComponent(".cairn/offload.json").path),
                    signature: stats.signature, unsorted: stats.unsorted
                ))
            }
            clients.append(CatalogClient(driveID: driveID, path: clientPath, name: folder.lastPathComponent,
                                         datedShoots: dated, folders: subfolders.count))
        }
        return Result(clients: clients, shoots: shoots)
    }

    static func relative(_ url: URL, to volume: URL) -> String {
        let base = volume.standardizedFileURL.path
        let full = url.standardizedFileURL.path
        return full.hasPrefix(base) ? String(full.dropFirst(base.count)).trimmingCharacters(in: ["/"]) : full
    }

    /// Total size, video count and a fingerprint of the files (name + size of each, in any
    /// subfolder). Renaming the folder or reshuffling its subfolders keeps the fingerprint.
    static func contents(of folder: URL) -> (bytes: Int64, clips: Int, signature: String?, unsorted: Int) {
        let keys: [URLResourceKey] = [.isRegularFileKey, .fileSizeKey]
        guard let walker = FileManager.default.enumerator(at: folder, includingPropertiesForKeys: keys,
                                                          options: [.skipsHiddenFiles]) else { return (0, 0, nil, 0) }
        var bytes: Int64 = 0
        var clips = 0
        var entries: [String] = []
        var unsorted = 0
        var top = ""  // the shoot's top-level entry the walker is currently inside
        for case let url as URL in walker {
            if walker.level == 1 { top = url.lastPathComponent.lowercased() }
            guard let v = try? url.resourceValues(forKeys: Set(keys)), v.isRegularFile == true else { continue }
            let size = v.fileSize ?? 0
            bytes += Int64(size)
            entries.append("\(url.lastPathComponent)\t\(size)")
            let ext = url.pathExtension.lowercased()
            if Card.videoExtensions.contains(ext) { clips += 1 }
            let isLoose = walker.level == 1 || !ShootTidy.sortedFolders.contains(top)
            if isLoose, Card.mediaExtensions.contains(ext) { unsorted += 1 }
        }
        guard !entries.isEmpty else { return (bytes, clips, nil, unsorted) }
        let digest = SHA256.hash(data: Data(entries.sorted().joined(separator: "\n").utf8))
        return (bytes, clips, digest.map { String(format: "%02x", $0) }.joined(), unsorted)
    }

    static func isoDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }
}
