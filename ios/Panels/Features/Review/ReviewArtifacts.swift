import SwiftUI

/// Proposals and messages share one route for managed file links:
/// a tap opens the in-app viewer, and other links go to the system.
struct ReviewArtifactLinks: ViewModifier {
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @Environment(\.openRoute) private var openRoute
    @Environment(\.screenTextEntry) private var textEntry

    func body(content: Content) -> some View {
        content
            .environment(\.openURL, OpenURLAction { url in follow(url); return .handled })
    }
    private func follow(_ url: URL) {
        if ArtifactTarget.isRetiredGoalURL(url, baseURL: store.baseURL) { return }
        if let target = ArtifactTarget.from(url, baseURL: store.baseURL) {
            let route = AppRoute.artifact(target, showsConversation: false)
            if let textEntry { textEntry.endEditingBeforeNavigation { openRoute(route) } }
            else { openRoute(route) }
        }
        else { openURL(url) }
    }
}

extension View {
    func reviewArtifactLinks() -> some View { modifier(ReviewArtifactLinks()) }
}
