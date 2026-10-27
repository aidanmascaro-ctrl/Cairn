import OffloadCore
import SwiftUI

/// Library tab: search every shoot on every drive, plugged in or not.
struct LibraryView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var library: LibraryModel

    static var listHeight: CGFloat {
        min(420, (NSScreen.main?.visibleFrame.height ?? 900) - 300)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if library.catalog.hasPending { pendingBanner }
            TextField("Search client, shoot, camera, year", text: $library.query)
                .textFieldStyle(.roundedBorder)
            results
            Divider()
            drives
            Divider()
            footer
        }
        // Clicking empty space ends editing a drive note.
        .background(Color.clear.contentShape(Rectangle()).onTapGesture {
            library.closeNote()
            NSApp.keyWindow?.makeFirstResponder(nil)
        })
    }

    // MARK: Pieces

    private var pendingBanner: some View {
        let clients = library.catalog.pendingClients
        let shoots = library.catalog.pendingShoots
        let drives = clients.map(\.driveID) + shoots.map(\.driveID)
        let driveNames = Set(drives.map(library.driveName)).sorted().joined(separator: ", ")
        let found = clients.isEmpty
            ? "\(shoots.count) shoot folder\(shoots.count == 1 ? "" : "s")"
            : "\(clients.count) folder\(clients.count == 1 ? "" : "s") that might be clients"
        return HStack {
            Image(systemName: "sparkle.magnifyingglass").foregroundStyle(.secondary)
            Text("Found \(found) on \(driveNames).")
                .font(.caption).fixedSize(horizontal: false, vertical: true)
            Spacer()
            Button("Review") { withAnimation(AppModel.pageAnimation) { library.reviewing = true } }
                .buttonStyle(.borderedProminent).controlSize(.small)
        }
        .padding(8)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
    }

    @ViewBuilder private var results: some View {
        let shoots = library.results
        if library.catalog.shoots.isEmpty {
            Text(library.scanning.isEmpty ? "No shoots yet. Plug in a drive and Offload finds the shoots on it."
                                          : "Looking for shoots…")
                .font(.callout).foregroundStyle(.secondary)
        } else if shoots.isEmpty {
            Text("No shoots match “\(library.query)”.").font(.callout).foregroundStyle(.secondary)
        } else {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(shoots) { ShootRow(model: model, library: library, shoot: $0, isLast: $0.id == shoots.last?.id) }
                    }
                    .padding(.horizontal, Self.rowInset)
                    .background(GeometryReader { geo in
                        Color.clear.preference(key: LibraryHeightKey.self, value: geo.size.height)
                    })
                }
                .onPreferenceChange(LibraryHeightKey.self) { library.listContentHeight = $0 }
                .frame(height: min(library.listContentHeight, Self.listHeight))
                // Rows stay lined up with the search field; the "last shoot" tint gets room either side.
                .padding(.horizontal, -Self.rowInset)
                .onAppear { scrollToLastShoot(proxy) }
                .onChange(of: shoots.map(\.id)) { scrollToLastShoot(proxy) }
            }
        }
    }

    static let rowInset: CGFloat = 6

    /// Coming back from a sort or rename: bring that shoot into view. A renamed shoot only
    /// shows up after its rescan, so this waits for the row rather than giving up.
    private func scrollToLastShoot(_ proxy: ScrollViewProxy) {
        guard library.scrollToLastShoot, let id = library.lastShootID,
              library.results.contains(where: { $0.id == id }) else { return }
        library.scrollToLastShoot = false
        // The lazy list lays its rows out after appearing; scroll on the next pass.
        DispatchQueue.main.async { proxy.scrollTo(id, anchor: .center) }
    }

    private var drives: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Drives").font(.caption).foregroundStyle(.secondary)
                Spacer()
                if !library.scanning.isEmpty {
                    ProgressView().controlSize(.mini)
                    Text("Scanning \(library.scanning.map(library.driveName).sorted().joined(separator: ", "))…")
                        .font(.caption).foregroundStyle(.secondary)
                } else if !library.mounted.isEmpty {
                    Button("Scan again") { library.rescanAll() }
                        .buttonStyle(.borderless).font(.caption)
                        .help("Look for new shoots on the plugged-in drives")
                }
            }
            ForEach(library.catalog.drives) { drive in
                DriveRow(library: library, drive: drive)
            }
        }
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Image(systemName: "doc.text").foregroundStyle(.secondary)
                if let path = library.notePath {
                    Button((path as NSString).lastPathComponent) {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
                    }
                    .help(path)
                    Spacer()
                    Button("Change") { library.chooseNoteLocation() }
                } else {
                    Button("Keep a copy in Obsidian…") { library.chooseNoteLocation() }
                    Spacer()
                }
            }
            .buttonStyle(.borderless)
            if let error = library.noteError {
                Text(error).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
        }
        .font(.caption)
    }
}

