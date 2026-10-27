import AppKit
import AVFoundation
import ImageIO
import OffloadCore

/// Small preview images for the session list. Read-only: never writes to the card.
enum Thumbnailer {
    /// Long edge of the preview, in pixels. The row fills 192×108 pt (@2x), so a portrait
    /// phone shot needs ~800 px tall to be 384 px wide.
    static let maxPixels = 800

    static func image(for file: MediaFile) async -> NSImage? {
        if PhoneSource.isPhone(file.url) {  // the phone's own preview; no reading whole videos over the cable
            return await PhoneSource.thumbnail(of: file.url, maxPixels: maxPixels).flatMap(NSImage.init(data:))
        }
        return switch file.kind {
        case .video: await frame(fromMiddleOf: file.url)
        case .photo, .thumbnail: still(file.url)
        case .metadata, .photoEdit: nil
        }
    }

    /// JPGs, and the preview JPG embedded in RAW files (ARW/CR3/NEF) — ImageIO only
    /// decodes what it needs for a thumbnail.
    private static func still(_ url: URL) -> NSImage? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageIfAbsent: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixels,
        ]
        guard let cg = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { return nil }
        return NSImage(cgImage: cg, size: .zero)
    }

    /// One frame from halfway through the clip.
    private static func frame(fromMiddleOf url: URL) async -> NSImage? {
        let asset = AVURLAsset(url: url)
        guard let duration = try? await asset.load(.duration) else { return nil }
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: maxPixels, height: maxPixels)
        let middle = CMTimeMultiplyByFloat64(duration, multiplier: 0.5)
        guard let (cg, _) = try? await generator.image(at: middle) else { return nil }
        return NSImage(cgImage: cg, size: .zero)
    }
}
