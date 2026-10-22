import SwiftUI

/// Mirrors assets/tokens.css. Typography uses Dynamic Type; motion never bounces.
enum PanelsTheme {
    static let surface = Color(hex: 0x151515)
    static let raised = Color(hex: 0x1c1c1c)
    static let recessed = Color(hex: 0x111111)
    static let ink = Color(hex: 0x111318)
    static let strong = Color(hex: 0xf2f2f2)
    static let text = Color(hex: 0xd8d8d8)
    static let muted = Color(hex: 0xbfbfbf)
    static let faint = Color(hex: 0x8e8e8e)
    static let accent = Color(hex: 0x9aadd2)
    static let accentSurface = Color(hex: 0x222a38)
    static let accentText = Color(hex: 0xdce6f8)
    static let success = Color(hex: 0x7fa564)
    static let error = Color(hex: 0xd85d5d)
    static let border = Color.white.opacity(0.10)
    enum Space { static let xs: CGFloat = 4; static let sm: CGFloat = 8; static let md: CGFloat = 12; static let lg: CGFloat = 16; static let xl: CGFloat = 24; static let xxl: CGFloat = 32; static let hero: CGFloat = 48 }
    enum Radius { static let sm: CGFloat = 4; static let md: CGFloat = 8; static let lg: CGFloat = 12 }
    enum TypeScale {
        static let caption = Font.caption2
        static let small = Font.footnote
        static let body = Font.body
        static let heading = Font.title3.weight(.semibold)
        static let title = Font.largeTitle.weight(.semibold)
    }
    enum Motion { static let fast = 0.09; static let base = 0.15; static let slow = 0.24 }
}

private extension Color {
    init(hex: UInt32) { self.init(.sRGB, red: Double((hex >> 16) & 255) / 255, green: Double((hex >> 8) & 255) / 255, blue: Double(hex & 255) / 255, opacity: 1) }
}

struct PanelsScreen: ViewModifier {
    func body(content: Content) -> some View {
        content.scrollContentBackground(.hidden)
            .background(PanelsTheme.surface)
            .foregroundStyle(PanelsTheme.text)
    }
}
extension View { func panelsScreen() -> some View { modifier(PanelsScreen()) } }
