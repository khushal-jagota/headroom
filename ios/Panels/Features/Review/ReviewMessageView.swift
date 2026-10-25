import SwiftUI
import QuickLook

struct ReviewMessageView: View {
    let item: ReviewItem
    let onSkip: () -> Void
    let onResolved: () -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var decision = ReviewMessageDecision()
    @State private var visible = false
    @State private var leaving = false
    @State private var drag: CGFloat = 0
    @State private var crossed = false
    @State private var recapExpanded = false
    @State private var capturing = false
    @State private var sendingPresentation = false
    @State private var outgoingAttempt: PendingWork?
    private var displayedAttempt: PendingWork? { sendingPresentation ? outgoingAttempt : decision.attempt }

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                    Text(item.title).font(ReviewStyle.title).foregroundStyle(PanelsTheme.strong)
                    if let recap = item.recap, !recap.isEmpty {
                        Button { withAnimation { recapExpanded.toggle() } } label: {
                            Text(.init(recap)).font(.subheadline).foregroundStyle(PanelsTheme.muted).lineLimit(recapExpanded ? nil : 4)
                        }.buttonStyle(.plain)
                    }
                    if decision.loaded {
                        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
                            VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
                                ForEach(decision.events) { event in
                                    if event.id != decision.events.first?.id { Divider().frame(width: 28) }
                                    ReviewMessageContent(pieces: event.payload.pieces, conversationID: item.conversationId ?? "")
                                        .accessibilityIdentifier("review.message.\(event.sequence)")
                                }
                                if decision.events.isEmpty { Text("These messages have been read.").foregroundStyle(PanelsTheme.faint) }
                                if decision.historical {
                                    Text("Historical conversation · read only").font(.footnote).foregroundStyle(PanelsTheme.faint)
                                }
                            }.padding(ReviewStyle.gutter)
                            if !decision.historical, let owner = item.owner, !decision.events.isEmpty {
                                ConversationComposer(model: decision.composer, owner: owner, reviewReply: true, sendReply: send, statusNote: displayedAttempt?.note, retryReply: displayedAttempt?.state == .failed, onCaptureChanged: { capturing = $0 })
                                    .disabled(decision.busy || leaving || (decision.attempt != nil && decision.attempt?.state != .failed))
                                    .padding(.bottom, ReviewStyle.gutter)
                            }
                        }
                        .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
                        .overlay(RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg).stroke(Color.white.opacity(0.05)))
                        .shadow(color: .black.opacity(0.12), radius: 20, y: 8)
                        (typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(spacing: PanelsTheme.Space.sm)) : AnyLayout(HStackLayout(spacing: PanelsTheme.Space.sm))) {
                            Button("Mark read") { Haptics.light(); Task { if await decision.markRead() { Haptics.success(); leave(resolved: true) } else { ReviewStyle.error() } } }
                                .buttonStyle(ReviewActionStyle()).accessibilityIdentifier("review.markRead")
                                .disabled(decision.through == 0 || decision.busy || decision.pending || capturing || leaving)
                            if let owner = item.owner, let id = item.conversationId {
                                NavigationLink(value: AppRoute.conversation(owner: owner, id: id)) { Text("Open conversation") }
                                    .buttonStyle(ReviewActionStyle()).accessibilityIdentifier("review.openConversation")
                            }
                        }
                    } else if decision.error == nil { ScreenLoadingView(title: "messages") }
                    if let error = decision.error {
                        Text(error).foregroundStyle(PanelsTheme.error)
                        if !decision.loaded { Button("Try again") { Task { await decision.load(item: item, api: store.client) } } }
                    }
                }
                .padding(.horizontal, ReviewStyle.gutter).padding(.top, PanelsTheme.Space.md).padding(.bottom, ReviewStyle.gutter)
                .opacity(visible && !leaving ? 1 : 0).offset(x: drag)
                .allowsHitTesting(!leaving)
                .simultaneousGesture(DragGesture(minimumDistance: 24).onChanged { value in
                    guard !decision.pending, !decision.busy, !capturing, !leaving, abs(value.translation.width) > abs(value.translation.height) * 1.5 else { return }
                    drag = min(0, value.translation.width)
                    let next = -drag > geometry.size.width * ReviewStyle.swipeFraction
                    if next && !crossed { Haptics.light() }; crossed = next
                }.onEnded { value in
                    if crossed { leave(resolved: false, width: geometry.size.width) }
                    else { withAnimation(ReviewStyle.entrance) { drag = 0 } }
                    crossed = false
                })
            }.scrollDismissesKeyboard(.interactively)
                .scrollDisabled(sendingPresentation || leaving)
        }
        .toolbar { skipToolbar }
        .task {
            await decision.load(item: item, api: store.client)
            if decision.accepted { finishReply() }
            else if decision.loaded, decision.events.isEmpty, decision.composer.draft.isEmpty, decision.composer.attachments.isEmpty, decision.attempt == nil { leave(resolved: true) }
        }
        .onAppear { withAnimation(.easeOut(duration: ReviewStyle.fadeIn)) { visible = true } }
        .onChange(of: decision.attempt?.state) { _, state in
            if state == .failed || state == .changed { ReviewStyle.error() }
            if state == .failed || state == .changed || state == .uncertain { sendingPresentation = false }
        }
        .onChange(of: decision.accepted) { _, accepted in if accepted { finishReply() } }
    }
    @ToolbarContentBuilder private var skipToolbar: some ToolbarContent {
        if #available(iOS 26.0, *) {
            ToolbarItem(placement: .topBarTrailing) { skipButton }.sharedBackgroundVisibility(.hidden)
        } else {
            ToolbarItem(placement: .topBarTrailing) { skipButton }
        }
    }
    private var skipButton: some View {
        Button("Skip") { Haptics.selection(); leave(resolved: false) }
            .buttonStyle(.plain).foregroundStyle(PanelsTheme.muted)
            .disabled(decision.busy || decision.pending || capturing || leaving)
            .accessibilityIdentifier("review.skip")
    }
    private func send() async -> Bool {
        guard !sendingPresentation else { return false }
        outgoingAttempt = decision.attempt
        sendingPresentation = true
        let accepted = await decision.send()
        if !decision.pending && !decision.accepted { sendingPresentation = false }
        return accepted
    }
    private func finishReply() {
        guard !leaving else { return }
        Haptics.success()
        // Keep the composer geometry until the exit completes.
        leave(resolved: true, clearDraft: true)
    }
    private func leave(resolved: Bool, width: CGFloat? = nil, clearDraft: Bool = false) {
        guard !leaving else { return }
        let duration = width == nil || reduceMotion ? ReviewStyle.fadeOut : ReviewStyle.swipeOut
        withAnimation(.easeOut(duration: duration)) { leaving = true; if let width, !reduceMotion { drag = -width } }
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(duration))
            guard !Task.isCancelled else { return }
            if clearDraft { decision.clearAcceptedDraft() }
            if resolved { onResolved() } else { onSkip() }
        }
    }
}

