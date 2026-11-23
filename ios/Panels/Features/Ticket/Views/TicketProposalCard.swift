import SwiftUI
import Observation

/// Both pieces of the Ticket decision share the exact proposal and saved note.
@MainActor @Observable final class TicketDecisionState {
    var scopeChange = DecisionScopeChange()
    var guidance = "" {
        didSet {
            guard !restoring, let server, let identity, guidance != oldValue else { return }
            LocalStore.shared.saveDraft(guidance, server: server, id: identity + ":guidance")
        }
    }
    var sheet: DecisionSheet?
    var busyAction: String?
    var error: String?
    var savedDecision: OutgoingStore.Summary?
    var snapshot: Snapshot?
    /// The note's pictures and files, kept beside its words under the same draft
    /// identity as Review's note for this proposal.
    let noteAttachments = ConversationModel()
    private var server: String?
    private var identity: String?
    private var restoring = false

    struct Snapshot {
        let presentation: TicketPresentation
        let proposal: TicketProposal
        let api: APIClient
        let guidance: DecisionGuidance?
        var server: String { LocalStore.server(api.baseURL) }
        var identity: String { DecisionGuidance.draftIdentity(ticketID: presentation.ticket.id, proposal: proposal) }
        var label: String { presentation.manifest.fields.first { $0.id == proposal.field }?.label ?? proposal.field }
    }

    var pending: Bool {
        savedDecision.map { !$0.refused } ?? false
    }

    func load(presentation: TicketPresentation, proposal: TicketProposal, api: APIClient) {
        let nextServer = LocalStore.server(api.baseURL)
        let nextIdentity = DecisionGuidance.draftIdentity(ticketID: presentation.ticket.id, proposal: proposal)
        if server != nextServer || identity != nextIdentity {
            scopeChange = DecisionScopeChange()
            restoring = true
            server = nextServer; identity = nextIdentity
            guidance = LocalStore.shared.draft(server: nextServer, id: nextIdentity + ":guidance") ?? ""
            noteAttachments.restore(owner: .ticket(presentation.ticket.id), api: api, draftID: nextIdentity + ":guidance", recoverConversationID: false)
            restoring = false
            busyAction = nil; error = nil; snapshot = nil
        }
        refresh(presentation: presentation, proposal: proposal)
    }

    func matches(presentation: TicketPresentation, proposal: TicketProposal, api: APIClient) -> Bool {
        server == LocalStore.server(api.baseURL) && identity == DecisionGuidance.draftIdentity(ticketID: presentation.ticket.id, proposal: proposal)
    }

    func refresh(presentation: TicketPresentation, proposal: TicketProposal) {
        guard let server else { return }
        let facts = (try? LocalStore.shared.outgoing.decisionSummaries(server: server, ticketID: presentation.ticket.id)) ?? []
        savedDecision = facts.last {
            $0.proposalField == proposal.field && $0.proposalRevision == proposal.revision && $0.proposalCreatedAt == proposal.createdAt
        }
        if !pending { busyAction = nil }
        if savedDecision?.refused == true { error = savedDecision?.note }
    }

    func capture(presentation: TicketPresentation, proposal: TicketProposal, api: APIClient) -> Snapshot {
        let server = LocalStore.server(api.baseURL)
        let identity = DecisionGuidance.draftIdentity(ticketID: presentation.ticket.id, proposal: proposal)
        let captured = try? LocalStore.shared.outgoing.captureDraft(server: server, id: identity + ":guidance").text
        let updated = captured?.payload == Data(guidance.utf8) ? captured?.updated : nil
        let value = Snapshot(presentation: presentation, proposal: proposal, api: api,
            guidance: DecisionGuidance.capture(text: guidance, server: server, ticketID: presentation.ticket.id,
                proposal: proposal, loadedServer: self.server, loadedIdentity: self.identity, draftUpdatedAt: updated))
        snapshot = value
        return value
    }

    func approve(_ snapshot: Snapshot, ceiling: String? = nil, holder: Principal? = nil) async throws {
        let approval = ReviewApproval(expectedProposalRevision: snapshot.proposal.revision,
            editedBody: nil, nextCeiling: snapshot.presentation.finalApproval ? nil : ceiling,
            nextHolder: snapshot.presentation.finalApproval ? nil : holder)
        let attachments = try await preparedAttachments(snapshot, action: "approve")
        // The draft identity goes with pictures and files even without words, so an
        // applied receipt can clear them from the note.
        var work = PendingWork(id: UUID().uuidString, created: .now, kind: .approval,
            ticketID: snapshot.presentation.ticket.id, proposal: snapshot.proposal, approval: approval,
            guidance: snapshot.guidance?.text, draftID: snapshot.guidance?.draftID ?? (attachments.isEmpty ? nil : snapshot.identity + ":guidance"),
            decisionDraftUpdatedAt: snapshot.guidance?.draftUpdatedAt,
            ticketTitle: snapshot.presentation.ticket.title, proposalFieldLabel: snapshot.label)
        work.decisionAttachments = attachments.isEmpty ? nil : attachments
        try enqueue(work, snapshot: snapshot, action: "approve")
    }

    func sendBack(_ snapshot: Snapshot) async throws {
        guard let guidance = snapshot.guidance else { return }
        let attachments = try await preparedAttachments(snapshot, action: "back")
        var work = PendingWork(id: UUID().uuidString, created: .now, kind: .sendBack,
            ticketID: snapshot.presentation.ticket.id, proposal: snapshot.proposal, guidance: guidance.text,
            draftID: guidance.draftID, decisionDraftUpdatedAt: guidance.draftUpdatedAt,
            ticketTitle: snapshot.presentation.ticket.title, proposalFieldLabel: snapshot.label)
        work.decisionAttachments = attachments.isEmpty ? nil : attachments
        try enqueue(work, snapshot: snapshot, action: "back")
    }

