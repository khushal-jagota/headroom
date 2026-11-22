import SwiftUI
import QuickLook
import PDFKit
import AVKit

struct ArtifactViewerView: View {
    let owner: ArtifactOwner
    let path: String
    let title: String
    let dock: ArtifactDock
    @State private var conversationID: String?
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var document: ArtifactDocument?
    @State private var htmlURL: URL?
    @State private var currentHTMLURL: URL?
    @State private var shareURL: URL?
    @State private var shareDocument: ArtifactDocument?
    @State private var isPreparingShare = false
    @State private var shareError: String?
    @State private var image: UIImage?
    @State private var error: String?
    @State private var isLoading = true
    @State private var showLoading = false
    @State private var sharePresented = false
    @Environment(\.openRoute) private var openRoute
    @State private var loadVersion = 0
    @State private var renderVersion = 0
    @State private var htmlReloadSignal = 0
    @State private var webReady = false
    @State private var desktopPreview = false
    private var target: ArtifactTarget { .init(owner: owner, path: path, title: title) }

    /// The conversation whose composer sits below, when the dock is a conversation's.
    private var activeConversationOwner: ConversationOwner? {
        if case .conversation(let owner) = dock { return owner }
        return nil
    }

    private var hasPreview: Bool { document != nil || webReady }
    /// A read that could not reach the server leaves no error, so the quiet placeholder stays.
    private var waitingForPreview: Bool { isLoading || (!hasPreview && error == nil) }

    var body: some View {
        ScreenTextEntryScope { textEntry in artifactContent(textEntry: textEntry) }
    }

