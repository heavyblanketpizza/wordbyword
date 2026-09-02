import AppKit
import CoreText
import SwiftUI

enum StudioTheme {
    static let ink = Color(white: 0.075)
    static let muted = Color(white: 0.37)
    static let hairline = Color(white: 0.80)
    static let canvas = Color.white
    static let faint = Color(white: 0.935)
}

enum StudioType {
    static let scale: CGFloat = 1.25
    static let body = system(14)
    static let detail = system(12)

    static func system(_ size: CGFloat, weight: Font.Weight = .regular,
                       design: Font.Design = .default) -> Font {
        .system(size: size * scale, weight: weight, design: design)
    }

    static func display(in language: StudioLanguage) -> Font {
        mono(24, weight: .bold, language: language)
    }

    static func heading(in language: StudioLanguage) -> Font {
        mono(17.5, weight: .medium, language: language)
    }

    static func mono(_ size: CGFloat, weight: Font.Weight = .regular,
                     language: StudioLanguage = .english) -> Font {
        // Martian Mono has no Hangul glyphs. Use the system's Korean typeface
        // explicitly so weight and line metrics are consistent across labels.
        let scaledSize = size * scale
        return language == .korean ? .system(size: scaledSize, weight: weight) :
            font(scaledSize, weight: weight, width: "Std")
    }

    static func condensed(_ size: CGFloat, weight: Font.Weight = .medium,
                          language: StudioLanguage = .english) -> Font {
        let scaledSize = size * scale
        return language == .korean ? .system(size: scaledSize, weight: weight) :
            font(scaledSize, weight: weight, width: "Cn")
    }

    private static func font(_ size: CGFloat, weight: Font.Weight, width: String) -> Font {
        _ = registerInstalledFonts
        let name = "MartianMono-\(width)\(faceSuffix(for: weight))"
        if NSFont(name: name, size: size) != nil {
            return .custom(name, fixedSize: size)
        }
        return .system(size: size, weight: weight, design: .monospaced)
    }

    private static func faceSuffix(for weight: Font.Weight) -> String {
        switch weight {
        case .bold, .semibold, .heavy, .black: return "Bd"
        case .medium: return "Md"
        default: return "Rg"
        }
    }

    // Register only known, already-installed faces for this process. The app
    // never installs or redistributes fonts and remains usable without them.
    private static let registerInstalledFonts: Void = {
        let directory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Fonts", isDirectory: true)
        for width in ["Std", "Cn"] {
            for suffix in ["Rg", "Md", "Bd"] {
                let url = directory.appendingPathComponent("MartianMono-\(width)\(suffix).otf")
                guard FileManager.default.isReadableFile(atPath: url.path) else { continue }
                CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
            }
        }
    }()
}