    /// The note's prepared pictures and files, ready to freeze with a decision. The note
    /// and its actions stay locked while they are prepared.
    private func preparedAttachments(_ snapshot: Snapshot, action: String) async throws -> [ConversationSend.Piece] {
        guard server == snapshot.server, identity == snapshot.identity else { throw Self.changed }
        busyAction = action
        let pieces: [ConversationSend.Piece]
        do { pieces = try await noteAttachments.preparedContent().filter { $0.piece != "text" } }
        catch {
            // Another proposal loaded meanwhile: its card keeps its own state.
            guard server == snapshot.server, identity == snapshot.identity else { throw Self.changed }
            busyAction = nil
            let message = error is CancellationError ? "A photo is still being prepared." : error.localizedDescription
            self.error = message
            throw APIError(status: 0, message: message)
        }
        guard server == snapshot.server, identity == snapshot.identity else { throw Self.changed }
        return pieces
    }
    private static let changed = APIError(status: 0, message: "The proposal changed. Review it again.")

    private func enqueue(_ work: PendingWork, snapshot: Snapshot, action: String) throws {
        do {
            let facts = try LocalStore.shared.outgoing.decisionSummaries(server: snapshot.server, ticketID: snapshot.presentation.ticket.id)
            guard !facts.contains(where: {
                $0.proposalField == snapshot.proposal.field && $0.proposalRevision == snapshot.proposal.revision
                    && $0.proposalCreatedAt == snapshot.proposal.createdAt
                    && !$0.refused
            }) else { throw APIError(status: 0, message: "This proposal already has a saved decision.") }
        } catch { if !pending { busyAction = nil }; self.error = error.localizedDescription; throw error }
        busyAction = action
        do {
            try Outbox.shared.enqueue(work, api: snapshot.api)
            error = nil
            refresh(presentation: snapshot.presentation, proposal: snapshot.proposal)
        } catch { busyAction = nil; self.error = error.localizedDescription; throw error }
    }
}

struct TicketProposalCard: View {
    let presentation: TicketPresentation
    let proposal: TicketProposal
    @Bindable var decision: TicketDecisionState
    let refresh: () async -> Void
    @Environment(AppStore.self) private var store
    private var label: String { presentation.manifest.fields.first { $0.id == proposal.field }?.label ?? proposal.field }
    private var identity: String { LocalStore.server(store.baseURL) + ":" + DecisionGuidance.draftIdentity(ticketID: presentation.ticket.id, proposal: proposal) }

    var body: some View {
        DecisionProposalCard(fieldLabel: label, reviewer: presentation.holderLabel(presentation.ticket.ceilingHolder),
            text: proposal.body, baseURL: store.baseURL, dock: .conversation(.ticket(presentation.ticket.id))) {
                guard !decision.pending else { return }
                _ = decision.capture(presentation: presentation, proposal: proposal, api: store.client)
                decision.sheet = .reviewer
            }
            .opacity(decision.pending ? 0.4 : 1)
            .task(id: identity) { decision.load(presentation: presentation, proposal: proposal, api: store.client) }
            .onChange(of: LocalStore.shared.outgoingRevision(LocalStore.server(store.baseURL))) { _, _ in
                decision.refresh(presentation: presentation, proposal: proposal)
                if ["sent", "failed"].contains(decision.savedDecision?.state ?? "") { Task { await refresh() } }
            }
            .decisionSheets($decision.sheet, ticket: decision.snapshot?.presentation.ticket,
                manifest: decision.snapshot.map { ReviewWorkerManifest(workflow: $0.presentation.manifest) },
                note: decision.snapshot?.guidance?.text,
                onDismiss: {},
                approve: { ceiling, holder in
                    guard let snapshot = decision.snapshot else { return }
                    try await decision.approve(snapshot, ceiling: ceiling, holder: holder)
                },
                setReviewer: { holder in
                    guard let snapshot = decision.snapshot else { return }
                    _ = try await decision.scopeChange.save(api: snapshot.api, ticket: snapshot.presentation.ticket, ceiling: nil, holder: holder)
                    await refresh()
                })
    }
}

struct TicketDecisionDock: View {
    let presentation: TicketPresentation
    let proposal: TicketProposal
    @Bindable var decision: TicketDecisionState
    @Environment(AppStore.self) private var store
    private var nextStage: String? { presentation.manifest.advance?[presentation.ticket.stage] }

    var body: some View {
        DecisionNoteActions(note: $decision.guidance, showsSendBack: proposal.field != "brief",
            canReturn: presentation.ticket.conversationId != nil, ready: nextStage != nil && !decision.pending && decision.matches(presentation: presentation, proposal: proposal, api: store.client),
            busyAction: decision.busyAction, error: decision.error,
            noteEnabled: !decision.pending && decision.matches(presentation: presentation, proposal: proposal, api: store.client),
            attachments: decision.noteAttachments,
            onApprove: {
                let snapshot = decision.capture(presentation: presentation, proposal: proposal, api: store.client)
                if presentation.finalApproval {
                    // The decision shows its own failure on the card.
                    Task { try? await decision.approve(snapshot) }
                } else {
                    decision.sheet = .approve
                }
            }, onSendBack: {
                let snapshot = decision.capture(presentation: presentation, proposal: proposal, api: store.client)
                Task { try? await decision.sendBack(snapshot) }
            })
    }
}
