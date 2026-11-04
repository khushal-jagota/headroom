import SwiftUI
import UIKit

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

        // Web role weights; keep the native sizes and Dynamic Type curves.
        // TicketRoute / ManagerHeader: 600. Review title and empty state: 500.
        static let detailTitle = Font.title.weight(.semibold)
        static let reviewTitle = Font.title.weight(.medium)
        static let reviewCompletion = Font.title.weight(.medium)
        static let captureTitle = Font.title3.weight(.medium)
        static let itemTitle = Font.body.weight(.medium)
        static let primaryAction = Font.body.weight(.semibold)
        static let reviewAction = Font.body.weight(.medium)
        static let sectionLabel = Font.footnote.weight(.semibold)
        static let reviewEyebrow = Font.caption2.weight(.semibold)
        static let savedDecisionTitle = Font.subheadline.weight(.semibold)
        static let latestAction = Font.footnote.weight(.semibold)
        static let artifactTitle = Font.subheadline
        static let artifactLink = Font.subheadline.weight(.semibold)
        static let feedbackText = Font.body.weight(.medium)
        static let feedbackGroupTitle = Font.title3.weight(.medium)
        static let configurationHeading = Font.title3.weight(.medium)
        static let allowanceLabel = Font.body.weight(.medium)
        static let allowanceValue = Font.body.weight(.semibold)
        // The renderer's inline body font has always determined heading size.
        // Preserve that size while matching the web's h2/h3 600 and other headings 700.
        static func markdownHeading(_ level: Int) -> Font {
            Font.body.weight(level == 2 || level == 3 ? .semibold : .bold)
        }

        enum SectionRole {
            case navigation, configuration, monitor, usage, notification
            var weight: Font.Weight {
                switch self {
                case .navigation, .monitor: .regular
                case .configuration, .usage, .notification: .medium
                }
            }
        }

        enum NavigationTitleRole {
            case page, usage
            var weight: UIFont.Weight {
                switch self {
                case .page: .medium // Ideas, Feedback, VPS and configuration headings: 500.
                case .usage: .semibold // UsageHeader h1: 600.
                }
            }
        }
    }
    enum Spinner {
        static let stroke: CGFloat = 2
        static let trackOpacity = 0.3
        static let compact: CGFloat = 14
        static let small: CGFloat = 16
        static let regular: CGFloat = 20
    }
    enum Motion {
        static let loopSpin = 0.9
        static let exit = 0.12
        static let fast = 0.09
        static let base = 0.15
        static let slow = 0.24
        static let enter = 0.32
        static let fade = Animation.timingCurve(0.2, 0.7, 0.3, 1, duration: base)
        static let entrance = Animation.timingCurve(0.2, 0.7, 0.3, 1, duration: enter)
        static let disclosure = Animation.smooth(duration: slow, extraBounce: 0)
    }
}

private extension Color {
    init(hex: UInt32) { self.init(.sRGB, red: Double((hex >> 16) & 255) / 255, green: Double((hex >> 8) & 255) / 255, blue: Double(hex & 255) / 255, opacity: 1) }
}

struct PanelsScreen: ViewModifier {
    func body(content: Content) -> some View {
        content.scrollContentBackground(.hidden)
            .textCase(nil)
            .background(PanelsTheme.surface)
            .foregroundStyle(PanelsTheme.text)
    }
}
extension View { func panelsScreen() -> some View { modifier(PanelsScreen()) } }

/// Preserve the approved default size while following the user's text size.
private struct PanelsScaledFont: ViewModifier {
    @ScaledMetric private var size: CGFloat
    let weight: Font.Weight

    init(size: CGFloat, relativeTo style: Font.TextStyle, weight: Font.Weight) {
        _size = ScaledMetric(wrappedValue: size, relativeTo: style)
        self.weight = weight
    }

    func body(content: Content) -> some View {
        content.font(.system(size: size, weight: weight))
    }
}

extension View {
    func panelsScaledFont(size: CGFloat, relativeTo style: Font.TextStyle = .body, weight: Font.Weight = .regular) -> some View {
        modifier(PanelsScaledFont(size: size, relativeTo: style, weight: weight))
    }

}

/// Set fonts on the owning navigation item, so pushing or popping another page
/// restores that page's own typography. Native title size, position and collapse stay native.
private struct PanelsNavigationTypography: UIViewControllerRepresentable {
    let role: PanelsTheme.TypeScale.NavigationTitleRole

    func makeUIViewController(context: Context) -> Controller { Controller(role: role) }
    func updateUIViewController(_ controller: Controller, context: Context) {
        controller.role = role
        controller.applyFonts()
    }

    final class Controller: UIViewController {
        var role: PanelsTheme.TypeScale.NavigationTitleRole
        init(role: PanelsTheme.TypeScale.NavigationTitleRole) {
            self.role = role
            super.init(nibName: nil, bundle: nil)
            registerForTraitChanges([UITraitPreferredContentSizeCategory.self]) { (controller: Controller, _: UITraitCollection) in
                controller.applyFonts()
            }
        }
        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
        override func loadView() { view = UIView(); view.isUserInteractionEnabled = false }
        override func viewWillAppear(_ animated: Bool) { super.viewWillAppear(animated); applyFonts() }
        override func didMove(toParent parent: UIViewController?) { super.didMove(toParent: parent); applyFonts() }

        func applyFonts() {
            var owner: UIViewController = self
            while let parent = owner.parent, !(parent is UINavigationController) { owner = parent }
            guard let bar = owner.navigationController?.navigationBar else { return }
            func appearance(_ original: UINavigationBarAppearance) -> UINavigationBarAppearance {
                let copy = original.copy() as! UINavigationBarAppearance
                func font(_ style: UIFont.TextStyle) -> UIFont {
                    let base = UIFont.preferredFont(forTextStyle: style, compatibleWith: traitCollection)
                    return UIFont.systemFont(ofSize: base.pointSize, weight: role.weight)
                }
                copy.titleTextAttributes[.font] = font(.headline)
                copy.largeTitleTextAttributes[.font] = font(.largeTitle)
                return copy
            }
            let item = owner.navigationItem
            item.standardAppearance = appearance(bar.standardAppearance)
            item.scrollEdgeAppearance = appearance(bar.scrollEdgeAppearance ?? bar.standardAppearance)
            item.compactAppearance = appearance(bar.compactAppearance ?? bar.standardAppearance)
            item.compactScrollEdgeAppearance = appearance(bar.compactScrollEdgeAppearance ?? bar.standardAppearance)
        }
    }
}

extension View {
    func panelsProjectLabel() -> some View {
        panelsScaledFont(size: 13, weight: .medium)
    }

    func panelsSectionHeading(_ role: PanelsTheme.TypeScale.SectionRole) -> some View {
        fontWeight(role.weight)
    }

    func panelsNavigationTitle(_ title: String, role: PanelsTheme.TypeScale.NavigationTitleRole = .page) -> some View {
        navigationTitle(title).background(PanelsNavigationTypography(role: role))
    }
}
