import SwiftUI
import QuickLook

/// Proposal and message cards share one route for managed file links:
/// a tap opens the in-app viewer, and other links go to the system.
struct ReviewArtifactLinks: ViewModifier {
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var selected: ArtifactTarget?

    func body(content: Content) -> some View {
        content
            .environment(\.openURL, OpenURLAction { url in follow(url); return .handled })
            .navigationDestination(item: $selected) { target in
                ArtifactViewerView(owner: target.owner, path: target.path, title: target.title, showsConversation: false)
            }
    }
    private func follow(_ url: URL) {
        if ArtifactTarget.isRetiredGoalURL(url, baseURL: store.baseURL) { return }
        if let target = ArtifactTarget.from(url, baseURL: store.baseURL) { selected = target }
        else { openURL(url) }
    }

    /// Managed files linked from markdown, once each, in reading order.
    static func targets(in text: String, baseURL: URL) -> [ArtifactTarget] {
        guard let rich = try? AttributedString(markdown: text, baseURL: baseURL) else { return [] }
        var seen = Set<String>()
        return rich.runs.compactMap { run in
            guard let url = run.link, let target = ArtifactTarget.from(url, baseURL: baseURL), seen.insert(target.id).inserted else { return nil }
            return target
        }
    }
}

extension View {
    func reviewArtifactLinks() -> some View { modifier(ReviewArtifactLinks()) }
}

/// Inline preview of one linked file. Links inside it follow the enclosing `reviewArtifactLinks()`.
struct ReviewInlineArtifact: View {
    let target: ArtifactTarget
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var document: ArtifactDocument?
    @State private var error: String?
    @State private var expanded = true
    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            if let document {
                if target.kind == .html, let text = document.text {
                    ArtifactWebView(source: text, url: document.remoteURL, openLink: { openURL($0) }, failed: { error = $0 }, ready: {}, reload: { Task { await load() } }).frame(height: 320)
                } else if target.kind == .markdown, let text = document.text {
                    ArtifactMarkdownContent(source: text, baseURL: document.remoteURL)
                } else if let text = document.text { Text(text).font(.system(.footnote, design: .monospaced)).textSelection(.enabled) }
                else if let image = UIImage(contentsOfFile: document.localURL.path) { ArtifactImageView(image: image).frame(height: 320) }
                else if target.path.lowercased().hasSuffix(".pdf") { ArtifactPDFView(url: document.localURL).frame(height: 320) }
                else if ["mp4", "mov", "m4v", "mp3", "m4a", "wav"].contains((target.path as NSString).pathExtension.lowercased()) { ArtifactMediaView(url: document.localURL).frame(height: 320) }
                else if QLPreviewController.canPreview(document.localURL as NSURL) { ArtifactSystemPreview(document: document).frame(height: 320) }
                NavigationLink(value: AppRoute.artifact(owner: target.owner, path: target.path, title: target.title)) { Text("Open artifact") }
            } else if let error { Text(error).foregroundStyle(PanelsTheme.error); Button("Retry") { Task { await load() } } }
            else { RingSpinner() }
        } label: { Text(target.title).font(PanelsTheme.TypeScale.artifactLink) }
            .task { await load() }
    }
    private func load() async {
        do { document = try await store.client.artifactDocument(target); error = nil }
        catch { self.error = error.localizedDescription }
    }
}
