import Foundation
import UniformTypeIdentifiers
import WebKit

/// Serves an HTML artifact's saved copy to its web view, only while the page opened from
/// that copy offline. It serves the page and the files saved with it, and forwards nothing:
/// any other request fails quietly. Each server has its own host, so pages from different
/// servers never share storage.
@MainActor final class ArtifactPageSource: NSObject, WKURLSchemeHandler {
    static let scheme = "panels-artifact"
    private let client: APIClient
    private let target: ArtifactTarget
    private let server: String
    private let remotePage: URL
    private var running: Set<ObjectIdentifier> = []

    init?(client: APIClient, target: ArtifactTarget) {
        guard let remotePage = try? client.artifactURL(target) else { return nil }
        self.client = client; self.target = target; self.remotePage = remotePage
        server = LocalStore.server(client.baseURL)
    }

    var pageURL: URL {
        var parts = URLComponents(url: remotePage, resolvingAgainstBaseURL: false)!
        parts.scheme = Self.scheme; parts.port = nil
        parts.host = "s" + ArtifactCopies.hash(server).prefix(16)
        return parts.url!
    }

    /// The server address of a page URL, for Copy link and Share.
    func remote(_ url: URL) -> URL {
        guard url.scheme == Self.scheme, let local = URLComponents(url: url, resolvingAgainstBaseURL: false),
              var remote = URLComponents(url: client.baseURL, resolvingAgainstBaseURL: false) else { return url }
        remote.percentEncodedPath = local.percentEncodedPath
        remote.percentEncodedQuery = local.percentEncodedQuery
        remote.percentEncodedFragment = local.percentEncodedFragment
        return remote.url ?? url
    }

    func webView(_ webView: WKWebView, start task: any WKURLSchemeTask) {
        guard let url = task.request.url, (task.request.httpMethod ?? "GET") == "GET" else {
            task.didFailWithError(URLError(.notConnectedToInternet)); return
        }
        let id = ObjectIdentifier(task)
        running.insert(id)
        Task { @MainActor in
            defer { running.remove(id) }
            // The copy store reads the file on its own executor, never on the main thread.
            let data = await ArtifactCopies.shared.data(server: server, id: target.id, path: url.path)
            guard running.contains(id) else { return }
            guard let data else { task.didFailWithError(URLError(.notConnectedToInternet)); return }
            let type = UTType(filenameExtension: (url.path as NSString).pathExtension)?.preferredMIMEType ?? "application/octet-stream"
            task.didReceive(HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: [
                "Content-Type": type.hasPrefix("text/") ? type + "; charset=utf-8" : type,
                "Content-Length": String(data.count),
            ])!)
            task.didReceive(data)
            task.didFinish()
        }
    }

    func webView(_ webView: WKWebView, stop task: any WKURLSchemeTask) {
        running.remove(ObjectIdentifier(task))
    }
}
