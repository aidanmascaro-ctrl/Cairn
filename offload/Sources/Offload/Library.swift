import AppKit
import SwiftUI
import CoreServices
import OffloadCore
import UniformTypeIdentifiers

/// The shoot catalog behind the Library tab: which shoots are on which drive, kept on the
/// Mac and mirrored to an Obsidian note. Drives are scanned when they're plugged in.
final class LibraryModel: ObservableObject {
    static let notePathKey = "catalogNotePath"

    @Published private(set) var catalog = Catalog.load()
    @Published var query = ""
    /// Plugged-in drives by catalog id.
    @Published private(set) var mounted: [String: URL] = [:]
    /// Drives being scanned right now (by catalog id).
    @Published private(set) var scanning: Set<String> = []
    @Published var reviewing = false
    @Published var renaming: RenameForm?
    @Published var sorting: SortForm?
    /// The shoot last opened for sorting or renaming: tinted in the list as a "you were here".
    @Published private(set) var lastShootID: String?
    /// Set when a page closes; the list scrolls to `lastShootID` once that row is there.
    var scrollToLastShoot = false
    /// Review-page ticks the user changed from the suggested default (by id).
    @Published var flipped: Set<String> = []
    /// Measured list heights, so lists hug their rows instead of leaving empty space.
    @Published var listContentHeight: CGFloat = 0
    @Published var reviewContentHeight: CGFloat = 0
    @Published var noteError: String?
    /// The drive whose "Where it lives" note is being edited (one at a time).
    @Published var editingNote: String?
    /// Box in and out of a drive note: a soft cross-fade, same easing as the tabs.
    static let noteFade = Animation.easeInOut(duration: 0.25)
    func closeNote() { withAnimation(Self.noteFade) { editingNote = nil } }
    @Published var notePath: String? = UserDefaults.standard.string(forKey: notePathKey) {
        didSet { UserDefaults.standard.set(notePath, forKey: Self.notePathKey); writeNote() }
    }

    /// Watches each plugged-in drive so renames and moves show up without a manual scan.
    private var watchers: [String: DriveWatcher] = [:]
    /// Drives that changed again while being scanned; scanned once more when that finishes.
    private var queued: Set<String> = []

    /// The working folder, for scanning the Mac's own disk only there.
    var root: () -> URL? = { nil }
    /// What to call a detected camera ("A-cam" if you've named it, else "FX3").
    var cameraName: (CameraInfo) -> String = { $0.shortName }

    var results: [CatalogShoot] { catalog.search(query) }

    func driveName(_ id: String) -> String { catalog.drive(id)?.name ?? "Unknown drive" }
    func isOnline(_ shoot: CatalogShoot) -> Bool { mounted[shoot.driveID] != nil }
    func folder(of shoot: CatalogShoot) -> URL? { mounted[shoot.driveID]?.appendingPathComponent(shoot.path) }

    // MARK: Drives coming and going

    func volumeMounted(_ volume: URL) {
        guard !Card.isCard(volume), let identity = DriveScan.identity(of: volume) else { return }
        let isInternal = (try? volume.resourceValues(forKeys: [.volumeIsInternalKey]).volumeIsInternal) ?? false
        let rootOnThisVolume = root().map { Self.volume(of: $0) == volume.standardizedFileURL } ?? false
        // The Mac's own disk is always there, so its shoots can always be opened; but it's
        // only scanned (and watched) inside the working folder — or, once the working folder
        // has moved to another drive, inside the folder it was last scanned in. Never all of it.
        mounted[identity.id] = volume
        let remembered = catalog.drive(identity.id)?.scanRoot.map { URL(fileURLWithPath: $0, isDirectory: true) }
            // Catalogs from before scanRoot was saved: the folder its clients sit in.
            ?? catalog.clients.first { $0.driveID == identity.id }
                .map { volume.appendingPathComponent($0.path).deletingLastPathComponent() }
        let scanRoot = isInternal ? (rootOnThisVolume ? root() : remembered) : nil
        guard !isInternal || scanRoot != nil else { return }
        if watchers[identity.id]?.path != scanRoot ?? volume {
            watchers[identity.id] = DriveWatcher(path: scanRoot ?? volume) { [weak self] in self?.volumeMounted(volume) }
        }
        scan(volume, identity: identity, under: scanRoot)
    }

    func volumeUnmounted(_ volume: URL) {
        let gone = mounted.filter { $0.value.standardizedFileURL == volume.standardizedFileURL }.map(\.key)
        gone.forEach { watchers[$0] = nil }
        mounted = mounted.filter { $0.value.standardizedFileURL != volume.standardizedFileURL }
    }

