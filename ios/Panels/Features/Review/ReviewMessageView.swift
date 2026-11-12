import SwiftUI

/// One conversation's unread run inside its card, with its reply box and Mark read.
/// The card supplies the title, recap, Skip and the exit.
struct ReviewMessageSection: View {
    let item: ReviewItem
    let isActive: Bool
    /// Owned by ReviewView so the next message can be read ahead, like proposals.
    let decision: ReviewMessageDecision
    /// True while the card or this part is leaving.
    let locked: Bool
    let onActivity: (Activity) -> Void
    /// Handled: the card runs the exit, then the optional step after it.
    let onResolved: (_ afterExit: (() -> Void)?) -> Void
    /// What the card needs to know: whether Skip must wait, and whether a reply is sending.
    struct Activity: Equatable { var locked: Bool; var sending: Bool }
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var capturing = false
    @State private var sendingPresentation = false
    @State private var outgoingAttempt: PendingWork?
    @State private var finished = false
    private var displayedAttempt: PendingWork? { sendingPresentation ? outgoingAttempt : decision.attempt }
    /// The view's current facts for this part. The part reads its run again when shown,
    /// keeping the read-ahead run on screen meanwhile.
    private var live: ReviewItem { store.review?.items.first { $0.id == item.id } ?? item }
    private var activity: Activity { Activity(locked: decision.busy || decision.pending || capturing, sending: sendingPresentation) }

    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
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
                            .disabled(decision.busy || locked || (decision.attempt != nil && decision.attempt?.state != .failed))
                            .padding(.bottom, ReviewStyle.gutter)
                    }
                }
                .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
                .overlay(RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg).stroke(Color.white.opacity(0.05)))
                .shadow(color: .black.opacity(0.12), radius: 20, y: 8)
                (typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(spacing: PanelsTheme.Space.sm)) : AnyLayout(HStackLayout(spacing: PanelsTheme.Space.sm))) {
                    Button("Mark read") { Haptics.light(); Task { if await decision.markRead() { Haptics.success(); finish() } else { ReviewStyle.error() } } }
                        .buttonStyle(ReviewActionStyle(context: .message)).accessibilityIdentifier("review.markRead")
                        .disabled(decision.through == 0 || decision.busy || decision.pending || capturing || locked)
                    if let owner = item.owner, let id = item.conversationId {
                        NavigationLink(value: AppRoute.conversation(owner: owner, id: id)) { Text("Open conversation") }
                            .buttonStyle(ReviewActionStyle(context: .message)).accessibilityIdentifier("review.openConversation")
                    }
                }
            } else if decision.error == nil { ScreenLoadingView(title: "messages") }
            if let error = decision.error {
                Text(error).foregroundStyle(PanelsTheme.error)
                if !decision.loaded { Button("Try again") { Task { await decision.load(item: live, api: store.client) } } }
            }
        }
        // Read again as the part is shown: a message arriving while it is on screen
        // stays outside the displayed run and returns to Review after a reply.
        .task(id: isActive) {
            guard isActive else { return }
            if let state = decision.attempt?.state, [.failed, .changed, .uncertain].contains(state) { sendingPresentation = false }
            await decision.load(item: live, api: store.client, shownAt: .now)
            guard !Task.isCancelled else { return }
            if decision.accepted { finishReply() }
            else if decision.loaded, decision.events.isEmpty, decision.composer.draft.isEmpty, decision.composer.attachments.isEmpty, decision.attempt == nil { finish() }
        }
        .onChange(of: activity, initial: true) { _, activity in onActivity(activity) }
        .onChange(of: decision.attempt?.state) { _, state in
            guard isActive else { return }
            if state == .failed || state == .changed { ReviewStyle.error() }
            if state == .failed || state == .changed || state == .uncertain { sendingPresentation = false }
        }
        .onChange(of: decision.accepted) { _, accepted in if isActive, accepted { finishReply() } }
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
        guard !finished else { return }
        Haptics.success()
        // Keep the composer geometry until the exit completes.
        finish { decision.clearAcceptedDraft() }
    }
    private func finish(afterExit: (() -> Void)? = nil) {
        guard !finished else { return }
        finished = true
        onResolved(afterExit)
    }
}

private struct ReviewMessageContent: View {
    let pieces: [ReviewMessageEvent.Piece]
    let conversationID: String
    @Environment(AppStore.self) private var store
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            ForEach(Array(pieces.enumerated()), id: \.offset) { _, piece in
                if let text = piece.text {
                    ArtifactMarkdownContent(source: text, baseURL: store.baseURL)
                    ForEach(ReviewArtifactLinks.targets(in: text, baseURL: store.baseURL)) { target in ReviewInlineArtifact(target: target) }
                } else if let id = piece.storedFileId,
                          let url = try? store.client.url("/api/conversation/conversations/\(conversationID)/files/\(id)") {
                    if piece.piece == "image" {
                        AsyncImage(url: url) { image in image.resizable().scaledToFit() } placeholder: { RingSpinner() }
                            .accessibilityLabel(piece.fileName ?? "Message image")
                    } else { Link(piece.fileName ?? "File", destination: url) }
                }
            }
        }
        .reviewArtifactLinks()
    }
}
