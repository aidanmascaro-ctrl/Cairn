import AppKit
import Combine
import OffloadCore
import SwiftUI

enum CardPhase {
    case scanning
    case ready
    case copying(CopyProgress)
    case failed(String)
}

/// A process with files open on a card.
struct Blocker: Identifiable {
    let pid: Int32
    let name: String
    let app: NSRunningApplication?
    var id: Int32 { pid }

    /// Background helpers that let go on their own within seconds.
    private static let systemNames = [
        "mds": "Spotlight", "mds_stores": "Spotlight", "mdworker": "Spotlight", "mdworker_shared": "Spotlight",
        "QuickLookUIService": "Quick Look", "com.apple.quicklook.ThumbnailsAgent": "Quick Look thumbnails",
    ]

    init(_ process: DiskUsers.Process) {
        pid = process.pid
        app = NSRunningApplication(processIdentifier: process.pid)
        name = app?.localizedName ?? Self.systemNames[process.command] ?? process.command
    }

    var isFinder: Bool { app?.bundleIdentifier == "com.apple.finder" }

    /// Only ordinary apps are asked to quit (politely — they can still prompt to save).
    /// Finder and system processes are never quit.
    var canQuit: Bool { app?.activationPolicy == .regular && !isFinder }

    var hint: String? {
        if isFinder { return "close its windows on the card" }
        if !canQuit { return "system — usually lets go in a few seconds" }
        return nil
    }
}

final class CardState: ObservableObject, Identifiable {
    let volume: URL
    var id: URL { volume }
    let name: String
    /// A plugged-in iPhone (read through Image Capture), not a mounted card.
    var isPhone: Bool { PhoneSource.isPhone(volume) }
    /// Eject is offered whenever nothing is reading the card, copied in full or not.
    var canEject: Bool {
        switch phase { case .ready, .failed: return true
        case .scanning, .copying: return false }
    }

    @Published var phase: CardPhase = .scanning
    @Published var files: [MediaFile] = []
    @Published var missing: [MediaFile] = []
    /// Card file -> where the drive already has it, for files that aren't missing.
    @Published var copies: [URL: [URL]] = [:]
    @Published var camera: CameraInfo?
    /// Files of types Offload doesn't recognise, by type ("XYZ": 3). Not copied.
    @Published var unknown: [String: Int] = [:]
    @Published var lastShoot: URL?
    /// How many separate shooting sessions are on the card (3h gap rule).
    @Published var sessionCount = 1
    @Published var ejecting = false
    @Published var ejectError: String?
    /// Who is holding the card open, filled in when an eject fails.
    @Published var blockers: [Blocker] = []
    var copyTask: Task<Void, Never>?

    init(volume: URL, name: String? = nil) {
        self.volume = volume
        self.name = name ?? volume.lastPathComponent
    }

    var hasPhotos: Bool { files.contains { $0.isPhoto || $0.kind == .photoEdit } }
    var shootDate: Date { Card.shootDate(of: files) ?? Date() }
}

/// The "New shoot" form. A class rather than SwiftUI @State because @State is a compiler
/// macro that Command Line Tools (no Xcode) can't build.
final class NewShootForm: ObservableObject {
    static let lastShootKey = "lastShootPath"

    let card: CardState
    let library: ShootLibrary?

    /// nil = "New client…"
    @Published var client: URL? {
        didSet { if shoot?.client != client { shoot = nil } }
    }
    @Published var newClientName = ""
    /// nil = "New shoot…"
    @Published var shoot: ExistingShoot?
    @Published var shootName = ""
    @Published var date: Date
    @Published var camera: String

    let hasPhotos: Bool

