import Foundation

/// A managed file's real owner can differ from the catalog it was linked from.
struct ArtifactTarget: Hashable, Identifiable, Sendable {
    let owner: ArtifactOwner
    let path: String
    let title: String
    var id: String { "\(owner.kind.rawValue):\(owner.id):\(path)" }

    static func isSafePath(_ path: String) -> Bool {
        !path.isEmpty && !path.contains("\\") &&
        path.range(of: "%25|%2e|%2f|%5c", options: [.regularExpression, .caseInsensitive]) == nil &&
        !path.split(separator: "/", omittingEmptySubsequences: false).contains { $0.isEmpty || $0 == "." || $0 == ".." }
    }

    static func from(_ url: URL, baseURL: URL, title: String? = nil) -> Self? {
        guard url.scheme == baseURL.scheme, url.host == baseURL.host, url.port == baseURL.port else { return nil }
        if let fragment = URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedFragment,
           let preview = URLComponents(string: fragment), preview.path.trimmingCharacters(in: CharacterSet(charactersIn: "/")) == "preview" {
            let query = preview.queryItems ?? []
            func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
            let source = value("source")
            let kind: ArtifactOwner.Kind = source == "ticket" ? .ticket : .manager
            let ownerKey = source == "ticket" ? "ticket" : source == "sprint-item" ? "item" : "manager"
            guard ["ticket", "manager", "sprint-item"].contains(source ?? ""),
                  let id = value(ownerKey), isSafePath(id), !id.contains("/"),
                  let path = value("path"), isSafePath(path) else { return nil }
            return Self(owner: .init(kind: kind, id: id), path: path, title: title ?? (path as NSString).lastPathComponent)
        }
        let parts = url.path.split(separator: "/", omittingEmptySubsequences: false)
        guard parts.count >= 5, parts[1] == "files",
              let kind = ["tickets": ArtifactOwner.Kind.ticket, "managers": .manager, "sprint-items": .manager][String(parts[2])] else { return nil }
        let id = String(parts[3])
        let path = parts.dropFirst(4).joined(separator: "/")
        guard isSafePath(id), !id.contains("/"), isSafePath(path) else { return nil }
        return Self(owner: .init(kind: kind, id: id), path: path, title: title ?? url.lastPathComponent)
    }

    static func from(entry: ArtifactEntry, owner: ArtifactOwner, baseURL: URL) -> Self? {
        if let href = entry.href {
            guard let url = URL(string: href, relativeTo: baseURL)?.absoluteURL else { return nil }
            return from(url, baseURL: baseURL, title: entry.label)
        }
        return nil
    }

    var kind: ArtifactKind { ArtifactKind(path: path) }
}

enum ArtifactKind: Equatable, Sendable {
    case html, markdown, image, text, file
    init(path: String) {
        switch (path as NSString).pathExtension.lowercased() {
        case "html", "htm": self = .html
        case "md", "markdown": self = .markdown
        case "png", "jpg", "jpeg", "heic", "heif", "webp", "bmp", "avif": self = .image
        case "txt", "json", "jsonl", "csv", "tsv", "log", "yaml", "yml", "swift", "py", "css", "js": self = .text
        default: self = .file
        }
    }
    var symbol: String {
        switch self {
        case .html: "globe"
        case .markdown, .text: "doc.text"
        case .image: "photo"
        case .file: "doc"
        }
    }
}