private struct ShootRow: View {
    @ObservedObject var model: AppModel
    @ObservedObject var library: LibraryModel
    let shoot: CatalogShoot
    /// The list's own divider follows the last row, so it draws none.
    var isLast = false

    var body: some View {
        let online = library.isOnline(shoot)
        HStack(alignment: .top) {
            Button {
                if let folder = library.folder(of: shoot) { model.reveal(folder) }
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(shoot.name.isEmpty ? (shoot.path as NSString).lastPathComponent : shoot.name)
                            .font(.callout.weight(.medium)).lineLimit(1)
                        Text(shoot.client).font(.caption2)
                            .padding(.horizontal, 6).padding(.vertical, 1)
                            .background(.quaternary, in: Capsule())
                            .lineLimit(1)
                    }
                    Text(details).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!online)
            .help(online ? "Show in Finder" : "Plug in \(library.driveName(shoot.driveID)) to open")
            VStack(alignment: .trailing, spacing: 3) {
                HStack(spacing: 4) {
                    Circle().fill(online ? Color.green : Color.secondary.opacity(0.5)).frame(width: 7, height: 7)
                    Text(library.driveName(shoot.driveID)).font(.caption).lineLimit(1)
                    actions(online: online)
                }
                if online, let loose = shoot.unsorted, loose > 0 {
                    Button("Sort \(loose) file\(loose == 1 ? "" : "s")") { library.startSort(shoot) }
                        .buttonStyle(.bordered).controlSize(.mini)
                        .help("Move loose footage into Footage/<Camera> and photos into Photos")
                } else if !online {
                    Text("Plug in to open").font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 6)
        .background {
            if library.lastShootID == shoot.id {
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.accentColor.opacity(0.12))
                    .padding(.horizontal, -LibraryView.rowInset)
            }
        }
        if !isLast { Divider() }
    }

    private func actions(online: Bool) -> some View {
        Menu {
            Button("Rename…") { library.startRename(shoot) }
            Button("Sort footage…") { library.startSort(shoot) }
            Divider()
            Button("Show in Finder") { if let folder = library.folder(of: shoot) { model.reveal(folder) } }
        } label: {
            Image(systemName: "ellipsis.circle")
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .disabled(!online)
        .help(online ? "Rename or sort this shoot" : "Plug in the drive to rename or sort")
    }

    private var details: String {
        var parts = [shoot.date.flatMap(Catalog.shortDate) ?? "No date"]
        if !shoot.cameras.isEmpty { parts.append(shoot.cameras.joined(separator: ", ")) }
        if shoot.clips > 0 { parts.append("\(shoot.clips) clips") }
        parts.append(Catalog.size(shoot.bytes))
        return parts.joined(separator: " · ")
    }
}

private struct DriveRow: View {
    @ObservedObject var library: LibraryModel
    let drive: CatalogDrive
    @FocusState private var focused: Bool
    private var editing: Bool { library.editingNote == drive.id }
    @State private var hovering = false

    var body: some View {
        let online = library.mounted[drive.id] != nil
        HStack(spacing: 6) {
            Circle().fill(online ? Color.green : Color.secondary.opacity(0.5)).frame(width: 7, height: 7)
            VStack(alignment: .leading, spacing: 0) {
                Text(drive.name).font(.callout).lineLimit(1)
                Text(status(online: online)).font(.caption2).foregroundStyle(.secondary)
            }
            Spacer()
            note.frame(width: 150, alignment: .trailing)
        }
    }

