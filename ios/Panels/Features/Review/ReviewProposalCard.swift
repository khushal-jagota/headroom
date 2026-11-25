import SwiftUI

/// A proposal card: the stage and its reviewer, the read-only proposal, then the note for
/// the worker with Skip, Return and Approve. The decision logic is ReviewDecision's.
struct ReviewProposalCard: View {
    let item: ReviewItem
    let isActive: Bool
    let savedWork: ReviewSavedWork
    @Bindable var decision: ReviewDecision
    /// The server's reason for refusing a saved decision on this proposal.
    let refusal: String?
    /// Set when the proposal moved to another reviewer while on screen.
    let notice: ReviewNotice?
    @Binding var cardHeight: CGFloat
    let motion: ReviewCardMotion
    let onSkip: (CGFloat?) -> Void
    /// Holds the card from the moment its decision is saved, while a sheet still closes.
    let onHold: () -> Void
    let onResolve: (_ afterExit: (() -> Void)?) -> Void
    @Environment(AppStore.self) private var store
    @State private var sheet: DecisionSheet?
    @State private var busyAction: String?
    @State private var appeared = false
    private var recap: String? { decision.ticket?.recap ?? store.presentation?.ticketsById[item.ticketId]?.recap }
    /// A notice or a refusal of a proposal that no longer waits turns the decision off.
    private var actionable: Bool { notice == nil && decision.ready }
    private var reviewer: String {
        if let notice { return notice.reviewer }
        guard let ticket = decision.ticket else { return "You" }
        return ticket.ceilingHolder.kind == "owner" ? "You" : ticket.holderLabel(ticket.ceilingHolder)
    }

    var body: some View {
        ReviewCardFrame(title: decision.ticket?.title ?? item.title, recap: recap, motion: motion,
                        locked: busyAction != nil || decision.busy, onSkip: onSkip, refresh: { await refresh() }) {
            VStack(alignment: .leading, spacing: 14) {
                if let notice {
                    ReviewInlineNote(tone: .info, title: "Reviewer changed", text: "\(notice.reviewer) reviews this now.")
                }
                if let refusal {
                    ReviewInlineNote(tone: .refused, title: "Refused", text: refusal)
                }
                if let proposal = decision.proposal {
                    DecisionProposalCard(fieldLabel: decision.fieldLabel(item.field), reviewer: reviewer, text: proposal.body,
                                         baseURL: store.baseURL, dock: .reviewProposal(itemID: item.id), reviewerEnabled: actionable && busyAction == nil) {
                        if actionable && busyAction == nil { sheet = .reviewer }
                    }
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { cardHeight = $0 }
                } else if decision.error == nil && refusal == nil {
                    ScreenLoadingView(title: "review").frame(height: cardHeight)
                }
                if let error = decision.error, refusal == nil, decision.proposal == nil || decision.changed {
                    Text(error).panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.Web.error)
                    Button(decision.changed ? "Reload proposal" : "Try again") {
                        Task { await store.refresh(); await decision.load(item: item, api: store.client, discardDraft: decision.changed) }
                    }.disabled(decision.busy).panelsScaledFont(size: 13)
                }
            }
            .opacity(busyAction != nil ? 0.4 : 1)
        } dock: {
            ReviewProposalDock(isActive: isActive, savedWork: savedWork, decision: decision, refusal: refusal, notice: notice,
                               sheet: $sheet, busyAction: $busyAction, onSkip: { onSkip(nil) }, onHold: onHold, onResolve: onResolve)
        }
        // The Ticket's record stays in the view while this card is on screen, so a move
        // to another reviewer can say who reviews it now.
        .navigationDemand("review.card:\(item.id)", selected: ViewOwner(kind: "ticket", id: item.ticketId))
        .task(id: "\(store.baseURL)|\(store.connectionSession)|\(isActive)") {
            guard isActive else { return }
            if refusal != nil { decision.restoreRefusedNote(api: store.client, item: item) }
            let firstAppearance = !appeared; appeared = true
            if firstAppearance, decision.proposal != nil, decision.loadedStamp == store.ticketStamp(item.ticketId) { return }
            guard sheet == nil, busyAction == nil, notice == nil else { return }
            await load()
        }
        .reload(onChangeOf: store.ticketStamp(item.ticketId)) {
            guard isActive, sheet == nil, busyAction == nil, notice == nil else { return }
            await load()
        }
    }

    private func load() async {
        let stamp = store.ticketStamp(item.ticketId)
        await decision.load(item: item, api: store.client)
        decision.loadedStamp = stamp
    }
    private func refresh() async {
        guard notice == nil else { return }
        await decision.load(item: item, api: store.client)
    }
}

