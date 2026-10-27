import AVFoundation
import Foundation

/// Renaming a shoot folder and sorting loose footage inside it into the house layout
/// (Footage/<Camera>, Photos). Both only rename and move within the same drive: no file
/// is copied, re-encoded, overwritten or deleted. Offload's own records (.cairn/offload.json
/// and Cairn's inbox) are updated to follow.
public enum ShootTidy {
    public enum TidyError: Error, LocalizedError {
        case exists(String)
        case emptyName

        public var errorDescription: String? {
            switch self {
            case .exists(let name): "There's already a folder called “\(name)” here."
            case .emptyName: "Give the shoot a name."
            }
        }
    }

    // MARK: Rename

    /// "261013 Spring launch", or just the name when there's no date.
    public static func folderName(name: String, date: Date?) -> String {
        let clean = ShootPlan.clean(name)
        return date.map { "\(ShootPlan.dateLabel($0)) \(clean)" } ?? clean
    }

    /// Renames the shoot folder in place and returns its new location.
    @discardableResult
    public static func rename(_ shoot: URL, to newName: String) throws -> URL {
        guard !newName.trimmingCharacters(in: .whitespaces).isEmpty else { throw TidyError.emptyName }
        let target = shoot.deletingLastPathComponent().appendingPathComponent(newName, isDirectory: true)
        guard target.standardizedFileURL != shoot.standardizedFileURL else { return shoot }
        // A case-only change ("pizza" -> "Pizza") is the same folder on case-insensitive drives.
        let caseOnly = target.path.lowercased() == shoot.path.lowercased()
        if FileManager.default.fileExists(atPath: target.path), !caseOnly { throw TidyError.exists(newName) }
        try FileManager.default.moveItem(at: shoot, to: target)
        let old = shoot.standardizedFileURL.path
        let new = target.standardizedFileURL.path
        try Handoff.relocate(shootFolder: shoot, to: target) { path in
            path.hasPrefix(old) ? new + path.dropFirst(old.count) : path
        }
        return target
    }

    // MARK: Sort

    /// Files of one type from one folder (and, for clips, one camera): the piece you drag
    /// between destination folders. "MP4 ×204 from CLIP".
    public struct Bundle: Identifiable, Sendable, Hashable {
        public let id: String
        /// Uppercased extension: "MP4", "XML", "ARW".
        public let type: String
        /// Folder inside the shoot the files are in now ("" = the shoot folder itself).
        public let origin: String
        public let kind: MediaFile.Kind?
        public let files: [URL]
        /// Camera that shot these clips, when it could be read.
        public let camera: CameraInfo?
        /// The destination these files already live in (sorted before), or "stay" for loose
        /// files. Left there, they don't move.
        public let home: String
        /// What a folder name chosen for these files is remembered under ("model:ILCE-7SM3",
        /// "from:render|MOV"), so the next shoot suggests the same name.
        public let learnKey: String?

        /// Gyroflow (or similar) output of a clip, or the stabilisation project: kept in
        /// <camera>/Stabilised.
        public var stabilised = false
        /// Sony card housekeeping copied over by hand: the only files allowed in the Bin.
        public var housekeeping = false
        /// The folder name the camera that shot these clips gets ("A7S III", or a saved
        /// "A-cam"), when the files say which camera. Checked against the folder they're in.
        public var cameraName: String?

        public var isSidecar: Bool { kind == .thumbnail || kind == .metadata }
        public var label: String {
            if stabilised { return kind == .video ? "stabilised" : "stabilisation" }
            if Card.musicExtensions.contains(type.lowercased()) { return "music" }
            return switch kind {
            case .video: "clips"
            case .photo: "photos"
            case .photoEdit: "photo edits"
            case .thumbnail: "thumbnails"
            case .metadata: "sidecars"
            case nil: "files"
            }
        }
    }

    /// A folder files can go to.
    public struct Destination: Identifiable, Sendable, Hashable {
        public enum Role: Sendable, Hashable { case camera, photos, project, assets, stay, bin }
        public let id: String
        public let role: Role
        /// Camera folder name inside Footage/ (editable); fixed for the others.
        public var name: String
        /// For a camera folder already on the drive: its name there. Differs from `name`
        /// when it's been renamed on the sort page.
        public let originalName: String?
        public var bundles: [Bundle]
        /// Added by hand on the sort page: created even if nothing is dragged into it.
        public var userAdded = false
        /// A camera folder already on the drive with nothing in it (e.g. Footage/GR III from a
        /// photo-only card) — offered for removal.
        public var emptyOnDisk = false
        /// A name kept on the sort page although it isn't a camera we know ("vid").
        public var keptName: String?

        public var fileCount: Int { bundles.reduce(0) { $0 + $1.files.count } }
        /// Files that will move into this folder (not counting ones already here).
        public var incoming: Int { role == .stay ? 0 : bundles.filter { $0.home != id }.reduce(0) { $0 + $1.files.count } }
        public var isRenamed: Bool { originalName.map { $0 != ShootPlan.clean(name) } ?? false }
        /// "MP4, XML" — the kinds of file in this folder, for naming it.
        public var types: [String] { Array(Set(bundles.map(\.type))).sorted() }
    }

    /// Why a camera folder's name needs a look before sorting.
    public enum CameraCheck: Equatable, Sendable {
        /// Its clips don't say which camera shot them (Gyroflow exports, renders) and the
        /// name isn't one of the user's cameras.
        case unknown
        /// Its clips were shot on this camera, but the folder is called something else.
        case differs(String)
    }

