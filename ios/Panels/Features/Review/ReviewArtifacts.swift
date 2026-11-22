import SwiftUI

/// Proposals, messages and conversations share one route for managed file links:
/// a tap opens the in-app viewer with the caller's dock, and other links go to the system.
struct ReviewArtifactLinks: ViewModifier {
    /// What the viewer docks below the artifact, from the place that shows the link.
    let dock: ArtifactDock
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
            let route = AppRoute.artifact(target, dock: dock)
            if let textEntry { textEntry.endEditingBeforeNavigation { openRoute(route) } }
            else { openRoute(route) }
        }
        else { openURL(url) }
    }
}

extension View {
    func reviewArtifactLinks(dock: ArtifactDock) -> some View { modifier(ReviewArtifactLinks(dock: dock)) }
}