    /// `date`/`hasPhotos` default to the whole card; a session passes its own.
    /// `copied` is where the drive already holds some of these files (a copy that stopped
    /// part way). `preferLastShoot` is for a single-shoot card (card two follows card one);
    /// sessions prefer a shoot dated the same day instead.
    init(card: CardState, model: AppModel, date: Date? = nil, hasPhotos: Bool? = nil,
         library: ShootLibrary? = nil, copied: [URL] = [], preferLastShoot: Bool = true) {
        self.card = card
        self.library = library ?? model.root.map { ShootLibrary(root: $0, known: model.library.knownClients()) }
        self.date = date ?? card.shootDate
        self.hasPhotos = hasPhotos ?? card.hasPhotos
        camera = model.cameraName(for: card.camera)
        preselect(copied: copied, preferLastShoot: preferLastShoot)
    }

    /// The rest of a half-copied shoot goes where its first part went, even when the card's
    /// clips were moved between folders or it ran past midnight. Otherwise card two defaults
    /// to wherever card one went; otherwise a shoot dated the same day; otherwise the most
    /// recent client with a new shoot.
    private func preselect(copied: [URL], preferLastShoot: Bool) {
        guard let library else { return }
        let last = preferLastShoot ? UserDefaults.standard.string(forKey: Self.lastShootKey) : nil
        let sameDay = library.shoots.first { $0.date.map { Calendar.current.isDate($0, inSameDayAs: date) } ?? false }
        if let match = library.shootHolding(copied) ?? library.shoots.first(where: { $0.url.path == last }) ?? sameDay {
            client = match.client
            shoot = match
        } else {
            client = library.clients.first
        }
    }

    var clientShoots: [ExistingShoot] { client.flatMap { library?.shoots(for: $0) } ?? [] }

    var clientFolder: URL? {
        if let client { return client }
        guard let library, !newClientName.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return library.workingFolder.appendingPathComponent(ShootPlan.clean(newClientName), isDirectory: true)
    }

    var shootFolder: URL? {
        if let shoot { return shoot.url }
        guard let clientFolder, !shootName.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return clientFolder.appendingPathComponent("\(ShootPlan.dateLabel(date)) \(ShootPlan.clean(shootName))", isDirectory: true)
    }

    var plan: ShootPlan? {
        guard let shootFolder, !camera.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return ShootPlan(shootFolder: shootFolder, camera: camera, hasPhotos: hasPhotos)
    }

    var manifestShootName: String { shoot?.shootName ?? shootName }
    var manifestDate: Date { shoot?.date ?? date }
}

/// One shoot's worth of files to copy.
struct CopyJob {
    let plan: ShootPlan
    let files: [MediaFile]
    let client: String
    let shootName: String
    let date: Date
    let camera: String
}

extension CopyJob {
    init?(form: NewShootForm, files: [MediaFile]) {
        guard let plan = form.plan else { return nil }
        self.plan = plan
        self.files = files
        client = form.clientFolder?.lastPathComponent ?? ""
        shootName = form.manifestShootName
        date = form.manifestDate
        camera = form.camera
    }
}

/// One session on a card, with where it should go.
final class SessionDraft: ObservableObject, Identifiable {
    let session: CardSession
    let form: NewShootForm
    /// Files in this session that aren't on the drive yet.
    let missing: [MediaFile]
    @Published var skip: Bool
    /// A good frame from the session, loaded in the background.
    @Published var thumbnail: NSImage?
    var id: Int { session.id }

    init(session: CardSession, form: NewShootForm, missing: [MediaFile], thumbnail: NSImage? = nil) {
        self.session = session
        self.form = form
        self.missing = missing
        self.thumbnail = thumbnail
        skip = missing.isEmpty
        if thumbnail == nil { loadThumbnail() }
    }

    private func loadThumbnail() {
        let session = session
        Task.detached(priority: .utility) { [weak self] in
            let image = await Thumbnailer.image(for: session)
            await MainActor.run { self?.thumbnail = image }
        }
    }
}

/// "Sort sessions": a card holding several shoots, split by time gaps.
final class SessionsForm: ObservableObject {
    static let gapChoices: [Double] = [1, 3, 6, 12]

