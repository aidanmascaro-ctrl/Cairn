import Foundation
import ImageIO

/// Which camera shot the card.
///
/// 1. Sony per-clip XML sidecars (`M4ROOT/CLIP/*M01.XML`, found under PRIVATE/ on
///    cinema-line cards and at the card root on Alpha cards) — model + serial.
/// 2. Otherwise the EXIF Make/Model/serial of the first photo — works for any brand
///    that shoots stills.
public struct CameraInfo: Hashable, Sendable {
    public let model: String
    public let serial: String?

    static let sonyClipFolders = ["PRIVATE/M4ROOT/CLIP", "M4ROOT/CLIP"]

    /// What you'd call the camera: "ILCE-7SM3" -> "A7S III", "ILME-FX30" -> "FX30",
    /// "Canon EOS R5" -> "EOS R5", "RICOH GR III" -> "GR III". Used as the default folder name until renamed.
    public var shortName: String {
        let code = model.split(separator: "-", maxSplits: 1).count == 2
            ? String(model.split(separator: "-", maxSplits: 1)[1]) : model
        if model.hasPrefix("ILCE-") { return "A" + Self.romanMark(code) }   // Sony Alpha
        if model.hasPrefix("ILME-") { return code }                          // Sony cinema line
        let words = model.split(separator: " ", maxSplits: 1)
        if words.count == 2, Self.brands.contains(words[0].uppercased()) { return String(words[1]) }
        return model
    }

    /// Brands dropped from the folder name. DJI, GoPro, Insta360 and Zoom keep theirs:
    /// "DJI Pocket 3" reads better than "Pocket 3".
    static let brands: Set<String> = ["CANON", "NIKON", "FUJIFILM", "PANASONIC", "OLYMPUS", "OM", "SONY", "RICOH",
                                      "PENTAX", "LEICA"]

    /// "7SM3" -> "7S III", "7RM5" -> "7R V", "6700" -> "6700".
    static func romanMark(_ code: String) -> String {
        let marks = ["M2": " II", "M3": " III", "M4": " IV", "M5": " V", "M6": " VI"]
        for (suffix, roman) in marks where code.hasSuffix(suffix) {
            return String(code.dropLast(suffix.count)) + roman
        }
        return code
    }

    /// 3. Otherwise the model tag inside the first clip (Canon, Nikon, Fuji, DJI, GoPro, iPhone…).
    /// 4. Last, the brand its folders point to ("Canon" from DCIM/100CANON) — no serial, so
    ///    the folder name it's given isn't remembered per body.
    public static func read(fromCard volume: URL, files: [MediaFile] = []) -> CameraInfo? {
        sonyClipXML(on: volume)
            ?? files.first(where: \.isPhoto).flatMap { exif(of: $0.url) }
            ?? files.first(where: { $0.kind == .video && !["wav", "mp3"].contains($0.url.pathExtension.lowercased()) })
                .flatMap { ShootTidy.quickTimeModel($0.url) }
            ?? Card.brandHint(of: volume).map { CameraInfo(model: $0, serial: nil) }
    }

    static func sonyClipXML(on volume: URL) -> CameraInfo? {
        for folder in sonyClipFolders {
            let clipDir = volume.appendingPathComponent(folder)
            guard let names = try? FileManager.default.contentsOfDirectory(atPath: clipDir.path),
                  let xmlName = names.sorted().first(where: { $0.uppercased().hasSuffix(".XML") && !$0.hasPrefix(".") }),
                  let xml = try? String(contentsOf: clipDir.appendingPathComponent(xmlName), encoding: .utf8),
                  let info = parse(sonyXML: xml)
            else { continue }
            return info
        }
        return nil
    }

    /// Reads only the photo's header (ImageIO doesn't decode the image for properties).
    static func exif(of url: URL) -> CameraInfo? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let tiff = props[kCGImagePropertyTIFFDictionary] as? [CFString: Any],
              let model = (tiff[kCGImagePropertyTIFFModel] as? String)?.trimmingCharacters(in: .whitespaces),
              !model.isEmpty
        else { return nil }
        let exif = props[kCGImagePropertyExifDictionary] as? [CFString: Any]
        let serial = exif?[kCGImagePropertyExifBodySerialNumber] as? String
        let make = (tiff[kCGImagePropertyTIFFMake] as? String)?.trimmingCharacters(in: .whitespaces) ?? ""
        // Sony EXIF model is already the code ("ILCE-7SM3"); others are prefixed with the make,
        // unless the model already starts with its brand ("RICOH IMAGING COMPANY, LTD." / "RICOH GR III").
        // Only the make's first word: "NIKON CORPORATION" + "NIKON Z 8" -> "NIKON Z 8",
        // "OLYMPUS CORPORATION" + "E-M1" -> "OLYMPUS E-M1", "OM Digital Solutions" + "OM-1" -> "OM-1".
        let brand = make.split(separator: " ").first.map(String.init) ?? ""
        let full = brand.isEmpty || model.uppercased().hasPrefix(brand.uppercased()) || brand.uppercased() == "SONY"
            ? model : "\(brand) \(model)"
        return CameraInfo(model: full, serial: serial)
    }

    // ponytail: regex over the <Device> tag, not a full XML parse; fine for Sony's fixed schema.
    public static func parse(sonyXML xml: String) -> CameraInfo? {
        guard let model = attribute("modelName", in: xml) else { return nil }
        return CameraInfo(model: model, serial: attribute("serialNo", in: xml))
    }

    private static func attribute(_ name: String, in xml: String) -> String? {
        guard let range = xml.range(of: "\(name)=\"([^\"]*)\"", options: .regularExpression) else { return nil }
        let match = xml[range]
        return match.split(separator: "\"").dropFirst().first.map(String.init)
    }
}
