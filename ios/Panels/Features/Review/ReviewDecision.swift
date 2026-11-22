import Foundation
import Observation

/// A decision submits only this proposal-bound snapshot of the visible note.
struct DecisionGuidance: Sendable, Equatable {
    let server: String
    let ticketID: String
    let field: String
    let proposalCreatedAt: Double
    let proposalRevision: Int
    let draftID: String
    let text: String
    let draftUpdatedAt: Date?

    static func draftIdentity(ticketID: String, proposal: TicketProposal) -> String {
        "review:\(ticketID):\(proposal.field):\(proposal.createdAt):\(proposal.revision)"
    }

    static func capture(text: String, server: String, ticketID: String, proposal: TicketProposal,
                        loadedServer: String?, loadedIdentity: String?, draftUpdatedAt: Date? = nil) -> DecisionGuidance? {
        let identity = draftIdentity(ticketID: ticketID, proposal: proposal)
        guard loadedServer == server, loadedIdentity == identity,
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return DecisionGuidance(server: server, ticketID: ticketID, field: proposal.field,
            proposalCreatedAt: proposal.createdAt, proposalRevision: proposal.revision,
            draftID: identity + ":guidance", text: text, draftUpdatedAt: draftUpdatedAt)
    }

    func matches(server: String, ticketID: String, proposal: TicketProposal) -> Bool {
        self.server == server && self.ticketID == ticketID && field == proposal.field
            && proposalCreatedAt == proposal.createdAt && proposalRevision == proposal.revision
    }
}

/// A reviewer or ceiling change is not a decision, so it never reaches the Outbox. The sheet
/// waits for the server and shows a failure inline. The change is a plain set, so a retry is safe.
@MainActor @Observable final class DecisionScopeChange {
    func save(api: APIClient, ticket: TicketDetail, ceiling: String?, holder: Principal) async throws -> TicketDetail {
        try await api.setTicketApprovalLimit(ticket.id, ceiling: ceiling, holder: holder)
        // APIClient.ticket always uses a network GET with reloadIgnoringLocalCacheData.
        return try await api.ticket(ticket.id)
    }
}

/// A draft belongs to the exact proposal the person read, including its revision.
@MainActor @Observable final class ReviewDecision {
    /// The Ticket's view stamp when the last load started; a read-ahead taken before a
    /// later change is not current when its card is shown.
    let scopeChange = DecisionScopeChange()
    var loadedStamp: String?
    var ticket: TicketDetail?
    var manifest: ReviewWorkerManifest?
    var revisionDraft = "" { didSet { persistDraft(revisionDraft, old: oldValue, suffix: ":guidance") } }
    private var draftServer: String?
    private var draftIdentity: String?
    var revisionDraftID: String? { draftIdentity.map { $0 + ":guidance" } }
    private var restoringDrafts = false
    private func persistDraft(_ text: String, old: String, suffix: String) {
        guard !restoringDrafts, text != old, let server = draftServer, let id = draftIdentity else { return }
        LocalStore.shared.saveDraft(text, server: server, id: id + suffix)
    }
    private func restoreDrafts(api: APIClient, ticket: TicketDetail, proposal: TicketProposal, discard: Bool = false) {
        let id = DecisionGuidance.draftIdentity(ticketID: ticket.id, proposal: proposal)
        guard draftIdentity != id || discard else { return }
        restoringDrafts = true
        draftServer = LocalStore.server(api.baseURL); draftIdentity = id
        revisionDraft = LocalStore.shared.draft(server: draftServer!, id: id + ":guidance") ?? ""
        restoringDrafts = false
    }
    /// A refused decision's card shows its saved note even when its proposal no longer waits.
    func restoreRefusedNote(server: String, item: ReviewItem) {
        guard draftIdentity == nil, let revision = item.proposalRevision else { return }
        let id = "review:\(item.ticketId):\(item.field):\(item.waitingSince):\(revision)"
        restoringDrafts = true
        draftServer = server; draftIdentity = id
        revisionDraft = LocalStore.shared.draft(server: server, id: id + ":guidance") ?? ""
        restoringDrafts = false
    }
    func refreshVoiceDraft() {
        guard let server = draftServer, let id = revisionDraftID else { return }
        let saved = LocalStore.shared.draft(server: server, id: id) ?? ""
        if saved != revisionDraft { restoringDrafts = true; revisionDraft = saved; restoringDrafts = false }
    }
    var error: String?
    var changed = false
    var loading = false
    var busy = false
    private(set) var resolved = false

    var proposal: TicketProposal? { ticket?.pendingProposal }
    var canReturn: Bool { proposal?.field != "brief" && ticket?.conversationId != nil }
    var nextStage: String? { manifest?.advance[ticket?.stage ?? ""] }
    var approvalStages: [ReviewWorkerManifest.Stage] { manifest?.approvalBoundaries ?? [] }
    var finalApproval: Bool { manifest?.isFinalApproval(at: ticket?.stage ?? "") == true }
    var ready: Bool { proposal != nil && !changed && !busy && !resolved && nextStage != nil && !approvalStages.isEmpty }

