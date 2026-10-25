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

/// Still blocks follow the content layout; quick reads never flash a placeholder.
struct ScreenLoadingView: View {
    let title: String
    @State private var visible = false
    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            RoundedRectangle(cornerRadius: 4).frame(width: 160, height: 26)
            ForEach(0..<(title == "review" ? 2 : 4), id: \.self) { _ in
                VStack(alignment: .leading, spacing: 12) {
                    RoundedRectangle(cornerRadius: 4).frame(height: 18)
                    RoundedRectangle(cornerRadius: 4).frame(maxWidth: 240).frame(height: title == "review" ? 88 : 14)
                }
            }
            Spacer()
        }.foregroundStyle(PanelsTheme.raised).padding(24)
            .opacity(visible ? 1 : 0).allowsHitTesting(false)
            .accessibilityElement(children: .ignore).accessibilityLabel("Loading \(title)")
            .task { do { try await Task.sleep(for: .milliseconds(300)); visible = true } catch {} }
    }
}

private struct ScreenLoadState: ViewModifier {
    let title: String
    let hasContent: Bool
    let error: String?
    let retry: () async -> Void
    let showsRefreshError: Bool

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
                if showsRefreshError, hasContent, let error {
                    LoadErrorView(message: "Couldn’t refresh. Showing the last loaded content. \(error)", retry: retry)
                        .padding(.horizontal, PanelsTheme.Space.lg)
                        .background(PanelsTheme.surface)
                }
            }
    }
}

extension View {
    func screenLoadState(_ title: String, hasContent: Bool, error: String?, showsRefreshError: Bool = true, retry: @escaping () async -> Void) -> some View {
        modifier(ScreenLoadState(title: title, hasContent: hasContent, error: error, retry: retry, showsRefreshError: showsRefreshError))
    }
}

struct ConversationSignalMark: View {
    let signal: ConversationSignal?
    var body: some View {
        Group {
            if signal?.messageForOwner == true {
                Circle().fill(PanelsTheme.accent).frame(width: 8, height: 8).accessibilityLabel("Unread message")
            } else if signal?.working == true {
                RingSpinner(size: .compact, color: PanelsTheme.faint)
                    .accessibilityLabel("Working")
            }
        }.frame(width: 14, height: 14, alignment: .trailing)
    }
}

extension View {
    /// A row is one stop; its visual marks are announced as state, not separate controls.
    func conversationAccessibility(label: String, signal: ConversationSignal?) -> some View {
        accessibilityElement(children: .ignore)
            .accessibilityLabel(label)
            .accessibilityValue(signal?.messageForOwner == true ? "Unread message" : signal?.working == true ? "Working" : "")
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
