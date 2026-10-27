import OffloadCore
import SwiftUI

/// "Sort sessions": a card holding several shoots. Each session gets its own client +
/// shoot, then one Copy all sends every session to its shoot.
struct SessionsView: View {
    /// Header, gap note and camera field fade in first, then each session, then the footer.
    static let headerPieces = 3

    @ObservedObject var model: AppModel
    @ObservedObject var form: SessionsForm
    let onDone: () -> Void

    /// Most room the session list may take before it scrolls: never taller than the
    /// screen allows under the menu bar. Shorter lists just take their own height.
    private static let preferredListHeight: CGFloat = 560
    private static let chromeHeight: CGFloat = 260   // header, camera field, footer, menu bar
    private var listHeight: CGFloat {
        let screen = NSScreen.main?.visibleFrame.height ?? 900
        return min(Self.preferredListHeight, screen - Self.chromeHeight)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Sort \(form.card.name)").font(.headline)
                Spacer()
                Picker("Gap", selection: $form.gapHours) {
                    ForEach(SessionsForm.gapChoices, id: \.self) { Text("\(Int($0)) h").tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 170)
                .help("Start a new session when recording stopped for longer than this")
            }
            .reveal(0, step: model.revealStep)
            Text("\(form.drafts.count) sessions — a new one starts after \(Int(form.gapHours)) h without recording.")
                .font(.caption).foregroundStyle(.secondary)
                .reveal(1, step: model.revealStep)
            HStack {
                Text("Camera folder").font(.caption)
                TextField("Folder name", text: $form.camera)
            }
            .reveal(2, step: model.revealStep)
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(Array(form.drafts.enumerated()), id: \.element.id) { index, draft in
                        VStack(alignment: .leading, spacing: 10) {
                            SessionRow(model: model, form: form, draft: draft, shoot: draft.form)
                                .padding(.vertical, 4)
                            if !form.isLast(draft) { Divider() }
                        }
                        .reveal(Self.headerPieces + index, step: model.revealStep)
                    }
                }
                .padding(.trailing, 6)
                .background(GeometryReader { geo in
                    Color.clear.preference(key: ListHeightKey.self, value: geo.size.height)
                })
            }
            .onPreferenceChange(ListHeightKey.self) { form.listContentHeight = $0 }
            .frame(height: min(form.listContentHeight, listHeight))
            Group {
            if let short = shortfall {
                Text("Won't fit: needs \(gb(neededBytes)) but only \(gb(availableBytes ?? 0)) is free on \(model.driveName) "
                     + "(keeping 2 GB spare). Untick sessions to free up \(gb(short)), or clear space on the drive.")
                    .font(.caption).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Text(summary).font(.caption).foregroundStyle(shortfall == nil ? Color.secondary : Color.red)
                Spacer()
                Button("Cancel", action: onDone)
                Button("Copy all", action: copyAll)
                    .buttonStyle(.borderedProminent)
                    .disabled(!form.canCopy || shortfall != nil)
                    .keyboardShortcut(.defaultAction)
            }
            }
            .reveal(Self.headerPieces + form.drafts.count, step: model.revealStep)
        }
    }

    private var neededBytes: Int64 { form.jobs.reduce(Int64(0)) { $0 + $1.files.reduce(0) { $0 + $1.size } } }
    private var availableBytes: Int64? { model.root.flatMap(DriveSpace.available) }

    /// nil = fits (or free space unknown — the copy itself re-checks).
    private var shortfall: Int64? {
        guard let free = availableBytes else { return nil }
        return DriveSpace.shortfall(needed: neededBytes, available: free)
    }

    private var summary: String {
        let free = availableBytes.map { " of \(gb($0)) free" } ?? ""
        return "\(form.jobs.count) of \(form.drafts.count) sessions · \(gb(neededBytes))\(free)"
    }

    private func gb(_ bytes: Int64) -> String { ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file) }

    private func copyAll() {
        let jobs = form.jobs
        guard !jobs.isEmpty else { return }
        model.rememberCameraName(form.camera, for: form.card.camera)
        if let last = jobs.last { UserDefaults.standard.set(last.plan.shootFolder.path, forKey: NewShootForm.lastShootKey) }
        model.copy(form.card, jobs: jobs)
        onDone()
    }
}

/// Reports the session list's natural height up to SessionsView.
private struct ListHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

struct SessionRow: View {
    @ObservedObject var model: AppModel
    @ObservedObject var form: SessionsForm
    @ObservedObject var draft: SessionDraft
    /// Observed so the row updates when its pickers change.
    @ObservedObject var shoot: NewShootForm

    private var session: CardSession { draft.session }
    private var hasActions: Bool { session.spansDays || !form.isLast(draft) }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Toggle("", isOn: Binding(get: { !draft.skip }, set: { draft.skip = !$0 }))
                .labelsHidden()
                .disabled(draft.missing.isEmpty)
            preview  // always full colour; only the text dims when the session is unticked
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline) {
                    Text(Self.title(session)).font(.callout.weight(.semibold))
                    Spacer()
                    if hasActions {
                        Menu {
                            if session.spansDays { Button("Split by day") { form.splitByDay(draft) } }
                            if !form.isLast(draft) { Button("Merge with next") { form.mergeWithNext(draft) } }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                        }
                        .menuStyle(.borderlessButton)
                        .fixedSize()
                    }
                }
                Text(details).font(.caption).foregroundStyle(.secondary)
                if draft.missing.isEmpty {
                    Text("Already on \(model.driveName) — nothing to copy.").font(.caption).foregroundStyle(.secondary)
                } else if !draft.skip {
                    ShootPickers(form: shoot, compact: true)
                }
            }
            .opacity(draft.skip ? 0.5 : 1)
        }
    }

    private static let previewSize = CGSize(width: 192, height: 108)   // 16:9

    /// Middle clip of the session, so you can tell the shoots apart at a glance.
    private var preview: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 4).fill(.quaternary)
            if let image = draft.thumbnail {
                Image(nsImage: image).resizable().aspectRatio(contentMode: .fill)
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .frame(width: Self.previewSize.width, height: Self.previewSize.height)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .help(session.middleShot.map { "Middle clip: \($0.url.lastPathComponent)" } ?? "")
    }

    private var details: String {
        var parts = ["\(session.clipCount) clips"]
        if session.photoCount > 0 { parts.append("\(session.photoCount) photos") }
        parts.append(ByteCountFormatter.string(fromByteCount: session.bytes, countStyle: .file))
        if !draft.missing.isEmpty, draft.missing.count < session.files.count {
            parts.append("\(draft.missing.count) files still to copy")
        }
        return parts.joined(separator: " · ")
    }

    /// "Mon 5 Oct · 05:43–07:31", or "Tue 13 Oct 22:00 – Wed 14 Oct 04:03" over midnight.
    static func title(_ session: CardSession) -> String {
        let day = DateFormatter()
        day.dateFormat = "EEE d MMM"
        let time = DateFormatter()
        time.dateFormat = "HH:mm"
        if session.spansDays {
            return "\(day.string(from: session.start)) \(time.string(from: session.start)) – "
                + "\(day.string(from: session.end)) \(time.string(from: session.end))"
        }
        return "\(day.string(from: session.start)) · \(time.string(from: session.start))–\(time.string(from: session.end))"
    }
}
