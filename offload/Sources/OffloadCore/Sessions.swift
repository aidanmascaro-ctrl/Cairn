import Foundation

/// One stretch of shooting on a card: clips/photos with no gap longer than the chosen
/// rule, plus the thumbnails and XML that belong to them.
public struct CardSession: Identifiable, Hashable, Sendable {
    public let id: Int
    public let start: Date
    public let end: Date
    /// Clips + photos + their sidecars + card-wide metadata. Everything that gets copied.
    public let files: [MediaFile]

    public var clipCount: Int { files.filter { $0.kind == .video }.count }
    public var photoCount: Int { files.filter { $0.kind == .photo }.count }
    public var bytes: Int64 { files.reduce(0) { $0 + $1.size } }
    public var hasPhotos: Bool { files.contains { $0.kind == .photo || $0.kind == .photoEdit } }
    public var spansDays: Bool { !Calendar.current.isDate(start, inSameDayAs: end) }

    /// The clip in the middle of the session by time (or middle photo if no clips).
    public var middleShot: MediaFile? {
        let clips = files.filter { $0.kind == .video }
        let pool = (clips.isEmpty ? files.filter { $0.kind == .photo } : clips).sorted { $0.created < $1.created }
        return pool.isEmpty ? nil : pool[pool.count / 2]
    }

    /// What to show for the session: the camera's own thumbnail of the middle clip if the
    /// card has one (instant), otherwise the middle clip/photo itself.
    public var preview: MediaFile? {
        guard let middle = middleShot else { return nil }
        let stem = SessionPlan.stem(middle.url)
        return files.first { $0.kind == .thumbnail && SessionPlan.ownerStem($0.url) == stem } ?? middle
    }

    /// Clips a quarter, half and three quarters of the way through, by time: frames from
    /// these are compared to find a good preview.
    public var previewClips: [MediaFile] {
        let clips = files.filter { $0.kind == .video }.sorted { $0.created < $1.created }
        guard !clips.isEmpty else { return [] }
        let picks = [clips.count / 4, clips.count / 2, clips.count * 3 / 4].map { clips[$0] }
        return picks.enumerated().filter { i, clip in !picks.prefix(i).contains(clip) }.map(\.element)
    }

    /// The camera's XML for `clip` (C0004.MP4 -> C0004M01.XML), which says how it was shot.
    public func sidecar(of clip: MediaFile) -> MediaFile? {
        let stem = SessionPlan.stem(clip.url)
        return files.first {
            $0.kind == .metadata && $0.url.pathExtension.lowercased() == "xml" && SessionPlan.ownerStem($0.url) == stem
        }
    }
}

/// Splits a card into sessions. State is just "where new sessions start" (cuts into the
/// time-sorted list of clips/photos), so merge and split are toggles on that set.
public struct SessionPlan: Sendable {
    /// Clips and photos, oldest first. Sessions are ranges of this.
    public let primaries: [MediaFile]
    /// Indices into `primaries` where a new session begins (0 is implicit).
    public private(set) var cuts: Set<Int>
    private let sidecarsByStem: [String: [MediaFile]]
    /// Clip sidecars whose clip isn't on the card (deleted in camera) — placed by time.
    private let orphans: [MediaFile]
    /// Card-level files (MEDIAPRO.XML, LUTs) — copied with every session.
    private let cardWide: [MediaFile]

    public static let defaultGap: TimeInterval = 3 * 3600

    public init(files: [MediaFile], gap: TimeInterval = SessionPlan.defaultGap) {
        primaries = files.filter { $0.kind == .video || $0.kind == .photo }.sorted { $0.created < $1.created }
        let sidecars = files.filter { $0.kind == .thumbnail || $0.kind == .metadata || $0.kind == .photoEdit }
        let primaryStems = Set(primaries.map { Self.stem($0.url) })
        var byStem: [String: [MediaFile]] = [:]
        var lost: [MediaFile] = []
        var wide: [MediaFile] = []
        for file in sidecars {
            let owner = Self.ownerStem(file.url)
            if primaryStems.contains(owner) {
                byStem[owner, default: []].append(file)
            } else if owner != Self.stem(file.url) {
                lost.append(file)  // looks like a clip's sidecar ("C0042T01"), clip is gone
            } else {
                wide.append(file)
            }
        }
        sidecarsByStem = byStem
        orphans = lost
        cardWide = wide
        cuts = []
        regroup(gap: gap)
    }

    /// Start a new session wherever recording stopped for longer than `gap`.
    public mutating func regroup(gap: TimeInterval) {
        cuts = Set(primaries.indices.dropFirst().filter {
            primaries[$0].created.timeIntervalSince(primaries[$0 - 1].created) > gap
        })
    }

    public var sessions: [CardSession] {
        guard !primaries.isEmpty else { return [] }
        let starts = [0] + cuts.sorted()
        return starts.enumerated().map { number, start in
            let end = number + 1 < starts.count ? starts[number + 1] : primaries.count
            let slice = Array(primaries[start..<end])
            let attached = slice.flatMap { sidecarsByStem[Self.stem($0.url)] ?? [] }
            // Orphans fall in the session whose time window they're in (before the first -> first).
            let windowStart = number == 0 ? Date.distantPast : slice[0].created
            let windowEnd = end < primaries.count ? primaries[end].created : Date.distantFuture
            let stray = orphans.filter { $0.created >= windowStart && $0.created < windowEnd }
            return CardSession(id: start, start: slice[0].created, end: slice[slice.count - 1].created,
                               files: slice + attached + stray + cardWide)
        }
    }

    /// Joins this session with the one after it.
    public mutating func mergeWithNext(_ session: CardSession) {
        guard let next = cuts.filter({ $0 > session.id }).min() else { return }
        cuts.remove(next)
    }

    /// Splits a session that runs over midnight into one session per calendar day.
    public mutating func splitByDay(_ session: CardSession) {
        let end = cuts.filter { $0 > session.id }.min() ?? primaries.count
        for i in (session.id + 1)..<end
        where !Calendar.current.isDate(primaries[i].created, inSameDayAs: primaries[i - 1].created) {
            cuts.insert(i)
        }
    }

    /// "C0001.MP4" -> "C0001"
    static func stem(_ url: URL) -> String {
        url.deletingPathExtension().lastPathComponent.uppercased()
    }

    /// Sony sidecars add a 3-character tag: "C0001M01.XML" / "C0001T01.JPG" -> "C0001".
    /// Others share the clip's stem exactly ("GX010001.THM" -> "GX010001").
    static func ownerStem(_ url: URL) -> String {
        let stem = Self.stem(url)
        if stem.range(of: "[A-Z][0-9]{2}$", options: .regularExpression) != nil, stem.count > 3 {
            return String(stem.dropLast(3))
        }
        return stem
    }
}
