import SwiftUI
import WebKit

/// As on the web, scripts run in an opaque-origin sandbox, never in the Panels origin.
struct ArtifactWebView: UIViewRepresentable {
    let source: String
    let url: URL
    let openLink: (URL) -> Void
    let failed: (String) -> Void
    let ready: () -> Void
    let reload: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(openLink: openLink, failed: failed, ready: ready, reload: reload) }
    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.isOpaque = false
        view.backgroundColor = .clear
        view.scrollView.backgroundColor = .clear
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.isInspectable = false
        view.scrollView.alwaysBounceVertical = true
        let refresh = UIRefreshControl()
        refresh.addTarget(context.coordinator, action: #selector(Coordinator.refresh(_:)), for: .valueChanged)
        view.scrollView.refreshControl = refresh
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) {
        guard context.coordinator.source != source else { return }
        context.coordinator.source = source
        // JSON encoding keeps arbitrary artifact source out of the host's script syntax.
        let sourceJSON = Self.literal(source)
        let baseJSON = Self.literal(url.absoluteString)
        let html = """
        <!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
        <meta http-equiv="Content-Security-Policy" content="default-src https: http: data: blob: 'unsafe-inline' 'unsafe-eval'; connect-src 'none'; form-action 'none'; object-src 'none'">
        <style>html,body{margin:0;width:100%;height:100%;background:#151515}iframe{border:0;width:100%;height:100%;display:block}</style>
        </head><body><iframe id="document" sandbox="allow-scripts" title="Artifact"></iframe>
        <script>
        const documentSource = new DOMParser().parseFromString(\(sourceJSON), 'text/html');
        documentSource.querySelectorAll('base').forEach(node => node.remove());
        const base = documentSource.createElement('base'); base.href = \(baseJSON);
        documentSource.head.prepend(base);
        const links = documentSource.createElement('script');
        links.textContent = `document.addEventListener('click', event => {
          const anchor = event.target.closest?.('a[href]');
          if (!anchor) return;
          anchor.removeAttribute('target');
          const href = anchor.getAttribute('href');
          if (href.startsWith('#') && !href.startsWith('#/preview?')) {
            event.preventDefault();
            try { document.getElementById(decodeURIComponent(href.slice(1)))?.scrollIntoView(); } catch (_) {}
          }
        }, true);`;
        documentSource.body.append(links);
        document.getElementById('document').srcdoc = '<!doctype html>' + documentSource.documentElement.outerHTML;
        </script></body></html>
        """
        view.loadHTMLString(html, baseURL: nil)
    }
    private static func literal(_ text: String) -> String {
        let data = try! JSONEncoder().encode(text)
        return String(decoding: data, as: UTF8.self).replacingOccurrences(of: "<", with: "\\u003c")
    }
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        var source: String?
        let openLink: (URL) -> Void
        let failed: (String) -> Void
        let ready: () -> Void
        let reload: () -> Void
        @objc func refresh(_ control: UIRefreshControl) { reload(); control.endRefreshing() }
        init(openLink: @escaping (URL) -> Void, failed: @escaping (String) -> Void, ready: @escaping () -> Void, reload: @escaping () -> Void) { self.openLink = openLink; self.failed = failed; self.ready = ready; self.reload = reload }
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
            if action.navigationType == .linkActivated, let url = action.request.url {
                if url.scheme == "about" { return .allow }
                openLink(url); return .cancel
            }
            // The document may run local interactions; navigation cannot turn it into an API client.
            if let scheme = action.request.url?.scheme, ["http", "https"].contains(scheme) {
                return .cancel
            } else { return .allow }
        }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            if (error as NSError).code != NSURLErrorCancelled { failed(error.localizedDescription) }
        }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { ready() }
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { failed("The preview stopped. Try loading it again.") }
    }
}
