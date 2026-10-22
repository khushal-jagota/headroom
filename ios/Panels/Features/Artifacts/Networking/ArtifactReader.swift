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
        let collection = target.owner.kind == .ticket ? "tickets" : "managers"
        return try url("/files/\(collection)/\(target.owner.id)/\(target.path)")
    }

    func artifactDocument(_ target: ArtifactTarget) async throws -> ArtifactDocument {
        let remoteURL = try artifactURL(target)
        var request = URLRequest(url: remoteURL)
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