private struct ReviewMessageContent: View {
    let pieces: [ReviewMessageEvent.Piece]
    let conversationID: String
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var selectedArtifact: ArtifactTarget?
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            ForEach(Array(pieces.enumerated()), id: \.offset) { _, piece in
                if let text = piece.text {
                    ArtifactMarkdownContent(source: text, baseURL: store.baseURL)
                    ForEach(artifacts(text)) { target in ReviewInlineArtifact(target: target, openLink: follow) }
                } else if let id = piece.storedFileId,
                          let url = try? store.client.url("/api/conversation/conversations/\(conversationID)/files/\(id)") {
                    if piece.piece == "image" {
                        AsyncImage(url: url) { image in image.resizable().scaledToFit() } placeholder: { RingSpinner() }
                            .accessibilityLabel(piece.fileName ?? "Message image")
                    } else { Link(piece.fileName ?? "File", destination: url) }
                }
            }
        }
        .environment(\.openURL, OpenURLAction { url in follow(url); return .handled })
        .navigationDestination(item: $selectedArtifact) { target in
            ArtifactViewerView(owner: target.owner, path: target.path, title: target.title, showsConversation: false)
        }
    }
    private func follow(_ url: URL) {
        if let target = ArtifactTarget.from(url, baseURL: store.baseURL) { selectedArtifact = target }
        else { openURL(url) }
    }
    private func artifacts(_ text: String) -> [ArtifactTarget] {
        guard let rich = try? AttributedString(markdown: text, baseURL: store.baseURL) else { return [] }
        var seen = Set<String>()
        return rich.runs.compactMap { run in
            guard let url = run.link, let target = ArtifactTarget.from(url, baseURL: store.baseURL), seen.insert(target.id).inserted else { return nil }
            return target
        }
    }
}

private struct ReviewInlineArtifact: View {
    let target: ArtifactTarget
    let openLink: (URL) -> Void
    @Environment(AppStore.self) private var store
    @State private var document: ArtifactDocument?
    @State private var error: String?
    @State private var expanded = true
    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            if let document {
                if target.kind == .html, let text = document.text {
                    ArtifactWebView(source: text, url: document.remoteURL, openLink: openLink, failed: { error = $0 }, ready: {}, reload: { Task { await load() } }).frame(height: 320)
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
        } label: { Text(target.title).font(.subheadline) }
            .task { await load() }
    }
    private func load() async {
        do { document = try await store.client.artifactDocument(target); error = nil }
        catch { self.error = error.localizedDescription }
    }
}
