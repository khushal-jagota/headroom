import SwiftUI
import WebKit

/// Loads managed HTML from its Panels URL with ordinary browser navigation.
struct ArtifactWebView: UIViewRepresentable {
    let url: URL
    var desktopPreview = false
    var reloadSignal = 0
    let failed: (String) -> Void
    let ready: () -> Void
    let reload: () -> Void
    let navigated: (URL) -> Void
    let openExternal: (URL) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(failed: failed, ready: ready, reload: reload, navigated: navigated, openExternal: openExternal) }
    func makeUIView(context: Context) -> ArtifactWebContainer {
        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = true
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.isOpaque = false
        view.backgroundColor = .clear
        view.scrollView.backgroundColor = .clear
        view.navigationDelegate = context.coordinator
        context.coordinator.observeNavigation(in: view)
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
            context.coordinator.reloadSignal = reloadSignal
            container.webView.load(URLRequest(url: url))
        } else if context.coordinator.reloadSignal != reloadSignal {
            context.coordinator.reloadSignal = reloadSignal
            container.webView.reload()
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
        var url: URL?
        var reloadSignal = 0
        weak var mainView: WKWebView?
        private var urlObservation: NSKeyValueObservation?
        func observeNavigation(in view: WKWebView) {
            mainView = view
            urlObservation = view.observe(\.url, options: [.new]) { [weak self] _, change in
                guard let url = change.newValue ?? nil else { return }
                Task { @MainActor [weak self] in self?.navigated(url) }
            }
        }
        private var downloads: [ObjectIdentifier: URL] = [:]
        private weak var hostView: WKWebView?
        let failed: (String) -> Void
        let ready: () -> Void
        let reload: () -> Void
        let navigated: (URL) -> Void
        let openExternal: (URL) -> Void
        @objc func refresh(_ control: UIRefreshControl) { reload(); control.endRefreshing() }
        init(failed: @escaping (String) -> Void, ready: @escaping () -> Void, reload: @escaping () -> Void, navigated: @escaping (URL) -> Void, openExternal: @escaping (URL) -> Void) {
            self.failed = failed; self.ready = ready; self.reload = reload; self.navigated = navigated; self.openExternal = openExternal
        }
        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
            if let url = navigationAction.request.url,
               ["mailto", "tel"].contains(url.scheme?.lowercased() ?? "") {
                openExternal(url)
                decisionHandler(.cancel)
            } else {
                decisionHandler(navigationAction.shouldPerformDownload ? .download : .allow)
            }
        }
        func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                     decisionHandler: @escaping @MainActor @Sendable (WKNavigationResponsePolicy) -> Void) {
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
                      suggestedFilename: String, completionHandler: @escaping @MainActor @Sendable (URL?) -> Void) {
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
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor @Sendable () -> Void) {
            guard let presenter = presenter(for: webView) else { completionHandler(); return }
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
            presenter.present(alert, animated: true)
        }
        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor @Sendable (Bool) -> Void) {
            guard let presenter = presenter(for: webView) else { completionHandler(false); return }
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) })
            presenter.present(alert, animated: true)
        }
        func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                     defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                     completionHandler: @escaping @MainActor @Sendable (String?) -> Void) {
            guard let presenter = presenter(for: webView) else { completionHandler(nil); return }
            let alert = UIAlertController(title: nil, message: prompt, preferredStyle: .alert)
            alert.addTextField { $0.text = defaultText }
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(nil) })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(alert.textFields?.first?.text) })
            presenter.present(alert, animated: true)
        }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            if let note = ServerReadFailure.note(error) { failed(note) }
        }
        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            if let note = ServerReadFailure.note(error) { failed(note) }
        }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            if webView === mainView, let url = webView.url { navigated(url) }
            ready()
        }
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
