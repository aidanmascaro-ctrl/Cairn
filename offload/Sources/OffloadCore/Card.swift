import Foundation

/// One file found on a card. Cards are only ever read, never written.
public struct MediaFile: Hashable, Sendable {
    public enum Kind: Sendable { case video, photo, thumbnail, metadata, photoEdit }

    public let url: URL
    public let size: Int64
    public let created: Date
    public let kind: Kind
    /// For a file inside a tree that must be copied whole (AVCHD, Canon XF, XDCAM, RED…):
    /// its folder below the card root, e.g. "PRIVATE/AVCHD/BDMV/STREAM". Rebuilt under
    /// Footage/<Camera>/ so the editor can still join spanned clips. Nil for everything else.
    public let tree: String?
    public var isPhoto: Bool { kind == .photo }

    public init(url: URL, size: Int64, created: Date, tree: String? = nil) {
        self.url = url
        self.size = size
        self.created = created
        self.tree = tree
        self.kind = Card.kind(of: url)
    }
}

/// What every brand writes to its cards, and which of it is worth keeping.
/// The full research (with sources) is in docs/camera-cards.md — update both together.
public enum Card {
    // ponytail: audio recorders' WAV/MP3 count as clips; add an audio kind if they need their own folder.
    static let videoExtensions: Set<String> = [
        "mp4", "mov", "mxf", "avi", "m2ts", "mts", "braw", "r3d", "crm", "nev", "insv", "360", "osv",
        "wav", "mp3",
    ]
    static let photoExtensions: Set<String> = [
        "arw", "jpg", "jpeg", "dng", "cr2", "cr3", "nef", "nrw", "raf", "heic", "heif", "hif", "rw2", "orf",
        "ori", "pef", "rwl", "srw", "gpr", "insp", "3fr", "iiq", "x3f",
    ]
    /// Small previews and low-res proxies: Canon/GoPro .THM, DJI .LRF, GoPro/Insta360 .LRV.
    static let thumbnailExtensions: Set<String> = ["thm", "lrf", "lrv"]
    /// Per-clip sidecars: Sony M01.XML, Canon XF .CIF/.CPF/.SIF, DJI .SRT telemetry, RED .RMD,
    /// Blackmagic .sidecar, Nikon dust-off .NDF, Zoom .hprj, iPhone .AAE. Plus in-camera LUTs.
    static let metadataExtensions: Set<String> = [
        "xml", "cube", "srt", "cif", "cpf", "sif", "rmd", "sidecar", "ndf", "hprj", "aae",
    ]
    /// Edit sidecars from Lightroom/Camera Raw (.xmp), RawTherapee, DxO, Capture One, ON1,
    /// Photomator (.photo-edit) — they belong next to their photo, always in Photos.
    static let photoEditExtensions: Set<String> = ["xmp", "pp3", "dop", "cos", "on1", "photo-edit"]
    /// Music and other audio no camera or recorder writes (a track laid under the edit): it
    /// goes in the shoot's Assets folder. Recorder WAV/MP3 stay clips (above).
    static let musicExtensions: Set<String> = ["m4a", "aac", "flac", "aif", "aiff", "ogg", "opus"]
    static let mediaExtensions = videoExtensions
        .union(photoExtensions).union(thumbnailExtensions).union(metadataExtensions).union(photoEditExtensions)
    /// Card housekeeping the camera rebuilds itself: Sony's card database (.BIN/.IND, AVF_INFO),
    /// AVCHD indexes outside a copied tree, Nikon NC_FLLST.DAT, DPOF print orders, Tascam's
    /// system file, Panasonic BACKUP.TMP. Not copied, and not reported as unknown.
    static let housekeepingExtensions: Set<String> = [
        "bin", "ind", "bnp", "inp", "int", "dat", "mrk", "tmp", "sys", "bdm", "cpi", "mpl", "mif", "txt", "log",
    ]

