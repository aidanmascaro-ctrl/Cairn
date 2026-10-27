import Foundation
@testable import OffloadCore

// Read-only preview of how a real card splits: swift run OffloadSelfTest --sessions /Volumes/Card [/Volumes/Drive]
// With a drive, also shows what's left to copy and which shoot already holds the rest.
if [3, 4].contains(CommandLine.arguments.count), CommandLine.arguments[1] == "--sessions" {
    let card = URL(fileURLWithPath: CommandLine.arguments[2])
    let drive = CommandLine.arguments.count == 4 ? URL(fileURLWithPath: CommandLine.arguments[3]) : nil
    let index = drive.map { DriveIndex(root: $0) }
    let library = drive.map { ShootLibrary(root: $0) }
    let f = DateFormatter()
    f.dateFormat = "EEE d MMM HH:mm"
    for s in SessionPlan(files: Card.mediaFiles(on: card)).sessions {
        let thumbs = s.files.filter { $0.kind == .thumbnail }.count
        let meta = s.files.filter { $0.kind == .metadata }.count
        print("\(f.string(from: s.start)) -> \(f.string(from: s.end))  clips \(s.clipCount), photos \(s.photoCount), thumbs \(thumbs), xml/lut \(meta), \(ByteCountFormatter.string(fromByteCount: s.bytes, countStyle: .file))")
        guard let index, let library else { continue }
        let copies = s.files.map { index.copies(of: $0) }
        let left = copies.filter(\.isEmpty).count
        let shoot = library.shootHolding(copies.flatMap { $0 }).map { library.label(for: $0.url) } ?? "none yet"
        print("    \(left) of \(s.files.count) files left to copy; already in: \(shoot)")
    }
    exit(0)
}

// Read-only preview of how Sort would arrange a shoot: swift run OffloadSelfTest --plan <shoot folder>
if CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--plan" {
    let plan = ShootTidy.plan(URL(fileURLWithPath: CommandLine.arguments[2]))
    for d in plan.destinations {
        print("\(d.role) \(d.name): \(d.bundles.map { "\($0.type)×\($0.files.count) [\($0.origin)]" }.joined(separator: ", "))")
        if let check = plan.cameraCheck(d) { print("  camera check: \(check)") }
    }
    exit(0)
}

// Copy speed + peak memory: swift run -c release OffloadSelfTest --bench <scratch folder> <card files...>
// Reads the card files only; everything it writes lands under the scratch folder.
if CommandLine.arguments.count > 3, CommandLine.arguments[1] == "--bench" {
    let scratch = URL(fileURLWithPath: CommandLine.arguments[2])
    let files = CommandLine.arguments.dropFirst(3).map { path -> MediaFile in
        let url = URL(fileURLWithPath: path)
        let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize).flatMap { $0 } ?? 0
        return MediaFile(url: url, size: Int64(size), created: Date())
    }
    let plan = ShootPlan(shootFolder: scratch.appendingPathComponent("Bench"), camera: "Bench", hasPhotos: false)
    let started = Date()
    let copied = try Copier.copy(files, plan: plan) { _ in }
    let seconds = Date().timeIntervalSince(started)
    let bytes = copied.reduce(Int64(0)) { $0 + $1.size }
    var usage = rusage()
    getrusage(RUSAGE_SELF, &usage)
    print(String(format: "%d files, %.2f GB in %.1f s = %.0f MB/s, peak memory %.0f MB",
                 copied.count, Double(bytes) / 1e9, seconds, Double(bytes) / 1e6 / seconds,
                 Double(usage.ru_maxrss) / 1e6))
    exit(0)
}

// A plugged-in iPhone: lists it, names it, splits sessions, then copies its 3 smallest photos
// and 1 smallest video into <scratch>/Phone with the normal verify. Reads the phone only.
// swift run OffloadSelfTest --phone <scratch folder>
if CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--phone" {
    let scratch = URL(fileURLWithPath: CommandLine.arguments[2])
    let phones = PhoneSource()
    phones.onConnect = { _, name in print("connected:", name, "— unlock it if asked") }
    phones.onReady = { volume in
        DispatchQueue.global().async {
            let (files, unknown) = PhoneSource.survey(of: volume)
            let camera = PhoneSource.camera(on: volume, files: files)
            print("camera:", camera?.model ?? "?", "→ folder", camera?.shortName ?? "?")
            print("files:", files.count, "not copied:", unknown)
            print("sessions (3 h gap):", SessionPlan(files: files).sessions.count)
            let photos = files.filter(\.isPhoto).sorted { $0.size < $1.size }.prefix(3)
            let video = files.filter { $0.kind == .video }.sorted { $0.size < $1.size }.prefix(1)
            let plan = ShootPlan(shootFolder: scratch.appendingPathComponent("Phone"), camera: camera?.shortName ?? "iPhone", hasPhotos: true)
            do {
                let copied = try Copier.copy(Array(photos + video), plan: plan) { _ in }
                for c in copied { print("copied + verified:", c.destination.path, c.size) }
                let index = DriveIndex(root: scratch)
                print("found on drive by content:", Array(photos + video).filter(index.contains).count, "of", copied.count)
            } catch { print("copy failed:", error.localizedDescription) }
            exit(0)
        }
    }
    phones.start()
    DispatchQueue.main.asyncAfter(deadline: .now() + 120) { print("no phone ready after 2 min"); exit(1) }
    RunLoop.main.run()
}

/// Runs without Xcode: `swift run OffloadSelfTest`. Exits 1 if any check fails.
var failures = 0
func expect(_ ok: @autoclosure () throws -> Bool, _ label: String, line: Int = #line) {
    let passed = (try? ok()) ?? false
    if !passed { failures += 1 }
    print(passed ? "  ok  \(label)" : "FAIL  \(label) (line \(line))")
}

