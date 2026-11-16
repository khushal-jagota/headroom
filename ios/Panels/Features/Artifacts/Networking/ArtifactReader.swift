import Foundation

/// Owns a temporary file for the lifetime of its preview and share sheet.
final class ArtifactDocument: Sendable {
    let localURL: URL
    let remoteURL: URL
    let text: String?
    init(localURL: URL, remoteURL: URL, text: String?) {
        self.localURL = localURL; self.remoteURL = remoteURL; self.text = text
    }
    deinit { try? FileManager.default.removeItem(at: localURL.deletingLastPathComponent()) }
}

extension APIClient {
    func artifactURL(_ target: ArtifactTarget) throws -> URL {
        guard ArtifactTarget.isSafePath(target.owner.id), !target.owner.id.contains("/"), ArtifactTarget.isSafePath(target.path) else {
            throw APIError(status: 0, message: "This artifact has an invalid file path.")
        }
        let collection = target.owner.collection
        return try url("/files/\(collection)/\(target.owner.id)/\(target.path)")
    }

    struct ArtifactOrigin: Decodable, Sendable { let origin: String? }

    func artifactPreviewURL(_ target: ArtifactTarget) async throws -> URL? {
        let result: ArtifactOrigin = try await get("/api/artifact-origin")
        guard let origin = result.origin else { return nil }
        guard let base = URL(string: origin),
              let scheme = base.scheme?.lowercased(), ["http", "https"].contains(scheme),
              base.host != nil, base.user == nil, base.password == nil,
              base.query == nil, base.fragment == nil, ["", "/"].contains(base.path),
              !(base.scheme?.lowercased() == baseURL.scheme?.lowercased() &&
                base.host?.lowercased() == baseURL.host?.lowercased() &&
                (base.port ?? (base.scheme?.lowercased() == "https" ? 443 : 80)) ==
                (baseURL.port ?? (baseURL.scheme?.lowercased() == "https" ? 443 : 80))) else {
            throw APIError(status: 0, message: "The separate artifact origin is unavailable.")
        }
        let source = try artifactURL(target)
        guard var destination = URLComponents(url: base, resolvingAgainstBaseURL: false),
              let original = URLComponents(url: source, resolvingAgainstBaseURL: false) else {
            throw APIError(status: 0, message: "The artifact URL is invalid.")
        }
        destination.percentEncodedPath = original.percentEncodedPath
        guard let url = destination.url else { throw APIError(status: 0, message: "The artifact URL is invalid.") }
        return url
    }

    func artifactDocument(_ target: ArtifactTarget) async throws -> ArtifactDocument {
        let remoteURL = try artifactURL(target)
        var request = try self.request(remoteURL)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 30
        let (download, response) = try await session.download(for: request)
        defer { try? FileManager.default.removeItem(at: download) }
        try Self.validate(response)
        try Task.checkCancellation()
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("artifact-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let localURL = folder.appendingPathComponent(remoteURL.lastPathComponent)
        do {
            try FileManager.default.moveItem(at: download, to: localURL)
            var text: String?
            if [.html, .markdown, .text].contains(target.kind) {
                let size = try localURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                // Large source files remain shareable without allocating an unbounded text view.
                if target.kind == .text {
                    let handle = try FileHandle(forReadingFrom: localURL)
                    defer { try? handle.close() }
                    let prefix = try handle.read(upToCount: 256 * 1_024) ?? Data()
                    text = String(decoding: prefix, as: UTF8.self)
                    if size > 256 * 1_024 { text! += "\n\n[Preview limited to 256 KB. Share exports the whole file.]" }
                } else if size <= 8 * 1_024 * 1_024 { text = try? String(contentsOf: localURL, encoding: .utf8) }
            }
            return ArtifactDocument(localURL: localURL, remoteURL: remoteURL, text: text)
        } catch {
            try? FileManager.default.removeItem(at: folder)
            throw error
        }
    }
}
