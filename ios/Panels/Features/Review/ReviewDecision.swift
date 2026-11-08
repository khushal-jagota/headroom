import Foundation
import Observation

/// A draft belongs to the exact proposal the person read, including its revision.
@MainActor @Observable final class ReviewDecision {
    /// The Ticket's view stamp when the last load started; a read-ahead taken before a
    /// later change is not current when its card is shown.
    var loadedStamp: String?
    var ticket: TicketDetail?
    var manifest: ReviewWorkerManifest?
    var draft = "" { didSet { persistDraft(draft, old: oldValue, suffix: ":edit") } }
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
        let id = "review:\(ticket.id):\(proposal.field):\(proposal.createdAt):\(proposal.revision)"
        guard draftIdentity != id || discard else { return }
        restoringDrafts = true
        draftServer = LocalStore.server(api.baseURL); draftIdentity = id
        draft = discard ? proposal.body : LocalStore.shared.draft(server: draftServer!, id: id + ":edit") ?? proposal.body
        revisionDraft = LocalStore.shared.draft(server: draftServer!, id: id + ":guidance") ?? ""
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
    var approvalStages: [ReviewWorkerManifest.Stage] { manifest?.approvalStages(after: ticket?.stage ?? "") ?? [] }
    var ready: Bool { proposal != nil && !changed && !busy && !resolved && !approvalStages.isEmpty }

    static func matches(_ lhs: TicketProposal, _ rhs: TicketProposal) -> Bool {
        lhs.field == rhs.field && lhs.createdAt == rhs.createdAt && lhs.revision == rhs.revision
    }

    func load(item: ReviewItem, api: APIClient, discardDraft: Bool = false) async {
        while loading {
            do { try await Task.sleep(for: .milliseconds(40)) } catch { return }
        }
        guard !busy, !resolved, !Task.isCancelled else { return }
        if ticket == nil, let saved = api.cachedTicket(item.ticketId), let proposal = saved.pendingProposal,
           proposal.field == item.field, proposal.createdAt == item.waitingSince {
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
                  incoming.createdAt == item.waitingSince else {
                changed = true
                error = "This proposal is no longer waiting. Refresh Review for the latest queue."
                return
            }
            if let existing = proposal, !Self.matches(existing, incoming), (draft != existing.body || !revisionDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty), !discardDraft {
                changed = true
                error = "The proposal changed while you were editing. Your draft is kept below. Reload to read the new version."
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
            ticket = fetched; manifest = lifecycle; changed = false; error = nil
        } catch {
            guard !Task.isCancelled else { return }
            self.error = error.localizedDescription
        }
    }

    func rename(api: APIClient, title: String) async -> Bool {
        guard !busy, let ticket, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return false }
        busy = true
        defer { busy = false }
        do {
            try await api.renameReviewTicket(ticket.id, title: title)
            self.ticket = try await api.ticket(ticket.id)
            error = nil
            return true
        } catch { self.error = error.localizedDescription; return false }
    }

    func approve(api: APIClient, ceiling: String, holder: Principal) async -> Bool {
        guard ready, let ticket, let proposal, approvalStages.contains(where: { $0.id == ceiling }) else { return false }
        let body = ReviewApproval(expectedProposalRevision: proposal.revision,
                                  editedBody: draft == proposal.body ? nil : draft,
                                  nextCeiling: ceiling, nextHolder: holder)
        return enqueue(PendingWork(id: UUID().uuidString, created: .now, kind: .approval,
            ticketID: ticket.id, proposal: proposal, approval: body,
            ticketTitle: ticket.title, proposalFieldLabel: manifest?.fields.first { $0.id == proposal.field }?.label ?? proposal.field.capitalized), api: api)
    }

    func sendBack(api: APIClient, message: String) async -> Bool {
        let message = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ready, canReturn, !message.isEmpty, let ticket else { return false }
        guard let proposal else { return false }
        return enqueue(PendingWork(id: UUID().uuidString, created: .now, kind: .sendBack,
            ticketID: ticket.id, proposal: proposal, guidance: message,
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

/// Skip is local, like the web. It never resolves a proposal or reduces the live badge.
/// Skip acts on a whole card; a decision resolves one part, and the card stays while
/// any part is unresolved. A new part on a skipped card brings the card back.
struct ReviewQueue {
    /// Skipped part identities, each with its card's identity.
    private(set) var skipped: [String: String] = [:]
    private(set) var resolved: Set<String> = []
    func pending(_ card: ReviewCard) -> [ReviewItem] { card.parts.filter { !resolved.contains($0.id) } }
    func isSkipped(_ card: ReviewCard) -> Bool {
        let parts = pending(card)
        return !parts.isEmpty && parts.allSatisfy { skipped[$0.id] != nil }
    }
    func current(in cards: [ReviewCard]) -> ReviewCard? {
        cards.first { !pending($0).isEmpty && !isSkipped($0) }
    }
    func skippedCount(in cards: [ReviewCard]) -> Int { cards.filter(isSkipped).count }
    /// Skipped cards, including those on pages that are no longer loaded.
    var skippedCardCount: Int { Set(skipped.filter { !resolved.contains($0.key) }.values).count }
    mutating func skip(_ card: ReviewCard) { for part in pending(card) { skipped[part.id] = card.id } }
    mutating func resolve(_ item: ReviewItem) { resolved.insert(item.id) }
    mutating func restore(_ item: ReviewItem) { resolved.remove(item.id); skipped[item.id] = nil }
    mutating func revisit() { skipped.removeAll() }
}
