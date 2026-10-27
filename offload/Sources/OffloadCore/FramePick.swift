import Foundation

/// A decoded preview frame: 8-bit RGBA, rows packed with no padding.
public struct Frame: Sendable, Equatable {
    public let width: Int
    public let height: Int
    public let pixels: [UInt8]

    public init(width: Int, height: Int, pixels: [UInt8]) {
        self.width = width
        self.height = height
        self.pixels = pixels
    }

    /// Brightness of each pixel, 0...1 (Rec.709 weights).
    var luma: [Double] {
        stride(from: 0, to: pixels.count, by: 4).map {
            (0.2126 * Double(pixels[$0]) + 0.7152 * Double(pixels[$0 + 1]) + 0.0722 * Double(pixels[$0 + 2])) / 255
        }
    }
}

/// Picks the best of a few frames for a session's preview: sharp (little motion blur or
/// focus hunting), detailed (wide shots beat a soft close-up), and properly exposed.
public enum FramePick {
    public struct Rect: Equatable, Sendable {
        public let x, y, width, height: Int
    }

    /// Mean brightness outside this range is a frame mostly black or blown out.
    static let usableBrightness = 0.12...0.88
    /// What a badly exposed frame's sharpness counts for.
    static let badExposureWeight = 0.25
    /// Rows and columns darker than this are letterbox or pillarbox bars. S-Log3 shadows
    /// never get this low (its black sits at ~9%), so dark footage isn't cropped.
    static let barBrightness = 0.03
    /// Never crop more than this much off any side.
    static let maxBarFraction = 0.25

    /// Higher is better: sharpness, held down when the frame is too dark or too bright.
    public static func score(_ frame: Frame) -> Double {
        let luma = frame.luma
        guard !luma.isEmpty else { return 0 }
        let mean = luma.reduce(0, +) / Double(luma.count)
        return sharpness(luma, width: frame.width, height: frame.height)
            * (usableBrightness.contains(mean) ? 1 : badExposureWeight)
    }

    /// Variance of the Laplacian: edges that are crisp score high, motion blur and soft
    /// focus score low.
    static func sharpness(_ luma: [Double], width: Int, height: Int) -> Double {
        guard width > 2, height > 2 else { return 0 }
        var responses: [Double] = []
        responses.reserveCapacity((width - 2) * (height - 2))
        for y in 1..<(height - 1) {
            for x in 1..<(width - 1) {
                let i = y * width + x
                responses.append(luma[i - width] + luma[i + width] + luma[i - 1] + luma[i + 1] - 4 * luma[i])
            }
        }
        let mean = responses.reduce(0, +) / Double(responses.count)
        return responses.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Double(responses.count)
    }

    /// The picture without black bars along any edge.
    public static func contentRect(_ frame: Frame) -> Rect {
        let luma = frame.luma
        let (w, h) = (frame.width, frame.height)
        func rowIsBar(_ y: Int) -> Bool { (0..<w).reduce(0) { $0 + luma[y * w + $1] } / Double(w) < barBrightness }
        func columnIsBar(_ x: Int) -> Bool { (0..<h).reduce(0) { $0 + luma[$1 * w + x] } / Double(h) < barBrightness }
        let maxX = Int(Double(w) * maxBarFraction), maxY = Int(Double(h) * maxBarFraction)
        let left = (0..<maxX).first { !columnIsBar($0) } ?? maxX
        let right = (0..<maxX).first { !columnIsBar(w - 1 - $0) } ?? maxX
        let top = (0..<maxY).first { !rowIsBar($0) } ?? maxY
        let bottom = (0..<maxY).first { !rowIsBar(h - 1 - $0) } ?? maxY
        return Rect(x: left, y: top, width: w - left - right, height: h - top - bottom)
    }

    public static func cropped(_ frame: Frame, to rect: Rect) -> Frame {
        var pixels: [UInt8] = []
        pixels.reserveCapacity(rect.width * rect.height * 4)
        for y in rect.y..<(rect.y + rect.height) {
            let start = (y * frame.width + rect.x) * 4
            pixels += frame.pixels[start..<(start + rect.width * 4)]
        }
        return Frame(width: rect.width, height: rect.height, pixels: pixels)
    }
}