    public struct SortPlan: Sendable {
        public let shoot: URL
        /// Footage/<camera> folders, then Photos, Project, Assets, and "stays where it is".
        public var destinations: [Destination]
        /// What the user calls their cameras ("A7S III", "FX30"): offered when a folder's
        /// clips don't say, and accepted as a folder name without asking.
        public var knownCameras: [String] = []

        public var cameras: [Destination] { destinations.filter { $0.role == .camera } }
        public func destination(_ role: Destination.Role) -> Destination? { destinations.first { $0.role == role } }
        /// Files that will move.
        public var fileCount: Int { destinations.reduce(0) { $0 + $1.incoming } }
        public var renames: [Destination] { cameras.filter(\.isRenamed) }
        /// Every file type in the shoot with its count, most first: [("MP4", 254), ("JPG", 251)…].
        public var typeCounts: [(type: String, count: Int)] {
            var counts: [String: Int] = [:]
            for bundle in destinations.flatMap(\.bundles) { counts[bundle.type, default: 0] += bundle.files.count }
            return counts.map { ($0.key, $0.value) }.sorted { ($0.count, $1.type) > ($1.count, $0.type) }
        }
        /// Something to do: files to move, folders to rename, or new folders to make.
        public var hasChanges: Bool { fileCount > 0 || !renames.isEmpty || destinations.contains(where: \.userAdded) }
        public var isEmpty: Bool { !hasChanges && !destinations.contains(where: \.emptyOnDisk) }

        /// Two camera folders with the same name (Finder would see one).
        public var duplicateName: String? {
            var seen = Set<String>()
            for camera in cameras where !seen.insert(ShootPlan.clean(camera.name).lowercased()).inserted { return camera.name }
            return nil
        }

        /// Footage/<name>, Photos, Project, Assets — after any rename.
        public func folder(for destination: Destination) -> URL? {
            switch destination.role {
            case .camera: shoot.appendingPathComponent("Footage").appendingPathComponent(ShootPlan.clean(destination.name))
            case .photos: shoot.appendingPathComponent("Photos")
            case .project: shoot.appendingPathComponent("Project")
            case .assets: shoot.appendingPathComponent("Assets")
            case .stay, .bin: nil
            }
        }

        /// Whether a camera folder's name matches the camera its clips came from.
        public func cameraCheck(_ destination: Destination) -> CameraCheck? {
            let clips = destination.bundles.filter { $0.kind == .video }
            let name = ShootPlan.clean(destination.name).lowercased()
            guard destination.role == .camera, !clips.isEmpty, name != destination.keptName?.lowercased() else { return nil }
            var shotOn: [String: Int] = [:]
            for bundle in clips { if let camera = bundle.cameraName { shotOn[camera, default: 0] += bundle.files.count } }
            if let most = shotOn.max(by: { ($0.value, $1.key) < ($1.value, $0.key) })?.key {
                return shotOn.keys.contains { $0.lowercased() == name } ? nil : .differs(most)
            }
            return knownCameras.contains { $0.lowercased() == name } ? nil : .unknown
        }

        /// Camera folders to look at before sorting.
        public var unchecked: [Destination] { cameras.filter { cameraCheck($0) != nil } }

        /// Known cameras a folder could be named after: not ones another folder already has.
        public func suggestions(for destination: Destination) -> [String] {
            let taken = Set(cameras.filter { $0.id != destination.id }.map { ShootPlan.clean($0.name).lowercased() })
            return knownCameras.filter { !taken.contains($0.lowercased()) }
        }

        public func exists(_ destination: Destination) -> Bool {
            if destination.originalName != nil { return true }
            return folder(for: destination).map { FileManager.default.fileExists(atPath: $0.path) } ?? true
        }

        // Editing on the sort page.

        /// Footage, photos and documents never go in the Bin: only card housekeeping can.
        public func accepts(_ bundle: Bundle, at destination: Destination) -> Bool {
            destination.role != .bin || bundle.housekeeping
        }

        public mutating func move(bundle id: String, to destination: String) {
            guard let bundle = destinations.lazy.flatMap(\.bundles).first(where: { $0.id == id }),
                  let target = destinations.first(where: { $0.id == destination }),
                  accepts(bundle, at: target) else { return }
            for i in destinations.indices { destinations[i].bundles.removeAll { $0.id == id } }
            if let i = destinations.firstIndex(where: { $0.id == destination }) { destinations[i].bundles.append(bundle) }
        }

        /// A new, empty camera folder at the end of Footage/. Returns its id.
        @discardableResult
        public mutating func addCameraFolder(named name: String = "New folder") -> String {
            var unique = name
            var n = 2
            while cameras.contains(where: { $0.name.lowercased() == unique.lowercased() }) { unique = "\(name) \(n)"; n += 1 }
            let folder = Destination(id: "camera:new:\(UUID().uuidString)", role: .camera, name: unique, originalName: nil,
                                     bundles: [], userAdded: true)
            let at = destinations.lastIndex(where: { $0.role == .camera }).map { $0 + 1 } ?? 0
            destinations.insert(folder, at: at)
            return folder.id
        }

        /// Whether Photos/Project/Assets belong in the tree: on the drive already, getting files,
        /// or added by hand. A photo-only shoot shows no Footage or Project.
        public func shows(_ destination: Destination) -> Bool {
            destination.fileCount > 0 || destination.userAdded || exists(destination)
        }

