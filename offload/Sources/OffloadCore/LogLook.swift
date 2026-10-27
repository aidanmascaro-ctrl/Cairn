import Foundation

/// Sony S-Log3 made viewable, the way an "S-Log3 to Rec.709" LUT does: the log curve back
/// to scene light, S-Gamut3(.Cine) to Rec.709 colours, highlights rolled off, then the
/// Rec.709 camera curve. Only ever applied to a preview, never to footage.
public struct LogLook: Sendable {
    /// Camera gamut (linear) -> Rec.709 (linear), row by row, from Sony's published
    /// primaries and D65 white. Each row sums to 1, so greys stay grey.
    static let sGamut3Cine: [Double] = [
        1.6269474097, -0.5401385389, -0.0868088709,
        -0.1785155271, 1.4179409275, -0.2394254003,
        -0.0444361150, -0.1959199662, 1.2403560812,
    ]
    static let sGamut3: [Double] = [
        1.8779151284, -0.7941687613, -0.0837463671,
        -0.1768069813, 1.3509996209, -0.1741926396,
        -0.0262011264, -0.1484222623, 1.1746233888,
    ]
    /// Scene light at S-Log3's brightest code value.
    static let whitePoint = 38.42
    /// Keeps 18% grey at 18% through the highlight roll-off.
    static let shoulderGain = (1 + 0.18) / (1 + 0.18 / (whitePoint * whitePoint))

    private let matrix: [Double]
    /// 8-bit code value -> scene light.
    private let linear: [Double]

    /// From a clip's Sony XML (`<Item name="CaptureGammaEquation" value="s-log3-cine"/>`);
    /// nil for footage that isn't S-Log3, which already looks right.
    public init?(sonyXML xml: String) {
        guard Self.item("CaptureGammaEquation", in: xml)?.lowercased().hasPrefix("s-log3") == true else { return nil }
        matrix = Self.item("CaptureColorPrimaries", in: xml)?.lowercased() == "s-gamut3" ? Self.sGamut3 : Self.sGamut3Cine
        linear = (0...255).map { Self.sceneLight(fromSLog3: Double($0) / 255) }
    }

    public func applied(to frame: Frame) -> Frame {
        var out = frame.pixels
        for i in stride(from: 0, to: out.count, by: 4) {
            let r = linear[Int(out[i])], g = linear[Int(out[i + 1])], b = linear[Int(out[i + 2])]
            for c in 0..<3 {
                let mixed = matrix[c * 3] * r + matrix[c * 3 + 1] * g + matrix[c * 3 + 2] * b
                out[i + c] = UInt8((Self.display(mixed) * 255).rounded())
            }
        }
        return Frame(width: frame.width, height: frame.height, pixels: out)
    }

    /// Sony's S-Log3 curve, inverted: code value (0...1 of 1023) -> scene light (18% grey = 0.18).
    static func sceneLight(fromSLog3 value: Double) -> Double {
        let code = value * 1023
        return code >= 171.2102946929
            ? pow(10, (code - 420) / 261.5) * 0.19 - 0.01
            : (code - 95) * 0.01125 / (171.2102946929 - 95)
    }

    /// Scene light -> display value 0...1: a soft shoulder for highlights, then Rec.709.
    static func display(_ light: Double) -> Double {
        let x = max(0, light)
        let toned = min(1, x * (1 + x / (whitePoint * whitePoint)) / (1 + x) * shoulderGain)
        return toned < 0.018 ? 4.5 * toned : 1.099 * pow(toned, 0.45) - 0.099
    }

    private static func item(_ name: String, in xml: String) -> String? {
        guard let range = xml.range(of: #"name="\#(name)" value="[^"]*""#, options: .regularExpression) else { return nil }
        let parts = xml[range].split(separator: "\"", omittingEmptySubsequences: false)  // name=, X, value=, Y, ""
        return parts.count > 3 ? String(parts[3]) : nil
    }
}
