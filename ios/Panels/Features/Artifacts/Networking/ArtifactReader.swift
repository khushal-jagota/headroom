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
        // The server no longer has this artifact: its copy leaves, and the caller shows why.
        if (response as? HTTPURLResponse)?.statusCode == 404 {
            await ArtifactCopies.shared.remove(server: LocalStore.server(baseURL), id: target.id)
        }
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

    /// Saves an HTML page that just loaded from the server, off the main thread: the page,
    /// then the files in `files` that it loaded from its own owner. A page the server no
    /// longer has loses its copy. Returns whether anything changed, or nil when a read failed.
    func refreshPageCopy(_ target: ArtifactTarget, files: [URL]) async -> Bool? {
        guard let page = try? artifactURL(target) else { return nil }
        let server = LocalStore.server(baseURL)
        let copies = ArtifactCopies.shared
        let saved = await copies.open(server: server, id: target.id)
        guard let pageChanged = await refreshCopyFile(target, main: page.path, path: page.path, url: page, etag: saved?.files[page.path]?.etag),
              let entry = await copies.open(server: server, id: target.id) else { return nil }
        let prefix = "/files/\(target.owner.collection)/\(target.owner.id)/"
        let paths = Set(files.compactMap { url -> String? in
            guard url.scheme == baseURL.scheme, url.host == baseURL.host, url.port == baseURL.port,
                  url.path.hasPrefix(prefix), url.path != page.path, ArtifactCopies.keepsPageFile(url.path) else { return nil }
            return url.path
        })
        var changed: Bool? = pageChanged
        await withTaskGroup(of: Bool?.self) { group in
            for (index, path) in paths.enumerated() {
                if index >= 4, let result = await group.next() { changed = result.flatMap { r in changed.map { $0 || r } } }
                var parts = URLComponents(url: page, resolvingAgainstBaseURL: false)!
                parts.path = path; parts.query = nil; parts.fragment = nil
                guard let url = parts.url else { continue }
                let etag = entry.files[path]?.etag
                group.addTask { await self.refreshCopyFile(target, main: page.path, path: path, url: url, etag: etag) }
            }
            for await result in group { changed = result.flatMap { r in changed.map { $0 || r } } }
        }
        return changed
    }

    /// One conditional read into the copy. True when the saved file changed or left. A file
    /// the server says is over the size limit is not read at all.
    private func refreshCopyFile(_ target: ArtifactTarget, main: String, path: String, url: URL, etag: String?) async -> Bool? {
        let server = LocalStore.server(baseURL)
        let copies = ArtifactCopies.shared
        guard var request = try? self.request(url) else { return nil }
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 30
        if let etag { request.setValue(etag, forHTTPHeaderField: "If-None-Match") }
        // The headers arrive before the body, so a file too large to keep is never read.
        guard let (bytes, response) = try? await session.bytes(for: request), let http = response as? HTTPURLResponse else { return nil }
        if http.statusCode == 200, http.expectedContentLength > ArtifactCopies.fileLimit {
            bytes.task.cancel()
            // The copy no longer holds an older version of it.
            if path == main { await copies.remove(server: server, id: target.id) }
            else { await copies.drop(server: server, id: target.id, path: path) }
            return false
        }
        var data = Data()
        data.reserveCapacity(max(0, Int(http.expectedContentLength)))
        do { for try await byte in bytes { data.append(byte) } } catch { return nil }
        switch http.statusCode {
        case 304 where etag != nil: return false
        case 404:
            let had = await copies.location(server: server, id: target.id, path: path) != nil
            if path == main { await copies.remove(server: server, id: target.id) }
            else { await copies.drop(server: server, id: target.id, path: path) }
            return had
        case 200:
            if await copies.data(server: server, id: target.id, path: path) == data { return false }
            let file = FileManager.default.temporaryDirectory.appendingPathComponent("artifact-file-\(UUID().uuidString)")
            defer { try? FileManager.default.removeItem(at: file) }
            guard (try? data.write(to: file)) != nil else { return nil }
            await copies.save(server: server, id: target.id, main: main, path: path, file: file,
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

