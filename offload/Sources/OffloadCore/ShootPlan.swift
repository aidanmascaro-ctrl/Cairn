import Foundation

/// The folder tree for one shoot:
///
///     <root>/<Client>/<yyMMdd Shoot>/Footage/<Camera>
///                                     /Photos        (only if the card has photos)
///                                     /Project       (only if the card has video)
public struct ShootPlan: Sendable {
    public let shootFolder: URL
    public let footageFolder: URL
    public let photosFolder: URL?
    public let projectFolder: URL

    public init(root: URL, client: String, shootName: String, date: Date, camera: String, hasPhotos: Bool) {
        let shoot = root
            .appendingPathComponent(Self.clean(client), isDirectory: true)
            .appendingPathComponent("\(Self.dateLabel(date)) \(Self.clean(shootName))", isDirectory: true)
        self.init(shootFolder: shoot, camera: camera, hasPhotos: hasPhotos)
    }

    /// Copy into a specific shoot folder — new or one already on the drive. A second
    /// card from the same camera lands in the same Footage/<Camera> folder.
    public init(shootFolder shoot: URL, camera: String, hasPhotos: Bool) {
        shootFolder = shoot
        footageFolder = shoot.appendingPathComponent("Footage", isDirectory: true)
            .appendingPathComponent(Self.clean(camera), isDirectory: true)
        photosFolder = hasPhotos ? shoot.appendingPathComponent("Photos", isDirectory: true) : nil
        projectFolder = shoot.appendingPathComponent("Project", isDirectory: true)
    }

    /// "Choose folder…": drop everything straight into a folder the user picked.
    public init(existingFolder folder: URL) {
        shootFolder = folder
        footageFolder = folder
        photosFolder = nil
        projectFolder = folder
    }

    public var folders: [URL] { [footageFolder, photosFolder, projectFolder].compactMap { $0 } }

    /// Clips sit straight in Footage/<Camera>; the camera's thumbnails and XML/LUT
    /// files go in their own sub-folders so the clip folder stays clean (and Cairn's
    /// ingest, which only reads the top level, never imports them as clips).
    /// A file from a tree copied whole (AVCHD, Canon XF…) keeps its path under Footage/<Camera>.
    public func destinationFolder(for file: MediaFile) -> URL {
        if let tree = file.tree { return footageFolder.appendingPathComponent(tree, isDirectory: true) }
        return switch file.kind {
        case .video: footageFolder
        case .photo, .photoEdit: photosFolder ?? footageFolder
        case .thumbnail: footageFolder.appendingPathComponent("Thumbnails", isDirectory: true)
        case .metadata: footageFolder.appendingPathComponent("Metadata", isDirectory: true)
        }
    }

    /// Makes the shoot folder, and its Project folder when there's video to edit. Footage/<Camera>
    /// and Photos are made by the copy as the first file lands in each, so a photo-only card
    /// leaves no empty Footage or Project folder.
    public func create(project: Bool) throws {
        try FileManager.default.createDirectory(at: project ? projectFolder : shootFolder, withIntermediateDirectories: true)
    }

    /// "261027": year, month, day, so Finder's name sort is also date order.
    public static func dateLabel(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyMMdd"
        return formatter.string(from: date)
    }

    /// Strips characters that would create extra path levels or break Finder.
    public static func clean(_ name: String) -> String {
        let trimmed = name
            .replacingOccurrences(of: "/", with: "-")
            .replacingOccurrences(of: ":", with: "-")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        // "." or ".." would point at the shoot folder itself (a crafted card's model name).
        return trimmed.isEmpty || trimmed.allSatisfy({ $0 == "." }) ? "Untitled" : trimmed
    }
}