private extension ReviewDecision {
    func fieldLabel(_ field: String) -> String { manifest?.fields.first(where: { $0.id == field })?.label ?? field.capitalized }
}

/// The proposal's dock: the note with its voice recording and attachments, Skip, Return
/// and Approve, and the approval and reviewer sheets. The card and an artifact opened from
/// it each mount one on the card's shared ReviewDecision, so a note typed in either is the
/// same draft. The sheet, the busy action and the exit belong to the screen that mounts it,
/// so a covered card never presents or leaves for the screen on top.
struct ReviewProposalDock: View {
    let isActive: Bool
    let savedWork: ReviewSavedWork
    @Bindable var decision: ReviewDecision
    /// The server's reason for refusing a saved decision on this proposal.
    let refusal: String?
    /// Set when the proposal moved to another reviewer while on screen.
    let notice: ReviewNotice?
    @Binding var sheet: DecisionSheet?
    @Binding var busyAction: String?
    let onSkip: () -> Void
    /// Holds the card from the moment its decision is saved, while a sheet still closes.
    let onHold: () -> Void
    /// Leaves once the decision is saved and any sheet has closed.
    let onResolve: (_ afterExit: (() -> Void)?) -> Void
    @Environment(AppStore.self) private var store
    @State private var exit = ReviewCardExit()
    @State private var approvalGuidance: DecisionGuidance?
    @State private var voice = ReviewVoiceCapture()
    @State private var focusRequest = 0
    @State private var blurRequest = 0
    @ScaledMetric(relativeTo: .body) private var microphoneSize: CGFloat = 17
    private var api: APIClient { store.client }
    /// A notice or a refusal of a proposal that no longer waits turns the decision off.
    private var actionable: Bool { notice == nil && decision.ready }
    private var ready: Bool { actionable && sheet == nil }
    /// A refusal and a changed proposal say so where the dock's screen shows them.
    private var error: String? { decision.error == refusal || decision.changed ? nil : decision.error }

    var body: some View {
        VStack(spacing: 0) {
            if voice.phase != .idle { voiceStatus.padding(.horizontal, 20).padding(.top, 8).background(PanelsTheme.Web.surface) }
            DecisionNoteActions(note: $decision.revisionDraft, showsSendBack: decision.proposal?.field != "brief",
                                canReturn: decision.canReturn || decision.proposal == nil, ready: ready,
                                busyAction: busyAction, error: error ?? voice.error,
                                noteEnabled: voice.phase == .idle,
                                accessory: AnyView(Button {
                                    Haptics.light(); blurRequest += 1
                                    Task { await voice.start(api: api, draftID: decision.revisionDraftID) }
                                } label: {
                                    Image(systemName: "mic").resizable().scaledToFit()
                                        .frame(width: min(microphoneSize, 24), height: min(microphoneSize, 24))
                                        .frame(width: 40, height: 40).contentShape(Rectangle())
                                }
                                    .buttonStyle(.plain).foregroundStyle(PanelsTheme.Web.secondary)
                                    .accessibilityLabel("Record a note").disabled(!ready || decision.busy || voice.phase != .idle)),
                                attachments: decision.noteAttachments,
                                focusRequest: focusRequest, blurRequest: blurRequest,
                                onSkip: onSkip, skipEnabled: busyAction == nil && !decision.busy,
                                onApprove: openApproval, onSendBack: { Task { await sendBack() } })
        }
        .onChange(of: isActive, initial: true) { _, active in
            guard active else { voice.preserve(); return }
            savedWork.refresh(LocalStore.server(api.baseURL))
            decision.refreshVoiceDraft()
            restoreVoice()
        }
        .onChange(of: savedWork.key) { _, _ in
            let server = LocalStore.server(api.baseURL)
            guard isActive, savedWork.key == ReviewSavedWork.Key(server: server, revision: LocalStore.shared.outgoingRevision(server)) else { return }
            decision.refreshVoiceDraft()
            restoreVoice()
        }
        .onDisappear { voice.preserve() }
        // A move to another reviewer turns the decision off, an open sheet included.
        .onChange(of: notice != nil) { _, moved in if moved { sheet = nil } }
        .decisionSheets($sheet, ticket: decision.ticket, manifest: decision.manifest, note: approvalGuidance?.text,
            onDismiss: {
                if exit.dismissed() { leave() }
            },
            approve: { ceiling, holder in
                guard notice == nil else { throw APIError(status: 0, message: "Someone else reviews this proposal now.") }
                busyAction = "approve"
                if await decision.approve(api: api, ceiling: ceiling, holder: holder, guidance: approvalGuidance) { complete() }
                else {
                    busyAction = nil
                    throw APIError(status: 0, message: decision.error ?? "The decision could not be saved.")
                }
            },
            setReviewer: { holder in
                guard notice == nil, let ticket = decision.ticket else { return }
                // The card is busy while the change saves, so a swiped-away sheet cannot
                // open Approve on a proposal that is about to move.
                busyAction = "reviewer"
                let current: TicketDetail
                do { current = try await decision.scopeChange.save(api: api, ticket: ticket, ceiling: nil, holder: holder) }
                catch { busyAction = nil; throw error }
                decision.acceptScopeRead(current)
                // Moved to someone else here: the card is held so the move reads as
                // the owner's, not as a notice, and leaves once the sheet closes. The stream
                // can close the sheet first, so a save that finds it closed leaves now.
                // Leaving clears the busy state.
                if holder.kind != "owner" { onHold(); if exit.saved(sheetOpen: sheet != nil) { leave() } }
                else { busyAction = nil }
                // The change is saved, so the sheet closes now. The refresh can take a
                // slow connection's full timeout, and it finishes in the background.
                Task { await store.refresh() }
            })
    }

