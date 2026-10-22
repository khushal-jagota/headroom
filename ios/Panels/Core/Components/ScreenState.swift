import SwiftUI

struct ScreenPlaceholder: View {
    let title: String
    let symbol: String
    var body: some View {
        ContentUnavailableView(title, systemImage: symbol, description: Text("This screen is being prepared."))
            .panelsScreen().navigationTitle(title)
    }
}

/// A refresh failure never replaces previously loaded content.
struct LoadErrorView: View {
    let message: String
    let retry: () async -> Void
    var body: some View {
        HStack(alignment: .center, spacing: PanelsTheme.Space.md) {
            Text(message).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
            ScreenRetryButton(retry: retry)
        }.padding(.vertical, PanelsTheme.Space.sm)
    }
}

private struct ScreenRetryButton: View {
    let retry: () async -> Void
    @State private var retrying = false
    var body: some View {
        Button {
            retrying = true
            Task { await retry(); retrying = false }
        } label: {
            Text(retrying ? "Retrying…" : "Try again").frame(minHeight: 44)
        }.disabled(retrying)
    }
}

struct ScreenLoadingView: View {
    let title: String
    var body: some View {
        ProgressView("Loading \(title)…")
            .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .allowsHitTesting(false)
    }
}

private struct ScreenLoadState: ViewModifier {
    let title: String
    let hasContent: Bool
    let error: String?
    let retry: () async -> Void

    func body(content: Content) -> some View {
        content
            .overlay {
                if !hasContent {
                    if let error {
                        ContentUnavailableView {
                            Label("Couldn’t load \(title)", systemImage: "exclamationmark.circle")
                        } description: {
                            Text(error)
                        } actions: {
                            ScreenRetryButton(retry: retry).buttonStyle(.bordered)
                        }
                    } else { ScreenLoadingView(title: title) }
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if hasContent, let error {
                    LoadErrorView(message: "Couldn’t refresh. Showing the last loaded content. \(error)", retry: retry)
                        .padding(.horizontal, PanelsTheme.Space.lg)
                        .background(PanelsTheme.surface)
                }
            }
    }
}

extension View {
    func screenLoadState(_ title: String, hasContent: Bool, error: String?, retry: @escaping () async -> Void) -> some View {
        modifier(ScreenLoadState(title: title, hasContent: hasContent, error: error, retry: retry))
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
