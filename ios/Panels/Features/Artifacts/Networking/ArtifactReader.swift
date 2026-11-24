import Foundation

/// Owns a temporary file for the lifetime of its preview and share sheet.
final class ArtifactDocument: Sendable {
    let localURL: URL
    let remoteURL: URL
    let text: String?
    /// The server's version of these bytes, when it sent one.
    let etag: String?
    init(localURL: URL, remoteURL: URL, text: String?, etag: String? = nil) {
        self.localURL = localURL; self.remoteURL = remoteURL; self.text = text; self.etag = etag
    }
    deinit { try? FileManager.default.removeItem(at: localURL.deletingLastPathComponent()) }

    func hasSameBytes(as other: ArtifactDocument) -> Bool {
        if let etag, etag == other.etag { return true }
        return FileManager.default.contentsEqual(atPath: localURL.path, andPath: other.localURL.path)
    }
}

extension APIClient {
    func artifactURL(_ target: ArtifactTarget) throws -> URL {
        guard ArtifactTarget.isSafePath(target.owner.id), !target.owner.id.contains("/"), ArtifactTarget.isSafePath(target.path) else {
            throw APIError(status: 0, message: "This artifact has an invalid file path.")
        }
        let collection = target.owner.collection
        return try url("/files/\(collection)/\(target.owner.id)/\(target.path)")
    }

    /// Reads the file, and keeps a copy of a kind the phone saves.
    func artifactDocument(_ target: ArtifactTarget) async throws -> ArtifactDocument {
        try await artifactDocument(target, unlessUnchanged: nil)!
    }

    /// Reads the file again for a document already on screen. Nil means the server's file
    /// is the one shown, so nothing needs to draw again.
    func refreshedArtifactDocument(_ target: ArtifactTarget, shown: ArtifactDocument?) async throws -> ArtifactDocument? {
        guard let fresh = try await artifactDocument(target, unlessUnchanged: shown?.etag) else { return nil }
        if let shown, shown.hasSameBytes(as: fresh) { return nil }
        return fresh
    }

