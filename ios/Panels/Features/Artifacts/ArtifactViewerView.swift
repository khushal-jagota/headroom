import SwiftUI
import QuickLook
import PDFKit
import AVKit

struct ArtifactViewerView: View {
    let owner: ArtifactOwner
    let path: String
    let title: String
    var showsConversationPlaceholder = true
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var document: ArtifactDocument?
    @State private var error: String?
    @State private var isLoading = true
    @State private var showLoading = false
    @State private var sharePresented = false
    @State private var linkedArtifact: ArtifactTarget?
    @State private var loadVersion = 0
    @State private var renderVersion = 0
    @State private var webReady = false
    private var target: ArtifactTarget { .init(owner: owner, path: path, title: title) }

    private var waitingForPreview: Bool { isLoading || (target.kind == .html && document != nil && !webReady && error == nil) }

    var body: some View {
        VStack(spacing: 0) {
            if let document {
                content(document)
                    .environment(\.openURL, OpenURLAction { url in follow(url); return .handled })
            } else if let error {
                ContentUnavailableView {
                    Label("Couldn't open artifact", systemImage: "doc.badge.ellipsis")
                } description: { Text(error) } actions: {
                    Button("Try again") { loadVersion += 1 }.buttonStyle(.bordered)
                }
            } else {
                Color.clear.overlay { if showLoading { ProgressView("Opening artifact…") } }
            }
            if showsConversationPlaceholder && target.kind != .image {
                ConversationPlaceholder().fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if let error, document != nil {
                LoadErrorView(message: "Couldn’t refresh. Showing the last loaded artifact. \(error)") { await load() }
                    .padding(.horizontal, PanelsTheme.Space.lg).background(PanelsTheme.surface)
            }
        }
        .panelsScreen()
        .navigationTitle((path as NSString).lastPathComponent)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text((path as NSString).lastPathComponent).panelsScaledFont(size: 15, relativeTo: .subheadline, weight: .semibold).lineLimit(1).truncationMode(.middle)
            }
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button { sharePresented = true } label: { Image(systemName: "square.and.arrow.up") }
                    .accessibilityLabel("Share artifact").disabled(document == nil)
                Menu {
                    Button("Reload", systemImage: "arrow.clockwise") { loadVersion += 1 }.disabled(isLoading)
                    Button("Copy link", systemImage: "link") {
                        UIPasteboard.general.url = target.previewURL(baseURL: store.baseURL); Haptics.success()
                    }
                } label: { Image(systemName: "ellipsis") }.accessibilityLabel("Artifact actions")
            }
        }
        .sheet(isPresented: $sharePresented) { if let document { ArtifactShareSheet(document: document).presentationDetents([.medium, .large]) } }
        .navigationDestination(item: $linkedArtifact) { target in
            ArtifactViewerView(owner: target.owner, path: target.path, title: target.title, showsConversationPlaceholder: showsConversationPlaceholder)
        }
        .task(id: loadVersion) { await load() }
        .task(id: waitingForPreview) {
            showLoading = false
            if waitingForPreview {
                try? await Task.sleep(for: .milliseconds(200))
                if !Task.isCancelled && waitingForPreview { showLoading = true }
            }
        }
    }

    @ViewBuilder private func content(_ document: ArtifactDocument) -> some View {
        switch target.kind {
        case .html where document.text != nil:
            ArtifactWebView(source: document.text!, url: document.remoteURL, openLink: follow, failed: { error = $0 }, ready: { webReady = true }, reload: { Task { await load() } })
                .id(renderVersion)
                .overlay { if !webReady && showLoading { ProgressView("Opening preview…").padding().background(PanelsTheme.surface, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md)) } }
        case .markdown where document.text != nil:
            ArtifactMarkdownView(source: document.text!, baseURL: document.remoteURL).refreshable { await load() }
        case .text where document.text != nil:
            ScrollView {
                Text(document.text!).font(.system(.footnote, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(PanelsTheme.Space.lg)
            }.refreshable { await load() }
        case .image:
            if let image = UIImage(contentsOfFile: document.localURL.path) { ArtifactImageView(image: image).id(renderVersion) }
            else { fallback(document) }
        default: fallback(document)
        }
    }
    @ViewBuilder private func fallback(_ document: ArtifactDocument) -> some View {
        if path.lowercased().hasSuffix(".pdf") { ArtifactPDFView(url: document.localURL).id(renderVersion) }
        else if ["mp4", "mov", "m4v", "mp3", "m4a", "wav"].contains((path as NSString).pathExtension.lowercased()) {
            ArtifactMediaView(url: document.localURL).id(renderVersion)
        } else if QLPreviewController.canPreview(document.localURL as NSURL) { ArtifactSystemPreview(document: document).id(renderVersion) }
        else {
            ContentUnavailableView {
                Label(document.localURL.lastPathComponent, systemImage: "doc")
            } description: { Text("Share this file to open it in another app.") } actions: {
                Button("Share file") { sharePresented = true }.buttonStyle(.bordered)
            }
        }
    }
    private func follow(_ url: URL) {
        if let target = ArtifactTarget.from(url, baseURL: store.baseURL) {
            if target.id != self.target.id { linkedArtifact = target }
        } else if ["https", "http", "mailto"].contains(url.scheme?.lowercased() ?? "") { openURL(url) }
    }
    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let result = try await store.client.artifactDocument(target)
            try Task.checkCancellation()
            document = result; error = nil; webReady = false; renderVersion += 1
        } catch {
            if !Task.isCancelled {
                self.error = error.localizedDescription
                Haptics.error()
            }
        }
    }
}

private struct ArtifactPDFView: UIViewRepresentable {
    let url: URL
    func makeUIView(context: Context) -> PDFView {
        let view = PDFView(); view.autoScales = true; view.displayMode = .singlePageContinuous
        view.backgroundColor = UIColor(PanelsTheme.surface); view.document = PDFDocument(url: url)
        return view
    }
    func updateUIView(_ view: PDFView, context: Context) {}
}
private struct ArtifactMediaView: View {
    @State private var player: AVPlayer
    init(url: URL) { _player = State(initialValue: AVPlayer(url: url)) }
    var body: some View { VideoPlayer(player: player).background(.black).onDisappear { player.pause() } }
}
