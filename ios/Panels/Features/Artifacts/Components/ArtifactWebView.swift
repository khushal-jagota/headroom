import SwiftUI
import WebKit

/// Loads managed HTML from the separate artifact origin, with ordinary browser navigation.
struct ArtifactWebView: UIViewRepresentable {
    let url: URL
    var desktopPreview = false
    let failed: (String) -> Void
    let ready: () -> Void
    let reload: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(failed: failed, ready: ready, reload: reload) }
    func makeUIView(context: Context) -> ArtifactWebContainer {
        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = true
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.isOpaque = false
        view.backgroundColor = .clear
        view.scrollView.backgroundColor = .clear
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.scrollView.alwaysBounceVertical = true
        let refresh = UIRefreshControl()
        refresh.addTarget(context.coordinator, action: #selector(Coordinator.refresh(_:)), for: .valueChanged)
        view.scrollView.refreshControl = refresh
        return ArtifactWebContainer(webView: view)
    }
    func updateUIView(_ container: ArtifactWebContainer, context: Context) {
        container.desktopPreview = desktopPreview
        if context.coordinator.url != url {
            context.coordinator.url = url
            container.webView.load(URLRequest(url: url))
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
        var url: URL?
        private var downloads: [ObjectIdentifier: URL] = [:]
        private weak var hostView: WKWebView?
        let failed: (String) -> Void
        let ready: () -> Void
        let reload: () -> Void
        @objc func refresh(_ control: UIRefreshControl) { reload(); control.endRefreshing() }
        init(failed: @escaping (String) -> Void, ready: @escaping () -> Void, reload: @escaping () -> Void) {
            self.failed = failed; self.ready = ready; self.reload = reload
        }
        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            decisionHandler(navigationAction.shouldPerformDownload ? .download : .allow)
        }
        func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                     decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
        }
        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
            hostView = webView
            download.delegate = self
        }
        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
            hostView = webView
            download.delegate = self
        }
        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                      suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent("artifact-download-\(UUID().uuidString)", isDirectory: true)
            do {
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                let destination = folder.appendingPathComponent((suggestedFilename as NSString).lastPathComponent)
                downloads[ObjectIdentifier(download)] = destination
                completionHandler(destination)
            } catch { failed(error.localizedDescription); completionHandler(nil) }
        }
        func downloadDidFinish(_ download: WKDownload) {
            guard let url = downloads.removeValue(forKey: ObjectIdentifier(download)),
                  let hostView, let presenter = presenter(for: hostView) else { return }
            let share = UIActivityViewController(activityItems: [url], applicationActivities: nil)
            share.popoverPresentationController?.sourceView = hostView
            share.completionWithItemsHandler = { _, _, _, _ in try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
            presenter.present(share, animated: true)
        }
        func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
            if let url = downloads.removeValue(forKey: ObjectIdentifier(download)) { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
            failed(error.localizedDescription)
        }
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            guard navigationAction.targetFrame == nil, let presenter = presenter(for: webView) else { return nil }
            let popup = WKWebView(frame: .zero, configuration: configuration)
            popup.navigationDelegate = self
            popup.uiDelegate = self
            let controller = ArtifactPopupController(webView: popup)
            presenter.present(UINavigationController(rootViewController: controller), animated: true)
            return popup
        }
        private func presenter(for webView: WKWebView) -> UIViewController? {
            var current = webView.window?.rootViewController
            while let presented = current?.presentedViewController { current = presented }
            return current
        }
        func webViewDidClose(_ webView: WKWebView) {
            presenter(for: webView)?.dismiss(animated: true)
        }
        func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            guard let presenter = presenter(for: webView) else { completionHandler(); return }
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
            presenter.present(alert, animated: true)
        }
        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            guard let presenter = presenter(for: webView) else { completionHandler(false); return }
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) })
            presenter.present(alert, animated: true)
        }
        func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                     defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                     completionHandler: @escaping (String?) -> Void) {
            guard let presenter = presenter(for: webView) else { completionHandler(nil); return }
            let alert = UIAlertController(title: nil, message: prompt, preferredStyle: .alert)
            alert.addTextField { $0.text = defaultText }
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(nil) })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(alert.textFields?.first?.text) })
            presenter.present(alert, animated: true)
        }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            if (error as NSError).code != NSURLErrorCancelled { failed(error.localizedDescription) }
        }
        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            if (error as NSError).code != NSURLErrorCancelled { failed(error.localizedDescription) }
        }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { ready() }
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { failed("The preview stopped. Try loading it again.") }
    }
}


final class ArtifactPopupController: UIViewController {
    private let webView: WKWebView
    init(webView: WKWebView) { self.webView = webView; super.init(nibName: nil, bundle: nil) }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func loadView() { view = webView }
    override func viewDidLoad() {
        super.viewDidLoad()
        navigationItem.rightBarButtonItem = UIBarButtonItem(barButtonSystemItem: .close, target: self, action: #selector(close))
    }
    @objc private func close() { dismiss(animated: true) }
}

final class ArtifactWebContainer: UIView {
    let webView: WKWebView
    var desktopPreview = false { didSet { setNeedsLayout() } }
    init(webView: WKWebView) {
        self.webView = webView
        super.init(frame: .zero)
        clipsToBounds = true
        addSubview(webView)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func layoutSubviews() {
        super.layoutSubviews()
        let width = desktopPreview ? max(bounds.width, 1024) : bounds.width
        let scale = width > 0 ? bounds.width / width : 1
        webView.transform = .identity
        webView.frame = CGRect(x: 0, y: 0, width: width, height: scale > 0 ? bounds.height / scale : bounds.height)
        webView.layer.anchorPoint = CGPoint(x: 0, y: 0)
        webView.layer.position = .zero
        webView.transform = CGAffineTransform(scaleX: scale, y: scale)
    }
}
import SwiftUI
import WebKit

/// As on the web, scripts run in an opaque-origin sandbox, never in the Panels origin.
struct LegacyArtifactWebView: UIViewRepresentable {
    let source: String
    let url: URL
    var desktopPreview = false
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
        guard context.coordinator.source != source || context.coordinator.desktopPreview != desktopPreview else { return }
        context.coordinator.source = source
        context.coordinator.desktopPreview = desktopPreview
        // JSON encoding keeps arbitrary artifact source out of the host's script syntax.
        let sourceJSON = Self.literal(source)
        let baseJSON = Self.literal(url.absoluteString)
        let html = """
        <!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
        <meta http-equiv="Content-Security-Policy" content="default-src https: http: data: blob: 'unsafe-inline' 'unsafe-eval'; connect-src 'none'; form-action 'none'; object-src 'none'">
        <style>html,body{margin:0;width:100%;height:100%;background:#151515}iframe{border:0;width:100%;height:100%;display:block;background:white}</style>
        </head><body><iframe id="document" sandbox="allow-scripts" title="Artifact"></iframe>
        <script>
        const frame = document.getElementById('document');
        const desktopWidth = \(desktopPreview ? String(ArtifactPreviewLayout.desktopWidth) : "null");
        function fitPreview() {
          if (desktopWidth === null) return;
          const scale = Math.min(1, window.innerWidth / desktopWidth);
          frame.style.width = desktopWidth + 'px';
          frame.style.height = (window.innerHeight / scale) + 'px';
          frame.style.transformOrigin = 'top left';
          frame.style.transform = 'scale(' + scale + ')';
          document.body.style.overflow = 'hidden';
        }
        window.addEventListener('resize', fitPreview);
        fitPreview();
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
        var desktopPreview = false
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

private enum ArtifactPreviewLayout {
    static let desktopWidth = 1024
}
