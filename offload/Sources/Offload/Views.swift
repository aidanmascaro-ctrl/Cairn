import OffloadCore
import SwiftUI

extension View {
    /// Hidden until the page's reveal reaches `index`, then pops in: appears at full
    /// colour and springs up from slightly smaller.
    ///
    /// The appearance is deliberately NOT faded: AppKit-backed controls (dropdowns, text
    /// fields) lose the popover's vibrancy while partly transparent and flash dark grey.
    /// So opacity switches instantly and only the scale animates.
    func reveal(_ index: Int, step: Int) -> some View {
        let shown = step > index
        return opacity(shown ? 1 : 0)
            .animation(nil, value: shown)
            .scaleEffect(shown ? 1 : 0.92, anchor: .center)
            .animation(.spring(response: 0.32, dampingFraction: 0.72), value: shown)
    }
}

enum Format {
    /// "182 MB/s · about 2 min left", or "Measuring speed…" for the first couple of seconds.
    static func speedAndTimeLeft(_ p: CopyProgress) -> String {
        guard let speed = p.bytesPerSecond(), let left = p.secondsRemaining() else { return "Measuring speed…" }
        let rate = ByteCountFormatter.string(fromByteCount: Int64(speed), countStyle: .file)
        return "\(rate)/s · about \(friendly(left)) left"
    }

    /// "2 min", "1 hr 5 min", "40 sec".
    static func friendly(_ seconds: TimeInterval) -> String {
        let f = DateComponentsFormatter()
        f.unitsStyle = .short
        f.maximumUnitCount = 2
        f.allowedUnits = seconds < 60 ? [.second] : [.hour, .minute]
        return f.string(from: max(seconds, 1)) ?? ""
    }

    /// Countdown for the menu bar pill: "2:06:54" or "4:05".
    static func clock(_ seconds: TimeInterval) -> String {
        let f = DateComponentsFormatter()
        f.unitsStyle = .positional
        f.allowedUnits = seconds >= 3600 ? [.hour, .minute, .second] : [.minute, .second]
        f.zeroFormattingBehavior = .pad
        return f.string(from: max(seconds, 0)) ?? ""
    }
}

struct PopoverView: View {
    @ObservedObject var model: AppModel
    /// Observed too, so the popover widens for the Library's Sort page.
    @ObservedObject var library: LibraryModel

    /// Pages swap with a fade; a form page's pieces then fade in one by one (`reveal`).
    static let pageTransition = AnyTransition.opacity

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let sessions = model.sessions {
                SessionsView(model: model, form: sessions) { model.closeForms() }
                    .transition(Self.pageTransition)
            } else if let form = model.newShoot {
                NewShootView(model: model, card: form.card, form: form) { model.closeForms() }
                    .transition(Self.pageTransition)
            } else {
                cardList.transition(.opacity)
            }
        }
        .padding(14)
        .frame(width: width)
        .fixedSize(horizontal: false, vertical: true)
        .background(GeometryReader { Color.clear.preference(key: PopoverHeightKey.self, value: $0.size.height) })
        .onPreferenceChange(PopoverHeightKey.self) { height in
            // First measurement sets the size outright; later ones glide.
            if model.popoverHeight == 0 {
                model.popoverHeight = height
            } else {
                withAnimation(TabSwitcher.glide) { model.popoverHeight = height }
            }
        }
        .modifier(AnimatedSize(width: width, height: model.popoverHeight))
    }

    /// Cards and Library share one width so switching tabs never jumps sideways.
    private var width: CGFloat {
        let wide = model.sessions != nil || (model.tab == .library && model.newShoot == nil && library.sorting != nil)
        return wide ? 600 : 420
    }

    private var cardList: some View {
        VStack(alignment: .leading, spacing: 12) {
            TabSwitcher(tab: $model.tab) { if $0 == .library { model.library.showList() } }
            // Both pages stay laid out on top of each other, so switching is a pure
            // cross-fade and nothing is rebuilt; the popover height eases to the new page.
            ZStack(alignment: .topLeading) {
                cardsPage.page(.cards, shown: model.tab)
                LibraryTab(model: model, library: model.library).page(.library, shown: model.tab)
            }
            // Only as tall as the chosen page; the other one is faded out and clipped.
            .frame(height: model.pageHeights[model.tab], alignment: .topLeading)
            .onPreferenceChange(PageHeightKey.self) { model.pageHeights = $0 }
            .animation(TabSwitcher.glide, value: model.tab)
            .onChange(of: model.tab) { NSApp.keyWindow?.makeFirstResponder(nil) }  // no stray focus ring
        }
    }

    private var cardsPage: some View {
        VStack(alignment: .leading, spacing: 12) {
            if model.cards.isEmpty {
                Text("Plug in a card.").foregroundStyle(.secondary)
            }
            ForEach(model.cards) { card in
                CardRow(model: model, card: card) { model.openNewShoot(card) }
                Divider()
            }
            footer
        }
    }

    private var footer: some View {
        HStack {
            if let root = model.root {
                Button { model.reveal(root) } label: {
                    Label(model.driveName, systemImage: "externaldrive")
                }
                .help("Show the working folder in Finder")
                Button(action: model.togglePin) {
                    Image(systemName: model.isRootPinned ? "pin.fill" : "pin")
                }
                .foregroundStyle(model.isRootPinned ? Color.accentColor : Color.secondary)
                .help(model.isRootPinned
                      ? "Pinned: used whenever this drive is plugged in. Click to unpin."
                      : "Pin this folder: it's used whenever its drive is plugged in.")
            } else {
                Label("No working folder", systemImage: "externaldrive")
            }
            Spacer()
            Button(model.rootPath == nil ? "Choose working folder…" : "Change") { model.chooseRoot() }
            Button("Quit") { NSApp.terminate(nil) }
        }
        .font(.caption)
        .buttonStyle(.borderless)
    }
}

