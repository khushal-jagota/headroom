import SwiftUI

struct TicketProposalCard: View {
    let presentation: TicketPresentation
    let proposal: TicketProposal
    let refresh: () async -> Void
    @Environment(AppStore.self) private var store
    @State private var approveOpen = false
    @State private var returnOpen = false
    @State private var scopeOpen = false
    @State private var editing = false
    @State private var draft = ""
    @State private var guidance = ""
    @State private var loadedDraftIdentity: String?
    @State private var loadedDraftServer: String?
    @State private var decisionSnapshot: DecisionSnapshot?
    private struct DecisionSnapshot {
        let presentation: TicketPresentation
        let proposal: TicketProposal
        let api: APIClient
        let draft: String
        let guidance: String
        let label: String
        var server: String { LocalStore.server(api.baseURL) }
        var draftIdentity: String { "review:\(presentation.ticket.id):\(proposal.field):\(proposal.createdAt):\(proposal.revision)" }
    }
    private func captureDecision() {
        decisionSnapshot = DecisionSnapshot(presentation: presentation, proposal: proposal, api: store.client,
                                            draft: draft, guidance: guidance, label: label)
    }
    @State private var savedDecision: OutgoingStore.Summary?
    private var server: String { LocalStore.server(store.baseURL) }
    private var draftIdentity: String { "review:\(presentation.ticket.id):\(proposal.field):\(proposal.createdAt):\(proposal.revision)" }
    private var decisionPending: Bool { savedDecision.map { ["waiting", "sending", "uncertain", "serverQueued", "sent"].contains($0.state) } ?? false }
    private func refreshSavedDecision() {
        let facts = (try? LocalStore.shared.outgoing.decisionSummaries(server: server, ticketID: presentation.ticket.id)) ?? []
        let next = facts.last { $0.proposalField == proposal.field && $0.proposalRevision == proposal.revision && $0.proposalCreatedAt == proposal.createdAt }
        let changed = next?.id != savedDecision?.id || next?.state != savedDecision?.state
        savedDecision = next
        if changed, let next, ["sent", "changed"].contains(next.state) { Task { await refresh() } }
    }
    private func enqueue(_ work: PendingWork, api: APIClient) throws {
        if let proposal = work.proposal, let ticketID = work.ticketID {
            let facts = try LocalStore.shared.outgoing.decisionSummaries(server: LocalStore.server(api.baseURL), ticketID: ticketID)
            if facts.contains(where: { $0.proposalField == proposal.field && $0.proposalRevision == proposal.revision && $0.proposalCreatedAt == proposal.createdAt && ["waiting", "sending", "uncertain", "serverQueued", "sent"].contains($0.state) }) {
                throw APIError(status: 0, message: "This proposal already has a saved decision.")
            }
        }
        try Outbox.shared.enqueue(work, api: api)
        refreshSavedDecision()
    }
    private var label: String { presentation.manifest.fields.first { $0.id == proposal.field }?.label ?? proposal.field }

    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            Button { scopeOpen = true } label: {
                Text("\(label) — for \(presentation.holderLabel(presentation.ticket.ceilingHolder)) to approve")
                    .font(PanelsTheme.TypeScale.heading).foregroundStyle(PanelsTheme.strong).multilineTextAlignment(.leading)
            }.buttonStyle(.plain)
            if editing {
                TextEditor(text: $draft).frame(minHeight: 160).scrollContentBackground(.hidden)
                    .padding(PanelsTheme.Space.sm).background(PanelsTheme.recessed)
                Button("Done editing") { editing = false }
            } else {
                TicketExpandableText(text: draft, emptyText: "")
                    .contextMenu { Button("Edit proposal", systemImage: "pencil") { editing = true } }
            }
            HStack(spacing: PanelsTheme.Space.lg) {
                if proposal.field != "brief" {
                    Button("Send it back") { captureDecision(); returnOpen = true }
                        .buttonStyle(ReviewActionStyle())
                        .disabled(presentation.ticket.conversationId == nil || decisionPending)
                }
                Button("Approve") { captureDecision(); approveOpen = true }
                    .buttonStyle(ReviewActionStyle(approval: true))
                    .disabled(presentation.manifest.advance?[presentation.ticket.stage] == nil || decisionPending)
            }
            if let savedDecision, savedDecision.state == "changed" {
                Text(savedDecision.note).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
            }
            if proposal.field != "brief" && presentation.ticket.conversationId == nil {
                Text("No existing Worker conversation to send a revision to.").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
            }
        }
        .task(id: server + ":" + draftIdentity) {
            loadedDraftServer = server
            loadedDraftIdentity = draftIdentity
            draft = LocalStore.shared.draft(server: server, id: draftIdentity + ":edit") ?? proposal.body
            guidance = LocalStore.shared.draft(server: server, id: draftIdentity + ":guidance") ?? ""
            refreshSavedDecision()
        }
        .onChange(of: draft) { _, value in
            if let loadedDraftIdentity, let loadedDraftServer { LocalStore.shared.saveDraft(value, server: loadedDraftServer, id: loadedDraftIdentity + ":edit") }
        }
        .onChange(of: LocalStore.shared.outgoingRevision(server)) { _, _ in refreshSavedDecision() }
        .sheet(isPresented: $approveOpen) {
            if let snapshot = decisionSnapshot {
                TicketApprovalLimitSheet(presentation: snapshot.presentation, approval: true) { ceiling, holder in
                    guard let ceiling else { return }
                    try LocalStore.shared.put(server: snapshot.server, kind: "draft", id: snapshot.draftIdentity + ":edit", data: Data(snapshot.draft.utf8))
                    let approval = ReviewApproval(expectedProposalRevision: snapshot.proposal.revision,
                        editedBody: snapshot.draft == snapshot.proposal.body ? nil : snapshot.draft,
                        nextCeiling: ceiling, nextHolder: holder)
                    try enqueue(PendingWork(id: UUID().uuidString, created: .now, kind: .approval,
                        ticketID: snapshot.presentation.ticket.id, proposal: snapshot.proposal, approval: approval,
                        ticketTitle: snapshot.presentation.ticket.title, proposalFieldLabel: snapshot.label), api: snapshot.api)
                }
            }
        }
        .sheet(isPresented: $scopeOpen) {
            TicketApprovalLimitSheet(presentation: presentation) { ceiling, holder in
                try await store.client.setTicketApprovalLimit(presentation.ticket.id, ceiling: ceiling, holder: holder)
                await refresh()
            }
        }
        .sheet(isPresented: $returnOpen) {
            if let snapshot = decisionSnapshot {
                TicketTextEditor(title: "What needs changing", actionTitle: "Send it back", requiresText: true, save: { text in
                    let capturedGuidance = text.trimmingCharacters(in: .whitespacesAndNewlines)
                    try LocalStore.shared.put(server: snapshot.server, kind: "draft", id: snapshot.draftIdentity + ":guidance", data: Data(capturedGuidance.utf8))
                    if snapshot.server == server && snapshot.draftIdentity == draftIdentity { guidance = capturedGuidance }
                    try enqueue(PendingWork(id: UUID().uuidString, created: .now, kind: .sendBack,
                        ticketID: snapshot.presentation.ticket.id, proposal: snapshot.proposal, guidance: capturedGuidance,
                        ticketTitle: snapshot.presentation.ticket.title, proposalFieldLabel: snapshot.label), api: snapshot.api)
                }, text: snapshot.guidance)
            }
        }
    }
}