    /// Re-reads the drive holding `folder`, e.g. after a copy landed there.
    func rescan(containing folder: URL) {
        guard let volume = Self.volume(of: folder) else { return }
        volumeMounted(volume)
    }

    func rescanAll() { mounted.values.forEach(volumeMounted) }

    private func scan(_ volume: URL, identity: DriveScan.Identity, under folder: URL?) {
        let scanRoot = folder?.path
        guard !scanning.contains(identity.id) else { queued.insert(identity.id); return }
        scanning.insert(identity.id)
        let root = folder ?? volume
        Task.detached(priority: .utility) { [weak self] in
            let found = DriveScan.scan(root, volume: volume, driveID: identity.id)
            await MainActor.run {
                guard let self else { return }
                self.scanning.remove(identity.id)
                // A drive with no folders (an installer disk image) isn't worth listing.
                guard !found.isEmpty || self.catalog.drive(identity.id) != nil else { return }
                // A scan that ran while the drive was ejecting reads folders as empty, and merging
                // it would wipe every shoot it missed. Keep the catalog unless the drive is still
                // here and every confirmed shoot it missed is really gone from it.
                let foundIDs = Set(found.shoots.map(\.id))
                let missedButThere = self.catalog.folders.contains {
                    $0.driveID == identity.id && $0.review == .confirmed && !foundIDs.contains($0.id)
                        && FileManager.default.fileExists(atPath: volume.appendingPathComponent($0.path).path)
                }
                guard self.mounted[identity.id] != nil, !missedButThere else {
                    self.queued.remove(identity.id)
                    return
                }
                let drive = CatalogDrive(id: identity.id, name: identity.name, capacity: identity.capacity,
                                         free: identity.free, lastSeen: Date(), scanRoot: scanRoot)
                self.update { $0.merge(drive: drive, clients: found.clients, shoots: found.shoots) }
                if self.queued.remove(identity.id) != nil { self.volumeMounted(volume) }
            }
        }
    }

    static func volume(of url: URL) -> URL? {
        (try? url.resourceValues(forKeys: [.volumeURLKey]).volume)?.standardizedFileURL
    }

    // MARK: Review and notes

    func isTicked(_ client: CatalogClient) -> Bool { client.looksLikeClient != flipped.contains(client.id) }
    func isTicked(_ shoot: CatalogShoot) -> Bool { shoot.looksLikeShoot != flipped.contains(shoot.id) }

    func setTicked(_ on: Bool, id: String, suggested: Bool) {
        if on == suggested { flipped.remove(id) } else { flipped.insert(id) }
    }

    /// Step 1 of review: ticked folders become clients, unticked ones are never suggested again.
    /// Their shoots are then up for review in step 2.
    func finishClients() {
        let asked = catalog.pendingClients
        let yes = Set(asked.filter(isTicked).map(\.id))
        flipped = []
        update { $0.answerClients(asked: Set(asked.map(\.id)), yes: yes) }
    }

    /// Step 2: ticked folders are shoots, unticked ones are never suggested again.
    func finishShoots() {
        let asked = catalog.pendingShoots
        let yes = Set(asked.filter(isTicked).map(\.id))
        flipped = []
        update { $0.answerShoots(asked: Set(asked.map(\.id)), yes: yes) }
    }

    func setNote(_ note: String, forDrive id: String) { update { $0.setNote(note, forDrive: id) } }

    private func update(_ change: (inout Catalog) -> Void) {
        var next = catalog
        change(&next)
        guard next != catalog else { return }
        catalog = next
        if !catalog.hasPending { reviewing = false }
        do {
            try catalog.save()
        } catch {
            NSLog("Offload: couldn't save catalog: \(error.localizedDescription)")
        }
        writeNote()
    }

    private func writeNote() {
        guard let notePath else { return }
        do {
            try catalog.markdown().write(toFile: notePath, atomically: true, encoding: .utf8)
            noteError = nil
        } catch {
            noteError = "Couldn't update the Obsidian note: \(error.localizedDescription)"
        }
    }

    /// Confirmed clients on plugged-in drives, with their confirmed shoot folders — so New
    /// shoot and Sort sessions can file a card straight into them.
    func knownClients() -> [(client: URL, shoots: [URL])] {
        let shoots = catalog.shoots
        return catalog.clients.compactMap { client in
            guard client.review == .confirmed, let volume = mounted[client.driveID] else { return nil }
            let mine = shoots.filter { $0.clientID == client.id }.map { volume.appendingPathComponent($0.path) }
            return (volume.appendingPathComponent(client.path), mine)
        }
    }

    // MARK: Rename and sort

