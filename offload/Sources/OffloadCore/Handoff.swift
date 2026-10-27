import Foundation

/// The record Offload leaves for the Cairn Resolve plugin once a shoot is safely copied.
///
/// Written in two places (sidecar-first, per Cairn's data rule):
///   1. `<shoot>/.cairn/offload.json` — travels with the footage; full per-file hashes.
///   2. `~/Library/Application Support/Cairn/offload-inbox.json` — the list of shoots Cairn
///      hasn't sorted yet. Cairn reads it, ingests each `shootFolder`, and removes the entry.
public struct ShootManifest: Codable, Sendable {
    public var schemaVersion = 1
    public let client: String
    public let shootName: String
    public let date: String          // yyyy-MM-dd
    public let camera: String
    public let cameraModel: String?
    public let cameraSerial: String?
    public let cardName: String
    public let shootFolder: String
    public let footageFolder: String
    public let photosFolder: String?
    public let copiedAt: Date
    public let files: [CopiedFile]

    public init(
        client: String, shootName: String, date: String, camera: String,
        cameraModel: String?, cameraSerial: String?, cardName: String,
        shootFolder: String, footageFolder: String, photosFolder: String?,
        copiedAt: Date, files: [CopiedFile]
    ) {
        self.client = client
        self.shootName = shootName
        self.date = date
        self.camera = camera
        self.cameraModel = cameraModel
        self.cameraSerial = cameraSerial
        self.cardName = cardName
        self.shootFolder = shootFolder
        self.footageFolder = footageFolder
        self.photosFolder = photosFolder
        self.copiedAt = copiedAt
        self.files = files
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", client, shootName = "shoot_name", date, camera
        case cameraModel = "camera_model", cameraSerial = "camera_serial", cardName = "card_name"
        case shootFolder = "shoot_folder", footageFolder = "footage_folder", photosFolder = "photos_folder"
        case copiedAt = "copied_at", files
    }
}

public struct InboxEntry: Codable, Sendable, Equatable {
    public let shootFolder: String
    public let manifest: String
    public let addedAt: Date

    enum CodingKeys: String, CodingKey {
        case shootFolder = "shoot_folder", manifest, addedAt = "added_at"
    }
}

public enum Handoff {
    public static var defaultInbox: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Cairn/offload-inbox.json")
    }

    /// Writes the shoot sidecar, then adds (or refreshes) the shoot in Cairn's inbox.
    @discardableResult
    public static func publish(_ manifest: ShootManifest, inbox: URL = defaultInbox) throws -> URL {
        let sidecarDir = URL(fileURLWithPath: manifest.shootFolder).appendingPathComponent(".cairn")
        try FileManager.default.createDirectory(at: sidecarDir, withIntermediateDirectories: true)
        let sidecar = sidecarDir.appendingPathComponent("offload.json")
        let merged = mergedWithExisting(manifest, at: sidecar)
        try encoder.encode(merged).write(to: sidecar, options: .atomic)

        var entries = readInbox(inbox).filter { $0.shootFolder != manifest.shootFolder }
        entries.append(InboxEntry(shootFolder: manifest.shootFolder, manifest: sidecar.path, addedAt: manifest.copiedAt))
        try FileManager.default.createDirectory(at: inbox.deletingLastPathComponent(), withIntermediateDirectories: true)
        try encoder.encode(entries).write(to: inbox, options: .atomic)
        return sidecar
    }

    public static func readInbox(_ inbox: URL = defaultInbox) -> [InboxEntry] {
        guard let data = try? Data(contentsOf: inbox) else { return [] }
        return (try? decoder.decode([InboxEntry].self, from: data)) ?? []
    }

    /// A second card into the same shoot (B-cam, another day's card) adds to the file list
    /// rather than replacing it.
    private static func mergedWithExisting(_ manifest: ShootManifest, at url: URL) -> ShootManifest {
        guard let data = try? Data(contentsOf: url),
              let old = try? decoder.decode(ShootManifest.self, from: data)
        else { return manifest }
        let newPaths = Set(manifest.files.map(\.destination))
        let files = old.files.filter { !newPaths.contains($0.destination) } + manifest.files
        return ShootManifest(
            client: manifest.client, shootName: manifest.shootName, date: manifest.date,
            camera: manifest.camera, cameraModel: manifest.cameraModel, cameraSerial: manifest.cameraSerial,
            cardName: manifest.cardName, shootFolder: manifest.shootFolder,
            footageFolder: manifest.footageFolder, photosFolder: manifest.photosFolder,
            copiedAt: manifest.copiedAt, files: files
        )
    }

    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        e.dateEncodingStrategy = .iso8601
        return e
    }()

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()
}