    /// Plain text in the row; becomes a field when clicked, back to text on Return or click-away.
    @ViewBuilder private var note: some View {
        if editing {
            TextField("Where it lives", text: Binding(
                get: { drive.note },
                set: { library.setNote($0, forDrive: drive.id) }
            ))
            .textFieldStyle(.roundedBorder)
            .font(.caption)
            .focused($focused)
            .onAppear { focused = true }
            .onSubmit { library.closeNote() }
            .onChange(of: focused) { if !focused, editing { library.closeNote() } }
            .transition(.opacity)
        } else {
            Text(drive.note.isEmpty ? "Where it lives" : drive.note)
                .font(.caption)
                .foregroundStyle(drive.note.isEmpty ? .tertiary : .secondary)
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .background(RoundedRectangle(cornerRadius: 5).fill(hovering ? Color.primary.opacity(0.06) : .clear))
                .contentShape(Rectangle())
                .onHover { hovering = $0 }
                .onTapGesture { withAnimation(LibraryModel.noteFade) { library.editingNote = drive.id } }
                .transition(.opacity)
                .help("Where this drive lives — click to edit")
        }
    }

    private func status(online: Bool) -> String {
        let count = library.catalog.shoots.filter { $0.driveID == drive.id }.count
        let shoots = "\(count) shoot\(count == 1 ? "" : "s")"
        if online { return "\(shoots) · \(drive.free.map(Catalog.size) ?? "?") free" }
        return "\(shoots) · seen \(drive.lastSeen.formatted(date: .abbreviated, time: .omitted))"
    }
}

/// Review what a drive scan found, in two steps: which top folders are clients, then
/// which folders inside those clients are shoots. Suggested ticks come pre-set; anything
/// left unticked is never suggested again.
struct LibraryReviewView: View {
    @ObservedObject var library: LibraryModel

    var body: some View {
        let clients = library.catalog.pendingClients
        if !clients.isEmpty {
            step(title: "Which of these are clients?",
                 hint: "Ticked folders become clients, and their shoots come up next. Ones with dated shoot folders are ticked for you.",
                 items: clients.map { client in
                     Item(id: client.id, suggested: client.looksLikeClient, title: client.name,
                          detail: clientDetail(client), help: client.path)
                 },
                 done: { n in n == 0 ? "None are clients" : "Next: \(n) client\(n == 1 ? "" : "s")" },
                 finish: library.finishClients)
        } else {
            let shoots = library.catalog.pendingShoots
            step(title: "Which of these are shoots?",
                 hint: "Folders with a date in the name are ticked for you. Untick things like brand kits or loose camera folders.",
                 items: shoots.map { shoot in
                     Item(id: shoot.id, suggested: shoot.looksLikeShoot, title: "\(shoot.client) › \(shoot.name)",
                          detail: shootDetail(shoot), help: shoot.path)
                 },
                 done: { n in n == 0 ? "None are shoots" : "Add \(n) to catalog" },
                 finish: library.finishShoots)
        }
    }

    struct Item: Identifiable {
        let id: String
        let suggested: Bool
        let title: String
        let detail: String
        let help: String
    }

    private func step(title: String, hint: String, items: [Item], done: (Int) -> String,
                      finish: @escaping () -> Void) -> some View {
        let ticked = items.filter { $0.suggested != library.flipped.contains($0.id) }.count
        return VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            Text(hint).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Tick all") { items.forEach { library.setTicked(true, id: $0.id, suggested: $0.suggested) } }
                Button("Untick all") { items.forEach { library.setTicked(false, id: $0.id, suggested: $0.suggested) } }
            }
            .buttonStyle(.borderless).font(.caption)
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(items) { item in
                        Toggle(isOn: Binding(
                            get: { item.suggested != library.flipped.contains(item.id) },
                            set: { library.setTicked($0, id: item.id, suggested: item.suggested) }
                        )) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text(item.title).font(.callout).lineLimit(1)
                                Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                        }
                        .toggleStyle(.checkbox)
                        .padding(.vertical, 4)
                        .help(item.help)
                    }
                }
                .padding(.trailing, 6)
                .background(GeometryReader { geo in
                    Color.clear.preference(key: LibraryHeightKey.self, value: geo.size.height)
                })
            }
            .onPreferenceChange(LibraryHeightKey.self) { library.reviewContentHeight = $0 }
            .frame(height: min(library.reviewContentHeight, LibraryView.listHeight))
            HStack {
                Spacer()
                Button("Later") { withAnimation(TabSwitcher.glide) { library.reviewing = false } }
                Button(done(ticked)) { withAnimation(TabSwitcher.glide) { finish() } }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
    }

    private func clientDetail(_ client: CatalogClient) -> String {
        let folders = "\(client.folders) folder\(client.folders == 1 ? "" : "s")"
        let dated = client.datedShoots > 0 ? " · \(client.datedShoots) dated" : ""
        return "\(library.driveName(client.driveID)) · \(folders)\(dated)"
    }

    private func shootDetail(_ shoot: CatalogShoot) -> String {
        var parts = [shoot.date.flatMap(Catalog.shortDate) ?? "No date in name", library.driveName(shoot.driveID)]
        if shoot.clips > 0 { parts.append("\(shoot.clips) clips") }
        parts.append(Catalog.size(shoot.bytes))
        return parts.joined(separator: " · ")
    }
}

