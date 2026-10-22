import SwiftUI
import UIKit

enum ReviewStyle {
    static let gutter: CGFloat = 20
    static let actionHeight: CGFloat = 50
    static let enter = 0.32
    static let fadeOut = PanelsTheme.Motion.exit
    static let fadeIn = 0.20
    static let swipeOut = 0.15
    static let swipeFraction: CGFloat = 0.30
    static let title = Font.title
    static let empty = Font.title3
    static var entrance: Animation { .timingCurve(0.2, 0.7, 0.3, 1, duration: enter) }
    @MainActor static func error() { UINotificationFeedbackGenerator().notificationOccurred(.error) }
    @MainActor static func warning() { UINotificationFeedbackGenerator().notificationOccurred(.warning) }
}

/// Equal-size decisions; approval uses the web's filled accent and dark ink.
struct ReviewActionStyle: ButtonStyle {
    var approval = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(PanelsTheme.TypeScale.body)
            .frame(maxWidth: .infinity, minHeight: ReviewStyle.actionHeight)
            .foregroundStyle(approval ? PanelsTheme.ink : PanelsTheme.strong)
            .background(approval ? PanelsTheme.accent : PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
            .overlay(RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg).stroke(approval ? Color.clear : PanelsTheme.border, lineWidth: 1))
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}
