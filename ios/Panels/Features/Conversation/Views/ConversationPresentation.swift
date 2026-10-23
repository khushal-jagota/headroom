import SwiftUI

/// A page supplies its conversation to the app-root overlay. Keeping it above
/// navigation lets the thread fill the screen without changing the page below.
struct ConversationPresentation {
    let owner: String
    let content: AnyView
}

struct ConversationPresentationPreference: PreferenceKey {
    static var defaultValue: ConversationPresentation? { nil }

    static func reduce(value: inout ConversationPresentation?, nextValue: () -> ConversationPresentation?) {
        value = nextValue() ?? value
    }
}

extension View {
    func conversationPresentationRoot() -> some View {
        overlayPreferenceValue(ConversationPresentationPreference.self) { presentation in
            if let presentation {
                presentation.content.id(presentation.owner)
            }
        }
    }
}
