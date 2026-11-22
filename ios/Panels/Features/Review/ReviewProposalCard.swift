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
    @State private var resolvedAfterDismiss = false
    @State private var approvalGuidance: DecisionGuidance?
    @State private var appeared = false
    private var server: String { LocalStore.server(store.baseURL) }
    private var recap: String? { decision.ticket?.recap ?? store.presentation?.ticketsById[item.ticketId]?.recap }
    /// A notice or a refusal of a proposal that no longer waits turns the decision off.
    private var actionable: Bool { notice == nil && decision.ready }
    private var reviewer: String {
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
                                         baseURL: store.baseURL, reviewerEnabled: actionable && busyAction == nil) {
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
            ReviewProposalDock(isActive: isActive, savedWork: savedWork, decision: decision, ready: actionable && sheet == nil,
                               busyAction: busyAction, error: decision.error == refusal || decision.changed ? nil : decision.error,
                               skipEnabled: busyAction == nil && !decision.busy,
                               onSkip: { onSkip(nil) }, onApprove: openApproval) {
                busyAction = "back"
                if await decision.sendBack(api: store.client, message: decision.revisionDraft) { complete() }
                else { busyAction = nil; ReviewStyle.error() }
            }
        }
        // The Ticket's record stays in the view while this card is on screen, so a move
        // to another reviewer can say who reviews it now.
        .navigationDemand("review.card:\(item.id)", selected: ViewOwner(kind: "ticket", id: item.ticketId))
        .task(id: "\(store.baseURL)|\(store.connectionSession)|\(isActive)") {
            guard isActive else { return }
            if refusal != nil { decision.restoreRefusedNote(server: server, item: item) }
            let firstAppearance = !appeared; appeared = true
            if firstAppearance, decision.proposal != nil, decision.loadedStamp == store.ticketStamp(item.ticketId) { return }
            guard sheet == nil, busyAction == nil, notice == nil else { return }
            await load()
        }
        .reload(onChangeOf: store.ticketStamp(item.ticketId)) {
            guard isActive, sheet == nil, busyAction == nil, notice == nil else { return }
            await load()
        }
        .decisionSheets($sheet, ticket: decision.ticket, manifest: decision.manifest, note: approvalGuidance?.text,
            onDismiss: {
                if resolvedAfterDismiss { resolvedAfterDismiss = false; leave() }
            },
            approve: { ceiling, holder in
                busyAction = "approve"
                if await decision.approve(api: store.client, ceiling: ceiling, holder: holder, guidance: approvalGuidance) { complete() }
                else {
                    busyAction = nil
                    throw APIError(status: 0, message: decision.error ?? "The decision could not be saved.")
                }
            },
            setReviewer: { holder in
                guard let ticket = decision.ticket else { return }
                let current = try await decision.scopeChange.save(api: store.client, ticket: ticket, ceiling: nil, holder: holder)
                decision.acceptScopeRead(current)
                // Moved to someone else here: the card leaves once the sheet closes, and
                // is held until then, so the move reads as Khushal's, not as a notice.
                if holder.kind != "owner" { onHold(); onResolveLater() }
                await store.refresh()
            })
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

    private func openApproval() {
        guard actionable else { return }
        approvalGuidance = decision.approvalGuidance(api: store.client)
        Haptics.light()
        if decision.finalApproval {
            busyAction = "approve"
            Task {
                if await decision.approve(api: store.client, guidance: approvalGuidance) { complete() }
                else { busyAction = nil; ReviewStyle.error() }
            }
        } else { sheet = .approve }
    }
    /// The decision is saved: the card leaves now, or once its sheet has closed.
    private func complete() {
        Haptics.light()
        // The decision is saved, so the saved work hides the card from now on.
        onHold()
        if sheet != nil { onResolveLater(); sheet = nil }
        else { leave() }
    }
    private func onResolveLater() { resolvedAfterDismiss = true }
    private func leave() { onResolve { busyAction = nil } }
}

private extension ReviewDecision {
    func fieldLabel(_ field: String) -> String { manifest?.fields.first(where: { $0.id == field })?.label ?? field.capitalized }
}

/// The proposal's dock: the note with its voice recording, and the decision row.
private struct ReviewProposalDock: View {
    let isActive: Bool
    let savedWork: ReviewSavedWork
    @Bindable var decision: ReviewDecision
    let ready: Bool
    let busyAction: String?
    let error: String?
    let skipEnabled: Bool
    let onSkip: () -> Void
    let onApprove: () -> Void
    let onReturn: () async -> Void
    @Environment(AppStore.self) private var store
    @State private var voice = ReviewVoiceCapture()
    @State private var focusRequest = 0
    @State private var blurRequest = 0
    @ScaledMetric(relativeTo: .body) private var microphoneSize: CGFloat = 17
    private var api: APIClient { store.client }

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
                                focusRequest: focusRequest, blurRequest: blurRequest,
                                onSkip: onSkip, skipEnabled: skipEnabled,
                                onApprove: onApprove, onSendBack: { Task { await onReturn() } })
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
    }
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
            case .transcribing: Text("transcribing"); Spacer(); RingSpinner()
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