    /// Folder names chosen on earlier sorts, by what was in them ("model:ILCE-7SM3" ->
    /// "A-cam", "from:render|MOV" -> "Renders"), so the next shoot suggests the same.
    static let learnedNamesKey = "learnedFolderNames"
    static var learnedNames: [String: String] {
        UserDefaults.standard.dictionary(forKey: learnedNamesKey) as? [String: String] ?? [:]
    }
    static func learn(_ names: [String: String]) {
        guard !names.isEmpty else { return }
        UserDefaults.standard.set(learnedNames.merging(names) { $1 }, forKey: learnedNamesKey)
    }

    func startRename(_ shoot: CatalogShoot) {
        guard let folder = folder(of: shoot) else { return }
        withAnimation(TabSwitcher.glide) { renaming = RenameForm(shoot: shoot, folder: folder, library: self) }
    }

    func startSort(_ shoot: CatalogShoot) {
        guard let folder = folder(of: shoot) else { return }
        withAnimation(TabSwitcher.glide) { sorting = SortForm(shoot: shoot, folder: folder, library: self) }
    }

    /// Back to the list, at the shoot the page was for. A rename passes the shoot's new id.
    func closePages(returningTo shootID: String? = nil) {
        if let id = shootID ?? sorting?.shoot.id ?? renaming?.shoot.id {
            lastShootID = id
            scrollToLastShoot = true
        }
        withAnimation(TabSwitcher.glide) {
            renaming = nil
            sorting = nil
        }
    }

    func chooseNoteLocation() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "Shoot catalog.md"
        panel.allowedContentTypes = [UTType(filenameExtension: "md") ?? .plainText]
        panel.prompt = "Keep note here"
        panel.message = "Pick a folder in your Obsidian vault. Offload rewrites this note whenever the catalog changes."
        NSApp.activate(ignoringOtherApps: true)
        if panel.runModal() == .OK, let url = panel.url { notePath = url.path }
    }
}

/// Calls `onChange` a few seconds after folders under `path` stop changing (a rename,
/// a move, a copy finishing). Changes to hidden files (Spotlight, .cairn, partial copies)
/// are ignored. Uses FSEvents, so a burst of changes costs one rescan.
final class DriveWatcher {
    static let settle: TimeInterval = 4

    let path: URL
    private let onChange: () -> Void
    private var stream: FSEventStreamRef?
    private var settleTimer: Timer?

    init(path: URL, onChange: @escaping () -> Void) {
        self.path = path
        self.onChange = onChange
        var context = FSEventStreamContext(version: 0, info: Unmanaged.passUnretained(self).toOpaque(),
                                           retain: nil, release: nil, copyDescription: nil)
        let callback: FSEventStreamCallback = { _, info, count, paths, _, _ in
            guard let info else { return }
            let watcher = Unmanaged<DriveWatcher>.fromOpaque(info).takeUnretainedValue()
            let changed = (unsafeBitCast(paths, to: NSArray.self) as? [String]) ?? []
            if changed.prefix(count).contains(where: { !$0.contains("/.") }) { watcher.changed() }
        }
        stream = FSEventStreamCreate(nil, callback, &context, [path.path] as CFArray,
                                     FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 1,
                                     FSEventStreamCreateFlags(kFSEventStreamCreateFlagUseCFTypes))
        guard let stream else { return }
        FSEventStreamSetDispatchQueue(stream, .main)
        FSEventStreamStart(stream)
    }

    private func changed() {
        settleTimer?.invalidate()
        settleTimer = Timer.scheduledTimer(withTimeInterval: Self.settle, repeats: false) { [weak self] _ in
            self?.onChange()
        }
    }

    deinit {
        settleTimer?.invalidate()
        guard let stream else { return }
        FSEventStreamStop(stream)
        FSEventStreamInvalidate(stream)
        FSEventStreamRelease(stream)
    }
}

/// Rename page: the shoot's name and date become its folder name ("261013 Spring launch").
final class RenameForm: ObservableObject {
    let shoot: CatalogShoot
    let folder: URL
    private weak var library: LibraryModel?
    @Published var name: String
    @Published var hasDate: Bool
    @Published var date: Date
    @Published var error: String?

    init(shoot: CatalogShoot, folder: URL, library: LibraryModel) {
        self.shoot = shoot
        self.folder = folder
        self.library = library
        let current = ShootLibrary.splitDate(folder.lastPathComponent)
        name = current.name.isEmpty ? folder.lastPathComponent : current.name
        hasDate = current.date != nil
        date = current.date ?? Date()
    }

    var newFolderName: String { ShootTidy.folderName(name: name, date: hasDate ? date : nil) }
    var unchanged: Bool { newFolderName == folder.lastPathComponent }
    /// The catalog id the shoot has once the rescan picks up its new folder name.
    private var renamedShootID: String {
        let parent = (shoot.path as NSString).deletingLastPathComponent as NSString
        return "\(shoot.driveID)/\(parent.appendingPathComponent(newFolderName))"
    }