    static func matches(_ lhs: TicketProposal, _ rhs: TicketProposal) -> Bool {
        lhs.field == rhs.field && lhs.createdAt == rhs.createdAt && lhs.revision == rhs.revision
    }

    func load(item: ReviewItem, api: APIClient, discardDraft: Bool = false) async {
        while loading {
            do { try await Task.sleep(for: .milliseconds(40)) } catch { return }
        }
        guard !busy, !resolved, !Task.isCancelled else { return }
        if ticket == nil, let saved = api.cachedTicket(item.ticketId), let proposal = saved.pendingProposal,
           proposal.field == item.field, proposal.createdAt == item.waitingSince,
           item.proposalRevision == nil || proposal.revision == item.proposalRevision {
            ticket = saved
            manifest = saved.workflow.map(ReviewWorkerManifest.init(workflow:))
            restoreDrafts(api: api, ticket: saved, proposal: proposal)
        }
        loading = true
        defer { loading = false }
        do {
            let fetched = try await api.ticket(item.ticketId)
            guard !Task.isCancelled else { return }
            guard let incoming = fetched.pendingProposal, incoming.field == item.field,
                  incoming.createdAt == item.waitingSince,
                  item.proposalRevision == nil || incoming.revision == item.proposalRevision else {
                changed = true
                error = "This proposal is no longer waiting. Refresh Review for the latest queue."
                return
            }
            if let existing = proposal, !Self.matches(existing, incoming), !revisionDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !discardDraft {
                changed = true
                error = "The proposal changed. Your note is kept below. Reload to read the new version."
                return
            }
            let lifecycle: ReviewWorkerManifest
            if let workflow = fetched.workflow { lifecycle = ReviewWorkerManifest(workflow: workflow) }
            else { lifecycle = try await api.reviewManifest(fetched.workerType) }
            guard !Task.isCancelled else { return }
            guard lifecycle.fields.contains(where: { $0.id == incoming.field }) else {
                throw APIError(status: 0, message: "This proposal field is unavailable for its worker type.")
            }
            restoreDrafts(api: api, ticket: fetched, proposal: incoming, discard: discardDraft)
            ticket = fetched; manifest = lifecycle; changed = false
            let facts = (try? LocalStore.shared.outgoing.decisionSummaries(server: LocalStore.server(api.baseURL), ticketID: fetched.id)) ?? []
            let terminal = facts.last { $0.proposalField == incoming.field && $0.proposalRevision == incoming.revision && $0.proposalCreatedAt == incoming.createdAt && $0.refused }
            error = terminal?.note
        } catch {
            // A read that cannot reach the server says nothing: the card keeps what it
            // has, and its decision is saved and sent when the server answers.
            guard !Task.isCancelled, !(error is URLError) else { return }
            self.error = error.localizedDescription
        }
    }

    func acceptScopeRead(_ current: TicketDetail) {
        guard let existing = proposal, let incoming = current.pendingProposal, Self.matches(existing, incoming) else {
            changed = true
            error = "The proposal changed. Refresh Review before a decision. Your note is kept."
            return
        }
        ticket = current
    }

    func approvalGuidance(api: APIClient) -> DecisionGuidance? {
        guard ready, let ticket, let proposal else { return nil }
        let server = LocalStore.server(api.baseURL)
        let captured = revisionDraftID.flatMap { try? LocalStore.shared.outgoing.captureDraft(server: server, id: $0).text }
        let updated = captured?.payload == Data(revisionDraft.utf8) ? captured?.updated : nil
        return DecisionGuidance.capture(text: revisionDraft, server: server,
            ticketID: ticket.id, proposal: proposal, loadedServer: draftServer, loadedIdentity: draftIdentity, draftUpdatedAt: updated)
    }

    func approve(api: APIClient, ceiling: String? = nil, holder: Principal? = nil, guidance: DecisionGuidance? = nil) async -> Bool {
        guard ready, let ticket, let proposal else { return false }
        if !finalApproval {
            guard let ceiling, let _ = holder, manifest?.ceilingRange.contains(ceiling) == true,
                  manifest?.selectionApprovesPending(ceiling: ceiling, waitingStage: ticket.stage) == true else { return false }
        }
        guard guidance == nil || guidance?.matches(server: LocalStore.server(api.baseURL), ticketID: ticket.id, proposal: proposal) == true else {
            error = "The proposal changed. Close approval and review it again."
            return false
        }
        let body = ReviewApproval(expectedProposalRevision: proposal.revision,
                                  editedBody: nil,
                                  nextCeiling: finalApproval ? nil : ceiling, nextHolder: finalApproval ? nil : holder)
        return enqueue(PendingWork(id: UUID().uuidString, created: .now, kind: .approval,
            ticketID: ticket.id, proposal: proposal, approval: body, guidance: guidance?.text, draftID: guidance?.draftID, decisionDraftUpdatedAt: guidance?.draftUpdatedAt,
            ticketTitle: ticket.title, proposalFieldLabel: manifest?.fields.first { $0.id == proposal.field }?.label ?? proposal.field.capitalized), api: api)
    }

