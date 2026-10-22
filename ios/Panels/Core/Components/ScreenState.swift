import SwiftUI

struct ScreenPlaceholder: View {
    let title: String
    let symbol: String
    var body: some View {
        ContentUnavailableView(title, systemImage: symbol, description: Text("This screen is being prepared."))
            .panelsScreen().navigationTitle(title)
    }
}

struct LoadErrorView: View {
    let message: String
    let retry: () async -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
            Text(message).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
            Button("Try again") { Task { await retry() } }
        }.padding(.vertical, PanelsTheme.Space.sm)
    }
}

struct ConversationPlaceholder: View {
    // Integration slot only. The conversation worker owns the eventual design.
    var body: some View {
        Label("Conversation", systemImage: "bubble.left.and.bubble.right")
            .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(PanelsTheme.Space.lg)
            .background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
            .accessibilityLabel("Conversation surface awaiting design")
    }
}

extension View {
    /// A row is one stop; its visual marks are announced as state, not separate controls.
    func conversationAccessibility(label: String, signal: ConversationSignal?) -> some View {
        accessibilityElement(children: .ignore)
            .accessibilityLabel(label)
            .accessibilityValue([
                signal?.messageForOwner == true ? "Unread message" : nil,
                signal?.working == true ? "Working" : nil
            ].compactMap { $0 }.joined(separator: ", "))
    }
}

/// Segments cannot wrap their titles; a menu keeps every choice readable at larger sizes.
private struct AccessibleSegmentedPicker: ViewModifier {
    @Environment(\.dynamicTypeSize) private var typeSize

    @ViewBuilder func body(content: Content) -> some View {
        if typeSize.isAccessibilitySize {
            content.pickerStyle(.menu)
        } else {
            content.pickerStyle(.segmented)
        }
    }
}

extension View {
    func accessibleSegmentedPicker() -> some View { modifier(AccessibleSegmentedPicker()) }
}
