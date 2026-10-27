import Foundation
import ImageCaptureCore

/// Something a copy can read from: a file on a card, or a file on a plugged-in iPhone.
public protocol ByteSource: AnyObject {
    func read(upToCount count: Int) throws -> Data?
    func seek(toOffset offset: UInt64) throws
    func close() throws
}

extension FileHandle: ByteSource {}

public enum ByteReader {
    /// Opens a card file, or an iPhone file (`offload-iphone://…` URLs from `PhoneSource`).
    public static func open(_ url: URL) -> ByteSource? {
        url.isFileURL ? try? FileHandle(forReadingFrom: url) : PhoneSource.reader(for: url)
    }
}

/// iPhones and iPads plugged in by cable. They aren't drives, so they're read through Image
/// Capture (the same way Image Capture.app does): read-only, originals as stored on the phone.
/// Each one appears as a card whose "volume" is `offload-iphone://<device id>/`, and whose
/// files are `offload-iphone://<device id>/<month folder>/<name>`.
public final class PhoneSource: NSObject, ICDeviceBrowserDelegate, ICCameraDeviceDelegate, @unchecked Sendable {
    public static let scheme = "offload-iphone"

    /// Main thread: a phone appeared (with its name) or was unplugged.
    public var onConnect: ((URL, String) -> Void)?
    public var onDisconnect: ((URL) -> Void)?
    /// Main thread: the phone was unlocked and its photo list is complete — scan it now.
    public var onReady: ((URL) -> Void)?

    private let browser = ICDeviceBrowser()
    private var devices: [URL: ICCameraDevice] = [:]

    private static let lock = NSLock()
    nonisolated(unsafe) private static var items: [URL: ICCameraFile] = [:]
    nonisolated(unsafe) private static var listed: [URL: [URL]] = [:]

    public static func isPhone(_ url: URL) -> Bool { url.scheme == scheme }

    public func start() {
        browser.delegate = self
        browser.browsedDeviceTypeMask = ICDeviceTypeMask(
            rawValue: ICDeviceTypeMask.camera.rawValue | ICDeviceLocationTypeMask.local.rawValue)!
        browser.start()
    }

    /// Lets go of the phone ("Disconnect" on its card).
    public func close(_ volume: URL) {
        devices[volume]?.requestCloseSession()
    }

    private static func volume(of device: ICDevice) -> URL {
        URL(string: "\(scheme)://\(device.uuidString ?? UUID().uuidString)/")!
    }

    // MARK: Device browser

    public func deviceBrowser(_ browser: ICDeviceBrowser, didAdd device: ICDevice, moreComing: Bool) {
        // Only Apple devices: other cameras over USB mount as cards and are read as files.
        guard let camera = device as? ICCameraDevice,
              ["iPhone", "iPad", "iPod"].contains(device.productKind ?? "") || camera.isAccessRestrictedAppleDevice
        else { return }
        let volume = Self.volume(of: device)
        devices[volume] = camera
        camera.delegate = self
        camera.requestOpenSession()
        DispatchQueue.main.async { self.onConnect?(volume, device.name ?? "iPhone") }
    }

    public func deviceBrowser(_ browser: ICDeviceBrowser, didRemove device: ICDevice, moreGoing: Bool) {
        let volume = Self.volume(of: device)
        devices[volume] = nil
        Self.lock.withLock {
            for url in Self.listed[volume] ?? [] { Self.items[url] = nil }
            Self.listed[volume] = nil
        }
        DispatchQueue.main.async { self.onDisconnect?(volume) }
    }

    public func deviceDidBecomeReady(withCompleteContentCatalog device: ICCameraDevice) {
        relist(device)
    }