    let card: CardState
    private let library: ShootLibrary?
    private weak var model: AppModel?
    @Published private(set) var plan: SessionPlan
    @Published private(set) var drafts: [SessionDraft] = [] {
        didSet { watchDrafts() }
    }
    /// Row edits (names, pickers, ticks) live on each draft; forward them so the
    /// footer's summary and Copy all re-check as you type.
    private var draftWatchers: [AnyCancellable] = []
    @Published var gapHours: Double = SessionPlan.defaultGap / 3600 {
        didSet { plan.regroup(gap: gapHours * 3600); rebuild() }
    }
    /// Measured height of the session list, so the popover hugs it instead of leaving
    /// empty space (scrolls only once it's taller than the screen allows).
    @Published var listContentHeight: CGFloat = 0
    /// Same camera for every session on the card.
    @Published var camera: String {
        didSet { drafts.forEach { $0.form.camera = camera } }
    }

    /// `library` and `plan` are built off the main thread by AppModel.openSessions —
    /// scanning the drive and grouping thousands of files would freeze the popover.
    init(card: CardState, model: AppModel, library: ShootLibrary?, plan: SessionPlan) {
        self.card = card
        self.model = model
        self.library = library
        self.plan = plan
        camera = model.cameraName(for: card.camera)
        rebuild()
    }

    private func watchDrafts() {
        draftWatchers = drafts.flatMap { draft in
            [draft.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() },
             draft.form.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }]
        }
    }

    func mergeWithNext(_ draft: SessionDraft) { plan.mergeWithNext(draft.session); rebuild() }
    func splitByDay(_ draft: SessionDraft) { plan.splitByDay(draft.session); rebuild() }
    func isLast(_ draft: SessionDraft) -> Bool { drafts.last?.id == draft.id }

    /// Re-derives drafts, keeping the picks of any session that still starts at the same clip.
    private func rebuild() {
        guard let model else { return }
        let missing = Set(card.missing.map(\.url))
        let old = Dictionary(uniqueKeysWithValues: drafts.map { ($0.id, $0) })
        drafts = plan.sessions.map { session in
            let form = old[session.id]?.form
                ?? NewShootForm(card: card, model: model, date: session.start, hasPhotos: session.hasPhotos,
                                library: library, copied: session.files.flatMap { card.copies[$0.url] ?? [] },
                                preferLastShoot: false)
            form.camera = camera
            let previous = old[session.id].flatMap { $0.session.files.count == session.files.count ? $0 : nil }
            let draft = SessionDraft(session: session, form: form, missing: session.files.filter { missing.contains($0.url) },
                                     thumbnail: previous?.thumbnail)
            if let previous { draft.skip = previous.skip }
            // A phone holds months of everything: nothing is copied until its session is ticked.
            else if card.isPhone { draft.skip = true }
            return draft
        }
    }

    var jobs: [CopyJob] {
        drafts.filter { !$0.skip && !$0.missing.isEmpty }.compactMap { CopyJob(form: $0.form, files: $0.missing) }
    }

    /// Every session being copied has somewhere to go.
    var canCopy: Bool {
        let active = drafts.filter { !$0.skip && !$0.missing.isEmpty }
        return !active.isEmpty && active.allSatisfy { $0.form.plan != nil }
    }
}

enum PopoverTab: Hashable { case cards, library }

/// Owns every card currently plugged in and the user's working-drive setting.
final class AppModel: ObservableObject {
    @Published var cards: [CardState] = []
    @Published var tab = PopoverTab.cards
    /// Measured heights for the popover and each tab's page, so size changes can ease.
    @Published var popoverHeight: CGFloat = 0
    @Published var pageHeights: [PopoverTab: CGFloat] = [:]
    let library = LibraryModel()
    private let phones = PhoneSource()
    @Published var newShoot: NewShootForm?
    @Published var sessions: SessionsForm?
    /// The card whose New shoot / Sort sessions page is being prepared (shows a spinner).
    @Published var preparing: URL?

    /// Fade between the card list and a form page (the popover border resizes meanwhile).
    static let pageAnimation = Animation.easeInOut(duration: 0.25)

