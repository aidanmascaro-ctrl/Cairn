import AppKit
import AVFoundation
import ImageIO
import OffloadCore

/// Small preview images for the session list. Read-only: never writes to the card.
enum Thumbnailer {
    /// Long edge of the preview, in pixels. The row fills 192×108 pt (@2x), so a portrait
    /// phone shot needs ~800 px tall to be 384 px wide.
    static let maxPixels = 800
    /// Where frames are taken in each sampled clip, as fractions of its length.
    static let framePoints = [0.3, 0.5, 0.7]
    /// Frames snap to a nearby keyframe within this much: quicker to decode, and sharper.
    static let keyframeTolerance = CMTime(seconds: 0.5, preferredTimescale: 600)

    /// A session's preview: the best of a few frames from clips spread through it (sharp,
    /// detailed, well exposed, black bars cropped, S-Log3 shown as Rec.709). Falls back to
    /// the camera's own thumbnail, and phones and photo-only sessions keep their own preview.
    static func image(for session: CardSession) async -> NSImage? {
        guard let middle = session.middleShot else { return nil }
        if middle.kind == .video, !PhoneSource.isPhone(middle.url), let best = await bestFrame(in: session) {
            return best
        }
        guard let preview = session.preview else { return nil }
        return await image(for: preview)
    }

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

    private static func bestFrame(in session: CardSession) async -> NSImage? {
        var best: (frame: Frame, score: Double)?
        for clip in session.previewClips {
            let look = session.sidecar(of: clip)
                .flatMap { try? String(contentsOf: $0.url, encoding: .utf8) }
                .flatMap(LogLook.init(sonyXML:))
            for frame in await frames(of: clip.url) {
                // Bars off first: their hard edge would read as sharp detail.
                let picture = FramePick.cropped(frame, to: FramePick.contentRect(frame))
                let viewable = look?.applied(to: picture) ?? picture
                let score = FramePick.score(viewable)
                if score > best?.score ?? -1 { best = (viewable, score) }
            }
        }
        return best.flatMap { image(from: $0.frame) }
    }

    private static func frames(of url: URL) async -> [Frame] {
        let asset = AVURLAsset(url: url)
        guard let duration = try? await asset.load(.duration), duration.seconds > 0 else { return [] }
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: maxPixels, height: maxPixels)
        generator.requestedTimeToleranceBefore = keyframeTolerance
        generator.requestedTimeToleranceAfter = keyframeTolerance
        var frames: [Frame] = []
        for point in framePoints {
            guard let (cg, _) = try? await generator.image(at: CMTimeMultiplyByFloat64(duration, multiplier: point)),
                  let frame = frame(from: cg) else { continue }
            frames.append(frame)
        }
        return frames
    }

    /// The decoded pixels as they are (in the frame's own colour space), so S-Log3 code
    /// values reach the look unchanged.
    private static func frame(from image: CGImage) -> Frame? {
        let (w, h) = (image.width, image.height)
        let own = image.colorSpace.flatMap { $0.model == .rgb ? $0 : nil }
        for space in [own, CGColorSpace(name: CGColorSpace.sRGB)].compactMap({ $0 }) {
            var pixels = [UInt8](repeating: 0, count: w * h * 4)
            let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
                guard let context = CGContext(data: buffer.baseAddress, width: w, height: h, bitsPerComponent: 8,
                                              bytesPerRow: w * 4, space: space,
                                              bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return false }
                context.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
                return true
            }
            if drawn { return Frame(width: w, height: h, pixels: pixels) }
        }
        return nil
    }

    private static func image(from frame: Frame) -> NSImage? {
        guard let provider = CGDataProvider(data: Data(frame.pixels) as CFData),
              let space = CGColorSpace(name: CGColorSpace.sRGB),
              let cg = CGImage(width: frame.width, height: frame.height, bitsPerComponent: 8, bitsPerPixel: 32,
                               bytesPerRow: frame.width * 4, space: space,
                               bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
                               provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent)
        else { return nil }
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
