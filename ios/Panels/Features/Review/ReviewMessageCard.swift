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
    @State private var actions = ReviewMessageActions()
    private var live: ReviewItem { store.reviewMessageFacts(item) }

    var body: some View {
        ReviewCardFrame(title: item.title, recap: item.recap, motion: motion, locked: actions.locks(decision), onSkip: onSkip) {
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
            ReviewMessageDock(item: item, isActive: isActive, decision: decision, actions: actions, onSkip: { onSkip(nil) }, onResolve: onResolve)
        }
        // Read again as the card is shown: a message arriving while it is on screen stays
        // outside the displayed run and returns to Review after a reply. Only the card reads:
        // Mark read acknowledges what the card displayed.
        .task(id: "\(isActive):\(store.connectionSession)") {
            guard isActive else { return }
            if decision.attempt?.state == .failed { actions.sendingPresentation = false }
            await decision.load(item: live, api: store.client, shownAt: .now)
            guard !Task.isCancelled else { return }
            if decision.accepted { actions.finishReply(onResolve: onResolve) { decision.clearAcceptedDraft() } }
            else if decision.loaded, decision.events.isEmpty, decision.composer.draft.isEmpty, decision.composer.attachments.isEmpty, decision.attempt == nil {
                actions.finish(onResolve: onResolve)
            }
        }
    }
}

extension AppStore {
    /// The view's current facts for a message card. The card reads its run again when
    /// shown, keeping the read-ahead run on screen meanwhile.
    func reviewMessageFacts(_ item: ReviewItem) -> ReviewItem {
        var current = review?.items.first { $0.id == item.id } ?? item
        // The owner reference distinguishes a replaced or cleared conversation from missing data.
        if let owner = item.owner, let reference = currentConversationID(owner) {
            current.addressCurrent = reference == item.conversationId
        }
        return current
    }
}

/// One screen's reply and Mark read on a message card. The card and an artifact opened from
/// it each own one on the card's shared ReviewMessageDecision, so a covered card never
/// finishes for the screen on top.
@MainActor @Observable final class ReviewMessageActions {
    var capturing = false
    var sendingPresentation = false
    private var outgoingAttempt: PendingWork?
    private(set) var finished = false
    func displayedAttempt(_ decision: ReviewMessageDecision) -> PendingWork? { sendingPresentation ? outgoingAttempt : decision.attempt }
    func locks(_ decision: ReviewMessageDecision) -> Bool { decision.busy || capturing || sendingPresentation || finished }

    func markRead(_ decision: ReviewMessageDecision, onResolve: @escaping (_ afterExit: (() -> Void)?) -> Void) {
        Haptics.light()
        Task {
            if await decision.markRead() { Haptics.success(); finish(onResolve: onResolve) } else { ReviewStyle.error() }
        }
    }
    func send(_ decision: ReviewMessageDecision, live: ReviewItem, onResolve: @escaping (_ afterExit: (() -> Void)?) -> Void) async -> Bool {
        guard !sendingPresentation, live.addressCurrent else { return false }
        decision.updateAddressCurrent(live.addressCurrent)
        outgoingAttempt = decision.attempt
        sendingPresentation = true
        let accepted = await decision.send()
        if !decision.accepted { sendingPresentation = false }
        // Leave in the same turn as the save, before the saved work hides the card.
        else { finishReply(onResolve: onResolve) { decision.clearAcceptedDraft() } }
        return accepted
    }
    /// A reply is saved. `clearDraft` runs once the exit completes, so the composer keeps
    /// its geometry until then.
    func finishReply(onResolve: (_ afterExit: (() -> Void)?) -> Void, clearDraft: @escaping () -> Void) {
        guard !finished else { return }
        Haptics.success()
        finish(onResolve: onResolve, afterExit: clearDraft)
    }
    func finish(onResolve: (_ afterExit: (() -> Void)?) -> Void, afterExit: (() -> Void)? = nil) {
        guard !finished else { return }
        finished = true
        onResolve(afterExit)
    }
}

/// The message's dock: the `Reply…` composer with its inline send, then Skip and Mark read.
/// The card and an artifact opened from it each mount one on the card's shared
/// ReviewMessageDecision, so a reply typed in either is the same draft.
struct ReviewMessageDock: View {
    let item: ReviewItem
    let isActive: Bool
    let decision: ReviewMessageDecision
    let actions: ReviewMessageActions
    let onSkip: () -> Void
    let onResolve: (_ afterExit: (() -> Void)?) -> Void
    @Environment(AppStore.self) private var store
    private var live: ReviewItem { store.reviewMessageFacts(item) }

    var body: some View {
        ReviewDock {
            if live.addressCurrent, let owner = item.owner {
                let attempt = actions.displayedAttempt(decision)
                ConversationComposer(model: decision.composer, owner: owner, reviewReply: true, placeholder: "Reply…",
                                     sendReply: { await actions.send(decision, live: live, onResolve: onResolve) },
                                     statusNote: attempt?.state == .failed ? attempt?.note : nil,
                                     retryReply: attempt?.state == .failed, onCaptureChanged: { actions.capturing = $0 })
                    .disabled(!decision.loaded || decision.events.isEmpty || decision.busy || actions.finished
                              || (decision.attempt != nil && decision.attempt?.state != .failed))
                ReviewButtonRow {
                    skipButton
                    Button("Mark read") { actions.markRead(decision, onResolve: onResolve) }
                        .buttonStyle(ReviewButtonStyle()).accessibilityIdentifier("review.markRead")
                        .disabled(decision.through == 0 || decision.busy || actions.capturing || actions.finished)
                }
            } else {
                skipButton
            }
        }
        .onChange(of: live.addressCurrent, initial: true) { _, current in decision.updateAddressCurrent(current) }
        .onChange(of: decision.attempt?.state) { _, state in
            guard isActive else { return }
            if state == .failed { ReviewStyle.error(); actions.sendingPresentation = false }
        }
        .onChange(of: decision.accepted) { _, accepted in
            if isActive, accepted { actions.finishReply(onResolve: onResolve) { decision.clearAcceptedDraft() } }
        }
    }

    private var skipButton: some View {
        Button("Skip") { Haptics.selection(); onSkip() }
            .buttonStyle(ReviewButtonStyle()).accessibilityIdentifier("review.skip")
            .disabled(actions.locks(decision))
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
                    PanelsMarkdown(source: text, baseURL: store.baseURL)
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
