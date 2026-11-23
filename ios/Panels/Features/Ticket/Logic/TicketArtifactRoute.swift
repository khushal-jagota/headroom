import Foundation

/// Adapt managed links in ticket prose to the shared artifact destination.
enum TicketArtifactRoute {
    /// `conversationOwner` is the page's own conversation, whose composer docks below.
    static func linkedDestination(_ url: URL, baseURL: URL, title: String, conversationOwner: ConversationOwner) -> AppRoute? {
        guard let resolved = URL(string: url.relativeString, relativeTo: baseURL)?.absoluteURL,
              let target = ArtifactTarget.from(resolved, baseURL: baseURL, title: title) else { return nil }
        return .artifact(owner: target.owner, path: target.path, title: target.title, dock: .conversation(conversationOwner))
    }
}
