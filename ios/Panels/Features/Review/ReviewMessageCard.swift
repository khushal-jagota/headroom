import SwiftUI

/// A message card: one conversation's unread run, with Reply and Mark read docked below.
/// The read and reply logic is ReviewMessageDecision's.
struct ReviewMessageCard: View {
    let item: ReviewItem
    let isActive: Bool
    /// Owned by the session so the next message can be read ahead, like proposals.
    let decision: ReviewMessageDecision
    /// The server's reason for refusing this card's latest reply or Mark read.
    let refusal: String?
    let motion: ReviewCardMotion
    let onSkip: (CGFloat?) -> Void
    let onResolve: (_ afterExit: (() -> Void)?) -> Void
    @Environment(AppStore.self) private var store
    @State private var capturing = false
    @State private var sendingPresentation = false
    @State private var outgoingAttempt: PendingWork?
    @State private var finished = false
    private var displayedAttempt: PendingWork? { sendingPresentation ? outgoingAttempt : decision.attempt }
    /// The view's current facts for this card. The card reads its run again when shown,
    /// keeping the read-ahead run on screen meanwhile.
    private var live: ReviewItem {
        var current = store.review?.items.first { $0.id == item.id } ?? item
        // The owner reference distinguishes a replaced or cleared conversation from missing data.
        if let owner = item.owner, let reference = store.currentConversationID(owner) {
            current.addressCurrent = reference == item.conversationId
        }
        return current
    }
    private var locked: Bool { decision.busy || capturing || sendingPresentation || finished }

    var body: some View {
        ReviewCardFrame(title: item.title, recap: item.recap, motion: motion, locked: locked, onSkip: onSkip) {
            VStack(alignment: .leading, spacing: 14) {
                // A refused reply shows its reason in its composer; a refused Mark read here.
                if let refusal, decision.attempt?.state != .failed {
                    ReviewInlineNote(tone: .refused, title: "Refused", text: refusal)
                }
                if decision.loaded {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("Message").panelsScaledFont(size: 13, weight: .semibold).foregroundStyle(PanelsTheme.Web.secondary)
                        ForEach(decision.events) { event in
                            if event.id != decision.events.first?.id { Rectangle().fill(Color.white.opacity(0.14)).frame(width: 32, height: 1) }
                            ReviewMessageContent(pieces: event.payload.pieces, conversationID: item.conversationId ?? "", dock: .reviewMessage(itemID: item.id))
                                .accessibilityIdentifier("review.message.\(event.sequence)")
                        }
                        if decision.events.isEmpty { Text("These messages have been read.").foregroundStyle(PanelsTheme.Web.tertiary) }
                        if !live.addressCurrent {
                            Text("Historical conversation · read only").panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Web.tertiary)
                        }
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(PanelsTheme.Web.surface2, in: RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
                } else if decision.error == nil { ScreenLoadingView(title: "messages") }
                if let error = decision.error {
                    Text(error).panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Web.error)
                    if !decision.loaded { Button("Try again") { Task { await decision.load(item: live, api: store.client) } }.panelsScaledFont(size: 13) }
                }
            }
        } dock: {
            ReviewDock {
                if live.addressCurrent, let owner = item.owner {
                    ConversationComposer(model: decision.composer, owner: owner, reviewReply: true, placeholder: "Reply…", sendReply: send,
                                         statusNote: displayedAttempt?.state == .failed ? displayedAttempt?.note : nil,
                                         retryReply: displayedAttempt?.state == .failed, onCaptureChanged: { capturing = $0 })
                        .disabled(!decision.loaded || decision.events.isEmpty || decision.busy || finished
                                  || (decision.attempt != nil && decision.attempt?.state != .failed))
                    ReviewButtonRow {
                        skipButton
                        Button("Mark read") { markRead() }
                            .buttonStyle(ReviewButtonStyle()).accessibilityIdentifier("review.markRead")
                            .disabled(decision.through == 0 || decision.busy || capturing || finished)
                    }
                } else {
                    skipButton
                }
            }
        }
        // Read again as the card is shown: a message arriving while it is on screen stays
        // outside the displayed run and returns to Review after a reply.
        .task(id: "\(isActive):\(store.connectionSession)") {
            guard isActive else { return }
            if decision.attempt?.state == .failed { sendingPresentation = false }
            await decision.load(item: live, api: store.client, shownAt: .now)
            guard !Task.isCancelled else { return }
            if decision.accepted { finishReply() }
            else if decision.loaded, decision.events.isEmpty, decision.composer.draft.isEmpty, decision.composer.attachments.isEmpty, decision.attempt == nil { finish() }
        }
        .onChange(of: live.addressCurrent, initial: true) { _, current in decision.updateAddressCurrent(current) }
        .onChange(of: decision.attempt?.state) { _, state in
            guard isActive else { return }
            if state == .failed { ReviewStyle.error(); sendingPresentation = false }
        }
        .onChange(of: decision.accepted) { _, accepted in if isActive, accepted { finishReply() } }
    }

    private var skipButton: some View {
        Button("Skip") { Haptics.selection(); onSkip(nil) }
            .buttonStyle(ReviewButtonStyle()).accessibilityIdentifier("review.skip")
            .disabled(locked)
    }
    private func markRead() {
        Haptics.light()
        Task {
            if await decision.markRead() { Haptics.success(); finish() } else { ReviewStyle.error() }
        }
    }
    private func send() async -> Bool {
        guard !sendingPresentation, live.addressCurrent else { return false }
        decision.updateAddressCurrent(live.addressCurrent)
        outgoingAttempt = decision.attempt
        sendingPresentation = true
        let accepted = await decision.send()
        if !decision.accepted { sendingPresentation = false }
        // Leave in the same turn as the save, before the saved work hides the card.
        else { finishReply() }
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
        onResolve(afterExit)
    }
}

private struct ReviewMessageContent: View {
    let pieces: [ReviewMessageEvent.Piece]
    let conversationID: String
    let dock: ArtifactDock
    @Environment(AppStore.self) private var store
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            ForEach(Array(pieces.enumerated()), id: \.offset) { _, piece in
                if let text = piece.text {
                    ArtifactMarkdownContent(source: text, baseURL: store.baseURL)
                } else if let id = piece.storedFileId,
                          let url = try? store.client.url("/api/conversation/conversations/\(conversationID)/files/\(id)") {
                    if piece.piece == "image" {
                        AsyncImage(url: url) { image in image.resizable().scaledToFit() } placeholder: { RingSpinner() }
                            .accessibilityLabel(piece.fileName ?? "Message image")
                    } else { Link(piece.fileName ?? "File", destination: url) }
                }
            }
        }
        .reviewArtifactLinks(dock: dock)
    }
}