private struct LibraryHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

/// Library tab body: the search page, or the review page while confirming found shoots.
struct LibraryTab: View {
    @ObservedObject var model: AppModel
    @ObservedObject var library: LibraryModel

    var body: some View {
        if let form = library.renaming {
            RenamePage(library: library, form: form).transition(.opacity)
        } else if let form = library.sorting {
            SortPage(model: model, library: library, form: form).transition(.opacity)
        } else if library.reviewing {
            LibraryReviewView(library: library).transition(.opacity)
        } else {
            LibraryView(model: model, library: library).transition(.opacity)
        }
    }
}

/// "Rename shoot": name + date become the folder name, shown before anything changes.
struct RenamePage: View {
    @ObservedObject var library: LibraryModel
    @ObservedObject var form: RenameForm

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Rename shoot").font(.headline)
            TextField("Shoot name", text: $form.name).textFieldStyle(.roundedBorder)
            HStack {
                Toggle("Date", isOn: $form.hasDate).toggleStyle(.checkbox)
                DatePicker("", selection: $form.date, displayedComponents: .date)
                    .labelsHidden().fixedSize().disabled(!form.hasDate)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("\(form.shoot.client) › \(form.folder.lastPathComponent)").foregroundStyle(.secondary)
                Text("\(form.shoot.client) › \(form.newFolderName)")
            }
            .font(.caption.monospaced())
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
            Text("Only the folder name changes. Offload and Cairn's records follow it.")
                .font(.caption).foregroundStyle(.secondary)
            if let error = form.error {
                Text(error).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Spacer()
                Button("Cancel") { library.closePages() }
                Button("Rename") { form.rename() }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(form.unchanged)
            }
        }
    }
}

/// "Sort footage": a Finder-style tree of where everything will go. Each file type from
/// each folder is a chip you can drag to another folder; camera folders can be renamed
/// in place, added and removed. Grey folders are already there, blue ones are new.
/// Clicking a folder that's already there shows it in Finder.
struct SortPage: View {
    /// Only for showing folders in Finder; not observed, so card progress doesn't redraw the tree.
    let model: AppModel
    @ObservedObject var library: LibraryModel
    @ObservedObject var form: SortForm

