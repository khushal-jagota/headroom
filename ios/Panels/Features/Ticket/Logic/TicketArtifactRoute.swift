import Foundation

/// Adapt managed links in ticket prose to the shared artifact destination.
enum TicketArtifactRoute {
    static func linkedDestination(_ url: URL, baseURL: URL, title: String) -> AppRoute? {
        guard let resolved = URL(string: url.relativeString, relativeTo: baseURL)?.absoluteURL,
              let target = ArtifactTarget.from(resolved, baseURL: baseURL, title: title) else { return nil }
        return .artifact(owner: target.owner, path: target.path, title: target.title)
    }
}
