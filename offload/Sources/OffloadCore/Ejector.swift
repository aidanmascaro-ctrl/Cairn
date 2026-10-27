import DiskArbitration
import Foundation

/// Unmount + eject through DiskArbitration — the same path Finder and `diskutil` use.
/// Unlike NSWorkspace.unmountAndEjectDevice (which only says "busy", -47), a refusal
/// comes back with the dissenting reason.
public enum Ejector {
    public struct Refused: Error, LocalizedError {
        public let status: DAReturn
        public let reason: String?
        public var isBusy: Bool { status == DAReturn(kDAReturnBusy) }
        public var errorDescription: String? {
            if let reason { return reason }
            switch Int(status) {
            case kDAReturnBusy: return "The card is busy."
            case kDAReturnNotPrivileged, kDAReturnNotPermitted:
                return "macOS won't let Offload eject from here. Open Offload.app (not the terminal build) and try again."
            case kDAReturnNotMounted, kDAReturnNotFound: return "The card is already unmounted — safe to remove."
            default: return "macOS refused to unmount the card (DiskArbitration \(String(format: "0x%08X", UInt32(bitPattern: status))))."
            }
        }
    }

    public enum Failure: Error, LocalizedError {
        case notAVolume(URL)
        public var errorDescription: String? {
            switch self {
            case .notAVolume(let url): "\(url.lastPathComponent) isn't a mounted disk any more."
            }
        }
    }

    /// Throws `Refused` if something is still using the card. Once the unmount succeeds
    /// the card is safe to remove, so a failed physical eject (built-in SD slots can't
    /// eject) is not treated as an error.
    public static func eject(_ volume: URL) async throws {
        guard let session = DASessionCreate(kCFAllocatorDefault),
              let disk = DADiskCreateFromVolumePath(kCFAllocatorDefault, session, volume as CFURL)
        else { throw Failure.notAVolume(volume) }
        let queue = DispatchQueue(label: "offload.eject")
        DASessionSetDispatchQueue(session, queue)
        defer { DASessionSetDispatchQueue(session, nil) }

        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            DADiskUnmount(disk, DADiskUnmountOptions(kDADiskUnmountOptionDefault), done, box(cont))
        }
        if let whole = DADiskCopyWholeDisk(disk) {
            try? await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
                DADiskEject(whole, DADiskEjectOptions(kDADiskEjectOptionDefault), done, box(cont))
            }
        }
    }

    private final class Box {
        let cont: CheckedContinuation<Void, Error>
        init(_ cont: CheckedContinuation<Void, Error>) { self.cont = cont }
    }

    private static func box(_ cont: CheckedContinuation<Void, Error>) -> UnsafeMutableRawPointer {
        Unmanaged.passRetained(Box(cont)).toOpaque()
    }

    /// Shared C callback for unmount and eject (same signature).
    private static let done: DADiskUnmountCallback = { _, dissenter, context in
        guard let context else { return }
        let box = Unmanaged<Box>.fromOpaque(context).takeRetainedValue()
        if let dissenter {
            let reason = DADissenterGetStatusString(dissenter) as String?
            box.cont.resume(throwing: Refused(status: DADissenterGetStatus(dissenter), reason: reason))
        } else {
            box.cont.resume()
        }
    }
}