    /// Sony card-level files the camera rebuilds itself (DATABASE.BIN, MEDIAPRO.XML, CUEUP.XML,
    /// in-camera LUTs, AVF_INFO indexes): never copied from a card, and binned when Tidy finds
    /// them in a shoot copied by hand. `folders` is the path inside the card or shoot; only
    /// files under PRIVATE/, M4ROOT/ or a top-level SONY/ (SONYCARD.IND, PRO/LUT) count, so
    /// nothing outside a Sony card's folders is touched.
    static func isSonyHousekeeping(_ url: URL, in folders: [String]) -> Bool {
        let upper = folders.map { $0.uppercased() }
        guard upper.contains("PRIVATE") || upper.contains("M4ROOT") || upper.first == "SONY" else { return false }
        if upper.contains("CLIP") || upper.contains("THMBNL") || upper.contains("SUB") { return false }  // a clip's own files
        let name = url.lastPathComponent.uppercased()
        let ext = url.pathExtension.lowercased()
        return housekeepingExtensions.contains(ext) || ext == "cube" || ["MEDIAPRO.XML", "CUEUP.XML"].contains(name)
    }

    /// Folders whose whole tree is copied as is: the editor needs their index and playlist
    /// files to join clips split across files. (Sony's XAVC M4ROOT isn't one: its clips stand alone.)
    static let treeRoots = ["PRIVATE/AVCHD", "AVCHD", "XDROOT", "BPAV", "CONTENTS", "PRIVATE/PANA_GRP"]

    /// Sony keeps a JPG thumbnail per clip in PRIVATE/M4ROOT/THMBNL; those are not photos.
    static func kind(of url: URL) -> MediaFile.Kind {
        let ext = url.pathExtension.lowercased()
        if videoExtensions.contains(ext) { return .video }
        if photoEditExtensions.contains(ext) { return .photoEdit }
        if photoExtensions.contains(ext) && !url.pathComponents.contains(where: {
            ["THMBNL", "THUMBNAILS", "AVCHDTN", "THMBNAIL"].contains($0.uppercased())
        }) { return .photo }
        if thumbnailExtensions.contains(ext) || photoExtensions.contains(ext) { return .thumbnail }
        return .metadata  // sidecars, and index files inside a copied tree
    }

    /// A volume looks like a camera card if it has DCIM, or the folder (or file) a video camera
    /// or recorder writes at its root: Sony M4ROOT/XDROOT/BPAV, AVCHD, Canon XF/Panasonic P2
    /// CONTENTS, Panasonic PANA_GRP, RED .RDM, Blackmagic .braw, Zoom FOLDER01.
    public static func isCard(_ volume: URL) -> Bool {
        let fm = FileManager.default
        let markers = ["DCIM", "PRIVATE/M4ROOT", "M4ROOT", "CLIP", "XDROOT", "BPAV", "PRIVATE/AVCHD", "AVCHD",
                       "CONTENTS/CLIPS001", "CONTENTS/VIDEO", "PRIVATE/PANA_GRP", "FOLDER01"]
        if markers.contains(where: { fm.fileExists(atPath: volume.appendingPathComponent($0).path) }) { return true }
        let root = (try? fm.contentsOfDirectory(atPath: volume.path)) ?? []
        return root.contains { $0.uppercased().hasSuffix(".RDM") || $0.lowercased().hasSuffix(".braw") }
    }

    /// Which tree a card path belongs to ("PRIVATE/AVCHD", "A001_0402XX.RDM"), if any.
    static func treeRoot(of relative: String) -> String? {
        let upper = relative.uppercased()
        if let root = treeRoots.first(where: { upper.hasPrefix($0 + "/") }) { return root }
        let first = relative.split(separator: "/").first.map(String.init) ?? ""
        return first.uppercased().hasSuffix(".RDM") ? first : nil
    }

    /// Everything on the card worth copying, plus files Offload doesn't recognise (by type,
    /// e.g. ["XYZ": 12]) so the card can say they're there and won't be copied.
    public static func survey(of volume: URL) -> (files: [MediaFile], unknown: [String: Int]) {
        let keys: [URLResourceKey] = [.isRegularFileKey, .fileSizeKey, .creationDateKey]
        guard let walker = FileManager.default.enumerator(
            at: volume, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles]
        ) else { return ([], [:]) }