    private func openApproval() {
        guard actionable else { return }
        approvalGuidance = decision.approvalGuidance(api: api)
        Haptics.light()
        if decision.finalApproval {
            busyAction = "approve"
            Task {
                if await decision.approve(api: api, guidance: approvalGuidance) { complete() }
                else { busyAction = nil; ReviewStyle.error() }
            }
        } else { sheet = .approve }
    }
    private func sendBack() async {
        busyAction = "back"
        if await decision.sendBack(api: api, message: decision.revisionDraft) { complete() }
        else { busyAction = nil; ReviewStyle.error() }
    }
    /// The decision is saved: the card leaves now, or once its sheet has closed.
    private func complete() {
        Haptics.light()
        // The decision is saved, so the saved work hides the card from now on.
        onHold()
        if exit.saved(sheetOpen: sheet != nil) { leave() } else { sheet = nil }
    }
    private func leave() { onResolve { busyAction = nil } }
    private func restoreVoice() {
        voice.restore(api: api, draftID: decision.revisionDraftID,
                      savedWork: decision.revisionDraftID.map { savedWork.voice(LocalStore.server(api.baseURL), draftID: $0) } ?? [])
    }
    @ViewBuilder private var voiceStatus: some View {
        HStack(spacing: PanelsTheme.Space.md) {
            Button { Haptics.selection(); voice.cancel(); focusRequest += 1 } label: { Image(systemName: "xmark").frame(width: 44, height: 44) }.accessibilityLabel("Cancel recording")
            Spacer()
            switch voice.phase {
            case .recording:
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let elapsed = max(0, Int(context.date.timeIntervalSince(voice.startedAt)))
                    Text("recording \(elapsed / 60):\(String(format: "%02d", elapsed % 60))").monospacedDigit()
                }
                Spacer()
                Button { Haptics.light(); Task { append(await voice.stop(api: api)) } } label: { Image(systemName: "stop.fill").frame(width: 44, height: 44) }.background(PanelsTheme.accent, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg)).foregroundStyle(PanelsTheme.ink).accessibilityLabel("Stop and transcribe")
            case .saved:
                Text(voice.transcribing ? "transcribing" : "saved"); Spacer()
                if voice.transcribing { RingSpinner() }
            case .failed:
                Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.red).accessibilityLabel("Not transcribed")
                Spacer()
                Button("Retry") { Task { append(await voice.transcribe(api: api)) } }
            case .idle: EmptyView()
            }
        }.foregroundStyle(PanelsTheme.muted)
    }
    private func append(_ transcript: String?) {
        if transcript != nil { decision.refreshVoiceDraft() }
        focusRequest += 1
    }
}
