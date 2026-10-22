import Foundation

enum TicketArtifactRoute {
    static func destination(_ entry: ArtifactEntry, owner: ArtifactOwner) -> AppRoute {
        if let directory = entry.directory { return .artifacts(owner: owner, directory: directory) }
        // Catalogs also include links to another ticket's or manager's files.
        if let href = entry.href, let url = URLComponents(string: href) {
            let parts = url.path.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
            if parts.count >= 4, parts[0] == "files",
               let kind: ArtifactOwner.Kind = parts[1] == "tickets" ? .ticket : parts[1] == "managers" ? .manager : nil {
                return .artifact(owner: ArtifactOwner(kind: kind, id: parts[2]),
                                 path: parts.dropFirst(3).joined(separator: "/"), title: entry.label)
            }
        }
        return .artifact(owner: owner, path: entry.path, title: entry.label)
    }
}