        /// "New folder ▸ Photos/Project/Assets": made on Sort even if nothing is dragged into it.
        public mutating func add(_ role: Destination.Role) {
            if let i = destinations.firstIndex(where: { $0.role == role }) { destinations[i].userAdded = true }
        }

        /// "Keep the name": the folder's current name stands without a camera check.
        public mutating func keepCameraName(_ id: String) {
            guard let i = destinations.firstIndex(where: { $0.id == id }) else { return }
            destinations[i].keptName = ShootPlan.clean(destinations[i].name)
        }

        /// Removes a new camera folder from the plan; whatever was going into it stays where
        /// it is. (Folders already on the drive aren't removed here.)
        public mutating func remove(_ id: String) {
            guard let i = destinations.firstIndex(where: { $0.id == id && $0.role == .camera && $0.originalName == nil })
            else { return }
            let bundles = destinations.remove(at: i).bundles
            if let stay = destinations.firstIndex(where: { $0.role == .stay }) { destinations[stay].bundles += bundles }
        }
    }

    /// Clips read per already-sorted bundle to tell which camera it holds.
    static let clipsSampled = 3

    /// Folders inside a shoot that are already sorted.
    static let sortedFolders: Set<String> = ["footage", "photos", "project", "assets"]

    /// "render" -> "Render": a camera folder named after a lowercase folder starts with a
    /// capital, like Footage and Photos. A lowercase one already on the drive is renamed on
    /// sort. Names with a capital or a digit ("iPhone", "fx3") are left as they are.
    static func titled(_ name: String) -> String {
        guard let first = name.first, name == name.lowercased(), !name.contains(where: \.isNumber) else { return name }
        return first.uppercased() + name.dropFirst()
    }