    /// The copy of an artifact this phone opened before, as its own temporary document.
    func savedArtifactDocument(_ target: ArtifactTarget) async -> ArtifactDocument? {
        guard ArtifactCopies.keeps(target.path), let remoteURL = try? artifactURL(target) else { return nil }
        let server = LocalStore.server(baseURL)
        guard let entry = await ArtifactCopies.shared.open(server: server, id: target.id),
              let saved = await ArtifactCopies.shared.location(server: server, id: target.id, path: entry.main) else { return nil }
        let folder = Self.temporaryFolder()
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let localURL = folder.appendingPathComponent(remoteURL.lastPathComponent)
            try FileManager.default.copyItem(at: saved, to: localURL)
            return try Self.document(localURL, remoteURL: remoteURL, kind: target.kind, etag: entry.files[entry.main]?.etag)
        } catch {
            try? FileManager.default.removeItem(at: folder)
            return nil
        }
    }

    private func artifactDocument(_ target: ArtifactTarget, unlessUnchanged etag: String?) async throws -> ArtifactDocument? {
        let remoteURL = try artifactURL(target)
        var request = try self.request(remoteURL)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 30
        if let etag { request.setValue(etag, forHTTPHeaderField: "If-None-Match") }
        let (download, response) = try await session.download(for: request)
        defer { try? FileManager.default.removeItem(at: download) }
        if etag != nil, (response as? HTTPURLResponse)?.statusCode == 304 { return nil }
        try Self.validate(response)
        try Task.checkCancellation()
        let folder = Self.temporaryFolder()
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let localURL = folder.appendingPathComponent(remoteURL.lastPathComponent)
        do {
            try FileManager.default.moveItem(at: download, to: localURL)
            let version = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "ETag")
            if ArtifactCopies.keeps(target.path) {
                await ArtifactCopies.shared.save(server: LocalStore.server(baseURL), id: target.id, main: remoteURL.path,
                                                 path: remoteURL.path, file: localURL, etag: version)
            }
            return try Self.document(localURL, remoteURL: remoteURL, kind: target.kind, etag: version)
        } catch {
            try? FileManager.default.removeItem(at: folder)
            throw error
        }
    }

    /// Brings an HTML page's copy up to date, off the main thread: the page, then the files
    /// it loads from its own owner. `files` names what the page just loaded on screen; without
    /// it, the files already saved are read again. Returns whether anything changed, or nil
    /// when a read failed, so a partial refresh never counts as fresh.
    func refreshPageCopy(_ target: ArtifactTarget, files: [URL]? = nil) async -> Bool? {
        guard let page = try? artifactURL(target) else { return nil }
        let server = LocalStore.server(baseURL)
        let copies = ArtifactCopies.shared
        let saved = await copies.open(server: server, id: target.id)
        guard let pageChanged = await refreshCopyFile(target, main: page.path, path: page.path, url: page, etag: saved?.files[page.path]?.etag) else { return nil }
        let prefix = "/files/\(target.owner.collection)/\(target.owner.id)/"
        let paths: [String]
        if let files {
            paths = files.compactMap { url in
                guard url.scheme == baseURL.scheme, url.host == baseURL.host, url.port == baseURL.port,
                      url.path.hasPrefix(prefix), url.path != page.path, ArtifactCopies.keepsPageFile(url.path) else { return nil }
                return url.path
            }
        } else { paths = saved?.files.keys.filter { $0 != page.path } ?? [] }
        let entry = await copies.open(server: server, id: target.id)
        var changed: Bool? = pageChanged
        await withTaskGroup(of: Bool?.self) { group in
            for (index, path) in Set(paths).enumerated() {
                if index >= 4, let result = await group.next() { changed = result.flatMap { r in changed.map { $0 || r } } }
                var parts = URLComponents(url: page, resolvingAgainstBaseURL: false)!
                parts.path = path; parts.query = nil; parts.fragment = nil
                guard let url = parts.url else { continue }
                let etag = entry?.files[path]?.etag
                group.addTask { await self.refreshCopyFile(target, main: page.path, path: path, url: url, etag: etag) }
            }
            for await result in group { changed = result.flatMap { r in changed.map { $0 || r } } }
        }
        return changed
    }

    /// One conditional read into the copy. True when the saved file changed or left.
    private func refreshCopyFile(_ target: ArtifactTarget, main: String, path: String, url: URL, etag: String?) async -> Bool? {
        let server = LocalStore.server(baseURL)
        guard var request = try? self.request(url) else { return nil }
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 30
        if let etag { request.setValue(etag, forHTTPHeaderField: "If-None-Match") }
        guard let (download, response) = try? await session.download(for: request), let http = response as? HTTPURLResponse else { return nil }
        defer { try? FileManager.default.removeItem(at: download) }
        switch http.statusCode {
        case 304 where etag != nil: return false
        case 404 where path != main:
            let had = await ArtifactCopies.shared.location(server: server, id: target.id, path: path) != nil
            await ArtifactCopies.shared.drop(server: server, id: target.id, path: path)
            return had
        case 200:
            let old = await ArtifactCopies.shared.data(server: server, id: target.id, path: path)
            if let old, (try? Data(contentsOf: download)) == old { return false }
            await ArtifactCopies.shared.save(server: server, id: target.id, main: main, path: path, file: download,
                                             etag: http.value(forHTTPHeaderField: "ETag"))
            return true
        default: return nil
        }
    }

    private static func temporaryFolder() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("artifact-\(UUID().uuidString)", isDirectory: true)
    }

    private static func document(_ localURL: URL, remoteURL: URL, kind: ArtifactKind, etag: String?) throws -> ArtifactDocument {
        var text: String?
        if [.markdown, .text].contains(kind) {
            let size = try localURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            // Large source files remain shareable without allocating an unbounded text view.
            if kind == .text {
                let handle = try FileHandle(forReadingFrom: localURL)
                defer { try? handle.close() }
                let prefix = try handle.read(upToCount: 256 * 1_024) ?? Data()
                text = String(decoding: prefix, as: UTF8.self)
                if size > 256 * 1_024 { text! += "\n\n[Preview limited to 256 KB. Share exports the whole file.]" }
            } else if size <= 8 * 1_024 * 1_024 { text = try? String(contentsOf: localURL, encoding: .utf8) }
        }
        return ArtifactDocument(localURL: localURL, remoteURL: remoteURL, text: text, etag: etag)
    }
}

/// Draws the saved copy first when nothing is on screen, then the file again when it
/// changed. A read that fails after a copy drew throws, and the caller keeps the copy.
@MainActor func loadArtifact(_ target: ArtifactTarget, client: APIClient, shown: ArtifactDocument?,
                             draw: (ArtifactDocument) async throws -> Void) async throws {
    var current = shown
    if current == nil, let saved = await client.savedArtifactDocument(target) {
        try await draw(saved); current = saved
    }
    if let fresh = try await client.refreshedArtifactDocument(target, shown: current) { try await draw(fresh) }
}
