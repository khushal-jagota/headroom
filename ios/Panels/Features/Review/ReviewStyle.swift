import SwiftUI
import CoreText
import UIKit

enum ReviewStyle {
    static let gutter: CGFloat = 20
    static let actionHeight: CGFloat = 50
    static let enter = 0.32
    static let swipeFraction: CGFloat = 0.30
    static let serifName: String = {
        guard let url = Bundle.main.url(forResource: "Newsreader", withExtension: "ttf") else { return "Georgia" }
        CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        let descriptors = CTFontManagerCreateFontDescriptorsFromURL(url as CFURL) as? [CTFontDescriptor]
        if let descriptor = descriptors?.first, let name = CTFontDescriptorCopyAttribute(descriptor, kCTFontNameAttribute) as? String { return name }
        return "Georgia"
    }()
    static let title = Font.custom(serifName, size: 28, relativeTo: .title)
    static let empty = Font.custom(serifName, size: 21, relativeTo: .title3)
    static var entrance: Animation { .timingCurve(0.2, 0.7, 0.3, 1, duration: enter) }
    @MainActor static func error() { UINotificationFeedbackGenerator().notificationOccurred(.error) }
    @MainActor static func warning() { UINotificationFeedbackGenerator().notificationOccurred(.warning) }
}