    /// Form pages fade in piece by piece: how many pieces are visible so far.
    @Published var revealStep = 0
    private var revealTimer: Timer?
    /// Wait for the popover border to finish resizing, then one piece every interval.
    static let revealDelay: TimeInterval = 0.25
    static let revealInterval: TimeInterval = 0.07

    func startReveal(pieces: Int) {
        revealTimer?.invalidate()
        revealStep = 0
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.revealDelay) { [weak self] in
            guard let self else { return }
            self.revealTimer = Timer.scheduledTimer(withTimeInterval: Self.revealInterval, repeats: true) { [weak self] timer in
                guard let self else { return timer.invalidate() }
                self.revealStep += 1
                if self.revealStep >= pieces { timer.invalidate() }
            }
        }
    }
    @Published var rootPath: String? = UserDefaults.standard.string(forKey: "rootPath") {
        didSet {
            UserDefaults.standard.set(rootPath, forKey: "rootPath")
            rescanAll()
            if let root { library.rescan(containing: root) }
        }
    }
    /// The pinned working folder (e.g. on the Lexar): used whenever its drive is plugged in,
    /// whatever was picked since. Starts as the folder chosen before pinning existed.
    @Published var pinnedPath: String? = UserDefaults.standard.string(forKey: "pinnedRoot")
        ?? UserDefaults.standard.string(forKey: "rootPath") {
        didSet {
            UserDefaults.standard.set(pinnedPath, forKey: "pinnedRoot")
            rescanAll()
        }
    }
    var isRootPinned: Bool { pinnedPath != nil && pinnedPath == root?.path }

    /// Pins the folder in use, or unpins it.
    func togglePin() { pinnedPath = isRootPinned ? nil : root?.path }

    var onCardInserted: (() -> Void)?

    /// The working folder in use, first that's reachable: the pinned one, the last one picked,
    /// then the folder last scanned on the Mac's own disk (the Library remembers it), else the Desktop.
    var root: URL? {
        let candidates = [pinnedPath, rootPath].compactMap { $0 }.map { URL(fileURLWithPath: $0, isDirectory: true) }
        guard !candidates.isEmpty else { return nil }
        return candidates.first { Self.unpluggedDrive(for: $0) == nil } ?? macFolder
    }

    private var macFolder: URL {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let remembered = library.catalog.drives.lazy.compactMap(\.scanRoot)
            .first { $0.hasPrefix(home + "/") && FileManager.default.fileExists(atPath: $0) }
        return URL(fileURLWithPath: remembered ?? home + "/Desktop", isDirectory: true)
    }

    var driveName: String {
        guard let root else { return "your drive" }
        let volume = try? root.resourceValues(forKeys: [.volumeNameKey]).volumeName
        return volume ?? root.lastPathComponent
    }

    // MARK: Cards coming and going

    func start() {
        library.root = { [weak self] in self?.root }
        library.cameraName = { [weak self] in self?.cameraName(for: $0) ?? $0.shortName }
        let center = NSWorkspace.shared.notificationCenter
        center.addObserver(forName: NSWorkspace.didMountNotification, object: nil, queue: .main) { [weak self] note in
            guard let url = note.userInfo?[NSWorkspace.volumeURLUserInfoKey] as? URL else { return }
            self?.volumeMounted(url, announce: true)
        }
        center.addObserver(forName: NSWorkspace.didUnmountNotification, object: nil, queue: .main) { [weak self] note in
            guard let url = note.userInfo?[NSWorkspace.volumeURLUserInfoKey] as? URL else { return }
            self?.cards.removeAll { $0.volume.standardizedFileURL == url.standardizedFileURL }
            self?.library.volumeUnmounted(url)
            self?.workingDriveChanged(url)
        }
        let mounted = FileManager.default.mountedVolumeURLs(includingResourceValuesForKeys: nil, options: [.skipHiddenVolumes]) ?? []
        mounted.forEach { volumeMounted($0, announce: false) }

        phones.onConnect = { [weak self] volume, name in
            guard let self, !cards.contains(where: { $0.volume == volume }) else { return }
            cards.append(CardState(volume: volume, name: name))  // stays "scanning" until unlocked
            tab = .cards
            onCardInserted?()
        }
        phones.onReady = { [weak self] volume in
            if let card = self?.cards.first(where: { $0.volume == volume }) { self?.scan(card) }
        }
        phones.onDisconnect = { [weak self] volume in self?.cards.removeAll { $0.volume == volume } }
        phones.start()
    }

    /// The chosen working drive came or went: the folder in use switches between it and the
    /// Mac, so every card checks again what's "missing".
    private func workingDriveChanged(_ volume: URL) {
        let prefix = volume.standardizedFileURL.path + "/"
        guard [pinnedPath, rootPath].contains(where: { $0?.hasPrefix(prefix) == true }) else { return }
        objectWillChange.send()
        rescanAll()
    }

    private func volumeMounted(_ url: URL, announce: Bool) {
        library.volumeMounted(url)
        if announce { workingDriveChanged(url) }
        guard Card.isCard(url), !cards.contains(where: { $0.volume == url }) else { return }
        let card = CardState(volume: url)
        cards.append(card)
        scan(card)
        if announce {
            tab = .cards
            onCardInserted?()
        }
    }

    func rescanAll() { cards.forEach(scan) }

    /// Every card currently copying, as one job — drives the menu-bar pill.
    var activeCopy: CopyProgress? {
        CopyProgress.combined(cards.compactMap {
            if case .copying(let p) = $0.phase { return p }
            return nil
        })
    }

    // MARK: Pages

    /// Scans the drive for clients/shoots in the background, then slides the page in.
    func openNewShoot(_ card: CardState) {
        prepare(card) { [weak self] library in
            guard let self else { return }
            let form = NewShootForm(card: card, model: self, library: library, copied: card.copies.values.flatMap { $0 })
            self.revealStep = 0
            withAnimation(Self.pageAnimation) { self.newShoot = form }
            self.startReveal(pieces: NewShootView.pieces)
        }
    }

    /// Same, plus groups the card into sessions off the main thread.
    func openSessions(_ card: CardState) {
        let files = card.files
        prepare(card, extra: { SessionPlan(files: files) }) { [weak self] library, plan in
            guard let self else { return }
            let form = SessionsForm(card: card, model: self, library: library, plan: plan)
            self.revealStep = 0
            withAnimation(Self.pageAnimation) { self.sessions = form }
            self.startReveal(pieces: SessionsView.headerPieces + form.drafts.count + 1)
        }
    }

    func closeForms() {
        withAnimation(Self.pageAnimation) {
            sessions = nil
            newShoot = nil
        }
    }

    private func prepare(_ card: CardState, then show: @escaping (ShootLibrary?) -> Void) {
        prepare(card, extra: { () }) { library, _ in show(library) }
    }

    private func prepare<Extra>(_ card: CardState, extra: @escaping @Sendable () -> Extra,
                                then show: @escaping (ShootLibrary?, Extra) -> Void) {
        guard preparing == nil else { return }
        preparing = card.volume
        let root = root
        let known = library.knownClients()
        Task.detached(priority: .userInitiated) {
            let library = root.map { ShootLibrary(root: $0, known: known) }
            let result = extra()
            await MainActor.run {
                self.preparing = nil
                show(library, result)
            }
        }
    }

    // MARK: Scan: what isn't on the drive yet

    func scan(_ card: CardState) {
        if card.isPhone, !PhoneSource.isReady(card.volume) {  // locked: wait for onReady
            card.phase = .scanning
            return
        }
        card.phase = .scanning
        let volume = card.volume
        let driveVolume = root.flatMap { try? $0.resourceValues(forKeys: [.volumeURLKey]).volume }
        Task.detached(priority: .userInitiated) {
            let phone = PhoneSource.isPhone(volume)
            let (files, unknown) = phone ? PhoneSource.survey(of: volume) : Card.survey(of: volume)
            let camera = phone ? PhoneSource.camera(on: volume, files: files) : CameraInfo.read(fromCard: volume, files: files)
            if !phone { CardLog.record(volume: volume, camera: camera, files: files, unknown: unknown) }
            // Content match against the whole working drive, so footage filed anywhere counts.
            let index = driveVolume.map { DriveIndex(root: $0) }
            let copies = files.reduce(into: [URL: [URL]]()) { found, file in
                let onDrive = index?.copies(of: file) ?? []
                if !onDrive.isEmpty { found[file.url] = onDrive }
            }
            let missing = files.filter { copies[$0.url] == nil }
            let sessionCount = SessionPlan(files: files).sessions.count
            await MainActor.run {
                card.sessionCount = sessionCount
                card.files = files
                card.camera = camera
                card.unknown = unknown
                card.missing = missing
                card.copies = copies
                card.phase = .ready
            }
        }
    }

    // MARK: Camera names (serial -> what the user calls it, e.g. "FX3" or "A-cam")

    func cameraName(for camera: CameraInfo?) -> String {
        guard let camera else { return "Camera" }
        let saved = camera.serial.flatMap { (UserDefaults.standard.dictionary(forKey: "cameraNames") as? [String: String])?[$0] }
        return saved ?? camera.shortName
    }

    func rememberCameraName(_ name: String, for camera: CameraInfo?) {
        guard let serial = camera?.serial else { return }
        var names = UserDefaults.standard.dictionary(forKey: "cameraNames") as? [String: String] ?? [:]
        names[serial] = name
        UserDefaults.standard.set(names, forKey: "cameraNames")
    }

    // MARK: Copy

    func copy(_ card: CardState, plan: ShootPlan, client: String, shootName: String, date shootDate: Date, camera: String) {
        copy(card, jobs: [CopyJob(plan: plan, files: card.missing, client: client, shootName: shootName,
                                  date: shootDate, camera: camera)])
    }

    /// Copies one or more shoots off the card as one job: one progress bar, one pill,
    /// one offload.json + Cairn inbox entry per shoot.
    func copy(_ card: CardState, jobs: [CopyJob], checkedOtherDrives: Bool = false) {
        guard !jobs.isEmpty else { return }
        if let drive = jobs.lazy.compactMap({ Self.unpluggedDrive(for: $0.plan.shootFolder) }).first {
            card.phase = .failed("\(drive) isn't plugged in. Plug it in, then copy again.")
            return
        }
        // The card was only checked against the working drive. A shoot going to a client on
        // another drive (from the Library) is checked against that drive first, so footage
        // already there isn't copied a second time.
        let rootVolume = root.flatMap(LibraryModel.volume(of:))
        if !checkedOtherDrives, jobs.contains(where: { Self.volume(holding: $0.plan.shootFolder) != rootVolume }) {
            card.phase = .scanning
            Task.detached(priority: .userInitiated) { [weak self] in
                var indexes: [URL: DriveIndex] = [:]
                let filtered: [CopyJob] = jobs.compactMap { job in
                    guard let volume = Self.volume(holding: job.plan.shootFolder), volume != rootVolume else { return job }
                    // A whole external drive is fine to check; the Mac's own disk isn't (it's
                    // everything) — there, only the folder holding this client's shoots.
                    let isInternal = (try? volume.resourceValues(forKeys: [.volumeIsInternalKey]).volumeIsInternal) ?? false
                    let scope = isInternal ? job.plan.shootFolder.deletingLastPathComponent().deletingLastPathComponent() : volume
                    let index = indexes[scope] ?? DriveIndex(root: scope)
                    indexes[scope] = index
                    let files = index.missing(from: job.files)
                    return files.isEmpty ? nil : CopyJob(plan: job.plan, files: files, client: job.client,
                                                         shootName: job.shootName, date: job.date, camera: job.camera)
                }
                await MainActor.run {
                    guard let self else { return }
                    if filtered.isEmpty { self.scan(card) } else { self.copy(card, jobs: filtered, checkedOtherDrives: true) }
                }
            }
            return
        }
        let cardInfo = card.camera
        let cardName = card.name
        let start = Date()
        let totalFiles = jobs.reduce(0) { $0 + $1.files.count }
        let totalBytes = jobs.reduce(Int64(0)) { $0 + $1.files.reduce(0) { $0 + $1.size } }
        if let free = DriveSpace.available(at: jobs[0].plan.shootFolder.deletingLastPathComponent()),
           let short = DriveSpace.shortfall(needed: totalBytes, available: free) {
            let fmt = { ByteCountFormatter.string(fromByteCount: $0, countStyle: .file) }
            card.phase = .failed("Not enough space: this needs \(fmt(totalBytes)) but only \(fmt(free)) is free "
                                 + "(keeping 2 GB spare). Free up \(fmt(short)) or copy fewer sessions.")
            return
        }
        card.phase = .copying(CopyProgress(filesTotal: totalFiles, bytesTotal: totalBytes, startedAt: start))
        card.copyTask = Task.detached(priority: .userInitiated) { [weak self] in
            var doneFiles = 0
            var doneBytes: Int64 = 0
            do {
                for job in jobs {
                    try job.plan.create(project: job.files.contains { $0.kind == .video })
                    let copied = try Copier.copy(job.files, plan: job.plan) { p in
                        var overall = CopyProgress(filesTotal: totalFiles, bytesTotal: totalBytes, startedAt: start)
                        overall.filesDone = doneFiles + p.filesDone
                        overall.bytesDone = doneBytes + p.bytesDone
                        DispatchQueue.main.async { card.phase = .copying(overall) }
                    }
                    doneFiles += job.files.count
                    doneBytes += job.files.reduce(0) { $0 + $1.size }
                    try Handoff.publish(ShootManifest(
                        client: job.client, shootName: job.shootName, date: Self.isoDate(job.date),
                        camera: job.camera, cameraModel: cardInfo?.model, cameraSerial: cardInfo?.serial,
                        cardName: cardName, shootFolder: job.plan.shootFolder.path,
                        footageFolder: job.plan.footageFolder.path, photosFolder: job.plan.photosFolder?.path,
                        copiedAt: Date(), files: copied
                    ))
                    await MainActor.run { card.lastShoot = job.plan.shootFolder }
                }
                await MainActor.run {
                    self?.scan(card)
                    self?.library.rescan(containing: jobs[0].plan.shootFolder)
                }
            } catch is CancellationError {
                await MainActor.run { self?.scan(card) }
            } catch {
                await MainActor.run { card.phase = .failed(error.localizedDescription) }
            }
        }
    }

    func stop(_ card: CardState) { card.copyTask?.cancel() }

    /// Spotlight often indexes a freshly inserted card for a few seconds, so retry
    /// before reporting. Then ask lsof who still has files open on the card.
    static let ejectAttempts = 5
    static let ejectRetryDelay: Duration = .seconds(2)
    /// How long to wait for apps to quit before giving up (they may be asking to save).
    static let quitTimeout: TimeInterval = 15

    /// Offered whenever no scan or copy is reading the card. Unmount can block for a
    /// moment, so it runs off the main thread. On success the didUnmount notification
    /// removes the card row.
    func eject(_ card: CardState) {
        guard card.canEject, !card.ejecting else { return }
        if card.isPhone {  // nothing to unmount: let go of it and drop the row
            phones.close(card.volume)
            cards.removeAll { $0 === card }
            return
        }
        card.ejecting = true
        card.ejectError = nil
        card.blockers = []
        let volume = card.volume
        Task.detached {
            var lastError: Error?
            for attempt in 0..<Self.ejectAttempts {
                do {
                    try await Ejector.eject(volume)
                    return  // didUnmount notification removes the card row
                } catch let refused as Ejector.Refused where refused.isBusy {
                    lastError = refused
                    if attempt < Self.ejectAttempts - 1 { try? await Task.sleep(for: Self.ejectRetryDelay) }
                } catch {
                    lastError = error
                    break  // not a "busy" refusal; retrying won't help
                }
            }
            let users = DiskUsers.processes(using: volume)
            let busy = (lastError as? Ejector.Refused)?.isBusy ?? false
            await MainActor.run {
                card.ejecting = false
                card.blockers = users.map(Blocker.init)
                guard users.isEmpty else { return }
                card.ejectError = busy
                    ? "Still busy, but no app has it open — a macOS service (usually Spotlight indexing a new card) is holding it. Try again in a few seconds."
                    : "Couldn't eject: \(lastError?.localizedDescription ?? "unknown error")"
            }
        }
    }

    /// Politely quits the apps holding the card (each can still ask to save), waits for
    /// them to exit, then ejects. Never force-quits; Finder and system processes are left alone.
    func quitBlockersAndEject(_ card: CardState) {
        let apps = card.blockers.filter(\.canQuit).compactMap(\.app)
        guard !apps.isEmpty, !card.ejecting else { return }
        card.ejecting = true
        card.ejectError = nil
        apps.forEach { $0.terminate() }
        Task.detached { [weak self] in
            let deadline = Date().addingTimeInterval(Self.quitTimeout)
            while Date() < deadline, apps.contains(where: { !$0.isTerminated }) {
                try? await Task.sleep(for: .milliseconds(250))
            }
            await MainActor.run {
                card.ejecting = false
                let stillOpen = apps.filter { !$0.isTerminated }.compactMap(\.localizedName)
                if stillOpen.isEmpty {
                    self?.eject(card)
                } else {
                    card.ejectError = "\(stillOpen.joined(separator: ", ")) didn't quit — it may be asking you to save."
                }
            }
        }
    }

    /// "Lexar 4TB" when `folder` is on an external drive that isn't plugged in. Without this
    /// check the folder resolves to the Mac's own disk, and a copy would look there instead.
    static func unpluggedDrive(for folder: URL) -> String? {
        let parts = folder.standardizedFileURL.pathComponents
        guard parts.count > 2, parts[1] == "Volumes",
              !FileManager.default.fileExists(atPath: "/Volumes/\(parts[2])") else { return nil }
        return parts[2]
    }

    /// The working drive, when it's an external one that isn't plugged in.

    /// The drive a (possibly not yet created) folder will live on.
    static func volume(holding folder: URL) -> URL? {
        var url = folder
        while !FileManager.default.fileExists(atPath: url.path), url.pathComponents.count > 1 {
            url.deleteLastPathComponent()
        }
        return LibraryModel.volume(of: url)
    }

    private static func isoDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }

    // MARK: Finder

    /// Shows `folder` in the front Finder window if one is open, else a new window.
    /// Command-click always opens a new window.
    /// Driving an existing window needs AppleScript (macOS asks once to allow Offload to
    /// control Finder); if that's refused, fall back to opening a new window.
    func reveal(_ folder: URL) {
        let newWindow = NSEvent.modifierFlags.contains(.command)
        let path = folder.path
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        let script = """
        tell application "Finder"
            set dest to (POSIX file "\(path)") as alias
            if \(newWindow ? "false" : "true") and (count of Finder windows) > 0 then
                set target of front Finder window to dest
            else
                make new Finder window to dest
            end if
            activate
        end tell
        """
        var error: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&error)
        if error != nil { NSWorkspace.shared.open(folder) }
    }

    // MARK: Folder pickers

    func chooseRoot() {
        if let url = pickFolder(prompt: "Use as working folder") { rootPath = url.path }
    }

    func pickFolder(prompt: String) -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = prompt
        panel.directoryURL = root
        NSApp.activate(ignoringOtherApps: true)
        return panel.runModal() == .OK ? panel.url : nil
    }
}
