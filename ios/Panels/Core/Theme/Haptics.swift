import UIKit

/// Tabs select and navigation impacts lightly by owner preference. Writes acknowledge their result.
@MainActor enum Haptics {
    static func light() { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
    static func success() { UINotificationFeedbackGenerator().notificationOccurred(.success) }
    static func error() { UINotificationFeedbackGenerator().notificationOccurred(.error) }
    static func selection() { UISelectionFeedbackGenerator().selectionChanged() }
}
