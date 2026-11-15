import SwiftUI

/// Proposals and messages share one route for managed file links:
/// a tap opens the in-app viewer, and other links go to the system.
struct ReviewArtifactLinks: ViewModifier {
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var selected: ArtifactTarget?

    func body(content: Content) -> some View {
        content
            .environment(\.openURL, OpenURLAction { url in follow(url); return .handled })
            .navigationDestination(item: $selected) { target in
                ArtifactViewerView(owner: target.owner, path: target.path, title: target.title, showsConversation: false)
            }
    }
    private func follow(_ url: URL) {
        if ArtifactTarget.isRetiredGoalURL(url, baseURL: store.baseURL) { return }
        if let target = ArtifactTarget.from(url, baseURL: store.baseURL) { selected = target }
        else { openURL(url) }
    }
}

extension View {
    func reviewArtifactLinks() -> some View { modifier(ReviewArtifactLinks()) }
}