        let base = volume.standardizedFileURL.path
        var files: [MediaFile] = []
        var unknown: [String: Int] = [:]
        for case let url as URL in walker {
            guard let values = try? url.resourceValues(forKeys: Set(keys)), values.isRegularFile == true,
                  let size = values.fileSize else { continue }
            let relative = String(url.deletingLastPathComponent().standardizedFileURL.path.dropFirst(base.count))
                .trimmingCharacters(in: ["/"])
            let ext = url.pathExtension.lowercased()
            let created = values.creationDate ?? .distantPast
            if treeRoot(of: relative + "/") != nil {
                files.append(MediaFile(url: url, size: Int64(size), created: created, tree: relative))
            } else if mediaExtensions.contains(ext) {
                files.append(MediaFile(url: url, size: Int64(size), created: created))
            } else if !housekeepingExtensions.contains(ext) {
                unknown[ext.isEmpty ? "no extension" : ext.uppercased(), default: 0] += 1
            }
        }
        return (keepingOnlyClipSidecars(files).sorted { $0.url.path < $1.url.path }, unknown)
    }

    public static func mediaFiles(on volume: URL) -> [MediaFile] { survey(of: volume).files }

    /// Footage, each clip's own XML and thumbnail, and photos — nothing else. Sony cards also
    /// carry card-level housekeeping (MEDIAPRO.XML, CUEUP.XML, in-camera LUTs): they aren't
    /// footage, so they're neither copied nor counted, and a card with only those left is
    /// safe to format. Inside a tree copied whole, everything is kept.
    static func keepingOnlyClipSidecars(_ files: [MediaFile]) -> [MediaFile] {
        let clips = Set(files.filter { $0.kind == .video }.map { SessionPlan.stem($0.url) })
        return files.filter { $0.tree != nil || $0.kind != .metadata || clips.contains(SessionPlan.ownerStem($0.url)) }
    }

    /// The brand a card's folders point to, when no file says which camera made it:
    /// DCF folder suffixes ("100CANON", "100_PANA", "100NCZ_8") and video root folders.
    /// Only a hint — the folder name can be changed in most cameras.
    public static func brandHint(of volume: URL) -> String? {
        let fm = FileManager.default
        let dcim = (try? fm.contentsOfDirectory(atPath: volume.appendingPathComponent("DCIM").path)) ?? []
        let suffixes: [(String, String)] = [
            ("MSDCF", "Sony"), ("CANON", "Canon"), ("EOS", "Canon"), ("_PANA", "Panasonic"), ("_FUJI", "Fujifilm"),
            ("NCZ_", "Nikon"), ("NIKON", "Nikon"), ("ND", "Nikon"), ("RICOH", "Ricoh"), ("PENTX", "Pentax"),
            ("OLYMP", "Olympus"), ("OMSYS", "OM System"), ("LEICA", "Leica"), ("GOPRO", "GoPro"),
            ("MEDIA", "DJI"), ("APPLE", "Apple"),
        ]
        for folder in dcim.map({ $0.uppercased() }) where folder.count > 3 {
            if folder.hasPrefix("DJI_") { return "DJI" }  // newer DJI: DCIM/DJI_001
            let tail = String(folder.dropFirst(3))
            if let match = suffixes.first(where: { tail.hasPrefix($0.0) }) { return match.1 }
        }
        let has = { fm.fileExists(atPath: volume.appendingPathComponent($0).path) }
        if has("PRIVATE/M4ROOT") || has("M4ROOT") || has("XDROOT") || has("BPAV") { return "Sony" }
        if has("CONTENTS/CLIPS001") || has("XFVC") || has("CRM") { return "Canon" }
        if has("PRIVATE/PANA_GRP") || has("CONTENTS/VIDEO") { return "Panasonic" }
        if has("FOLDER01") { return "Zoom" }
        let root = (try? fm.contentsOfDirectory(atPath: volume.path)) ?? []
        if root.contains(where: { $0.uppercased().hasSuffix(".RDM") }) { return "RED" }
        if root.contains(where: { $0.lowercased().hasSuffix(".braw") }) { return "Blackmagic" }
        return nil
    }

    /// The shoot date is the earliest clip on the card.
    public static func shootDate(of files: [MediaFile]) -> Date? {
        files.map(\.created).filter { $0 != .distantPast }.min()
    }
}