    /// The whole shoot as a tree of destinations: what's already in Footage/<camera>, Photos
    /// and Project stays there (and can be renamed or moved), and everything loose is placed
    /// where it would normally go — clips (with their thumbnails and XML) in a folder per
    /// camera, photos and their edits in Photos, music in Assets, anything else left where it is.
    /// `name` turns a detected camera into a folder name (saved names like "A-cam" apply);
    /// `learned` holds folder names chosen on earlier sorts, by `Bundle.learnKey`.
    /// `knownCameras` are what the user calls their cameras; `kept` (lowercased) are camera
    /// folder names in this shoot already kept on the sort page. Both feed `cameraCheck`.
    public static func plan(_ shoot: URL, name: (CameraInfo) -> String = { $0.shortName },
                            learned: [String: String] = [:], knownCameras: [String] = [],
                            kept: Set<String> = []) -> SortPlan {
        var all: [URL] = []
        let keys: [URLResourceKey] = [.isRegularFileKey]
        let walker = FileManager.default.enumerator(at: shoot, includingPropertiesForKeys: keys,
                                                    options: [.skipsHiddenFiles])
        while let url = walker?.nextObject() as? URL {
            if (try? url.resourceValues(forKeys: Set(keys)))?.isRegularFile == true { all.append(url) }
        }
        all.sort { $0.path < $1.path }

        let shootDepth = shoot.standardizedFileURL.pathComponents.count
        func parts(_ url: URL) -> [String] {
            Array(url.deletingLastPathComponent().standardizedFileURL.pathComponents.dropFirst(shootDepth))
        }
        func isMedia(_ url: URL) -> Bool { Card.mediaExtensions.contains(url.pathExtension.lowercased()) }
        func kind(_ url: URL) -> MediaFile.Kind? { isMedia(url) ? Card.kind(of: url) : nil }
        /// The folder a camera's clips go in: the name chosen for it on an earlier sort, else its own.
        func folderName(_ camera: CameraInfo) -> String { ShootPlan.clean(learned["model:\(camera.model)"] ?? name(camera)) }

        // Camera folders by lowercased name: (display name, name on disk, bundles).
        var cameras: [String: (name: String, original: String?, bundles: [Bundle])] = [:]
        let footage = shoot.appendingPathComponent("Footage")
        for existing in ShootLibrary.subfolders(of: footage).map(\.lastPathComponent) where !isLeftoverFolder(existing) {
            cameras[existing.lowercased()] = (titled(existing), existing, [])
        }
        var photos: [Bundle] = []
        var project: [Bundle] = []
        var assets: [Bundle] = []
        var stay: [Bundle] = []
        var housekeeping: [String: [URL]] = [:]

        // Which camera folder each clip went to, so its sidecars can follow it. Keyed by card
        // folder + stem: two cameras' cards both have a "C0001", and a Sony card keeps the clip
        // in CLIP/ and its thumbnail in THMBNL/ beside it.
        func clipKey(_ url: URL) -> String {
            var p = parts(url)
            if let last = p.last, ["CLIP", "THMBNL", "SUB"].contains(last.uppercased()) { p.removeLast() }
            return "\(p.joined(separator: "/"))|\(stemKey(url))"
        }

        var cameraOfStem: [String: String] = [:]
        var cameraOfFolder: [String: String] = [:]

        // Same, by stem alone: a stabilised export ("C8801_stabilized.mp4") is often in another
        // folder from its source clip.
        var cameraOfAnyStem: [String: String] = [:]

        // Already sorted: bundle by folder and type, home is where they are.
        var homed: [String: [URL]] = [:]
        var loose: [URL] = []
        for url in all {
            let p = parts(url)
            let top = p.first?.lowercased()
            // A card tree copied whole (AVCHD, Canon XF, RED…) is never taken apart: its index
            // files join the clips. Inside a camera folder it stays put; anywhere else it's left out.
            let inTree = p.indices.contains { Card.treeRoot(of: p[$0...].joined(separator: "/") + "/") != nil }
            if inTree && !(top == "footage" && p.count >= 3) { continue }
            if !inTree, Card.isSonyHousekeeping(url, in: p) {
                housekeeping["\(p.joined(separator: "/"))|\(url.pathExtension.uppercased())", default: []].append(url)
                continue
            }
            if top == "footage", p.count >= 2, inTree {
                homed["\(p.joined(separator: "/"))|\(url.pathExtension.uppercased())", default: []].append(url)
            } else if top == "footage", p.count >= 2 {
                // Sorted already — unless it's in a leftover folder ("CLIP", "Untitled") or in the
                // wrong sub-folder of its camera (an XML outside Metadata/): then it's re-sorted.
                if isLeftoverFolder(p[1]) || misplaced(url, inCamera: Array(p.dropFirst(2))) {
                    loose.append(url)
                } else {
                    homed["\(p.joined(separator: "/"))|\(url.pathExtension.uppercased())", default: []].append(url)
                }
            } else if top == "photos" || top == "project" || top == "assets" {
                homed["\(p.joined(separator: "/"))|\(url.pathExtension.uppercased())", default: []].append(url)
            } else {
                loose.append(url)
            }
        }
        for (key, files) in homed {
            let split = key.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
            let p = split[0].split(separator: "/").map(String.init)
            let type = split[1].isEmpty ? "FILE" : split[1]
            switch p[0].lowercased() {
            case "footage":
                let folder = p[1]
                for url in files where kind(url) == .video {
                    cameraOfStem[clipKey(url)] = folder
                    cameraOfAnyStem[stemKey(url)] = folder
                }
                // A few clips are enough to say which camera the folder holds.
                let shotOn = kind(files[0]) == .video ? files.prefix(Self.clipsSampled).lazy.compactMap(camera(ofClip:)).first : nil
                var bundle = Bundle(id: "home:\(key)", type: type, origin: split[0], kind: kind(files[0]), files: files,
                                    camera: shotOn, home: "camera:\(folder.lowercased())", learnKey: nil)
                bundle.cameraName = shotOn.map(folderName)
                cameras[folder.lowercased(), default: (folder, folder, [])].bundles.append(bundle)
            case "photos":
                photos.append(Bundle(id: "home:\(key)", type: type, origin: split[0], kind: kind(files[0]), files: files,
                                     camera: nil, home: "photos", learnKey: nil))
            case "assets":
                assets.append(Bundle(id: "home:\(key)", type: type, origin: split[0], kind: kind(files[0]), files: files,
                                     camera: nil, home: "assets", learnKey: nil))
            default:
                project.append(Bundle(id: "home:\(key)", type: type, origin: split[0], kind: kind(files[0]), files: files,
                                      camera: nil, home: "project", learnKey: nil))
            }
        }

        // Loose clips: camera per file, so a folder mixing two cameras splits in two.
        var clipGroups: [String: (camera: CameraInfo?, folder: String, learnKey: String, files: [URL])] = [:]
        for url in loose where kind(url) == .video && stabilisedSource(url) == nil {
            let model = camera(ofClip: url)
            let from = parts(url).joined(separator: "/")
            let fromName = (from as NSString).lastPathComponent
            let type = url.pathExtension.uppercased()
            let learnKey = model.map { "model:\($0.model)" } ?? "from:\(fromName.lowercased())|\(type)"
            let folder = model.map(folderName) ?? titled(ShootPlan.clean(learned[learnKey] ?? (from.isEmpty ? "Camera" : fromName)))
            let key = "\(from)|\(type)|\(folder)"
            clipGroups[key, default: (model, folder, learnKey, [])].files.append(url)
            cameraOfStem[clipKey(url)] = folder
            // First clip with this stem wins: the original, not its proxy in a Proxy/ sub-folder.
            cameraOfAnyStem[stemKey(url)] = cameraOfAnyStem[stemKey(url)] ?? folder
            cameraOfFolder[from] = cameraOfFolder[from] ?? folder
        }

        // Stabilised exports go with the camera of the clip they came from, in Stabilised/;
        // Gyroflow project files go with the stabilised clips in their folder.
        var stabVotes: [String: [String: Int]] = [:]
        var stabGroups: [String: [URL]] = [:]
        var stabNamed: Set<String> = []  // groups whose own files named the camera
        for url in loose where kind(url) == .video {
            guard let source = stabilisedSource(url) else { continue }
            let from = parts(url).joined(separator: "/")
            let named = cameraOfAnyStem[source] == nil ? camera(ofClip: url).map(folderName) : nil
            let folder = cameraOfAnyStem[source] ?? named ?? cameraOfFolder[from]
                ?? titled(ShootPlan.clean(from.isEmpty ? "Camera" : (from as NSString).lastPathComponent))
            let key = "\(from)|\(url.pathExtension.uppercased())|\(folder)"
            stabGroups[key, default: []].append(url)
            if named != nil { stabNamed.insert(key) }
            stabVotes[from, default: [:]][folder, default: 0] += 1
        }
        for url in loose where url.pathExtension.lowercased() == "gyroflow" {
            let from = parts(url).joined(separator: "/")
            guard let folder = stabVotes[from]?.max(by: { $0.value < $1.value })?.key
                    ?? cameraOfFolder[from] else { continue }
            stabGroups["\(from)|GYROFLOW|\(folder)", default: []].append(url)
        }
        for (key, files) in stabGroups {
            let split = key.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
            var bundle = Bundle(id: "stab:\(key)", type: split[1], origin: split[0], kind: kind(files[0]), files: files,
                                camera: nil, home: "stay", learnKey: nil, stabilised: true)
            if stabNamed.contains(key) { bundle.cameraName = split[2] }
            cameras[split[2].lowercased(), default: (split[2], nil, [])].bundles.append(bundle)
        }
        let stabFiles = Set(stabGroups.values.joined())
        for (key, group) in clipGroups {
            let split = key.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
            var bundle = Bundle(id: key, type: split[1], origin: split[0], kind: .video, files: group.files,
                                camera: group.camera, home: "stay", learnKey: group.learnKey)
            if group.camera != nil { bundle.cameraName = group.folder }
            cameras[group.folder.lowercased(), default: (group.folder, nil, [])].bundles.append(bundle)
        }

        // A Sony clip XML whose clip isn't here still names its camera.
        func sidecarCamera(_ url: URL) -> String? {
            guard url.pathExtension.lowercased() == "xml",
                  let xml = try? String(contentsOf: url, encoding: .utf8),
                  let info = CameraInfo.parse(sonyXML: xml) else { return nil }
            return folderName(info)
        }

        // The rest of the loose files, by (folder, type); sidecars follow their clip's camera.
        // Each camera keeps its own thumbnails and XML. A sidecar goes with its clip (same stem);
        // one whose clip isn't here goes with the camera the rest of its folder's sidecars
        // belong to (cards keep each camera's THMBNL apart), else the camera its XML names.
        let sidecars = loose.filter { kind($0) == .thumbnail || kind($0) == .metadata }
        var sidecarVotes: [String: [String: Int]] = [:]
        for url in sidecars {
            if let folder = cameraOfStem[clipKey(url)] ?? cameraOfAnyStem[stemKey(url)] { sidecarVotes[parts(url).joined(separator: "/"), default: [:]][folder, default: 0] += 1 }
        }
        let sidecarFolder = sidecarVotes.mapValues { $0.max { $0.value < $1.value }!.key }
        var buckets: [String: [URL]] = [:]
        for url in loose where kind(url) != .video && !stabFiles.contains(url) {
            let from = parts(url).joined(separator: "/")
            var key = "\(from)|\(url.pathExtension.uppercased())|"
            if let k = kind(url), k == .thumbnail || k == .metadata,
               let folder = cameraOfStem[clipKey(url)] ?? sidecarFolder[from] ?? cameraOfFolder[from]
                   ?? cameraOfAnyStem[stemKey(url)] ?? sidecarCamera(url) {
                key += folder
            }
            buckets[key, default: []].append(url)
        }
        for (key, files) in buckets {
            let split = key.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
            let k = kind(files[0])
            let fromName = (split[0] as NSString).lastPathComponent.lowercased()
            let bundle = Bundle(id: key, type: split[1].isEmpty ? "FILE" : split[1], origin: split[0], kind: k,
                                files: files, camera: nil, home: "stay",
                                learnKey: k == nil ? "from:\(fromName)|\(split[1])" : nil)
            if Card.musicExtensions.contains(split[1].lowercased()) { assets.append(bundle); continue }
            switch k {
            case .photo, .photoEdit: photos.append(bundle)  // edit sidecars (.xmp) stay with their photos
            case .thumbnail?, .metadata?:
                if split[2].isEmpty { stay.append(bundle); break }  // no camera to follow
                cameras[split[2].lowercased(), default: (split[2], nil, [])].bundles.append(bundle)
            default: stay.append(bundle)  // documents, and sidecars with no clip to follow
            }
        }

        let order: (Bundle, Bundle) -> Bool = {
            ($0.home == "stay" ? 1 : 0, $0.isSidecar ? 1 : 0, $0.origin, $0.type)
                < ($1.home == "stay" ? 1 : 0, $1.isSidecar ? 1 : 0, $1.origin, $1.type)
        }
        var destinations = cameras.keys.sorted().map { key in
            let c = cameras[key]!
            return Destination(id: "camera:\(key)", role: .camera, name: c.name, originalName: c.original,
                               bundles: c.bundles.sorted(by: order),
                               emptyOnDisk: c.original != nil && c.bundles.isEmpty
                                   && isEmptyFolder(footage.appendingPathComponent(c.original!)),
                               keptName: kept.contains(c.name.lowercased()) ? c.name : nil)
        }
        destinations.append(Destination(id: "photos", role: .photos, name: "Photos", originalName: nil,
                                        bundles: photos.sorted(by: order)))
        destinations.append(Destination(id: "project", role: .project, name: "Project", originalName: nil,
                                        bundles: project.sorted(by: order)))
        destinations.append(Destination(id: "assets", role: .assets, name: "Assets", originalName: nil,
                                        bundles: assets.sorted(by: order)))
        destinations.append(Destination(id: "stay", role: .stay, name: "Stays where it is", originalName: nil,
                                        bundles: stay.sorted(by: order)))
        let binned = housekeeping.map { key, files in
            let split = key.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
            var bundle = Bundle(id: "bin:\(key)", type: split[1].isEmpty ? "FILE" : split[1], origin: split[0],
                                kind: kind(files[0]), files: files, camera: nil, home: "stay", learnKey: nil)
            bundle.housekeeping = true
            return bundle
        }
        destinations.append(Destination(id: "bin", role: .bin, name: "Goes in the Bin", originalName: nil,
                                        bundles: binned.sorted(by: order)))
        return SortPlan(shoot: shoot, destinations: destinations, knownCameras: knownCameras)
    }