    func rename() {
        do {
            let renamed = try ShootTidy.rename(folder, to: newFolderName)
            library?.rescan(containing: renamed)
            library?.closePages(returningTo: renamedShootID)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Sort page: works out where each loose file goes, lets you name the camera folders,
/// then moves them (and can put them back).
final class SortForm: ObservableObject {
    enum Stage { case planning, ready, sorting, done(String), failed(String) }

    let shoot: CatalogShoot
    let folder: URL
    private weak var library: LibraryModel?
    @Published var plan: ShootTidy.SortPlan?
    @Published var stage = Stage.planning
    @Published var canUndo: Bool
    /// Result of the last sort or undo, shown above the refreshed tree.
    @Published var notice: String?
    /// Destination folder a chip is being dragged over.
    @Published var dropTarget: String?

    init(shoot: CatalogShoot, folder: URL, library: LibraryModel) {
        self.shoot = shoot
        self.folder = folder
        self.library = library
        canUndo = ShootTidy.canUndoSort(folder)
        let name = library.cameraName
        let learned = LibraryModel.learnedNames
        // Reading camera tags blocks a thread per clip, so stay off Swift's shared task pool.
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var plan = ShootTidy.plan(folder, name: name, learned: learned)
            if ShootTidy.removeUnusedFolders(plan) {  // drop empty Footage/Camera folders from the tree too
                plan = ShootTidy.plan(folder, name: name, learned: learned)
            }
            DispatchQueue.main.async {
                self?.plan = plan
                self?.stage = .ready
            }
        }
    }

    func move(_ bundle: String, to destination: String) {
        notice = nil
        plan?.move(bundle: bundle, to: destination)
    }

    /// Back to the tree after a sort or undo, re-read from the drive, to keep tidying.
    func reload() {
        stage = .planning
        let name = library?.cameraName ?? { $0.shortName }
        let learned = LibraryModel.learnedNames
        let folder = folder
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let plan = ShootTidy.plan(folder, name: name, learned: learned)
            DispatchQueue.main.async {
                self?.plan = plan
                self?.stage = .ready
            }
        }
    }
    func addFolder() { plan?.addCameraFolder() }
    func add(_ role: ShootTidy.Destination.Role) { plan?.add(role) }
    func remove(_ destination: String) { plan?.remove(destination) }

    /// Deletes an empty camera folder from the drive (only if still empty) and drops it from the tree.
    func deleteEmptyFolder(_ destination: String) {
        guard let plan, let target = plan.destinations.first(where: { $0.id == destination }),
              let url = plan.folder(for: target), ShootTidy.deleteEmptyCameraFolder(url) else { return }
        self.plan?.destinations.removeAll { $0.id == destination }
        library?.rescan(containing: folder)
    }

    func rename(_ destination: String, to name: String) {
        guard let i = plan?.destinations.firstIndex(where: { $0.id == destination }) else { return }
        plan?.destinations[i].name = name
    }

    func setDropTarget(_ destination: String, _ over: Bool) {
        if over { dropTarget = destination } else if dropTarget == destination { dropTarget = nil }
    }

    func sort() {
        guard let plan else { return }
        stage = .sorting
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result: Stage
            do {
                let moved = try ShootTidy.apply(plan)
                LibraryModel.learn(ShootTidy.learnedNames(plan))
                result = .done("Done: \(moved) change\(moved == 1 ? "" : "s"). Nothing was copied or deleted.")
            } catch {
                result = .failed("Stopped partway: \(error.localizedDescription). Undo puts back what moved.")
            }
            DispatchQueue.main.async {
                guard let self else { return }
                self.finish(result)
            }
        }
    }

    /// After a sort or undo: show what happened above the tree as it now is on the drive,
    /// so it can be checked and tweaked straight away.
    private func finish(_ result: Stage) {
        canUndo = ShootTidy.canUndoSort(folder)
        library?.rescan(containing: folder)
        if case .done(let message) = result {
            notice = message
            reload()
        } else {
            stage = result
        }
    }

    func undo() {
        stage = .sorting
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let result: Stage
            do {
                let r = try ShootTidy.undoSort(self.folder)
                result = .done(r.skipped == 0 ? "Put \(r.restored) files back where they were."
                    : "Put \(r.restored) files back; \(r.skipped) stayed sorted because their old spot is taken.")
            } catch {
                result = .failed("Couldn't undo: \(error.localizedDescription)")
            }
            DispatchQueue.main.async { self.finish(result) }
        }
    }
}