    /// A locked phone reports an empty list; its photos arrive once it's unlocked.
    public func cameraDevice(_ camera: ICCameraDevice, didAdd items: [ICCameraItem]) {
        guard Self.isReady(Self.volume(of: camera)) else { return }
        pendingRelist?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.relist(camera) }
        pendingRelist = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 1, execute: work)  // batches arrive in bursts
    }

    // Unlocking isn't the moment to list: the photos aren't loaded yet. The phone calls
    // deviceDidBecomeReady again once they are.
    public func cameraDeviceDidRemoveAccessRestriction(_ device: ICDevice) {}

    private var pendingRelist: DispatchWorkItem?

    /// Whether the phone is unlocked and its list complete. Until then it has nothing to show,
    /// which must never read as "everything's copied".
    public static func isReady(_ volume: URL) -> Bool { lock.withLock { listed[volume] != nil } }

    private func relist(_ device: ICCameraDevice) {
        guard !device.isAccessRestrictedAppleDevice else { return }
        let volume = Self.volume(of: device)
        var map: [URL: ICCameraFile] = [:]
        for case let file as ICCameraFile in device.mediaFiles ?? [] {
            guard let name = file.name else { continue }
            var parts = URLComponents()
            parts.scheme = Self.scheme
            parts.host = volume.host
            parts.path = "/\(file.parentFolder?.name ?? "DCIM")/\(name)"
            if let url = parts.url { map[url] = file }
        }
        Self.lock.withLock {
            for url in Self.listed[volume] ?? [] { Self.items[url] = nil }
            Self.items.merge(map) { _, new in new }
            Self.listed[volume] = Array(map.keys)
        }
        DispatchQueue.main.async { self.onReady?(volume) }
    }

    // MARK: Reading

    /// Same split as a card: media to copy, plus types Offload doesn't copy (screenshots
    /// are PNG) by count. The phone keeps month folders ("202609_a"); they're not used.
    public static func survey(of volume: URL) -> (files: [MediaFile], unknown: [String: Int]) {
        let entries: [(URL, ICCameraFile)] = lock.withLock {
            (listed[volume] ?? []).compactMap { url in items[url].map { (url, $0) } }
        }
        var files: [MediaFile] = []
        var unknown: [String: Int] = [:]
        for (url, item) in entries {
            let ext = url.pathExtension.lowercased()
            if Card.mediaExtensions.contains(ext) {
                files.append(MediaFile(url: url, size: item.fileSize,
                                       created: item.creationDate ?? item.fileCreationDate ?? .distantPast))
            } else {
                unknown[ext.isEmpty ? "no extension" : ext.uppercased(), default: 0] += 1
            }
        }
        return (files.sorted { $0.url.path < $1.url.path }, unknown)
    }

    static func reader(for url: URL) -> ByteSource? {
        lock.withLock { items[url] }.map(PhoneReader.init)
    }

    /// "iPhone 16 Pro", from the EXIF of a photo the phone itself took. A camera roll also holds
    /// saved and AirDropped images from apps and other cameras, so only Apple-made HEIC/DNG
    /// photos count (a few are tried, newest first).
    public static func camera(on volume: URL, files: [MediaFile]) -> CameraInfo? {
        let candidates = files.filter { $0.isPhoto && ["heic", "dng"].contains($0.url.pathExtension.lowercased()) }
            .sorted { $0.created > $1.created }.prefix(5)
        for photo in candidates {
            guard let item = lock.withLock({ items[photo.url] }) else { continue }
            let done = DispatchSemaphore(value: 0)
            nonisolated(unsafe) var tiff: [String: Any]?
            item.requestMetadataDictionary(options: [:]) { metadata, _ in
                tiff = metadata?["{TIFF}"] as? [String: Any]
                done.signal()
            }
            _ = done.wait(timeout: .now() + 10)
            if (tiff?["Make"] as? String)?.hasPrefix("Apple") == true,
               let model = (tiff?["Model"] as? String)?.trimmingCharacters(in: .whitespaces), !model.isEmpty {
                return CameraInfo(model: model, serial: volume.host)
            }
        }
        return CameraInfo(model: "iPhone", serial: volume.host)
    }

    /// The phone's own preview of a photo or video, `maxPixels` on the long edge (it sends
    /// 160 px unless asked for more).
    public static func thumbnail(of url: URL, maxPixels: Int) async -> Data? {
        guard let item = lock.withLock({ items[url] }) else { return nil }
        return await withCheckedContinuation { done in
            item.requestThumbnailData(options: [.imageSourceThumbnailMaxPixelSize: maxPixels]) { data, _ in done.resume(returning: data) }
        }
    }

    // MARK: Unused delegate calls

    public func device(_ device: ICDevice, didOpenSessionWithError error: Error?) {}
    public func device(_ device: ICDevice, didCloseSessionWithError error: Error?) {}
    public func didRemove(_ device: ICDevice) {}
    public func cameraDevice(_ camera: ICCameraDevice, didRemove items: [ICCameraItem]) {}
    public func cameraDevice(_ camera: ICCameraDevice, didReceiveThumbnail thumbnail: CGImage?, for item: ICCameraItem, error: Error?) {}
    public func cameraDevice(_ camera: ICCameraDevice, didReceiveMetadata metadata: [AnyHashable: Any]?, for item: ICCameraItem, error: Error?) {}
    public func cameraDevice(_ camera: ICCameraDevice, didRenameItems items: [ICCameraItem]) {}
    public func cameraDeviceDidChangeCapability(_ camera: ICCameraDevice) {}
    public func cameraDevice(_ camera: ICCameraDevice, didReceivePTPEvent eventData: Data) {}
    public func cameraDeviceDidEnableAccessRestriction(_ device: ICDevice) {}
}

/// Reads an iPhone file in pieces over the cable, one request at a time.
final class PhoneReader: ByteSource {
    private let item: ICCameraFile
    private var offset: Int64 = 0
    /// A piece that takes longer than this means the phone was locked or unplugged.
    static let timeout: TimeInterval = 60

    init(_ item: ICCameraFile) { self.item = item }

    /// Like FileHandle: `count` bytes unless the file ends first (the phone may send less per request).
    func read(upToCount count: Int) throws -> Data? {
        var data = Data()
        while data.count < count, offset < item.fileSize {
            let piece = try request(min(Int64(count - data.count), item.fileSize - offset))
            offset += Int64(piece.count)
            data.append(piece)
        }
        return data.isEmpty ? nil : data
    }

    private func request(_ length: Int64) throws -> Data {
        let done = DispatchSemaphore(value: 0)
        nonisolated(unsafe) var result: Result<Data, Error> = .failure(PhoneError.timedOut)
        item.requestReadData(atOffset: offset, length: length) { data, error in
            result = data.map { .success($0) } ?? .failure(error ?? PhoneError.timedOut)
            done.signal()
        }
        guard done.wait(timeout: .now() + Self.timeout) == .success else { throw PhoneError.timedOut }
        let data = try result.get()
        guard !data.isEmpty else { throw PhoneError.timedOut }
        return data
    }

    func seek(toOffset offset: UInt64) throws { self.offset = Int64(offset) }
    func close() throws {}
}

public enum PhoneError: Error, LocalizedError {
    case timedOut
    public var errorDescription: String? {
        "The iPhone stopped sending. Keep it unlocked and plugged in, then try again."
    }
}
