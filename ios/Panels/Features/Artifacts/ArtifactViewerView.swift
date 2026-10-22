import SwiftUI
import QuickLook

struct ArtifactViewerView: View {
    let owner: ArtifactOwner
    let path: String
    let title: String
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var document: ArtifactDocument?
    @State private var error: String?
    @State private var isLoading = true
    @State private var sharePresented = false
    @State private var linkedArtifact: ArtifactTarget?
    @State private var loadVersion = 0
    @State private var webReady = false
    private var target: ArtifactTarget { .init(owner: owner, path: path, title: title) }

    var body: some View {
        Group {
            if let error {
                ContentUnavailableView {
                    Label("Couldn't open artifact", systemImage: "doc.badge.ellipsis")
                } description: { Text(error) } actions: {
                    Button("Try again") { Haptics.light(); loadVersion += 1 }.buttonStyle(.bordered)
                }
            } else if let document {
                content(document)
                    .environment(\.openURL, OpenURLAction { url in follow(url); return .handled })
            } else { ProgressView("Opening artifact…").frame(maxWidth: .infinity, maxHeight: .infinity) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .panelsScreen()
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button { Haptics.light(); loadVersion += 1 } label: { Image(systemName: "arrow.clockwise") }
                    .accessibilityLabel("Reload artifact").disabled(isLoading)
                Button { Haptics.light(); sharePresented = true } label: { Image(systemName: "square.and.arrow.up") }
                    .accessibilityLabel("Share artifact").disabled(document == nil)
            }
        }
        .sheet(isPresented: $sharePresented) { if let document { ArtifactShareSheet(document: document).presentationDetents([.medium, .large]) } }
        .navigationDestination(item: $linkedArtifact) { target in
            ArtifactViewerView(owner: target.owner, path: target.path, title: target.title)
        }
        .task(id: loadVersion) { await load() }
    }

    @ViewBuilder private func content(_ document: ArtifactDocument) -> some View {
        switch target.kind {
        case .html where document.text != nil:
            ArtifactWebView(source: document.text!, url: document.remoteURL, openLink: follow, failed: { error = $0 }, ready: { webReady = true })
                .id(loadVersion)
                .overlay { if !webReady { ProgressView("Opening preview…").padding().background(PanelsTheme.surface, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md)) } }
        case .markdown where document.text != nil:
            ArtifactMarkdownView(source: document.text!, baseURL: document.remoteURL)
        case .text where document.text != nil:
            ScrollView([.vertical, .horizontal]) {
                Text(document.text!).font(.system(.footnote, design: .monospaced)).textSelection(.enabled).padding(PanelsTheme.Space.lg)
            }
        case .image:
            if let image = UIImage(contentsOfFile: document.localURL.path) { ArtifactImageView(image: image).id(loadVersion) }
            else { fallback(document) }
        default: fallback(document)
        }
    }
    @ViewBuilder private func fallback(_ document: ArtifactDocument) -> some View {
        if QLPreviewController.canPreview(document.localURL as NSURL) { ArtifactSystemPreview(document: document).id(loadVersion) }
        else {
            ContentUnavailableView {
                Label(document.localURL.lastPathComponent, systemImage: "doc")
            } description: { Text("Share this file to open it in another app.") } actions: {
                Button("Share file") { Haptics.light(); sharePresented = true }.buttonStyle(.bordered)
            }
        }
    }
    private func follow(_ url: URL) {
        Haptics.light()
        if let target = ArtifactTarget.from(url, baseURL: store.baseURL) {
            if target.id != self.target.id { linkedArtifact = target }
        } else if ["https", "http", "mailto"].contains(url.scheme?.lowercased() ?? "") { openURL(url) }
    }
    private func load() async {
        isLoading = true; error = nil; webReady = false
        defer { isLoading = false }
        do {
            let result = try await store.client.artifactDocument(target)
            try Task.checkCancellation()
            document = result
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}