    /// Folder names to remember after a sort: each bundle's learn key -> the name of the
    /// camera or other folder it ended up in.
    public static func learnedNames(_ plan: SortPlan) -> [String: String] {
        var names: [String: String] = [:]
        for destination in plan.cameras {
            for bundle in destination.bundles { if let key = bundle.learnKey { names[key] = destination.name } }
        }
        return names
    }

    /// Folders inside Footage/ that aren't a camera: card structure and placeholder names
    /// ("CLIP", "THMBNL", "Untitled", "New folder", "100MSDCF"…). What's in them is re-sorted.
    static func isLeftoverFolder(_ name: String) -> Bool {
        let n = name.lowercased()
        return ["clip", "thmbnl", "sub", "m4root", "private", "dcim", "camera", "thumbnails", "metadata",
                "stabilised", "stabilized"].contains(n)
            || n.hasPrefix("untitled") || n.hasPrefix("new folder")
            || n.range(of: #"^\d{3}[a-z_]{5}$"#, options: .regularExpression) != nil
    }

    /// A file inside a camera folder but not in the sub-folder its kind belongs in.
    /// `below` is the path under Footage/<camera>/ (empty for the camera folder itself).
    static func misplaced(_ url: URL, inCamera below: [String]) -> Bool {
        let sub = below.first?.lowercased()
        let kind = Card.mediaExtensions.contains(url.pathExtension.lowercased()) ? Card.kind(of: url) : nil
        let expected: String? = if stabilisedSource(url) != nil && kind == .video || url.pathExtension.lowercased() == "gyroflow" {
            "stabilised"
        } else if kind == .thumbnail {
            "thumbnails"
        } else if kind == .metadata {
            "metadata"
        } else {
            nil
        }
        if let expected { return sub != expected }
        return kind == .video && ["thumbnails", "metadata", "stabilised"].contains(sub ?? "")
    }

    /// "C8801_stabilized.mp4" / "C8801_stab422.mp4" / "C8801 stabilised.mov" -> "C8801";
    /// nil for anything that isn't a stabilised export.
    static func stabilisedSource(_ url: URL) -> String? {
        let stem = url.deletingPathExtension().lastPathComponent
        guard let range = stem.range(of: #"[ _.-]*stab(ili[sz]ed)?"#, options: [.regularExpression, .caseInsensitive]),
              range.lowerBound > stem.startIndex else { return nil }
        return String(stem[..<range.lowerBound]).uppercased()
    }

    /// "C0001M01.XML" / "C0001T01.JPG" / "C0001.THM" / "C0001.MP4" -> "C0001".
    static func stemKey(_ url: URL) -> String { SessionPlan.ownerStem(url) }

    /// Which camera made a clip: Sony's sidecar XML, then the metadata Sony embeds in the
    /// file itself, then QuickTime's model tag (iPhone, DJI, most others).
    static func camera(ofClip url: URL) -> CameraInfo? {
        let folder = url.deletingLastPathComponent()
        let stem = url.deletingPathExtension().lastPathComponent
        // Beside the clip on a card; in Metadata/ once sorted.
        for sidecar in ["\(stem)M01.XML", "\(stem)M01.xml", "Metadata/\(stem)M01.XML", "Metadata/\(stem)M01.xml"] {
            if let xml = try? String(contentsOf: folder.appendingPathComponent(sidecar), encoding: .utf8),
               let info = CameraInfo.parse(sonyXML: xml) { return info }
        }
        if let info = embeddedSonyXML(url) { return info }
        return quickTimeModel(url)
    }

    /// Sony MP4s carry their XML (with modelName) in a box near the start or end of the file.
    static func embeddedSonyXML(_ url: URL) -> CameraInfo? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        let window = 2 << 20
        var chunks: [Data] = []
        if let head = try? handle.read(upToCount: window) { chunks.append(head) }
        if let size = try? handle.seekToEnd(), size > UInt64(window) {
            try? handle.seek(toOffset: size - UInt64(window))
            if let tail = try? handle.readToEnd() { chunks.append(tail) }
        }
        for chunk in chunks {
            guard let range = chunk.range(of: Data("<Device ".utf8)) else { continue }
            let end = min(chunk.endIndex, range.lowerBound + 400)
            if let text = String(data: chunk[range.lowerBound..<end], encoding: .ascii) ?? String(data: chunk[range.lowerBound..<end], encoding: .isoLatin1),
               let info = CameraInfo.parse(sonyXML: text) { return info }
        }
        return nil
    }

    static func quickTimeModel(_ url: URL) -> CameraInfo? {
        let asset = AVURLAsset(url: url)
        let wait = DispatchSemaphore(value: 0)
        nonisolated(unsafe) var found: CameraInfo?
        Task {
            defer { wait.signal() }
            guard let items = try? await asset.load(.metadata) else { return }
            for item in items {
                let key = (item.identifier?.rawValue ?? "").lowercased()
                guard key.hasSuffix("model") || item.commonKey == .commonKeyModel,
                      let model = try? await item.load(.stringValue), !model.isEmpty else { continue }
                found = CameraInfo(model: model, serial: nil)
                return
            }
        }
        wait.wait()
        return found
    }

    // MARK: Apply / undo

    public struct Move: Codable, Sendable {
        public let from: String
        public let to: String
    }

    struct SortLog: Codable {
        var moves: [Move]
        /// Folders that were left empty and removed; recreated on undo.
        var removedFolders: [String]
        var sortedAt: Date
        /// Camera folders renamed on the sort page; renamed back on undo.
        var renamedFolders: [Move]?
    }

    static func logURL(_ shoot: URL) -> URL { shoot.appendingPathComponent(".cairn/sort-log.json") }

    public static func canUndoSort(_ shoot: URL) -> Bool {
        FileManager.default.fileExists(atPath: logURL(shoot).path)
    }

    /// Renames camera folders, then moves every file whose destination changed. Never
    /// overwrites: a name already taken gets " (2)". Writes the log first and after every
    /// step, so a sort cut short can still be undone.
    @discardableResult
    public static func apply(_ plan: SortPlan) throws -> Int {
        let fm = FileManager.default
        if let duplicate = plan.duplicateName { throw TidyError.exists(duplicate) }
        try fm.createDirectory(at: logURL(plan.shoot).deletingLastPathComponent(), withIntermediateDirectories: true)
        var log = SortLog(moves: [], removedFolders: [], sortedAt: Date(), renamedFolders: [])
        func save() throws { try Handoff.encoder.encode(log).write(to: logURL(plan.shoot), options: .atomic) }
        try save()

        // 1. Renames (through a temporary name, so swapping two names or a case-only change works).
        let footage = plan.shoot.appendingPathComponent("Footage")
        var renamed: [(old: String, new: String)] = []
        for destination in plan.renames {
            let old = footage.appendingPathComponent(destination.originalName!)
            let new = footage.appendingPathComponent(ShootPlan.clean(destination.name))
            let caseOnly = old.path.lowercased() == new.path.lowercased()
            if fm.fileExists(atPath: new.path), !caseOnly, !plan.renames.contains(where: { $0.originalName == new.lastPathComponent }) {
                throw TidyError.exists(new.lastPathComponent)
            }
            let temp = footage.appendingPathComponent(".renaming-\(UUID().uuidString)")
            try fm.moveItem(at: old, to: temp)
            renamed.append((temp.path, new.path))
            log.renamedFolders?.append(Move(from: old.path, to: new.path))
            try save()
        }
        for (temp, new) in renamed { try fm.moveItem(atPath: temp, toPath: new) }

        // 2. Moves.
        var targets: [(URL, URL)] = []
        for destination in plan.destinations {
            guard let folder = plan.folder(for: destination) else { continue }
            if destination.userAdded { try fm.createDirectory(at: folder, withIntermediateDirectories: true) }
            for bundle in destination.bundles where bundle.home != destination.id {
                // In a camera folder, thumbnails and XML get their own sub-folders (as when copying a card).
                let target: URL = switch (destination.role, bundle.kind) {
                case (.camera, .thumbnail?): folder.appendingPathComponent("Thumbnails")
                case (.camera, .metadata?): folder.appendingPathComponent("Metadata")
                case (.camera, _) where bundle.stabilised: folder.appendingPathComponent("Stabilised")
                default: folder
                }
                targets += bundle.files.map { (current($0, after: log.renamedFolders ?? []), target) }
            }
        }
        var sourceFolders = Set(targets.map { $0.0.deletingLastPathComponent() })
        var mapping: [String: String] = [:]
        for (source, folder) in targets {
            try fm.createDirectory(at: folder, withIntermediateDirectories: true)
            let dest = Copier.uniqueDestination(for: source.lastPathComponent, in: folder)
            try fm.moveItem(at: source, to: dest)
            log.moves.append(Move(from: source.path, to: dest.path))
            mapping[source.standardizedFileURL.path] = dest.standardizedFileURL.path
            try save()
        }
        // 3. Card housekeeping to the drive's Bin (not deleted outright, so undo can bring it back).
        for bundle in plan.destination(.bin)?.bundles ?? [] where bundle.housekeeping {
            for file in bundle.files {
                var trashed: NSURL?
                try fm.trashItem(at: file, resultingItemURL: &trashed)
                guard let trashed = trashed as URL? else { continue }
                log.moves.append(Move(from: file.path, to: trashed.path))
                sourceFolders.insert(file.deletingLastPathComponent())
                try save()
            }
        }
        // Tidy up folders the move emptied (only if nothing but Finder's .DS_Store is left).
        // (and on up: Footage/Untitled/Thumbnails, then Footage/Untitled).
        let shootPath = plan.shoot.standardizedFileURL.path
        for start in sourceFolders.sorted(by: { $0.path.count > $1.path.count }) {
            var folder = start.standardizedFileURL
            while folder.path.count > shootPath.count, folder.path.hasPrefix(shootPath), removeIfEmpty(folder) {
                log.removedFolders.append(folder.path)
                folder = folder.deletingLastPathComponent()
            }
        }
        try save()
        let renames = log.renamedFolders ?? []
        try Handoff.relocate(shootFolder: plan.shoot, to: plan.shoot) { path in
            mapping[path] ?? current(URL(fileURLWithPath: path), after: renames).path
        }
        return log.moves.count + renames.count
    }

    /// Where a file is after its camera folder was renamed.
    static func current(_ url: URL, after renames: [Move]) -> URL {
        let path = url.standardizedFileURL.path
        for rename in renames where path.hasPrefix(rename.from + "/") {
            return URL(fileURLWithPath: rename.to + path.dropFirst(rename.from.count))
        }
        return url
    }

    /// Puts every file back where the last sort found it, and renamed folders back to their
    /// old names. A spot that's been taken since is skipped and counted in the result.
    @discardableResult
    public static func undoSort(_ shoot: URL) throws -> (restored: Int, skipped: Int) {
        let fm = FileManager.default
        let data = try Data(contentsOf: logURL(shoot))
        let log = try Handoff.decoder.decode(SortLog.self, from: data)
        for folder in log.removedFolders {
            try fm.createDirectory(atPath: folder, withIntermediateDirectories: true)
        }
        var restored = 0
        var skipped = 0
        var mapping: [String: String] = [:]
        for move in log.moves.reversed() {
            guard fm.fileExists(atPath: move.to), !fm.fileExists(atPath: move.from) else { skipped += 1; continue }
            try fm.createDirectory(at: URL(fileURLWithPath: move.from).deletingLastPathComponent(),
                                   withIntermediateDirectories: true)
            try fm.moveItem(atPath: move.to, toPath: move.from)
            mapping[move.to] = move.from
            restored += 1
        }
        let renames = log.renamedFolders ?? []
        let renamedNow = Set(renames.map(\.to))
        for folder in log.moves.map({ URL(fileURLWithPath: $0.to).deletingLastPathComponent() })
        where !renamedNow.contains(folder.path) && !folder.path.contains("/.Trash") {
            _ = removeIfEmpty(folder)
            let parent = folder.deletingLastPathComponent()
            if !renamedNow.contains(parent.path) { _ = removeIfEmpty(parent) }  // Footage/<Camera> -> Footage
        }
        let back = renames.map { Move(from: $0.to, to: $0.from) }
        for rename in renames.reversed() {
            guard fm.fileExists(atPath: rename.to) else { skipped += 1; continue }
            let caseOnly = rename.to.lowercased() == rename.from.lowercased()
            guard caseOnly || !fm.fileExists(atPath: rename.from) else { skipped += 1; continue }
            try fm.moveItem(atPath: rename.to, toPath: rename.from)
            restored += 1
        }
        try Handoff.relocate(shootFolder: shoot, to: shoot) { path in
            mapping[path] ?? current(URL(fileURLWithPath: path), after: back).path
        }
        try fm.removeItem(at: logURL(shoot))  // Offload's own log file, not footage
        return (restored, skipped)
    }

    /// Holds nothing but Finder's .DS_Store (and exFAT's ._ companions of it).
    static func isEmptyFolder(_ folder: URL) -> Bool {
        guard let items = try? FileManager.default.contentsOfDirectory(atPath: folder.path) else { return false }
        return items.allSatisfy { $0 == ".DS_Store" || $0 == "._.DS_Store" }
    }

    /// Removes `folder` only if it's empty (see `isEmptyFolder`). Returns whether it did.
    static func removeIfEmpty(_ folder: URL) -> Bool {
        isEmptyFolder(folder) && (try? FileManager.default.removeItem(at: folder)) != nil
    }

    /// A shoot with no video doesn't need Footage or Project: removes them (and any camera
    /// folders inside Footage) if they're empty. Older offloads made them for every card.
    @discardableResult
    public static func removeUnusedFolders(_ plan: SortPlan) -> Bool {
        guard !plan.destinations.flatMap(\.bundles).contains(where: { $0.kind == .video }) else { return false }
        let footage = plan.shoot.appendingPathComponent("Footage")
        for camera in ShootLibrary.subfolders(of: footage) { _ = removeIfEmpty(camera) }
        let a = removeIfEmpty(footage), b = removeIfEmpty(plan.shoot.appendingPathComponent("Project"))
        return a || b
    }

    /// "Delete empty folder" on the sort page: removes an empty camera folder, and Footage/
    /// too if that leaves it empty. Refuses (returns false) if anything has appeared in it.
    @discardableResult
    public static func deleteEmptyCameraFolder(_ folder: URL) -> Bool {
        guard removeIfEmpty(folder) else { return false }
        _ = removeIfEmpty(folder.deletingLastPathComponent())
        return true
    }
}

extension Handoff {
    /// After a shoot folder is renamed or its files moved: rewrites every path in its
    /// .cairn/offload.json through `transform`, and points Cairn's inbox at the new folder.
    static func relocate(shootFolder old: URL, to new: URL, inbox: URL = defaultInbox,
                         transform: (String) -> String) throws {
        let sidecar = new.appendingPathComponent(".cairn/offload.json")
        if let data = try? Data(contentsOf: sidecar), let m = try? decoder.decode(ShootManifest.self, from: data) {
            let moved = ShootManifest(
                client: m.client, shootName: m.shootName, date: m.date, camera: m.camera,
                cameraModel: m.cameraModel, cameraSerial: m.cameraSerial, cardName: m.cardName,
                shootFolder: new.path, footageFolder: transform(m.footageFolder), photosFolder: m.photosFolder.map { transform($0) },
                copiedAt: m.copiedAt,
                files: m.files.map {
                    CopiedFile(source: $0.source, destination: URL(fileURLWithPath: transform($0.destination.path)),
                               size: $0.size, sha256: $0.sha256)
                }
            )
            try encoder.encode(moved).write(to: sidecar, options: .atomic)
        }
        guard old.standardizedFileURL != new.standardizedFileURL else { return }
        let entries = readInbox(inbox)
        guard entries.contains(where: { $0.shootFolder == old.path }) else { return }
        let updated = entries.map { entry in
            entry.shootFolder == old.path
                ? InboxEntry(shootFolder: new.path, manifest: sidecar.path, addedAt: entry.addedAt) : entry
        }
        try encoder.encode(updated).write(to: inbox, options: .atomic)
    }
}
