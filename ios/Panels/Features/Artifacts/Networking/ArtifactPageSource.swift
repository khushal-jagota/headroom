import Foundation
import UniformTypeIdentifiers
import WebKit

/// Serves an HTML artifact to its web view from this phone. The page comes from the
/// viewer's document. A file the page loads comes from the page's saved copy first, else
/// from the server, and a file from the page's own owner then joins that copy. The page
/// keeps one origin, so its relative links, styles and scripts resolve as on the server.
@MainActor final class ArtifactPageSource: NSObject, WKURLSchemeHandler {
    static let scheme = "panels-artifact"
    private let client: APIClient
    private let target: ArtifactTarget
    private let server: String
    private let remotePage: URL
    private let ownerPrefix: String
    /// The page on screen: the saved copy, then the server's file when it changed.
    var page: ArtifactDocument?
    private var running: Set<ObjectIdentifier> = []

    init?(client: APIClient, target: ArtifactTarget) {
        guard let remotePage = try? client.artifactURL(target) else { return nil }
        self.client = client; self.target = target; self.remotePage = remotePage
        server = LocalStore.server(client.baseURL)
        ownerPrefix = "/files/\(target.owner.collection)/\(target.owner.id)/"
    }

    var pageURL: URL { local(remotePage) }

    /// The server address of a page URL, for Copy link and Share.
    func remote(_ url: URL) -> URL {
        guard url.scheme == Self.scheme, let local = URLComponents(url: url, resolvingAgainstBaseURL: false),
              var remote = URLComponents(url: client.baseURL, resolvingAgainstBaseURL: false) else { return url }
        remote.percentEncodedPath = local.percentEncodedPath
        remote.percentEncodedQuery = local.percentEncodedQuery
        remote.percentEncodedFragment = local.percentEncodedFragment
        return remote.url ?? url
    }

    private func local(_ url: URL) -> URL {
        var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        parts.scheme = Self.scheme; parts.host = "artifact"; parts.port = nil
        return parts.url!
    }

    func webView(_ webView: WKWebView, start task: any WKURLSchemeTask) {
        guard let url = task.request.url else { task.didFailWithError(URLError(.badURL)); return }
        let id = ObjectIdentifier(task)
        running.insert(id)
        let path = url.path
        let plainGet = (task.request.httpMethod ?? "GET") == "GET" && task.request.value(forHTTPHeaderField: "Range") == nil
        // Only a file the page itself loads joins its copy; a followed link is its own page.
        let pageFile = plainGet && path != remotePage.path && task.request.mainDocumentURL?.path == remotePage.path
        Task { @MainActor in
            defer { running.remove(id) }
            guard running.contains(id) else { return }
            if plainGet, path == remotePage.path, let page {
                serve(task, url: url, data: try? Data(contentsOf: page.localURL)); return
            }
            if pageFile, let saved = await ArtifactCopies.shared.data(server: server, id: target.id, path: path) {
                guard running.contains(id) else { return }
                serve(task, url: url, data: saved); return
            }
            do {
                // The server sees the page's own request: its method, body, range and headers.
                var request = try client.request(remote(url))
                request.httpMethod = task.request.httpMethod
                request.httpBody = task.request.httpBody
                for (name, value) in task.request.allHTTPHeaderFields ?? [:] where request.value(forHTTPHeaderField: name) == nil {
                    request.setValue(value, forHTTPHeaderField: name)
                }
                request.cachePolicy = .reloadIgnoringLocalCacheData
                request.timeoutInterval = 30
                let (data, response) = try await client.session.data(for: request)
                guard running.contains(id) else { return }
                guard let http = response as? HTTPURLResponse else { task.didFailWithError(URLError(.badServerResponse)); return }
                // URLSession already decoded the body and follows redirects itself.
                var headers = http.allHeaderFields as? [String: String] ?? [:]
                for name in headers.keys where ["content-encoding", "content-length", "transfer-encoding"].contains(name.lowercased()) {
                    headers[name] = nil
                }
                headers["Content-Length"] = String(data.count)
                if !headers.keys.contains(where: { $0.lowercased() == "content-type" }) { headers["Content-Type"] = Self.mimeType(path) }
                task.didReceive(HTTPURLResponse(url: url, statusCode: http.statusCode, httpVersion: "HTTP/1.1", headerFields: headers)!)
                task.didReceive(data)
                task.didFinish()
                if pageFile, http.statusCode == 200, path.hasPrefix(ownerPrefix) {
                    await keep(path: path, data: data, etag: http.value(forHTTPHeaderField: "ETag"))
                }
            } catch {
                guard running.contains(id) else { return }
                task.didFailWithError(error)
            }
        }
    }

    func webView(_ webView: WKWebView, stop task: any WKURLSchemeTask) {
        running.remove(ObjectIdentifier(task))
    }

    /// Reads the page's saved files again. True when any of them changed, or left the copy
    /// because the new version cannot be kept, so the page must load again.
    func refreshFiles() async -> Bool {
        guard let entry = await ArtifactCopies.shared.open(server: server, id: target.id) else { return false }
        let files = entry.files.filter { $0.key != entry.main }
        var changed = false
        await withTaskGroup(of: Bool.self) { group in
            for (index, file) in files.enumerated() {
                if index >= 4, let result = await group.next() { changed = changed || result }
                group.addTask { await self.refresh(path: file.key, etag: file.value.etag) }
            }
            for await result in group { changed = changed || result }
        }
        return changed
    }

    private func refresh(path: String, etag: String?) async -> Bool {
        guard var parts = URLComponents(url: remotePage, resolvingAgainstBaseURL: false) else { return false }
        parts.path = path
        guard let url = parts.url, var request = try? client.request(url) else { return false }
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 30
        if let etag { request.setValue(etag, forHTTPHeaderField: "If-None-Match") }
        guard let (data, response) = try? await client.session.data(for: request),
              let http = response as? HTTPURLResponse else { return false }
        // A file the page no longer has leaves the copy; the page reads it from the server.
        if http.statusCode == 404 { await ArtifactCopies.shared.drop(server: server, id: target.id, path: path); return false }
        guard http.statusCode == 200 else { return false }
        if await ArtifactCopies.shared.data(server: server, id: target.id, path: path) == data { return false }
        await keep(path: path, data: data, etag: http.value(forHTTPHeaderField: "ETag"))
        return true
    }

    private func keep(path: String, data: Data, etag: String?) async {
        guard !Self.isMedia(path) else { return }
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("artifact-file-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: file) }
        guard (try? data.write(to: file)) != nil else { return }
        await ArtifactCopies.shared.save(server: server, id: target.id, main: remotePage.path, path: path, file: file, etag: etag)
    }

    private func serve(_ task: any WKURLSchemeTask, url: URL, data: Data?) {
        guard let data else { task.didFailWithError(URLError(.fileDoesNotExist)); return }
        task.didReceive(HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1",
                                        headerFields: ["Content-Type": Self.mimeType(url.path), "Content-Length": String(data.count)])!)
        task.didReceive(data)
        task.didFinish()
    }

    private static func isMedia(_ path: String) -> Bool {
        guard let type = UTType(filenameExtension: (path as NSString).pathExtension) else { return false }
        return type.conforms(to: .audiovisualContent)
    }

    private static func mimeType(_ path: String) -> String {
        let type = UTType(filenameExtension: (path as NSString).pathExtension)
        let mime = type?.preferredMIMEType ?? "application/octet-stream"
        return mime.hasPrefix("text/") ? mime + "; charset=utf-8" : mime
    }
}