    private static let indent: CGFloat = 18

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Sort “\(form.shoot.name)”").font(.headline)
                Text("Moves files inside \(library.driveName(form.shoot.driveID)) › \(form.shoot.client) › \(form.folder.lastPathComponent)")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
            }
            switch form.stage {
            case .planning:
                status("Working out which camera shot each clip…")
                footer(primary: nil)
            case .ready:
                if let plan = form.plan {
                    if let notice = form.notice {
                        Label(notice, systemImage: "checkmark.circle.fill")
                            .font(.callout)
                            .foregroundStyle(.green)
                            .transition(.opacity)
                    }
                    if !plan.typeCounts.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("In this shoot").font(.subheadline.weight(.medium))
                            FlowLayout(spacing: 6) {
                                ForEach(plan.typeCounts, id: \.type) { item in
                                    HStack(spacing: 4) {
                                        Text(item.type).font(.caption.weight(.semibold).monospaced())
                                        Text("\(item.count)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                                    }
                                    .padding(.horizontal, 7).padding(.vertical, 2)
                                    .overlay(Capsule().strokeBorder(.quaternary))
                                }
                            }
                        }
                    }
                    Text("Folders").font(.subheadline.weight(.medium))
                    tree(plan)
                    if let duplicate = plan.duplicateName {
                        Text("Two folders are called “\(duplicate)”. Rename one.").font(.caption).foregroundStyle(.red)
                    } else {
                        Text(plan.fileCount == 0 && plan.renames.isEmpty
                             ? "Everything is sorted. Rename a folder or drag a file type to reorganise."
                             : "Drag a file type onto a folder to move it. Click a folder's name to rename it.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    footer(primary: plan.hasChanges && plan.duplicateName == nil ? (actionLabel(plan), form.sort) : nil)
                }
            case .sorting:
                status("Moving files…")
            case .done(let message):
                Text(message).font(.callout)
                footer(primary: nil)
            case .failed(let message):
                Text(message).font(.callout).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
                footer(primary: nil)
            }
        }
    }

    private func status(_ text: String) -> some View {
        HStack {
            ProgressView().controlSize(.small)
            Text(text).font(.callout).foregroundStyle(.secondary)
        }
    }

    // MARK: Tree

    private func tree(_ plan: ShootTidy.SortPlan) -> some View {
        let footageExists = FileManager.default.fileExists(atPath: plan.shoot.appendingPathComponent("Footage").path)
        let others = plan.destinations.filter { ($0.role == .photos || $0.role == .project) }
        return ScrollView {
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    folderLine(form.folder.lastPathComponent, level: 0, existing: true, bold: false, folder: form.folder)
                    Spacer()
                    Menu {
                        Button("Camera folder in Footage") { withAnimation(TabSwitcher.glide) { form.addFolder() } }
                        ForEach(others.filter { !plan.shows($0) }) { destination in
                            Button(destination.name) { withAnimation(TabSwitcher.glide) { form.add(destination.role) } }
                        }
                    } label: {
                        Label("New folder", systemImage: "folder.badge.plus")
                    }
                    .menuStyle(.borderlessButton).fixedSize().font(.caption)
                    .help("Add a camera, Photos or Project folder, then drag files into it")
                }
                if !plan.cameras.isEmpty {
                    folderLine("Footage", level: 1, existing: footageExists, bold: false,
                               folder: footageExists ? plan.shoot.appendingPathComponent("Footage") : nil)
                }
                ForEach(plan.cameras) { destinationBlock($0, plan: plan, level: 2) }
                ForEach(others.filter { plan.shows($0) }) {
                    destinationBlock($0, plan: plan, level: 1)
                }
                Divider().padding(.vertical, 4)
                if let stay = plan.destination(.stay) { destinationBlock(stay, plan: plan, level: 0) }
                if let bin = plan.destination(.bin), !bin.bundles.isEmpty { destinationBlock(bin, plan: plan, level: 0) }
            }
            .padding(10)
            .background(GeometryReader { geo in
                Color.clear.preference(key: LibraryHeightKey.self, value: geo.size.height)
            })
        }
        .onPreferenceChange(LibraryHeightKey.self) { library.reviewContentHeight = $0 }
        .frame(height: min(library.reviewContentHeight, LibraryView.listHeight))
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary))
    }

    /// One destination: its folder row (rename/remove/count) and its chips. The whole block
    /// is a drop target.
    private func destinationBlock(_ destination: ShootTidy.Destination, plan: ShootTidy.SortPlan, level: Int) -> some View {
        let targeted = form.dropTarget == destination.id
        let existing = plan.exists(destination)
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                if destination.role == .camera {
                    // The name is a rename field, so only the icon opens Finder here.
                    revealable(onDisk(destination, plan: plan)) { folderIcon(existing: existing) }
                    TextField("Folder name", text: Binding(
                        get: { destination.name },
                        set: { form.rename(destination.id, to: $0) }
                    ))
                    .textFieldStyle(.plain)
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(existing ? Color.primary : Color.accentColor)
                    .fixedSize()
                    tag(existing: existing)
                } else if destination.role == .stay || destination.role == .bin {
                    Image(systemName: destination.role == .bin ? "trash" : "tray").foregroundStyle(.secondary).frame(width: 18)
                    Text(destination.name).font(.callout).foregroundStyle(.secondary)
                        .help(destination.role == .bin
                              ? "Sony card files the camera rebuilds itself. Sort moves them to the drive's Bin; Undo brings them back."
                              : "")
                } else {
                    revealable(onDisk(destination, plan: plan)) {
                        HStack(spacing: 6) {
                            folderIcon(existing: existing)
                            Text(destination.name).font(.callout)
                        }
                    }
                    tag(existing: existing)
                }
                Spacer(minLength: 12)
                Text(countLabel(destination))
                    .font(.caption)
                    .foregroundStyle(destination.incoming > 0 ? Color.accentColor : Color.secondary)
                if destination.emptyOnDisk && destination.bundles.isEmpty {
                    Button { withAnimation(TabSwitcher.glide) { form.deleteEmptyFolder(destination.id) } } label: {
                        Image(systemName: "trash")
                    }
                    .buttonStyle(.borderless).foregroundStyle(.secondary)
                    .help("Delete this empty folder from the drive")
                } else if destination.role == .camera && destination.originalName == nil {
                    Button { withAnimation(TabSwitcher.glide) { form.remove(destination.id) } } label: {
                        Image(systemName: "minus.circle")
                    }
                    .buttonStyle(.borderless).foregroundStyle(.secondary)
                    .help("Remove this folder; its files stay where they are")
                }
            }
            if !destination.bundles.isEmpty {
                FlowLayout(spacing: 6) {
                    ForEach(destination.bundles) { chip($0, plan: plan) }
                }
                .padding(.leading, 24)
            }
        }
        .padding(.vertical, 4)
        .padding(.horizontal, 6)
        .padding(.leading, CGFloat(level) * Self.indent)
        .background(targeted ? Color.accentColor.opacity(0.18) : .clear, in: RoundedRectangle(cornerRadius: 6))
        .contentShape(Rectangle())
        .dropDestination(for: String.self) { ids, _ in
            withAnimation(TabSwitcher.glide) { ids.forEach { form.move($0, to: destination.id) } }
            return true
        } isTargeted: { form.setDropTarget(destination.id, $0) }
    }

    private func folderLine(_ name: String, level: Int, existing: Bool, bold: Bool, folder: URL?) -> some View {
        HStack(spacing: 6) {
            revealable(folder) {
                HStack(spacing: 6) {
                    folderIcon(existing: existing)
                    Text(name).font(.callout.weight(bold ? .semibold : .regular)).lineLimit(1)
                }
            }
            tag(existing: existing)
        }
        .padding(.vertical, 4)
        .padding(.horizontal, 6)
        .padding(.leading, CGFloat(level) * Self.indent)
    }

    /// A folder's icon and name, clickable to show it in Finder once it's on the drive.
    @ViewBuilder private func revealable<Label: View>(_ folder: URL?, @ViewBuilder label: () -> Label) -> some View {
        if let folder {
            Button { model.reveal(folder) } label: { label().contentShape(Rectangle()) }
                .buttonStyle(.plain)
                .help("Show in Finder")
        } else {
            label()
        }
    }

    /// Where a folder sits on the drive right now, or nil if it isn't there yet. A camera
    /// folder renamed on this page keeps its old name on disk until the sort runs.
    private func onDisk(_ destination: ShootTidy.Destination, plan: ShootTidy.SortPlan) -> URL? {
        let folder = destination.originalName.map { plan.shoot.appendingPathComponent("Footage").appendingPathComponent($0) }
            ?? plan.folder(for: destination)
        return folder.flatMap { FileManager.default.fileExists(atPath: $0.path) ? $0 : nil }
    }

    private func folderIcon(existing: Bool) -> some View {
        Image(systemName: "folder.fill")
            .foregroundStyle(existing ? Color.secondary : Color.accentColor)
            .frame(width: 18)
    }

    private func tag(existing: Bool) -> some View {
        Text(existing ? "already there" : "new")
            .font(.caption)
            .foregroundStyle(existing ? Color.secondary : Color.accentColor)
    }

    private func countLabel(_ destination: ShootTidy.Destination) -> String {
        let n = destination.fileCount
        let files = { (n: Int) in "\(n) file\(n == 1 ? "" : "s")" }
        if destination.role == .stay { return n == 0 ? "nothing" : files(n) }
        if destination.role == .bin { return n == 0 ? "nothing" : destination.incoming == 0 ? files(n) : "\(files(n)) go in the Bin" }
        if destination.emptyOnDisk && n == 0 { return "empty" }
        if n == 0 { return "drop files here" }
        let incoming = destination.incoming
        if incoming == 0 { return files(n) }
        return incoming == n ? "\(files(n)) go here" : "\(files(n - incoming)) + \(incoming) going here"
    }

    private func actionLabel(_ plan: ShootTidy.SortPlan) -> String {
        let n = plan.fileCount
        if n > 0 { return "Sort \(n) file\(n == 1 ? "" : "s")" }
        let r = plan.renames.count
        return r > 0 ? "Rename \(r) folder\(r == 1 ? "" : "s")" : "Make folders"
    }

    /// "MP4 204 · from CLIP" — drag it to another folder, or right-click to pick one.
    private func chip(_ bundle: ShootTidy.Bundle, plan: ShootTidy.SortPlan) -> some View {
        HStack(spacing: 4) {
            Text(bundle.type).font(.caption.weight(.semibold).monospaced())
            Text("\(bundle.files.count)").font(.caption.monospacedDigit())
            Text(chipCaption(bundle)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(moving(bundle, plan: plan) ? AnyShapeStyle(Color.accentColor.opacity(0.22)) : AnyShapeStyle(.quaternary),
                    in: Capsule())
        .help(chipHelp(bundle))
        .draggable(bundle.id) {
            Text("\(bundle.type) ×\(bundle.files.count)").font(.caption.weight(.semibold))
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(.regularMaterial, in: Capsule())
        }
        .contextMenu {
            Menu("Move to") {
                ForEach(plan.destinations.filter { plan.accepts(bundle, at: $0) }) { destination in
                    Button(destination.role == .camera ? "Footage / \(destination.name)" : destination.name) {
                        withAnimation(TabSwitcher.glide) { form.move(bundle.id, to: destination.id) }
                    }
                }
            }
        }
    }

    /// Moving if it isn't already in the folder it's shown under (and not just staying put).
    private func moving(_ bundle: ShootTidy.Bundle, plan: ShootTidy.SortPlan) -> Bool {
        guard let at = plan.destinations.first(where: { $0.bundles.contains { $0.id == bundle.id } }) else { return false }
        return at.role != .stay && bundle.home != at.id
    }

    /// "clips · from CLIP" for files on the move; "clips" (or "clips · Metadata") once home.
    private func chipCaption(_ bundle: ShootTidy.Bundle) -> String {
        guard !bundle.origin.isEmpty else { return bundle.label }
        if bundle.home == "stay" { return "\(bundle.label) · from \((bundle.origin as NSString).lastPathComponent)" }
        let below = bundle.origin.split(separator: "/").dropFirst(bundle.origin.lowercased().hasPrefix("footage/") ? 2 : 1)
        return below.isEmpty ? bundle.label : "\(bundle.label) · \(below.joined(separator: "/"))"
    }

    private func chipHelp(_ bundle: ShootTidy.Bundle) -> String {
        let names = bundle.files.prefix(3).map(\.lastPathComponent).joined(separator: ", ")
        let more = bundle.files.count > 3 ? " and \(bundle.files.count - 3) more" : ""
        let camera = bundle.camera.map { "Shot on \($0.model). " } ?? ""
        let from = bundle.origin.isEmpty ? "the shoot folder" : bundle.origin
        return "\(camera)\(names)\(more), now in \(from)"
    }

    private func footer(primary: (String, () -> Void)?) -> some View {
        HStack {
            if form.canUndo {
                Button("Undo last sort") { form.undo() }
            }
            Spacer()
            if let primary {
                Button("Cancel") { library.closePages() }
                    .keyboardShortcut(.cancelAction)
                Button(primary.0, action: primary.1)
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            } else {
                // Nothing left to do on this page: Return closes it.
                Button("Done") { library.closePages() }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
    }
}

/// Lays chips out left to right, wrapping onto new lines like text.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        rows(proposal.width ?? .infinity, subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for (index, point) in rows(bounds.width, subviews).origins.enumerated() {
            subviews[index].place(at: CGPoint(x: bounds.minX + point.x, y: bounds.minY + point.y), proposal: .unspecified)
        }
    }

    private func rows(_ width: CGFloat, _ subviews: Subviews) -> (origins: [CGPoint], size: CGSize) {
        var origins: [CGPoint] = []
        var x: CGFloat = 0, y: CGFloat = 0, lineHeight: CGFloat = 0, widest: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width {
                x = 0
                y += lineHeight + spacing
                lineHeight = 0
            }
            origins.append(CGPoint(x: x, y: y))
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
            widest = max(widest, x - spacing)
        }
        return (origins, CGSize(width: widest, height: y + lineHeight))
    }
}
