import SwiftUI
import UIKit

enum ReviewStyle {
    static let gutter: CGFloat = 20
    static let actionHeight: CGFloat = 50
    static let enter = 0.32
    static let swipeFraction: CGFloat = 0.30
    static let title = Font.title
    static let empty = Font.title3
    static var entrance: Animation { .timingCurve(0.2, 0.7, 0.3, 1, duration: enter) }
    @MainActor static func error() { UINotificationFeedbackGenerator().notificationOccurred(.error) }
    @MainActor static func warning() { UINotificationFeedbackGenerator().notificationOccurred(.warning) }
}

/// Equal-weight review decisions with the app's accent text and surface tokens.
struct ReviewActionStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(PanelsTheme.TypeScale.body)
            .frame(maxWidth: .infinity, minHeight: ReviewStyle.actionHeight)
            .foregroundStyle(PanelsTheme.accentText)
            .background(PanelsTheme.accentSurface, in: Capsule())
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}