private func tempDir() throws -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("offload-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

private func write(_ bytes: [UInt8], to url: URL) throws -> MediaFile {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data(bytes).write(to: url)
    return MediaFile(url: url, size: Int64(bytes.count), created: Date(timeIntervalSince1970: 1_792_627_200))
}

func parsesSonyCameraXML() {
    let xml = #"<Device manufacturer="Sony" modelName="ILME-FX3" serialNo="5014471"/>"#
    let info = CameraInfo.parse(sonyXML: xml)
    expect(info?.model == "ILME-FX3", "info?.model == 'ILME-FX3'")
    expect(info?.serial == "5014471", "info?.serial == '5014471'")
    expect(info?.shortName == "FX3", "info?.shortName == 'FX3'")
}

func plansBackedStyleFolders() {
    let root = URL(fileURLWithPath: "/Volumes/T7/2026")
    let date = Date(timeIntervalSince1970: 1_792_627_200)  // 2026-10-22
    let plan = ShootPlan(root: root, client: "Harbour Coffee Co.", shootName: "Brand/film", date: date, camera: "FX3", hasPhotos: false)
    let label = ShootPlan.dateLabel(date)
    expect(label == "261022", "new shoots are named yyMMdd so Finder sorts them by date")
    expect(plan.footageFolder.path == "/Volumes/T7/2026/Harbour Coffee Co./\(label) Brand-film/Footage/FX3", "plan.footageFolder.path == '/Volumes/T7/2026/Harbour Coffee Co./(label) Brand-film/Footage/FX3'")
    expect(plan.photosFolder == nil, "plan.photosFolder == nil")
    expect(plan.folders.count == 2, "plan.folders.count == 2")
}

func matchesByContentNotName() throws {
    let card = try tempDir(), drive = try tempDir()
    let big = (0..<(3 << 20)).map { UInt8($0 % 251) }
    let onCard = try write(big, to: card.appendingPathComponent("DCIM/C0001.MP4"))
    _ = try write(big, to: drive.appendingPathComponent("old/renamed.mp4"))
    var changed = big; changed[big.count - 1] ^= 1
    let lookalike = try write(changed, to: card.appendingPathComponent("DCIM/C0002.MP4"))

    let index = DriveIndex(root: drive)
    expect(index.contains(onCard), "index.contains(onCard)")
    expect(!index.contains(lookalike), "!index.contains(lookalike)")  // same size, different tail
}

func copiesVerifiesAndNeverOverwrites() throws {
    let card = try tempDir(), root = try tempDir()
    let file = try write([1, 2, 3, 4], to: card.appendingPathComponent("DCIM/C0001.MP4"))
    let plan = ShootPlan(root: root, client: "Client", shootName: "Shoot", date: Date(), camera: "FX3", hasPhotos: false)
    try plan.create(project: true)
    _ = try write([9], to: plan.footageFolder.appendingPathComponent("C0001.MP4"))

    let copied = try Copier.copy([file], plan: plan) { _ in }

    expect(copied.first?.destination.lastPathComponent == "C0001 (2).MP4", "copied.first?.destination.lastPathComponent == 'C0001 (2).MP4'")
    expect(try Data(contentsOf: plan.footageFolder.appendingPathComponent("C0001.MP4")) == Data([9]), "try Data(contentsOf: plan.footageFolder.appendingPathComponent('C0001.MP4')) == Data([9])")
    expect(try Data(contentsOf: copied[0].destination) == Data([1, 2, 3, 4]), "try Data(contentsOf: copied[0].destination) == Data([1, 2, 3, 4])")
    expect(try Data(contentsOf: file.url) == Data([1, 2, 3, 4]), "try Data(contentsOf: file.url) == Data([1, 2, 3, 4])")  // card untouched
}

func sameNamedFilesBothSurviveBackToBack() throws {
    // DCIM/100MSDCF/DSC0001.JPG and DCIM/101MSDCF/DSC0001.JPG both land in Photos; the first is
    // still being read back when the second starts, so it must not take the same name.
    let card = try tempDir(), root = try tempDir()
    let first = try write(Array(repeating: 1, count: 3 << 20), to: card.appendingPathComponent("DCIM/100MSDCF/DSC0001.JPG"))
    let second = try write([2, 2], to: card.appendingPathComponent("DCIM/101MSDCF/DSC0001.JPG"))
    let plan = ShootPlan(root: root, client: "C", shootName: "S", date: Date(), camera: "A", hasPhotos: true)
    try plan.create(project: false)

    let copied = try Copier.copy([first, second], plan: plan) { _ in }
    expect(!FileManager.default.fileExists(atPath: plan.footageFolder.path)
           && !FileManager.default.fileExists(atPath: plan.projectFolder.path), "photo-only card: no Footage or Project folder")

    expect(copied.map(\.destination.lastPathComponent) == ["DSC0001.JPG", "DSC0001 (2).JPG"], "same-named files get distinct names")
    expect(try Data(contentsOf: copied[0].destination).count == 3 << 20, "first file intact")
    expect(try Data(contentsOf: copied[1].destination) == Data([2, 2]), "second file intact")
}

func handoffWritesSidecarAndInbox() throws {
    let shoot = try tempDir(), inbox = try tempDir().appendingPathComponent("inbox.json")
    func manifest(_ name: String) -> ShootManifest {
        ShootManifest(client: "C", shootName: "S", date: "2026-10-22", camera: "FX3", cameraModel: nil,
                      cameraSerial: nil, cardName: "Card A", shootFolder: shoot.path, footageFolder: shoot.path,
                      photosFolder: nil, copiedAt: Date(),
                      files: [CopiedFile(source: URL(fileURLWithPath: "/c/\(name)"),
                                         destination: shoot.appendingPathComponent(name), size: 1, sha256: "ab")])
    }
    try Handoff.publish(manifest("A.MP4"), inbox: inbox)
    let sidecar = try Handoff.publish(manifest("B.MP4"), inbox: inbox)

    expect(Handoff.readInbox(inbox).count == 1, "Handoff.readInbox(inbox).count == 1")  // same shoot, one entry
    let saved = try Handoff.decoder.decode(ShootManifest.self, from: Data(contentsOf: sidecar))
    expect(saved.files.count == 2, "saved.files.count == 2")  // second card merged in
}

func skipsSonyCardHousekeeping() throws {
    let card = try tempDir()
    for path in ["PRIVATE/M4ROOT/CLIP/C0001.MP4", "PRIVATE/M4ROOT/CLIP/C0001M01.XML", "PRIVATE/M4ROOT/THMBNL/C0001T01.JPG",
                 "PRIVATE/M4ROOT/MEDIAPRO.XML", "PRIVATE/M4ROOT/CUEUP.XML", "PRIVATE/M4ROOT/GENERAL/LUT/look.cube",
                 "PRIVATE/M4ROOT/DATABASE/DATABASE.BIN", "PRIVATE/DATABASE/DATABASE.BIN", "PRIVATE/SONY/SONYCARD.IND", "DCIM/100MSDCF/DSC0001.ARW"] {
        _ = try write([1], to: card.appendingPathComponent(path))
    }
    let names = Set(Card.mediaFiles(on: card).map(\.url.lastPathComponent))
    expect(names == ["C0001.MP4", "C0001M01.XML", "C0001T01.JPG", "DSC0001.ARW"],
           "only clips, their XML and thumbnails, and photos are copied: \(names.sorted())")
}

func sortsThumbnailsAndMetadataOutOfTheClipFolder() {
    let card = "/Volumes/Untitled 1/PRIVATE/M4ROOT"
    func file(_ path: String) -> MediaFile { MediaFile(url: URL(fileURLWithPath: path), size: 1, created: Date()) }
    let clip = file("\(card)/CLIP/C9973.MP4")
    let thumb = file("\(card)/THMBNL/C9973T01.JPG")
    let xml = file("\(card)/CLIP/C9973M01.XML")
    let lut = file("\(card)/GENERAL/LUT/look.cube")
    let photo = file("/Volumes/Untitled 1/DCIM/100MSDCF/DSC0001.JPG")
    expect(clip.kind == .video && thumb.kind == .thumbnail && xml.kind == .metadata, "Sony card kinds")
    expect(lut.kind == .metadata && photo.kind == .photo, "LUT is metadata, DCIM JPG is a photo")
    let edit = file("/Volumes/Untitled 1/DCIM/100MSDCF/DSC0001.xmp")
    let withPhotos = ShootPlan(shootFolder: URL(fileURLWithPath: "/T7/S"), camera: "A7", hasPhotos: true)
    expect(edit.kind == .photoEdit && withPhotos.destinationFolder(for: edit) == withPhotos.photosFolder,
           ".xmp edit goes to Photos with its photo")

    let plan = ShootPlan(root: URL(fileURLWithPath: "/T7"), client: "2 Cats", shootName: "Sports Dinner",
                         date: Date(), camera: "FX30", hasPhotos: false)
    expect(plan.destinationFolder(for: clip) == plan.footageFolder, "clip -> Footage/FX30")
    expect(plan.destinationFolder(for: thumb).path.hasSuffix("Footage/FX30/Thumbnails"), "thumb -> Footage/FX30/Thumbnails")
    expect(plan.destinationFolder(for: xml).path.hasSuffix("Footage/FX30/Metadata"), "xml -> Footage/FX30/Metadata")
}

func speedAndTimeLeft() {
    let start = Date(timeIntervalSince1970: 0)
    var p = CopyProgress(filesTotal: 44, bytesTotal: 1_000, startedAt: start)
    p.bytesDone = 100
    expect(p.bytesPerSecond(at: start.addingTimeInterval(1)) == nil, "no speed during warm-up")
    expect(p.bytesPerSecond(at: start.addingTimeInterval(10)) == 10, "100 bytes in 10 s = 10 B/s")
    expect(p.secondsRemaining(at: start.addingTimeInterval(10)) == 90, "900 bytes left at 10 B/s = 90 s")

    var q = CopyProgress(filesTotal: 2, bytesTotal: 1_000, startedAt: start.addingTimeInterval(5))
    q.bytesDone = 500
    let both = CopyProgress.combined([p, q])
    expect(both?.bytesTotal == 2_000 && both?.bytesDone == 600 && both?.startedAt == start, "two cards combine")
    expect(CopyProgress.combined([]) == nil, "nothing copying -> no pill")
}

func findsNestedClientsAndShoots() throws {
    // Mirrors the real Lexar drive: <drive>/untitled folder/Northwind Media/15.10.26 2 Harvest Dinner
    let drive = try tempDir()
    let work = drive.appendingPathComponent("untitled folder")
    let shoot = work.appendingPathComponent("Northwind Media/15.10.26 2 Harvest Dinner")
    _ = try write([1], to: shoot.appendingPathComponent("Footage/FX30/C9973.MP4"))
    _ = try write([2], to: shoot.appendingPathComponent("Footage/FX30/C9974.MP4"))
    _ = try write([0], to: shoot.appendingPathComponent("Footage/FX30/._C9973.MP4"))  // exFAT companion
    try FileManager.default.createDirectory(at: work.appendingPathComponent("Other Client/01.09.26 Wedding"),
                                            withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: drive.appendingPathComponent("Music"), withIntermediateDirectories: true)

    let library = ShootLibrary(root: drive)
    expect(library.shoots.count == 2, "finds both shoots two levels down")
    expect(library.shoots.first?.folderName == "15.10.26 2 Harvest Dinner", "newest shoot first")
    expect(library.label(for: library.clients[0]) == "untitled folder › Northwind Media", "client label shows path")
    expect(library.workingFolder.standardizedFileURL == work.standardizedFileURL, "new clients go beside existing ones")
    expect(library.shoots.first?.cameras == ["FX30": 2], "counts clips, ignores ._ files")
    expect(library.shoots.first?.shootName == "2 Harvest Dinner", "shoot name without date")

    let plan = ShootPlan(shootFolder: library.shoots[0].url, camera: "FX30", hasPhotos: false)
    expect(plan.footageFolder.standardizedFileURL == shoot.appendingPathComponent("Footage/FX30").standardizedFileURL,
           "second card joins the existing Footage/FX30")
}

func resumesHalfCopiedShootMovedAroundTheCard() throws {
    // The real 13 Oct night shoot: the after-midnight half was moved by hand into M4ROOT/acme,
    // and an earlier copy into ACME/261013 15 spring launch stopped part way.
    let cal = Calendar.current
    func at(_ day: Int, _ hour: Int, _ minute: Int) -> Date {
        cal.date(from: DateComponents(year: 2026, month: 10, day: day, hour: hour, minute: minute))!
    }
    let card = try tempDir(), drive = try tempDir()
    func clip(_ path: String, _ seed: UInt8, _ date: Date) throws {
        let url = card.appendingPathComponent(path)
        _ = try write((0..<64).map { UInt8($0) &+ seed }, to: url)
        try FileManager.default.setAttributes([.creationDate: date], ofItemAtPath: url.path)
    }
    try clip("M4ROOT/CLIP/C0337.MP4", 1, at(13, 23, 45))
    try clip("M4ROOT/acme/C0338.MP4", 2, at(14, 0, 7))
    try clip("M4ROOT/acme/C0398.MP4", 3, at(14, 1, 30))
    let shoot = drive.appendingPathComponent("ACME/261013 15 spring launch")
    for (name, seed) in [("C0337.MP4", UInt8(1)), ("C0338.MP4", 2)] {
        _ = try write((0..<64).map { UInt8($0) &+ seed }, to: shoot.appendingPathComponent("Footage/A7S III/\(name)"))
    }
    // Decoys: a shoot dated the after-midnight day, and a stray copy outside any shoot.
    _ = try write([9], to: drive.appendingPathComponent("Other/261014 Gig/Footage/FX30/C0001.MP4"))
    _ = try write((0..<64).map { UInt8($0) &+ 2 }, to: drive.appendingPathComponent("Marley/vid/untitled folder/C0338.MP4"))

    let files = Card.mediaFiles(on: card)
    expect(files.filter { $0.kind == .video }.count == 3, "clips moved into another card folder are still found")
    var sessions = SessionPlan(files: files)
    expect(sessions.sessions.count == 1, "the night stays one session across midnight and both folders")

    let index = DriveIndex(root: drive)
    let copies = Dictionary(uniqueKeysWithValues: files.map { ($0.url, index.copies(of: $0)) })
    expect(files.filter { copies[$0.url]!.isEmpty }.map(\.url.lastPathComponent) == ["C0398.MP4"],
           "only the clip the stopped copy never reached is left to copy")

    let library = ShootLibrary(root: drive)
    func target(_ session: CardSession) -> String? {
        library.shootHolding(session.files.flatMap { copies[$0.url] ?? [] })?.folderName
    }
    expect(target(sessions.sessions[0]) == "261013 15 spring launch", "the rest goes where the first part went")
    sessions.splitByDay(sessions.sessions[0])
    expect(sessions.sessions.count == 2, "split by day still works")
    expect(target(sessions.sessions[1]) == "261013 15 spring launch",
           "after a split, the after-midnight half still joins its shoot, not the shoot dated that day")
    expect(library.shootHolding([]) == nil, "nothing copied yet: falls back to the usual pick")
}

func parsesLsofOutput() {
    let output = "p412\ncFinder\nf12\np412\ncFinder\np9001\ncDaVinci Resolve\np88\ncmds_stores\n"
    let users = DiskUsers.parse(output)
    expect(users.map(\.pid) == [412, 9001, 88], "one entry per process, in order")
    expect(users.map(\.command) == ["Finder", "DaVinci Resolve", "mds_stores"], "command names kept")
    expect(DiskUsers.parse("").isEmpty, "nothing open -> empty")
}

func namesCamerasLikeAPerson() {
    func name(_ model: String) -> String { CameraInfo(model: model, serial: nil).shortName }
    expect(name("ILME-FX30") == "FX30", "ILME-FX30 -> FX30")
    expect(name("ILME-FX3") == "FX3", "ILME-FX3 -> FX3")
    expect(name("ILCE-7SM3") == "A7S III", "ILCE-7SM3 -> A7S III")
    expect(name("ILCE-7RM5") == "A7R V", "ILCE-7RM5 -> A7R V")
    expect(name("ILCE-6700") == "A6700", "ILCE-6700 -> A6700")
    expect(name("Canon EOS R5") == "EOS R5", "Canon EOS R5 -> EOS R5")
    expect(name("RICOH GR III") == "GR III", "RICOH GR III -> GR III")
    expect(name("DJI Pocket 3") == "DJI Pocket 3", "unknown brands left alone")
}

func findsSonyXMLAtCardRoot() throws {
    let card = try tempDir()
    let xml = #"<Device manufacturer="Sony" modelName="ILCE-7SM3" serialNo="123"/>"#
    try FileManager.default.createDirectory(at: card.appendingPathComponent("M4ROOT/CLIP"), withIntermediateDirectories: true)
    try xml.write(to: card.appendingPathComponent("M4ROOT/CLIP/C0001M01.XML"), atomically: true, encoding: .utf8)
    expect(CameraInfo.read(fromCard: card)?.model == "ILCE-7SM3", "Alpha card: M4ROOT at the card root")
}

func splitsCardIntoSessions() {
    // Shape of the real A7S III card: 5 Oct morning, 13 Oct night running past midnight, 15 Oct morning.
    let cal = Calendar.current
    func at(_ day: Int, _ hour: Int, _ minute: Int) -> Date {
        cal.date(from: DateComponents(year: 2026, month: 10, day: day, hour: hour, minute: minute))!
    }
    func file(_ name: String, _ date: Date) -> MediaFile {
        MediaFile(url: URL(fileURLWithPath: "/Volumes/Untitled/M4ROOT/CLIP/\(name)"), size: 10, created: date)
    }
    func thumb(_ name: String, _ date: Date) -> MediaFile {
        MediaFile(url: URL(fileURLWithPath: "/Volumes/Untitled/M4ROOT/THMBNL/\(name)"), size: 1, created: date)
    }
    let files = [
        file("C0001.MP4", at(5, 5, 43)), file("C0001M01.XML", at(5, 5, 43)), thumb("C0001T01.JPG", at(5, 5, 43)),
        file("C0002.MP4", at(5, 7, 31)),
        file("C0100.MP4", at(13, 22, 0)), file("C0101.MP4", at(13, 23, 45)),
        file("C0102.MP4", at(14, 0, 7)), file("C0103.MP4", at(14, 2, 0)), file("C0104.MP4", at(14, 4, 3)),
        thumb("C0150T01.JPG", at(14, 1, 0)),  // orphan: its clip was deleted in camera
        file("C0200.MP4", at(15, 6, 27)),
        MediaFile(url: URL(fileURLWithPath: "/Volumes/Untitled/M4ROOT/MEDIAPRO.XML"), size: 1, created: at(15, 9, 0)),
    ]
    var plan = SessionPlan(files: files)
    var sessions = plan.sessions
    expect(sessions.count == 3, "3h rule: 5 Oct, 13-14 Oct overnight, 15 Oct")
    expect(sessions.map(\.clipCount) == [2, 5, 1], "clips per session")
    let first = Set(sessions[0].files.map(\.url.lastPathComponent))
    expect(first.isSuperset(of: ["C0001M01.XML", "C0001T01.JPG"]), "XML + thumbnail follow their clip")
    expect(sessions.allSatisfy { $0.files.contains { $0.url.lastPathComponent == "MEDIAPRO.XML" } }, "card-wide XML goes with every session")
    expect(sessions[1].spansDays && !sessions[0].spansDays, "overnight session spans days")
    let orphanHomes = sessions.map { $0.files.contains { $0.url.lastPathComponent == "C0150T01.JPG" } }
    expect(orphanHomes == [false, true, false], "orphan thumbnail goes only to the session it was shot in")

    expect(sessions[0].middleShot?.url.lastPathComponent == "C0002.MP4", "middle clip of [C0001, C0002]")
    expect(sessions[1].middleShot?.url.lastPathComponent == "C0102.MP4", "middle of 5 overnight clips")
    let firstPreview = SessionPlan(files: [files[0], files[1], files[2]]).sessions[0].preview
    expect(firstPreview?.url.lastPathComponent == "C0001T01.JPG", "uses the camera's own thumbnail when there is one")
    expect(sessions[2].preview?.url.lastPathComponent == "C0200.MP4", "no thumbnail -> the clip itself")

    plan.splitByDay(sessions[1])
    sessions = plan.sessions
    expect(sessions.map(\.clipCount) == [2, 2, 3, 1], "split by day: 13 Oct and 14 Oct apart")
    plan.mergeWithNext(sessions[1])
    expect(plan.sessions.map(\.clipCount) == [2, 5, 1], "merge joins them back")

    plan.regroup(gap: 24 * 3600 * 30)
    expect(plan.sessions.count == 1, "huge gap -> one session")
}

func checksFreeSpace() {
    let gb: Int64 = 1_000_000_000
    expect(DriveSpace.shortfall(needed: 189 * gb, available: 240 * gb) == nil, "189 GB into 240 GB free fits")
    expect(DriveSpace.shortfall(needed: 239 * gb, available: 240 * gb) == 1 * gb, "keeps 2 GB spare: 1 GB short")
    expect(DriveSpace.available(at: URL(fileURLWithPath: NSTemporaryDirectory())).map { $0 > 0 } == true, "reads real free space")
    expect(DriveScan.identity(of: URL(fileURLWithPath: "/")).flatMap(\.free).map { $0 > 0 } == true,
           "drive scan reports real free space")
}

func readsBothShootDateStyles() {
    let dotted = ShootLibrary.splitDate("15.10.26 2 Harvest Dinner")
    let compact = ShootLibrary.splitDate("260729 Pod Sam")
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd"
    expect(dotted.date.map(f.string) == "2026-10-15" && dotted.name == "2 Harvest Dinner", "dd.MM.yy folder")
    expect(compact.date.map(f.string) == "2026-07-29" && compact.name == "Pod Sam", "yyMMdd folder")
    expect(ShootLibrary.splitDate("263412 Not a date").date == nil, "impossible yyMMdd is not a date")
    expect(ShootLibrary.splitDate("100MSDCF").date == nil, "camera folder is not a date")
}

func catalogsDrivesAndAsksAboutUnknownShoots() throws {
    // Mirrors the Lexar: client folders at the top, shoots inside, some undated.
    let drive = try tempDir()
    let copied = drive.appendingPathComponent("ACME/13.10.26 Spring launch")
    _ = try write([1, 2, 3], to: copied.appendingPathComponent("Footage/FX3/C0001.MP4"))
    _ = try write([0], to: copied.appendingPathComponent(".cairn/offload.json"))
    _ = try write([1, 2], to: drive.appendingPathComponent("ACME/260729 acme coast 12/A001.MOV"))
    _ = try write([1], to: drive.appendingPathComponent("ACME/Brand kit/logo.png"))
    _ = try write([1], to: drive.appendingPathComponent("ORBIT/orbit short film 1/A.MOV"))
    _ = try write([1], to: drive.appendingPathComponent("$RECYCLE.BIN/sub/desktop.ini"))
    _ = try write([1], to: drive.appendingPathComponent("desk/notes.pdf"))

    let found = DriveScan.scan(drive, volume: drive, driveID: "T7")
    expect(Set(found.clients.map(\.name)) == ["ACME", "ORBIT", "desk"], "top folders are client candidates, system ones skipped")
    expect(found.clients.first { $0.name == "ACME" }?.looksLikeClient == true
           && found.clients.first { $0.name == "ORBIT" }?.looksLikeClient == false, "dated shoots pre-tick a client")
    let coast = found.shoots.first { $0.path == "ACME/260729 acme coast 12" }
    expect(coast.map { $0.name == "acme coast 12" && $0.date == "2026-07-29" && $0.clips == 1 && $0.bytes == 2 } == true,
           "old shoot: name, date, clips, size")
    expect(found.shoots.first { $0.name == "Brand kit" }?.looksLikeShoot == false, "undated folder isn't pre-ticked")

    var catalog = Catalog()
    let t7 = CatalogDrive(id: "T7", name: "Samsung T7", capacity: nil, free: nil, lastSeen: Date())
    catalog.merge(drive: t7, clients: found.clients, shoots: found.shoots)
    expect(catalog.shoots.map(\.name) == ["Spring launch"], "Offload's own shoot (and its client) logged straight away")
    expect(Set(catalog.pendingClients.map(\.name)) == ["ORBIT", "desk"], "other clients wait for review")
    expect(Set(catalog.pendingShoots.map(\.name)) == ["acme coast 12", "Brand kit"], "only confirmed clients' shoots are asked about")

    let orbit = catalog.pendingClients.first { $0.name == "ORBIT" }!.id
    catalog.answerClients(asked: Set(catalog.pendingClients.map(\.id)), yes: [orbit])
    expect(catalog.pendingClients.isEmpty && catalog.pendingShoots.count == 3, "confirming ORBIT puts its shoot up for review")
    let skip = catalog.pendingShoots.first { $0.name == "Brand kit" }!.id
    let asked = Set(catalog.pendingShoots.map(\.id))
    catalog.answerShoots(asked: asked, yes: asked.subtracting([skip]))
    catalog.setNote("Home shelf", forDrive: "T7")

    catalog.merge(drive: t7, clients: found.clients, shoots: found.shoots)
    expect(catalog.shoots.count == 3 && !catalog.hasPending, "answers survive a rescan; no and ignored aren't asked again")
    expect(catalog.drive("T7")?.note == "Home shelf", "drive note survives rescan")
    expect(catalog.search("acme 2026 t7").count == 2 && catalog.search("coast 260729").count == 1
           && catalog.search("wedding").isEmpty, "search matches every word")

    catalog.merge(drive: t7, clients: found.clients, shoots: found.shoots.filter { $0.client != "ORBIT" })
    expect(!catalog.shoots.contains { $0.client == "ORBIT" }, "shoot gone from drive drops out")

    let note = catalog.markdown()
    expect(note.contains("## ACME") && note.contains("| 261013 | Spring launch | FX3 |")
           && note.contains("| Samsung T7 | Home shelf |"), "Obsidian note has client and drive tables")

    let file = drive.appendingPathComponent("catalog.json")
    try catalog.save(to: file)
    let loaded = Catalog.load(from: file)  // dates come back to the second
    expect(loaded.folders == catalog.folders && loaded.clients == catalog.clients
           && loaded.drives.map(\.note) == ["Home shelf"], "catalog round-trips through disk")
    try #"{"schema_version": 1, "shoots": []}"#.write(to: file, atomically: true, encoding: .utf8)
    expect(Catalog.load(from: file).drives.isEmpty && !FileManager.default.fileExists(atPath: file.path),
           "old-format catalog is set aside, not overwritten")
}

func renamedFoldersKeepTheirAnswers() throws {
    let drive = try tempDir()
    _ = try write([1, 2], to: drive.appendingPathComponent("ORBIT/orbit short film 1/A001.MOV"))
    _ = try write([3], to: drive.appendingPathComponent("ORBIT/orbit short film 1/B/B001.MOV"))
    _ = try write([4], to: drive.appendingPathComponent("ORBIT/brand kit/logo.png"))
    let t7 = CatalogDrive(id: "T7", name: "T7", capacity: nil, free: nil, lastSeen: Date())
    var catalog = Catalog()
    let first = DriveScan.scan(drive, volume: drive, driveID: "T7")
    catalog.merge(drive: t7, clients: first.clients, shoots: first.shoots)
    catalog.answerClients(asked: Set(catalog.pendingClients.map(\.id)), yes: Set(catalog.pendingClients.map(\.id)))
    let film = catalog.pendingShoots.first { $0.name == "orbit short film 1" }!.id
    catalog.answerShoots(asked: Set(catalog.pendingShoots.map(\.id)), yes: [film])

    // Rename the shoot and the client, and reshuffle a subfolder inside the shoot.
    let fm = FileManager.default
    try fm.moveItem(at: drive.appendingPathComponent("ORBIT/orbit short film 1"),
                    to: drive.appendingPathComponent("ORBIT/260726 orbit short film 1"))
    try fm.moveItem(at: drive.appendingPathComponent("ORBIT"), to: drive.appendingPathComponent("ORBIT FILMS"))
    try fm.moveItem(at: drive.appendingPathComponent("ORBIT FILMS/260726 orbit short film 1/B"),
                    to: drive.appendingPathComponent("ORBIT FILMS/260726 orbit short film 1/Cam B"))
    let second = DriveScan.scan(drive, volume: drive, driveID: "T7")
    catalog.merge(drive: t7, clients: second.clients, shoots: second.shoots)
    expect(catalog.shoots.map { "\($0.client)/\($0.name)/\($0.date ?? "-")" } == ["ORBIT FILMS/orbit short film 1/2026-07-26"],
           "renamed shoot in renamed client keeps its yes, with the new name and date")
    expect(!catalog.hasPending, "renamed folders aren't asked about again (brand kit stays a no)")

    _ = try write([9], to: drive.appendingPathComponent("ORBIT FILMS/260726 orbit short film 1/extra.MOV"))
    try fm.moveItem(at: drive.appendingPathComponent("ORBIT FILMS/260726 orbit short film 1"),
                    to: drive.appendingPathComponent("ORBIT FILMS/260726 orbit film"))
    let third = DriveScan.scan(drive, volume: drive, driveID: "T7")
    catalog.merge(drive: t7, clients: third.clients, shoots: third.shoots)
    expect(catalog.pendingShoots.map(\.name) == ["orbit film"], "renamed and changed footage is asked about once more")
}

func readsEveryBrandsCardLayout() throws {
    // A Lumix card: AVCHD (copied as a whole tree), RW2 stills, a Sony-style card database
    // (housekeeping) and one file nobody knows.
    let card = try tempDir()
    for path in ["PRIVATE/AVCHD/BDMV/STREAM/00000.MTS", "PRIVATE/AVCHD/BDMV/CLIPINF/00000.CPI",
                 "PRIVATE/AVCHD/BDMV/PLAYLIST/00000.MPL", "PRIVATE/AVCHD/BDMV/INDEX.BDM",
                 "DCIM/100_PANA/P1000001.RW2", "PRIVATE/SONY/SONYCARD.IND", "MYSTERY.XYZ"] {
        _ = try write([1], to: card.appendingPathComponent(path))
    }
    let (files, unknown) = Card.survey(of: card)
    let names = Set(files.map(\.url.lastPathComponent))
    expect(names == ["00000.MTS", "00000.CPI", "00000.MPL", "INDEX.BDM", "P1000001.RW2"], "AVCHD tree whole + RW2 photo")
    expect(unknown == ["XYZ": 1], "unknown type reported, card database isn't")
    expect(Card.isCard(card) && Card.brandHint(of: card) == "Panasonic", "Lumix card recognised")
    let plan = ShootPlan(shootFolder: try tempDir(), camera: "S5 II", hasPhotos: true)
    let mts = files.first { $0.url.pathExtension == "MTS" }!
    expect(plan.destinationFolder(for: mts).path.hasSuffix("Footage/S5 II/PRIVATE/AVCHD/BDMV/STREAM"),
           "AVCHD keeps its tree under the camera folder")
    expect(plan.destinationFolder(for: files.first { $0.url.pathExtension == "RW2" }!).lastPathComponent == "Photos",
           "RW2 goes to Photos")

    // The sort never takes a copied tree apart.
    let shoot = plan.shootFolder
    _ = try write([1], to: shoot.appendingPathComponent("Footage/S5 II/PRIVATE/AVCHD/BDMV/STREAM/00000.MTS"))
    _ = try write([1], to: shoot.appendingPathComponent("Footage/S5 II/PRIVATE/AVCHD/BDMV/INDEX.BDM"))
    expect(ShootTidy.plan(shoot).fileCount == 0, "sort leaves the AVCHD tree alone")

    // Folder hints and model names.
    for (folder, brand) in [("100NCZ_8", "Nikon"), ("DJI_001", "DJI"), ("100CANON", "Canon"), ("100GOPRO", "GoPro"),
                            ("100_FUJI", "Fujifilm"), ("100RICOH", "Ricoh"), ("100OMSYS", "OM System")] {
        let other = try tempDir()
        try FileManager.default.createDirectory(at: other.appendingPathComponent("DCIM/\(folder)"), withIntermediateDirectories: true)
        expect(Card.brandHint(of: other) == brand, "DCIM/\(folder) -> \(brand)")
    }
    expect(CameraInfo(model: "OLYMPUS E-M1MarkII", serial: nil).shortName == "E-M1MarkII"
           && CameraInfo(model: "GoPro HERO12 Black", serial: nil).shortName == "GoPro HERO12 Black"
           && CameraInfo(model: "NIKON Z 8", serial: nil).shortName == "Z 8", "brand dropped from folder name")

    // Learning: each new camera + layout is logged once.
    let log = try tempDir().appendingPathComponent("cards-seen.md")
    expect(CardLog.record(volume: card, camera: nil, files: files, unknown: unknown, to: log), "new card logged")
    expect(!CardLog.record(volume: card, camera: nil, files: files, unknown: unknown, to: log), "same card not logged twice")
    let text = try String(contentsOf: log, encoding: .utf8)
    expect(text.contains("folders say Panasonic") && text.contains("XYZ ×1"), "log names brand and unknown files")
}

func photoOnlyShootDropsEmptyFootageAndProject() throws {
    let shoot = try tempDir().appendingPathComponent("261024 Party")
    let fm = FileManager.default
    _ = try write([1], to: shoot.appendingPathComponent("Photos/DSC0001.JPG"))
    for empty in ["Footage/GR III", "Project"] {
        try fm.createDirectory(at: shoot.appendingPathComponent(empty), withIntermediateDirectories: true)
    }
    let plan = ShootTidy.plan(shoot)
    expect(ShootTidy.removeUnusedFolders(plan), "photo-only: empty Footage/Project removed")
    expect(!fm.fileExists(atPath: shoot.appendingPathComponent("Footage").path)
           && !fm.fileExists(atPath: shoot.appendingPathComponent("Project").path), "Footage and Project gone")
    var after = ShootTidy.plan(shoot)
    let project = after.destination(.project)!
    expect(after.cameras.isEmpty && !after.shows(project) && after.shows(after.destination(.photos)!), "tree: only Photos")
    after.add(.project)
    expect(after.shows(after.destination(.project)!) && after.hasChanges, "Project can be added by hand")

    // With video, an empty Project stays.
    _ = try write([1], to: shoot.appendingPathComponent("C0001.MP4"))
    try fm.createDirectory(at: shoot.appendingPathComponent("Project"), withIntermediateDirectories: true)
    expect(!ShootTidy.removeUnusedFolders(ShootTidy.plan(shoot))
           && fm.fileExists(atPath: shoot.appendingPathComponent("Project").path), "video shoot keeps Project")
}

func sortsMusicIntoAssets() throws {
    let shoot = try tempDir().appendingPathComponent("260723 1 4 summer days")
    let fm = FileManager.default
    _ = try write([1], to: shoot.appendingPathComponent("vids/C0001.MP4"))
    _ = try write([2], to: shoot.appendingPathComponent("song.m4a"))
    _ = try write([3], to: shoot.appendingPathComponent("Assets/bed.flac"))  // already sorted
    var plan = ShootTidy.plan(shoot)
    let assets = plan.destination(.assets)!
    expect(assets.bundles.map(\.type).sorted() == ["FLAC", "M4A"] && assets.incoming == 1
           && plan.destination(.stay)!.bundles.isEmpty && assets.bundles.allSatisfy { $0.label == "music" },
           "loose .m4a goes to Assets, labelled music; Assets/ is already sorted")
    expect(plan.shows(assets), "Assets shows in the tree")
    try ShootTidy.apply(plan)
    expect(fm.fileExists(atPath: shoot.appendingPathComponent("Assets/song.m4a").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Assets/bed.flac").path), "song moved into Assets")
    plan = ShootTidy.plan(shoot)
    expect(plan.destination(.assets)!.incoming == 0, "nothing to move on a second look")
}

func binsSonyHousekeepingCopiedByHand() throws {
    // A Sony card dragged in with Finder: the clip is sorted, the card's own files go in the Bin.
    let shoot = try tempDir().appendingPathComponent("BAND/fx30")
    let fm = FileManager.default
    for path in ["private/M4ROOT/CLIP/C0001.MP4", "private/M4ROOT/CLIP/C0001M01.XML", "private/M4ROOT/MEDIAPRO.XML",
                 "private/M4ROOT/GENERAL/LUT/look.cube", "private/DATABASE/DATABASE.BIN", "notes.txt"] {
        _ = try write([1], to: shoot.appendingPathComponent(path))
    }
    var plan = ShootTidy.plan(shoot)
    let bin = plan.destination(.bin)!
    expect(Set(bin.bundles.flatMap(\.files).map(\.lastPathComponent)) == ["MEDIAPRO.XML", "look.cube", "DATABASE.BIN"],
           "card housekeeping goes in the Bin")
    expect(bin.incoming == 3 && plan.hasChanges, "binning counts as something to sort")
    expect(plan.destination(.stay)?.bundles.flatMap(\.files).map(\.lastPathComponent) == ["notes.txt"],
           "your own files stay where they are")
    let clips = plan.cameras.flatMap(\.bundles).first { $0.kind == .video }!
    plan.move(bundle: clips.id, to: "bin")
    expect(plan.destination(.bin)!.bundles.count == bin.bundles.count, "footage can't be put in the Bin")

    try ShootTidy.apply(plan)
    expect(!fm.fileExists(atPath: shoot.appendingPathComponent("private").path), "emptied card folders are removed")
    expect(fm.fileExists(atPath: shoot.appendingPathComponent("notes.txt").path), "notes.txt left alone")
    expect(ShootTidy.plan(shoot).destination(.bin)!.bundles.isEmpty, "nothing left for the Bin")

    try ShootTidy.undoSort(shoot)
    expect(fm.fileExists(atPath: shoot.appendingPathComponent("private/DATABASE/DATABASE.BIN").path),
           "undo brings DATABASE.BIN back from the Bin")
}

func sortsLooseFootageAndUndoes() throws {
    let shoot = try tempDir().appendingPathComponent("ORBIT/260726 orbit short film 1")
    let xml = #"<NonRealTimeMeta><Device manufacturer="Sony" modelName="ILME-FX3" serialNo="1"/></NonRealTimeMeta>"#
    _ = try write([1, 2], to: shoot.appendingPathComponent("Aidan back/C0001.MP4"))
    _ = try write(Array(xml.utf8), to: shoot.appendingPathComponent("Aidan back/C0001M01.XML"))
    _ = try write([3], to: shoot.appendingPathComponent("Eva/clip.MOV"))  // no camera info: named after its folder
    _ = try write([4], to: shoot.appendingPathComponent("pics/IMG_0001.JPG"))
    _ = try write([5], to: shoot.appendingPathComponent("notes.pdf"))
    _ = try write([7], to: shoot.appendingPathComponent("pics/IMG_0001.xmp"))     // Lightroom edit, with its photo
    _ = try write([8], to: shoot.appendingPathComponent("pics/_DSC0554.xmp"))     // edit whose photo is gone
    _ = try write([9], to: shoot.appendingPathComponent("pics/IMG_0001.photo-edit"))  // Photomator edit
    _ = try write([6], to: shoot.appendingPathComponent("Footage/FX3/C0001.MP4"))  // already sorted, same name

    let found = DriveScan.scan(shoot.deletingLastPathComponent().deletingLastPathComponent(),
                               volume: shoot.deletingLastPathComponent().deletingLastPathComponent(), driveID: "D")
    expect(found.shoots.first?.unsorted == 7, "scan counts 7 loose media files incl. .xmp and .photo-edit (not the pdf, not Footage/)")

    var plan = ShootTidy.plan(shoot)
    expect(plan.cameras.map(\.name) == ["Eva", "FX3"], "cameras: Sony XML names FX3, unknown clip takes its folder name")
    let fx3 = plan.cameras.first { $0.name == "FX3" }!
    expect(fx3.bundles.map { "\($0.type)/\($0.origin)" } == ["MP4/Footage/FX3", "MP4/Aidan back", "XML/Aidan back"]
           && fx3.incoming == 2
           && plan.destination(.photos)?.fileCount == 4 && plan.destination(.stay)?.bundles.map(\.type) == ["PDF"],
           "bundles by type and folder: XML follows its clip, JPG, .xmp and .photo-edit to Photos, PDF stays")

    // Edit like the sort page: add a folder, move Eva's clips into it, rename it, send the PDF to Project.
    let added = plan.addCameraFolder(named: "B-cam")
    let eva = plan.cameras.first { $0.name == "Eva" }!
    plan.move(bundle: eva.bundles[0].id, to: added)
    plan.remove(eva.id)
    if let i = plan.destinations.firstIndex(where: { $0.id == added }) { plan.destinations[i].name = "Eva cam" }
    plan.move(bundle: plan.destination(.stay)!.bundles[0].id, to: "project")
    expect(plan.cameras.map(\.name) == ["FX3", "Eva cam"] && plan.fileCount == 8, "add, move, rename and remove folders")
    try ShootTidy.apply(plan)
    let fm = FileManager.default
    expect(fm.fileExists(atPath: shoot.appendingPathComponent("Footage/FX3/C0001 (2).MP4").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Footage/FX3/C0001.MP4").path),
           "never overwrites: second C0001 becomes C0001 (2)")
    expect(fm.fileExists(atPath: shoot.appendingPathComponent("Footage/FX3/Metadata/C0001M01.XML").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Footage/Eva cam/clip.MOV").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Photos/IMG_0001.JPG").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Photos/IMG_0001.xmp").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Photos/_DSC0554.xmp").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Photos/IMG_0001.photo-edit").path), "everything lands in the layout")
    expect(!fm.fileExists(atPath: shoot.appendingPathComponent("Aidan back").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Project/notes.pdf").path), "emptied folders tidied, pdf in Project")
    expect(ShootTidy.plan(shoot).isEmpty, "nothing left to sort")
    let gr = shoot.appendingPathComponent("Footage/GR III")
    try FileManager.default.createDirectory(at: gr, withIntermediateDirectories: true)
    let withEmpty = ShootTidy.plan(shoot)
    expect(withEmpty.cameras.first { $0.name == "GR III" }?.emptyOnDisk == true
           && withEmpty.cameras.first { $0.name == "FX3" }?.emptyOnDisk == false, "existing empty camera folder is shown as empty")
    expect(ShootTidy.deleteEmptyCameraFolder(gr) && !FileManager.default.fileExists(atPath: gr.path)
           && !ShootTidy.deleteEmptyCameraFolder(shoot.appendingPathComponent("Footage/FX3")), "deletes only empty folders")

    let undone = try ShootTidy.undoSort(shoot)
    expect(undone.restored == 8 && undone.skipped == 0
           && fm.fileExists(atPath: shoot.appendingPathComponent("Aidan back/C0001.MP4").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Footage/FX3/C0001.MP4").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("notes.pdf").path)
           && !fm.fileExists(atPath: shoot.appendingPathComponent("Footage/Eva cam").path), "undo puts everything back")
    expect(!ShootTidy.canUndoSort(shoot), "undo log cleared")
    // Rename an existing folder (FX3 -> A-cam) in an already-sorted shoot: files inside stay
    // put and only the folder is renamed.
    let sorted = try tempDir()
    _ = try write([1], to: sorted.appendingPathComponent("Footage/FX3/C0001.MP4"))
    var tidy = ShootTidy.plan(sorted)
    expect(!tidy.hasChanges && tidy.cameras.first?.fileCount == 1, "sorted shoot shows its files, nothing to do")
    tidy.destinations[0].name = "A-cam"
    expect(tidy.fileCount == 0 && tidy.renames.count == 1 && tidy.hasChanges, "a rename alone is a change, moves nothing")
    try ShootTidy.apply(tidy)
    expect(fm.fileExists(atPath: sorted.appendingPathComponent("Footage/A-cam/C0001.MP4").path)
           && !fm.fileExists(atPath: sorted.appendingPathComponent("Footage/FX3").path), "existing folder renamed with its files")
    try ShootTidy.undoSort(sorted)
    expect(fm.fileExists(atPath: sorted.appendingPathComponent("Footage/FX3/C0001.MP4").path), "undo renames it back")
}

func eachCameraKeepsItsOwnSidecars() throws {
    // Two Sony cards dumped into one shoot, each with its own CLIP and THMBNL.
    let shoot = try tempDir()
    func xml(_ model: String) -> [UInt8] {
        Array(#"<NonRealTimeMeta><Device manufacturer="Sony" modelName="\#(model)" serialNo="1"/></NonRealTimeMeta>"#.utf8)
    }
    for (card, model, clip) in [("A cam", "ILCE-7SM3", "C0001"), ("B cam", "ILME-FX3", "C0001")] {
        _ = try write([1], to: shoot.appendingPathComponent("\(card)/M4ROOT/CLIP/\(clip).MP4"))
        _ = try write(xml(model), to: shoot.appendingPathComponent("\(card)/M4ROOT/CLIP/\(clip)M01.XML"))
        _ = try write([2], to: shoot.appendingPathComponent("\(card)/M4ROOT/THMBNL/\(clip)T01.JPG"))
        _ = try write([3], to: shoot.appendingPathComponent("\(card)/M4ROOT/THMBNL/C9999T01.JPG"))  // clip deleted in camera
    }
    _ = try write(xml("ILCE-7SM3"), to: shoot.appendingPathComponent("loose/C0500M01.XML"))  // XML alone: names its camera
    let plan = ShootTidy.plan(shoot)
    func bundles(_ name: String) -> [String] { plan.cameras.first { $0.name == name }?.bundles.map { "\($0.type)×\($0.files.count)" } ?? [] }
    expect(bundles("A7S III").sorted() == ["JPG×2", "MP4×1", "XML×1", "XML×1"] && bundles("FX3").sorted() == ["JPG×2", "MP4×1", "XML×1"],
           "each camera gets its own clips, thumbnails (orphans too) and XML: \(bundles("A7S III")) / \(bundles("FX3"))")
    try ShootTidy.apply(plan)
    let fm = FileManager.default
    expect(fm.fileExists(atPath: shoot.appendingPathComponent("Footage/A7S III/Thumbnails/C9999T01.JPG").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Footage/FX3/Metadata/C0001M01.XML").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Footage/A7S III/Metadata/C0500M01.XML").path),
           "sidecars land in <camera>/Thumbnails and <camera>/Metadata")
}

func stabilisedClipsGoWithTheirCamera() throws {
    let shoot = try tempDir()
    let xml = Array(#"<NonRealTimeMeta><Device manufacturer="Sony" modelName="ILCE-7SM3" serialNo="1"/></NonRealTimeMeta>"#.utf8)
    _ = try write([1], to: shoot.appendingPathComponent("vids/C8801.MP4"))
    _ = try write(xml, to: shoot.appendingPathComponent("vids/C8801M01.XML"))
    _ = try write([2], to: shoot.appendingPathComponent("vids/Proxy/C8801.mov"))       // proxy: doesn't steal the camera
    _ = try write([3], to: shoot.appendingPathComponent("TEST/C8801_stabilized.mp4"))  // another folder from its source
    _ = try write([4], to: shoot.appendingPathComponent("TEST/C8801_stab422.mp4"))
    _ = try write([5], to: shoot.appendingPathComponent("TEST/look 7pct.gyroflow"))
    expect(ShootTidy.stabilisedSource(URL(fileURLWithPath: "/x/C8801_stab422.mp4")) == "C8801"
           && ShootTidy.stabilisedSource(URL(fileURLWithPath: "/x/stab.mp4")) == nil, "spots stabilised exports by name")
    let plan = ShootTidy.plan(shoot)
    try ShootTidy.apply(plan)
    let fm = FileManager.default
    expect(["C8801_stabilized.mp4", "C8801_stab422.mp4", "look 7pct.gyroflow"].allSatisfy {
        fm.fileExists(atPath: shoot.appendingPathComponent("Footage/A7S III/Stabilised/\($0)").path)
    } && fm.fileExists(atPath: shoot.appendingPathComponent("Footage/A7S III/C8801.MP4").path),
           "stabilised clips and the Gyroflow file go to <camera>/Stabilised")
}

func checksWhichCameraEachFolderIs() throws {
    func xml(_ model: String) -> [UInt8] {
        Array(#"<NonRealTimeMeta><Device manufacturer="Sony" modelName="\#(model)" serialNo="1"/></NonRealTimeMeta>"#.utf8)
    }
    // Gyroflow exports put in Footage/vid by hand: nothing in them says which camera.
    let shoot = try tempDir()
    _ = try write([1], to: shoot.appendingPathComponent("Footage/vid/C0496_stabilized.mov"))
    _ = try write([2], to: shoot.appendingPathComponent("Footage/FX30/C9973.MP4"))
    _ = try write(xml("ILME-FX30"), to: shoot.appendingPathComponent("Footage/FX30/Metadata/C9973M01.XML"))
    var plan = ShootTidy.plan(shoot, knownCameras: ["A7S III", "FX30"])
    let vid = plan.cameras.first { $0.name == "Vid" }!
    let fx30 = plan.cameras.first { $0.name == "FX30" }!
    expect(plan.cameraCheck(vid) == .unknown && plan.cameraCheck(fx30) == nil && plan.unchecked.map(\.name) == ["Vid"],
           "a folder of clips that don't name their camera is asked about; FX30's clips say FX30")
    expect(plan.suggestions(for: vid) == ["A7S III"], "offers the known cameras not already in the shoot")
    plan.destinations[plan.destinations.firstIndex { $0.id == vid.id }!].name = "a7s iii"
    expect(plan.unchecked.isEmpty, "naming it after a known camera answers the check")
    plan.destinations[plan.destinations.firstIndex { $0.id == vid.id }!].name = "vid"
    plan.keepCameraName(vid.id)
    expect(plan.unchecked.isEmpty, "keeping the name answers it too")
    expect(ShootTidy.plan(shoot, knownCameras: ["A7S III"], kept: ["vid"]).unchecked.isEmpty, "a kept name isn't asked again")

    // Real A7S III clips sorted into a folder called vid: the files say it's the wrong name.
    let misnamed = try tempDir()
    _ = try write([1], to: misnamed.appendingPathComponent("Footage/vid/C0001.MP4"))
    _ = try write(xml("ILCE-7SM3"), to: misnamed.appendingPathComponent("Footage/vid/Metadata/C0001M01.XML"))
    let wrong = ShootTidy.plan(misnamed)
    expect(wrong.cameras.first.map(wrong.cameraCheck) == .differs("A7S III"), "a folder named for the wrong camera is caught")

    // Loose clips get their camera's name, so there's nothing to ask.
    let loose = try tempDir()
    _ = try write([1], to: loose.appendingPathComponent("vid/C0001.MP4"))
    _ = try write(xml("ILCE-7SM3"), to: loose.appendingPathComponent("vid/C0001M01.XML"))
    let fine = ShootTidy.plan(loose)
    expect(fine.cameras.map(\.name) == ["A7S III"] && fine.unchecked.isEmpty, "clips named by their camera need no check")
}

func resortsLeftoverFolders() throws {
    // An earlier sort left Footage/CLIP (stabilised exports) and Footage/Untitled/Thumbnails.
    let shoot = try tempDir()
    _ = try write([1], to: shoot.appendingPathComponent("Footage/A7S III/C8209.MP4"))
    _ = try write([2], to: shoot.appendingPathComponent("Footage/A7S III/C8209M01.XML"))      // XML outside Metadata/
    _ = try write([3], to: shoot.appendingPathComponent("Footage/CLIP/C8209_stabilized.mp4"))
    _ = try write([4], to: shoot.appendingPathComponent("Footage/Untitled/Thumbnails/C8209T01.JPG"))
    let plan = ShootTidy.plan(shoot)
    expect(plan.cameras.map(\.name) == ["A7S III"] && plan.fileCount == 3, "leftover folders aren't cameras; their files re-sort")
    expect(plan.typeCounts.map(\.type).sorted() == ["JPG", "MP4", "XML"], "summary lists every file type")
    try ShootTidy.apply(plan)
    let fm = FileManager.default
    expect(fm.fileExists(atPath: shoot.appendingPathComponent("Footage/A7S III/Stabilised/C8209_stabilized.mp4").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Footage/A7S III/Thumbnails/C8209T01.JPG").path)
           && fm.fileExists(atPath: shoot.appendingPathComponent("Footage/A7S III/Metadata/C8209M01.XML").path)
           && !fm.fileExists(atPath: shoot.appendingPathComponent("Footage/CLIP").path)
           && !fm.fileExists(atPath: shoot.appendingPathComponent("Footage/Untitled").path),
           "everything in the camera's own sub-folders, leftovers cleaned up")
}

func learnsFolderNamesFromWhatsInThem() throws {
    let shoot = try tempDir()
    let xml = #"<NonRealTimeMeta><Device manufacturer="Sony" modelName="ILME-FX3" serialNo="1"/></NonRealTimeMeta>"#
    _ = try write([1], to: shoot.appendingPathComponent("CLIP/C0001.MP4"))
    _ = try write(Array(xml.utf8), to: shoot.appendingPathComponent("CLIP/C0001M01.XML"))
    _ = try write([2], to: shoot.appendingPathComponent("render/final.MOV"))
    var plan = ShootTidy.plan(shoot)
    let render = plan.cameras.first { $0.name == "Render" }!
    let fx3 = plan.cameras.first { $0.name == "FX3" }!
    for (id, name) in [(render.id, "Renders"), (fx3.id, "A-cam")] {
        if let i = plan.destinations.firstIndex(where: { $0.id == id }) { plan.destinations[i].name = name }
    }
    let learned = ShootTidy.learnedNames(plan)
    expect(learned["model:ILME-FX3"] == "A-cam" && learned["from:render|MOV"] == "Renders", "names learned by camera and by folder + type")
    let next = try tempDir()
    _ = try write([3], to: next.appendingPathComponent("render/cut.MOV"))
    _ = try write([4], to: next.appendingPathComponent("render/still.png"))
    expect(ShootTidy.plan(next, learned: learned).cameras.map(\.name) == ["Renders"], "next shoot suggests the learned name")
    expect(ShootTidy.plan(next).destination(.stay)?.types == ["PNG"], "folders list their file types")
}

func capitalisesFolderNames() throws {
    // A folder named after a lowercase folder starts with a capital, like Footage and Photos.
    let shoot = try tempDir()
    _ = try write([1], to: shoot.appendingPathComponent("render/final.MOV"))
    _ = try write([2], to: shoot.appendingPathComponent("Footage/drone/DJI_0001.MP4"))
    _ = try write([3], to: shoot.appendingPathComponent("Footage/iPhone/IMG_0001.MOV"))
    _ = try write([4], to: shoot.appendingPathComponent("Footage/fx3/C0001.MP4"))
    let plan = ShootTidy.plan(shoot)
    expect(plan.cameras.map(\.name).sorted() == ["Drone", "Render", "fx3", "iPhone"],
           "lowercase names get a capital; ones with capitals or digits are left alone")
    expect(plan.renames.map(\.originalName) == ["drone"], "an existing lowercase folder is renamed on sort")
    try ShootTidy.apply(plan)
    let names = try FileManager.default.contentsOfDirectory(atPath: shoot.appendingPathComponent("Footage").path)
    expect(names.contains("Drone") && names.contains("Render") && !names.contains("drone"),
           "the case-only rename lands on disk")
}

func datesShootFromItsFootage() throws {
    // "11 oct ricoh" has no date in its name: Rename offers the day the footage was shot.
    let shoot = try tempDir()
    let fm = FileManager.default
    let day = { (d: Int) in DateComponents(calendar: .current, year: 2025, month: 10, day: d, hour: 14).date! }
    let shot = try write([1], to: shoot.appendingPathComponent("R0001234.JPG"))
    let later = try write([2], to: shoot.appendingPathComponent("Footage/R0001300.MOV"))
    let copied = try write([3], to: shoot.appendingPathComponent("R0001235.DNG"))
    let edit = try write([4], to: shoot.appendingPathComponent("R0001234.xmp"))
    try fm.setAttributes([.creationDate: day(11), .modificationDate: day(11)], ofItemAtPath: shot.url.path)
    try fm.setAttributes([.creationDate: day(12), .modificationDate: day(12)], ofItemAtPath: later.url.path)
    // Copied without its created date: the older modified date still says when it was shot.
    try fm.setAttributes([.creationDate: day(20), .modificationDate: day(11)], ofItemAtPath: copied.url.path)
    try fm.setAttributes([.creationDate: day(1), .modificationDate: day(1)], ofItemAtPath: edit.url.path)
    expect(ShootLibrary.footageDate(in: shoot) == day(11), "earliest photo or clip, ignoring sidecars")
    expect(ShootLibrary.footageDate(in: try tempDir()) == nil, "no footage, no date")
}

func clearsEmptyCardFolders() throws {
    // A Sony card copied by hand, sorted: M4ROOT and SONY are left with nothing in them.
    let shoot = try tempDir()
    let fm = FileManager.default
    _ = try write([1], to: shoot.appendingPathComponent("Footage/A7S III/C0001.MP4"))
    for dir in ["M4ROOT/SUB", "M4ROOT/TAKE", "SONY/PRO/LUT", "Project"] {
        try fm.createDirectory(at: shoot.appendingPathComponent(dir), withIntermediateDirectories: true)
    }
    _ = try write([], to: shoot.appendingPathComponent("M4ROOT/.DS_Store"))
    _ = try write([], to: shoot.appendingPathComponent("SONY/SONYCARD.IND"))
    let plan = ShootTidy.plan(shoot)
    expect(plan.emptyCardFolders == ["M4ROOT"], "an empty card folder is found, sub-folders and all")
    expect(plan.destination(.bin)?.bundles.map(\.type) == ["IND"], "the card index in SONY/ goes in the Bin")
    expect(plan.hasChanges, "so the shoot isn't called sorted")
    try ShootTidy.apply(plan)
    let left = try fm.contentsOfDirectory(atPath: shoot.path).filter { !$0.hasPrefix(".") }.sorted()
    expect(left == ["Footage", "Project"], "card folders gone; an empty Project folder stays")
}

func renamesShootAndFollowsRecords() throws {
    let client = try tempDir()
    let shoot = client.appendingPathComponent("13.10.26 Spring launch")
    let clip = try write([1], to: shoot.appendingPathComponent("Footage/FX3/C0001.MP4"))
    let inbox = try tempDir().appendingPathComponent("inbox.json")
    try Handoff.publish(ShootManifest(
        client: "ACME", shootName: "Spring launch", date: "2026-10-13", camera: "FX3", cameraModel: nil, cameraSerial: nil,
        cardName: "A", shootFolder: shoot.path, footageFolder: shoot.appendingPathComponent("Footage/FX3").path,
        photosFolder: nil, copiedAt: Date(),
        files: [CopiedFile(source: URL(fileURLWithPath: "/c/C0001.MP4"), destination: clip.url, size: 1, sha256: "ab")]
    ), inbox: inbox)

    let name = ShootTidy.folderName(name: "Spring launch promo", date: ShootLibrary.splitDate("261013 x").date)
    expect(name == "261013 Spring launch promo", "folder name is yyMMdd + name")
    let target = client.appendingPathComponent(name)
    try FileManager.default.moveItem(at: shoot, to: target)
    try Handoff.relocate(shootFolder: shoot, to: target, inbox: inbox) { p in
        p.hasPrefix(shoot.path) ? target.path + p.dropFirst(shoot.path.count) : p
    }
    let saved = try Handoff.decoder.decode(ShootManifest.self,
                                           from: Data(contentsOf: target.appendingPathComponent(".cairn/offload.json")))
    expect(saved.shootFolder == target.path && saved.files[0].destination.path == target.path + "/Footage/FX3/C0001.MP4"
           && Handoff.readInbox(inbox).first?.shootFolder == target.path, "offload.json and Cairn inbox follow the rename")

    expect((try? ShootTidy.rename(target, to: name)) == target, "same name is a no-op")
    try FileManager.default.createDirectory(at: client.appendingPathComponent("taken"), withIntermediateDirectories: true)
    expect((try? ShootTidy.rename(target, to: "taken")) == nil, "won't rename onto an existing folder")
}

func offersLibraryClientsFromOtherDrives() throws {
    let work = try tempDir(), other = try tempDir()
    try FileManager.default.createDirectory(at: work.appendingPathComponent("PERSONAL/261024 Sam"), withIntermediateDirectories: true)
    let acme = other.appendingPathComponent("ACME")
    let undated = acme.appendingPathComponent("brand shoot")
    try FileManager.default.createDirectory(at: undated, withIntermediateDirectories: true)
    let orbit = other.appendingPathComponent("ORBIT")
    let library = ShootLibrary(root: work, known: [(acme, [undated]), (orbit, [])])
    expect(library.clients.map(\.lastPathComponent) == ["PERSONAL", "ACME", "ORBIT"], "Library clients join the picker")
    expect(library.shoots(for: acme).map(\.folderName) == ["brand shoot"], "their confirmed shoots too, dated or not")
    expect(library.shortLabel(for: work.appendingPathComponent("PERSONAL")) == "PERSONAL"
           && library.shortLabel(for: acme).hasPrefix("ACME · "), "other-drive clients name their drive")
}

for (name, test) in [
    ("offersLibraryClientsFromOtherDrives", { try offersLibraryClientsFromOtherDrives() }),
    ("sortsLooseFootageAndUndoes", { try sortsLooseFootageAndUndoes() }),
    ("binsSonyHousekeepingCopiedByHand", { try binsSonyHousekeepingCopiedByHand() }),
    ("sortsMusicIntoAssets", { try sortsMusicIntoAssets() }),
    ("learnsFolderNamesFromWhatsInThem", { try learnsFolderNamesFromWhatsInThem() }),
    ("capitalisesFolderNames", { try capitalisesFolderNames() }),
    ("eachCameraKeepsItsOwnSidecars", { try eachCameraKeepsItsOwnSidecars() }),
    ("stabilisedClipsGoWithTheirCamera", { try stabilisedClipsGoWithTheirCamera() }),
    ("checksWhichCameraEachFolderIs", { try checksWhichCameraEachFolderIs() }),
    ("resortsLeftoverFolders", { try resortsLeftoverFolders() }),
    ("datesShootFromItsFootage", { try datesShootFromItsFootage() }),
    ("clearsEmptyCardFolders", { try clearsEmptyCardFolders() }),
    ("renamesShootAndFollowsRecords", { try renamesShootAndFollowsRecords() }),
    ("renamedFoldersKeepTheirAnswers", { try renamedFoldersKeepTheirAnswers() }),
    ("readsBothShootDateStyles", { readsBothShootDateStyles() }),
    ("skipsSonyCardHousekeeping", { try skipsSonyCardHousekeeping() }),
    ("catalogsDrivesAndAsksAboutUnknownShoots", { try catalogsDrivesAndAsksAboutUnknownShoots() }),
    ("checksFreeSpace", { checksFreeSpace() }),
    ("splitsCardIntoSessions", { splitsCardIntoSessions() }),
    ("namesCamerasLikeAPerson", { namesCamerasLikeAPerson() }),
    ("findsSonyXMLAtCardRoot", { try findsSonyXMLAtCardRoot() }),
    ("parsesLsofOutput", { parsesLsofOutput() }),
    ("findsNestedClientsAndShoots", { try findsNestedClientsAndShoots() }),
    ("sortsThumbnailsAndMetadataOutOfTheClipFolder", { sortsThumbnailsAndMetadataOutOfTheClipFolder() }),
    ("speedAndTimeLeft", { speedAndTimeLeft() }),
    ("parsesSonyCameraXML", { parsesSonyCameraXML() }),
    ("plansBackedStyleFolders", { plansBackedStyleFolders() }),
    ("matchesByContentNotName", { try matchesByContentNotName() }),
    ("resumesHalfCopiedShootMovedAroundTheCard", { try resumesHalfCopiedShootMovedAroundTheCard() }),
    ("copiesVerifiesAndNeverOverwrites", { try copiesVerifiesAndNeverOverwrites() }),
    ("sameNamedFilesBothSurviveBackToBack", { try sameNamedFilesBothSurviveBackToBack() }),
    ("handoffWritesSidecarAndInbox", { try handoffWritesSidecarAndInbox() }),
    ("photoOnlyShootDropsEmptyFootageAndProject", { try photoOnlyShootDropsEmptyFootageAndProject() }),
    ("readsEveryBrandsCardLayout", { try readsEveryBrandsCardLayout() }),
] as [(String, () throws -> Void)] {
    print(name)
    do { try test() } catch { failures += 1; print("FAIL  threw \(error)") }
}
print(failures == 0 ? "All checks passed." : "\(failures) check(s) failed.")
exit(failures == 0 ? 0 : 1)