    func sendBack(api: APIClient, message: String) async -> Bool {
        guard ready, canReturn, !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, let ticket else { return false }
        guard let proposal else { return false }
        return enqueue(PendingWork(id: UUID().uuidString, created: .now, kind: .sendBack,
            ticketID: ticket.id, proposal: proposal, guidance: message, draftID: revisionDraftID,
            ticketTitle: ticket.title, proposalFieldLabel: manifest?.fields.first { $0.id == proposal.field }?.label ?? proposal.field.capitalized), api: api)
    }

    private func enqueue(_ work: PendingWork, api: APIClient) -> Bool {
        do {
            try Outbox.shared.enqueue(work, api: api)
            resolved = true; error = nil
            return true
        } catch { self.error = "Couldn’t save this decision: \(error.localizedDescription)"; return false }
    }
}

/// One snapshot for Review and its badge. Rendering reads memory, never the store.
@MainActor @Observable final class ReviewSavedWork {
    struct Key: Equatable {
        let server: String
        let revision: Int
    }
    @ObservationIgnored private let outbox: Outbox
    @ObservationIgnored private let read: (String) -> [OutgoingStore.Summary]
    private(set) var key: Key?
    private var saved: [OutgoingStore.Summary] = []
    init(outbox: Outbox = .shared, read: ((String) -> [OutgoingStore.Summary])? = nil) {
        self.outbox = outbox
        self.read = read ?? outbox.reviewSummaries
    }
    func refresh(_ server: String) {
        let next = Key(server: server, revision: outbox.store.outgoingRevision(server))
        guard next != key else { return }
        saved = read(server)
        key = next
    }
    func decisions(_ server: String) -> [OutgoingStore.Summary] { key?.server == server ? saved : [] }
    func messages(_ server: String, conversationID: String) -> [PendingWork] { outbox.entries(server, conversationID: conversationID) }
    func voice(_ server: String, draftID: String) -> [PendingWork] { outbox.entries(server, draftID: draftID) }
    /// Only a visible exact proposal can justify subtracting a pending card badge.
    static func pendingDecisionTickets(in items: [ReviewItem], decisions: [OutgoingStore.Summary]) -> Set<String> {
        Set(items.filter { item in
            item.isProposal && item.proposalRevision != nil && decisions.contains {
                decides($0, item) && $0.state == "waiting"
            }
        }.map(\.ticketId))
    }
    private static func decides(_ saved: OutgoingStore.Summary, _ item: ReviewItem) -> Bool {
        ["approval", "sendBack"].contains(saved.kind) && item.proposalRevision != nil && saved.proposalRevision == item.proposalRevision
            && saved.ticketID == item.ticketId && saved.proposalField == item.field && saved.proposalCreatedAt == item.waitingSince
    }
    /// A saved mark read or reply covers the messages read before it, until the server's
    /// own read point catches up. A newer message then shows again.
    private static func covers(_ saved: OutgoingStore.Summary, _ item: ReviewItem) -> Bool {
        ["markRead", "message"].contains(saved.kind) && saved.conversationID != nil && saved.conversationID == item.conversationId
            && (saved.ownerReadThroughSequence ?? 0) > item.ownerReadThroughSequence
    }
    /// Decided proposals and handled messages leave Review at once. A refusal brings them back.
    static func cards(_ items: [ReviewItem], decisions: [OutgoingStore.Summary]) -> [ReviewItem] {
        items.filter { item in
            item.isAsk || !decisions.contains { !$0.refused && (item.isMessage ? covers($0, item) : decides($0, item)) }
        }
    }
    /// Each proposal whose latest saved decision was refused, as a card. A card in the
    /// window keeps its own facts; one that left takes its Ticket's title from `title`.
    static func refusals(_ decisions: [OutgoingStore.Summary], live: [ReviewItem], title: (OutgoingStore.Summary) -> String?) -> [ReviewRefusal] {
        var latest: [String: OutgoingStore.Summary] = [:]
        var order: [String] = []
        for saved in decisions where ["approval", "sendBack"].contains(saved.kind) {
            guard let ticket = saved.ticketID, let field = saved.proposalField, let created = saved.proposalCreatedAt else { continue }
            let key = "\(ticket):\(field):\(created):\(saved.proposalRevision ?? 0)"
            if latest[key] == nil { order.append(key) }
            if latest[key].map({ $0.sequence < saved.sequence }) ?? true { latest[key] = saved }
        }
        return order.compactMap { key in
            guard let saved = latest[key], saved.refused, let ticket = saved.ticketID, let field = saved.proposalField,
                  let created = saved.proposalCreatedAt else { return nil }
            let item = live.first { $0.isProposal && decides(saved, $0) }
                ?? ReviewItem(reviewItemType: "proposal", ticketId: ticket, field: field, title: title(saved) ?? "Proposal",
                              waitingSince: created, proposalRevision: saved.proposalRevision)
            return ReviewRefusal(item: item, note: saved.note, summaryID: saved.id)
        }
    }
}
