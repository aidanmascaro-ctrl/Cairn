import Foundation

/// Will a copy fit on the working drive?
public enum DriveSpace {
    /// Left free after any copy, so the drive never fills to zero mid-copy
    /// (Spotlight, the .part file and the Finder all need a little room).
    public static let reserve: Int64 = 2_000_000_000

    /// Free bytes on the volume holding `folder`. Prefers the "important usage" figure
    /// (counts purgeable space macOS would clear), falls back to the plain figure, which
    /// is what exFAT drives report.
    public static func available(at folder: URL) -> Int64? {
        let values = try? folder.resourceValues(forKeys: [
            .volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityKey,
        ])
        if let important = values?.volumeAvailableCapacityForImportantUsage, important > 0 { return important }
        return values?.volumeAvailableCapacity.map(Int64.init)
    }

    /// nil when it fits; otherwise how many bytes are missing.
    public static func shortfall(needed: Int64, available: Int64) -> Int64? {
        let short = needed + reserve - available
        return short > 0 ? short : nil
    }
}
