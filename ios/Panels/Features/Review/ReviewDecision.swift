import Foundation
import Observation

/// A draft belongs to the exact proposal the person read, including its revision.
@MainActor @Observable final class ReviewDecision {
    var ticket: TicketDetail?
    var manifest: ReviewWorkerManifest?
    var draft = ""
    var error: String?
    var changed = false
    var loading = false
    var busy = false
    private(set) var resolved = false

    var proposal: TicketProposal? { ticket?.pendingProposal }
    var canReturn: Bool { proposal?.field != "brief" && ticket?.conversationId != nil }
    var approvalStages: [ReviewWorkerManifest.Stage] { manifest?.approvalStages(after: ticket?.stage ?? "") ?? [] }
    var ready: Bool { proposal != nil && !changed && !loading && !busy && !resolved && !approvalStages.isEmpty }

    static func matches(_ lhs: TicketProposal, _ rhs: TicketProposal) -> Bool {
        lhs.field == rhs.field && lhs.createdAt == rhs.createdAt && lhs.revision == rhs.revision
    }

    func load(item: ReviewItem, api: APIClient, discardDraft: Bool = false) async {
        while loading {
            do { try await Task.sleep(for: .milliseconds(40)) } catch { return }
        }
        guard !busy, !resolved, !Task.isCancelled else { return }
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
            if let existing = proposal, !Self.matches(existing, incoming), draft != existing.body, !discardDraft {
                changed = true
                error = "The proposal changed while you were editing. Your draft is kept below. Reload to read the new version."
                return
            }
            let lifecycle = try await api.reviewManifest(fetched.workerType)
            guard !Task.isCancelled else { return }
            guard lifecycle.fields.contains(where: { $0.id == incoming.field }) else {
                throw APIError(status: 0, message: "This proposal field is unavailable for its worker type.")
            }
            if proposal == nil || discardDraft || !Self.matches(proposal!, incoming) { draft = incoming.body }
            ticket = fetched; manifest = lifecycle; changed = false; error = nil
        } catch {
            guard !Task.isCancelled else { return }
            self.error = error.localizedDescription
        }
    }

    func approve(api: APIClient, ceiling: String, holder: Principal) async -> Bool {
        guard ready, let ticket, let proposal, approvalStages.contains(where: { $0.id == ceiling }) else { return false }
        let body = ReviewApproval(expectedProposalRevision: proposal.revision,
                                  editedBody: draft == proposal.body ? nil : draft,
                                  nextCeiling: ceiling, nextHolder: holder)
        return await write(api: api) {
            try await api.approveReview(ticket.id, field: proposal.field, approval: body)
        }
    }

    func sendBack(api: APIClient, message: String) async -> Bool {
        let message = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ready, canReturn, !message.isEmpty, let ticket else { return false }
        return await write(api: api) { try await api.returnReview(ticket.id, message: message) }
    }

    private func write(api: APIClient, operation: () async throws -> Void) async -> Bool {
        guard let ticket, let proposal else { return false }
        busy = true; error = nil
        defer { busy = false }
        do {
            // Also protects send-back, whose existing backend API has no revision parameter.
            let latest = try await api.ticket(ticket.id)
            guard let current = latest.pendingProposal, Self.matches(proposal, current) else {
                changed = true
                throw APIError(status: 409, message: "This proposal changed. Reload it before deciding.")
            }
            try Task.checkCancellation()
            try await operation()
            resolved = true
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }
}

/// Skip is local, like the web. It never resolves a proposal or reduces the live badge.
struct ReviewQueue {
    private(set) var skipped: Set<String> = []
    private(set) var resolved: Set<String> = []
    func current(in items: [ReviewItem]) -> ReviewItem? {
        items.first { !skipped.contains($0.id) && !resolved.contains($0.id) }
    }
    mutating func skip(_ item: ReviewItem) { skipped.insert(item.id) }
    mutating func resolve(_ item: ReviewItem) { resolved.insert(item.id) }
    mutating func revisit() { skipped.removeAll() }
}