    private func artifactContent(textEntry: ScreenTextEntryContext) -> some View {
        VStack(spacing: 0) {
            if let htmlURL {
                htmlContent(htmlURL)
            } else if let document {
                content(document)
                    .environment(\.openURL, OpenURLAction { url in follow(url, textEntry: textEntry); return .handled })
            } else if let error {
                ContentUnavailableView {
                    Label("Couldn't open artifact", systemImage: "doc.badge.ellipsis")
                } description: { Text(error) } actions: {
                    Button("Try again") { loadVersion += 1 }.buttonStyle(.bordered)
                }
            } else {
                Color.clear.overlay { if showLoading { RingSpinner("Opening artifact…") } }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            // A page that never drew has no saved copy to keep; a failed reload says nothing.
            if let error, htmlURL != nil, !hasPreview {
                LoadErrorView(message: error) { reloadPreview() }
                    .padding(.horizontal, PanelsTheme.Space.lg).background(PanelsTheme.surface)
            }
        }
        .panelsScreen()
        .modifier(ArtifactConversationSurface(owner: activeConversationOwner, conversationID: conversationID, textEntry: textEntry))
        .navigationTitle((path as NSString).lastPathComponent)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text((path as NSString).lastPathComponent).font(PanelsTheme.TypeScale.artifactTitle).lineLimit(1).truncationMode(.middle)
            }
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button { Task { await share() } } label: { Image(systemName: "square.and.arrow.up") }
                    .accessibilityLabel("Share artifact").disabled((document == nil && htmlURL == nil) || isPreparingShare)
                Menu {
                    Button("Reload", systemImage: "arrow.clockwise") { reloadPreview() }.disabled(isLoading)
                    Button("Copy link", systemImage: "link") {
                        UIPasteboard.general.url = currentHTMLURL ?? target.previewURL(baseURL: store.baseURL); Haptics.success()
                    }
                } label: { Image(systemName: "ellipsis") }.accessibilityLabel("Artifact actions")
            }
        }
        .alert("Couldn’t share artifact", isPresented: Binding(
            get: { shareError != nil }, set: { if !$0 { shareError = nil } }
        )) {
            Button("OK", role: .cancel) { shareError = nil }
        } message: { Text(shareError ?? "") }
        .sheet(isPresented: $sharePresented) {
            if let shareDocument { ArtifactShareSheet(document: shareDocument).presentationDetents([.medium, .large]) }
            else if let shareURL { ArtifactURLShareSheet(url: shareURL).presentationDetents([.medium, .large]) }
        }
        .task(id: store.baseURL.absoluteString + "|" + String(describing: activeConversationOwner)) {
            guard let activeConversationOwner else { return }
            let generation = store.resources.admissionGeneration
            do {
                let resolved: String?
                switch activeConversationOwner {
                case .ticket(let id): resolved = try await store.resources.read(TicketResources.detail(id)).detail.conversationId
                case .manager(let id): resolved = try await store.resources.read(ManagerResources.detail(id)).manager.conversationId
                case .goal: return
                case .chief: resolved = try await store.client.chiefConversation().conversationId
                }
                guard !Task.isCancelled, generation == store.resources.admissionGeneration else { return }
                conversationID = resolved
            } catch {
                guard !Task.isCancelled, generation == store.resources.admissionGeneration else { return }
                conversationID = nil
            }
        }
        // With nothing drawn yet, the next connection reads again.
        .task(id: "\(target.id)|\(loadVersion)|\(hasPreview ? 0 : store.connectionSession)") { await load() }
        .task(id: waitingForPreview) {
            showLoading = false
            if waitingForPreview {
                try? await Task.sleep(for: .milliseconds(200))
                if !Task.isCancelled && waitingForPreview { showLoading = true }
            }
        }
    }

    private func htmlContent(_ url: URL) -> some View {
        VStack(spacing: 0) {
            Picker("Preview width", selection: $desktopPreview) {
                Text("Phone width").tag(false)
                Text("Fit desktop").tag(true)
            }.pickerStyle(.segmented).padding(PanelsTheme.Space.md)
                .accessibilityIdentifier("artifact-preview-width")
            ArtifactWebView(url: url, desktopPreview: desktopPreview, reloadSignal: htmlReloadSignal, failed: { error = $0 }, ready: { webReady = true }, reload: { reloadPreview() }, navigated: { currentHTMLURL = $0 }, openExternal: { openURL($0) })
                .id(renderVersion)
                .overlay { if !webReady && showLoading { RingSpinner("Opening preview…").padding().background(PanelsTheme.surface, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md)) } }
        }
    }

    @ViewBuilder private func content(_ document: ArtifactDocument) -> some View {
        switch target.kind {
        case .markdown where document.text != nil:
            ArtifactMarkdownView(source: document.text!, baseURL: document.remoteURL).panelsRefreshable { await load() }
        case .text where document.text != nil:
            ScrollView {
                Text(document.text!).font(.system(.footnote, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(PanelsTheme.Space.lg)
            }.panelsRefreshable { await load() }
        case .image:
            if let image { ArtifactImageView(image: image).id(renderVersion) }
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
                Button("Share file") { Task { await share() } }.buttonStyle(.bordered)
            }
        }
    }
    private func follow(_ url: URL, textEntry: ScreenTextEntryContext) {
        if ArtifactTarget.isRetiredGoalURL(url, baseURL: store.baseURL) { return }
        if let target = ArtifactTarget.from(url, baseURL: store.baseURL) {
            if target.id != self.target.id { textEntry.endEditingBeforeNavigation { openRoute(.artifact(target, dock: dock)) } }
        } else if ["https", "http", "mailto"].contains(url.scheme?.lowercased() ?? "") { openURL(url) }
    }
    private func share() async {
        isPreparingShare = true
        defer { isPreparingShare = false }
        do {
            shareDocument = nil; shareURL = nil
            if let currentHTMLURL, currentHTMLURL != htmlURL {
                if currentHTMLURL.query == nil, currentHTMLURL.fragment == nil,
                   let currentTarget = ArtifactTarget.from(currentHTMLURL, baseURL: store.baseURL) {
                    shareDocument = try await store.client.artifactDocument(currentTarget)
                } else { shareURL = currentHTMLURL }
            } else if let document { shareDocument = document }
            else { shareDocument = try await store.client.artifactDocument(target) }
            sharePresented = true
        } catch {
            shareError = error.localizedDescription
            Haptics.error()
        }
    }
    private func reloadPreview() {
        if htmlURL != nil {
            error = nil; webReady = false; htmlReloadSignal += 1
        } else { loadVersion += 1 }
    }
    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            if target.kind == .html {
                document = nil
                htmlURL = try store.client.artifactURL(target)
                currentHTMLURL = htmlURL
                error = nil; webReady = false; renderVersion += 1
                return
            }
            htmlURL = nil; currentHTMLURL = nil
            let result = try await store.client.artifactDocument(target)
            let image = target.kind == .image ? await Task.detached(priority: .userInitiated) {
                UIImage(contentsOfFile: result.localURL.path)
            }.value : nil
            try Task.checkCancellation()
            self.image = image
            document = result; error = nil; webReady = false; renderVersion += 1
        } catch {
            if !Task.isCancelled, let note = ServerReadFailure.note(error) {
                self.error = note
                Haptics.error()
            }
        }
    }
}

struct ArtifactPDFView: UIViewRepresentable {
    let url: URL
    func makeUIView(context: Context) -> PDFView {
        let view = PDFView(); view.autoScales = true; view.displayMode = .singlePageContinuous
        view.backgroundColor = UIColor(PanelsTheme.surface); view.document = PDFDocument(url: url)
        return view
    }
    func updateUIView(_ view: PDFView, context: Context) {}
}
struct ArtifactMediaView: View {
    @State private var player: AVPlayer
    init(url: URL) { _player = State(initialValue: AVPlayer(url: url)) }
    var body: some View { VideoPlayer(player: player).background(.black).onDisappear { player.pause() } }
}

private struct ArtifactConversationSurface: ViewModifier {
    let owner: ConversationOwner?
    let conversationID: String?
    let textEntry: ScreenTextEntryContext
    @ViewBuilder func body(content: Content) -> some View {
        if let owner { content.conversationNavigation(owner: owner, conversationID: conversationID, textEntry: textEntry) }
        else { content }
    }
}