struct CardRow: View {
    @ObservedObject var model: AppModel
    @ObservedObject var card: CardState
    let onNewShoot: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button { model.reveal(card.volume) } label: {
                    Label(card.name, systemImage: card.isPhone ? "iphone" : "sdcard").font(.headline)
                }
                .buttonStyle(.plain)
                .disabled(card.isPhone)
                .help("Show \(card.name) in Finder")
                if card.camera != nil {
                    Text("· \(model.cameraName(for: card.camera))")
                        .font(.headline).foregroundStyle(.secondary)
                }
                Spacer()
                ejectControl
            }
            status
        }
    }

    /// Files of a type Offload doesn't know aren't copied — say so, so nothing is lost on format.
    /// "2 of 3 shoots aren't fully on Lexar 4TB yet." — or "This shoot isn't…" for one.
    private var shootsLeft: String {
        let (left, total) = (card.sessionsLeft, card.sessionCount)
        if total <= 1 { return "This shoot isn't fully on \(model.driveName) yet." }
        return "\(left) of \(total) shoots \(left == 1 ? "isn't" : "aren't") fully on \(model.driveName) yet."
    }

    @ViewBuilder private var unknownNote: some View {
        if !card.unknown.isEmpty {
            let list = card.unknown.sorted { $0.value > $1.value }.map { "\($0.value) \($0.key)" }.joined(separator: ", ")
            Label(card.isPhone
                  ? "Not copied: \(list) (screenshots and the like)."
                  : "Not copied — Offload doesn't know these yet: \(list). Check them before formatting.",
                  systemImage: "exclamationmark.triangle")
                .font(.caption).foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var status: some View {
        switch card.phase {
        case .scanning:
            ProgressView().controlSize(.small)
            Text(card.isPhone && !PhoneSource.isReady(card.volume)
                 ? "Unlock the iPhone (tap Trust if it asks)…"
                 : "Checking what's on \(model.driveName)…").font(.callout).foregroundStyle(.secondary)
        case .ready where card.files.isEmpty:
            Text(card.isPhone ? "No photos or videos found on the iPhone." : "No footage or photos found on this card.")
                .font(.callout).foregroundStyle(.secondary)
            unknownNote
        case .ready where card.missing.isEmpty:
            Text(card.unknown.isEmpty
                 ? "All \(card.files.count) files are on \(model.driveName). Safe to \(card.isPhone ? "delete from the iPhone" : "format")."
                 : "All \(card.files.count) files Offload knows are on \(model.driveName).").font(.callout)
            unknownNote
            if let error = card.ejectError {
                Text(error).font(.caption).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !card.blockers.isEmpty { blockerList }
            handoffLink
        case .ready:
            Text(shootsLeft + (card.isPhone ? " Tick the sessions you want." : " Don't format."))
                .font(.callout)
                .help("\(card.missing.count) of \(card.files.count) files aren't on \(model.driveName) yet")
            unknownNote
            HStack {
                Spacer()
                Button("Folder", action: chooseFolder)
                    .help("Copy everything on the card into a folder you pick")
                if card.sessionCount > 1 || card.isPhone {
                    Button("New shoot", action: onNewShoot)
                    Button { model.openSessions(card) } label: {
                        if model.preparing == card.volume {
                            ProgressView().controlSize(.small).padding(.horizontal, 30)
                        } else {
                            Text("Sort sessions")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    Button("New shoot", action: onNewShoot).buttonStyle(.borderedProminent)
                }
            }
            .fixedSize()
            .frame(maxWidth: .infinity, alignment: .trailing)
            .disabled(model.rootPath == nil)
        case .copying(let p):
            Text("Copying \(min(p.filesDone + 1, p.filesTotal)) of \(p.filesTotal) · \(gb(p.bytesDone)) of \(gb(p.bytesTotal))")
                .font(.callout)
            ProgressView(value: p.fraction)
            Text(Format.speedAndTimeLeft(p))
                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            HStack {
                Text("Each file is checked against the card. Safe to close this; it carries on.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Stop") { model.stop(card) }
            }
        case .failed(let message):
            Text(message).font(.callout).foregroundStyle(.red)
            Button("Check again") { model.scan(card) }
        }
    }

    /// Who is holding the card open, with one button to quit them and eject.
    private var blockerList: some View {
        let quittable = card.blockers.filter(\.canQuit)
        return VStack(alignment: .leading, spacing: 6) {
            Text("Can't eject yet — still in use by:").font(.caption).foregroundStyle(.secondary)
            ForEach(card.blockers) { blocker in
                HStack(spacing: 6) {
                    if let icon = blocker.app?.icon {
                        Image(nsImage: icon).resizable().frame(width: 16, height: 16)
                    } else {
                        Image(systemName: "gearshape").frame(width: 16, height: 16).foregroundStyle(.secondary)
                    }
                    Text(blocker.name).font(.callout)
                    if let hint = blocker.hint {
                        Text(hint).font(.caption2).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            HStack {
                Spacer()
                Button("Try again") { model.eject(card) }
                if !quittable.isEmpty {
                    Button("Quit \(quittable.map(\.name).joined(separator: ", ")) & Eject") {
                        model.quitBlockersAndEject(card)
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
            .disabled(card.ejecting)
        }
    }

    /// Small eject icon, top right — hidden only while a scan or copy is reading the card.
    @ViewBuilder private var ejectControl: some View {
        if card.canEject {
            if card.ejecting {
                ProgressView().controlSize(.small)
            } else {
                Button { model.eject(card) } label: {
                    Image(systemName: "eject.fill")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
                .help(card.isPhone ? "Disconnect \(card.name)" : "Eject \(card.name)")
                .accessibilityLabel(card.isPhone ? "Disconnect \(card.name)" : "Eject \(card.name)")
            }
        }
    }

    @ViewBuilder private var handoffLink: some View {
        if let shoot = card.lastShoot {
            HStack {
                Text("Sent to Cairn for sorting.").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([shoot]) }
                    .buttonStyle(.borderless).font(.caption)
            }
        }
    }

    private func chooseFolder() {
        guard let folder = model.pickFolder(prompt: "Copy here") else { return }
        let name = model.cameraName(for: card.camera)
        model.copy(card, plan: ShootPlan(existingFolder: folder), client: "", shootName: folder.lastPathComponent,
                   date: card.shootDate, camera: name)
    }

    private func gb(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}

struct NewShootView: View {
    /// Pieces that fade in one by one when the page opens.
    static let pieces = 5

    @ObservedObject var model: AppModel
    @ObservedObject var card: CardState
    @ObservedObject var form: NewShootForm
    let onDone: () -> Void

    private var library: ShootLibrary? { form.library }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(form.shoot == nil ? "New shoot" : "Add to shoot").font(.headline)
                .reveal(0, step: model.revealStep)
            ShootPickers(form: form).reveal(1, step: model.revealStep)
            cameraRow.reveal(2, step: model.revealStep)
            if let plan = form.plan { destinationSummary(plan).reveal(3, step: model.revealStep) }
            HStack {
                Spacer()
                Button("Cancel", action: onDone)
                Button(form.shoot == nil ? "Create" : "Copy here", action: create)
                    .buttonStyle(.borderedProminent)
                    .disabled(form.plan == nil)
                    .keyboardShortcut(.defaultAction)
            }
            .reveal(4, step: model.revealStep)
        }
    }

    // MARK: Camera + summary

    private var cameraRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Camera on this card").font(.caption)
            HStack {
                Text(card.camera.map { "\($0.model) · serial …\(String(($0.serial ?? "").suffix(4)))" } ?? "Unknown camera")
                    .font(.caption).foregroundStyle(.secondary)
                Image(systemName: "arrow.right").font(.caption)
                TextField("Folder name", text: $form.camera)
            }
        }
    }

    private func destinationSummary(_ plan: ShootPlan) -> some View {
        let existing = form.shoot?.cameras[ShootPlan.clean(form.camera)]
        let note = existing.map { "Joins Footage/\(form.camera) — \($0) clips already there; anything already backed up is skipped." }
            ?? "\(card.missing.count) files go in Footage/\(form.camera)"
        return VStack(alignment: .leading, spacing: 2) {
            Text(library?.label(for: plan.shootFolder) ?? plan.shootFolder.path)
            Text(note).foregroundStyle(.secondary)
        }
        .font(.caption.monospaced())
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
    }

    private func create() {
        guard let plan = form.plan else { return }
        let clientName = form.clientFolder?.lastPathComponent ?? ""
        model.rememberCameraName(form.camera, for: card.camera)
        UserDefaults.standard.set(plan.shootFolder.path, forKey: NewShootForm.lastShootKey)
        model.copy(card, plan: plan, client: clientName, shootName: form.manifestShootName,
                   date: form.manifestDate, camera: form.camera)
        onDone()
    }
}

/// Client + Shoot dropdowns, each with a "New…" option. Shared by New shoot and each
/// session in Sort sessions.
struct ShootPickers: View {
    @ObservedObject var form: NewShootForm
    /// Session rows: dropdowns sized to their text, side by side, no captions or hints.
    var compact = false

    private var library: ShootLibrary? { form.library }

    var body: some View {
        if compact { compactBody } else { fullBody }
    }

    // MARK: Layouts

    private var fullBody: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Client").font(.caption)
                clientMenu
                if form.client == nil {
                    TextField("Client name", text: $form.newClientName)
                    if let library {
                        Text("Goes in \(library.label(for: library.workingFolder))")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Shoot").font(.caption)
                shootMenu
                if form.shoot == nil {
                    newShootFields
                    Text("Date taken from the card").font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var compactBody: some View {
        VStack(alignment: .leading, spacing: 6) {
            // Side by side when they fit; else stacked; else squeezed (long names truncate) —
            // never wider than the row, or the whole Sort sessions page overflows the popover.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 4) {
                    clientMenu.fixedSize()
                    Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
                    shootMenu.fixedSize()
                }
                VStack(alignment: .leading, spacing: 4) {
                    clientMenu.fixedSize()
                    shootMenu.fixedSize()
                }
                VStack(alignment: .leading, spacing: 4) {
                    clientMenu
                    shootMenu
                }
            }
            if form.client == nil {
                TextField("New client name", text: $form.newClientName).frame(maxWidth: Self.fieldWidth)
            }
            if form.shoot == nil { newShootFields }
        }
    }

    private static let fieldWidth: CGFloat = 240

    // MARK: Pieces

    private var clientMenu: some View {
        Picker("Client", selection: $form.client) {
            ForEach(library?.clients ?? [], id: \.self) { url in
                Text(compact ? (library?.shortLabel(for: url) ?? url.lastPathComponent)
                             : (library?.label(for: url) ?? url.lastPathComponent))
                    .tag(Optional(url))
            }
            if library?.clients.isEmpty == false { Divider() }
            Text("New client…").tag(URL?.none)
        }
        .labelsHidden()
    }

    private var shootMenu: some View {
        Picker("Shoot", selection: $form.shoot) {
            ForEach(form.clientShoots, id: \.self) { shoot in
                Text(compact ? shoot.folderName : shootLabel(shoot)).tag(Optional(shoot))
            }
            if !form.clientShoots.isEmpty { Divider() }
            Text("New shoot…").tag(ExistingShoot?.none)
        }
        .labelsHidden()
    }

    private var newShootFields: some View {
        HStack {
            TextField("Shoot name", text: $form.shootName)
                .frame(maxWidth: compact ? Self.fieldWidth : .infinity)
            DatePicker("", selection: $form.date, displayedComponents: .date).labelsHidden().fixedSize()
        }
    }

    private func shootLabel(_ shoot: ExistingShoot) -> String {
        let clips = shoot.clipCount == 0 ? "no footage yet" : "\(shoot.clipCount) clips"
        let cams = shoot.cameras.keys.sorted().joined(separator: ", ")
        return cams.isEmpty ? "\(shoot.folderName) · \(clips)" : "\(shoot.folderName) · \(clips) · \(cams)"
    }
}

/// Cards / Library switch whose highlight glides to the chosen tab (Liquid Glass on
/// macOS 26+, a plain fill before that).
struct TabSwitcher: View {
    @Binding var tab: PopoverTab
    /// A tab clicked (also when it's already the chosen one).
    var onSelect: (PopoverTab) -> Void = { _ in }
    @Namespace private var highlight

    static let glide = Animation.easeInOut(duration: 0.3)
    private static let tabs: [(PopoverTab, String)] = [(.cards, "Cards"), (.library, "Library")]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Self.tabs, id: \.0) { value, title in
                Button {
                    withAnimation(Self.glide) { tab = value }
                    onSelect(value)
                } label: {
                    Text(title)
                        .font(.callout.weight(.medium))
                        .foregroundStyle(tab == value ? .primary : .secondary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 5)
                        .background {
                            if tab == value {
                                Selection().matchedGeometryEffect(id: "tab", in: highlight)
                            }
                        }
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(3)
        .background(.quaternary.opacity(0.5), in: Capsule())
    }

    private struct Selection: View {
        var body: some View {
            if #available(macOS 26, *) {
                Capsule().fill(.clear).glassEffect(.regular, in: Capsule())
            } else {
                Capsule().fill(.quaternary)
            }
        }
    }
}

private extension View {
    /// One tab's page inside the shared ZStack: measures its own height, and is faded
    /// out, unclickable and hidden from VoiceOver when it isn't the chosen tab. A hidden
    /// page is also disabled, so a focused field there (Library's search) lets go of focus:
    /// macOS draws the blue focus ring outside SwiftUI's fade, and it would stay on screen.
    func page(_ tab: PopoverTab, shown: PopoverTab) -> some View {
        fixedSize(horizontal: false, vertical: true)
            .background(GeometryReader { Color.clear.preference(key: PageHeightKey.self, value: [tab: $0.size.height]) })
            .focusEffectDisabled(tab != shown)
            .disabled(tab != shown)
            .opacity(tab == shown ? 1 : 0)
            .allowsHitTesting(tab == shown)
            .accessibilityHidden(tab != shown)
    }
}

private struct PageHeightKey: PreferenceKey {
    static let defaultValue: [PopoverTab: CGFloat] = [:]
    static func reduce(value: inout [PopoverTab: CGFloat], nextValue: () -> [PopoverTab: CGFloat]) {
        value.merge(nextValue()) { $1 }
    }
}

private struct PopoverHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

/// Sizes the popover's content frame by frame during an animation, so the popover
/// window (which follows the content's size) grows and shrinks smoothly instead of
/// jumping to the end size while the content catches up.
private struct AnimatedSize: ViewModifier, Animatable {
    var width: CGFloat
    var height: CGFloat

    var animatableData: AnimatablePair<CGFloat, CGFloat> {
        get { AnimatablePair(width, height) }
        set { width = newValue.first; height = newValue.second }
    }

    func body(content: Content) -> some View {
        content.frame(width: width, height: height > 0 ? height : nil, alignment: .top).clipped()
    }
}
