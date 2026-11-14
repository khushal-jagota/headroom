import Foundation
import Observation

/// The final Review page uses Chief's ordinary draft and the existing durable outbox.
@MainActor @Observable final class ReviewChiefMessageModel {
    static let attemptDraftID = "review-chief-attempt"
    static let chiefDraftID = "conversation:" + ConversationOwner.chief.path!
    let composer = ConversationModel()
    private(set) var attempt: PendingWork?
    private(set) var busy = false
    var error: String?
    private(set) var storageError: String?
    private var generation = 0
    private var api: APIClient?
    private var server: String { api.map { LocalStore.server($0.baseURL) } ?? "" }
    var blocked: Bool {
        busy || storageError != nil || attempt.map { [.waiting, .sending, .uncertain].contains($0.state) } == true
    }
    var canCheckDelivery: Bool { storageError == nil && attempt?.state == .uncertain }
    var note: String? {
        guard let attempt else { return nil }
        switch attempt.state {
        case .sent: return "Sent to Chief"
        case .serverQueued: return "Queued for Chief"
        case .failed, .changed:
            return composer.draft.isEmpty && composer.attachments.isEmpty
                ? "The message did not reach Chief. Open Chief to review the saved message."
                : "The message did not reach Chief. Your draft is ready to retry."
        case .uncertain: return "Delivery is uncertain. Your draft is kept."
        default: return attempt.note
        }
    }
    func restore(api: APIClient) {
        if self.api?.baseURL != api.baseURL { generation += 1; busy = false; attempt = nil; error = nil }
        self.api = api
        composer.restore(owner: .chief, api: api, recoverConversationID: false)
        composer.reloadDraft()
        composer.deliveryMode = "queue"
        refreshAttempt()
    }
    func refreshAttempt() {
        guard api != nil else { return }
        do {
            let summaries = try LocalStore.shared.outgoing.summaries(server: server, draftID: Self.attemptDraftID)
            if let summary = summaries.last(where: \.scheduled) {
                guard let saved = try LocalStore.shared.outgoing.work(server: server, id: summary.id) else {
                    throw OutgoingStore.StoreError.missingWork
                }
                attempt = saved
            } else { attempt = nil }
            storageError = nil
            if let attempt, [.sent, .serverQueued].contains(attempt.state) { clearAcceptedDraft(attempt) }
        } catch {
            storageError = "The saved delivery could not be read. Open Chief before you send again."
        }
    }
    /// Clear only the original draft. A newer edit, even identical words, belongs to the user.
    private func clearAcceptedDraft(_ work: PendingWork) {
        guard let content = work.message?.content else { return }
        do {
            let text = content.compactMap(\.text).joined(separator: "\n")
            if let record = try LocalStore.shared.record(server: server, kind: "draft", id: Self.chiefDraftID),
               record.updatedAt <= work.created,
               String(data: record.payload, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) == text {
                try LocalStore.shared.put(server: server, kind: "draft", id: Self.chiefDraftID, data: Data())
            }
            if let record = try LocalStore.shared.record(server: server, kind: "attachments", id: Self.chiefDraftID),
               record.updatedAt <= work.created,
               let saved = try? JSONDecoder().decode([ConversationSend.Piece].self, from: record.payload),
               try JSONEncoder().encode(saved) == JSONEncoder().encode(content.filter { $0.piece != "text" }) {
                try LocalStore.shared.put(server: server, kind: "attachments", id: Self.chiefDraftID, data: Data("[]".utf8))
            }
            composer.reloadDraft()
        } catch {
            storageError = "The accepted message could not clear its saved draft. Open Chief before you send again."
        }
    }
    func send() async -> Bool {
        guard let api else { return false }
        refreshAttempt()
        guard !blocked else { return false }
        let text = composer.draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let content: [ConversationSend.Piece] = (text.isEmpty ? [] : [.init(text: text)]) + composer.attachments
        guard !content.isEmpty else { return false }
        let created = Date.now
        let server = LocalStore.server(api.baseURL)
        let generation = self.generation
        busy = true; error = nil
        defer { if generation == self.generation { busy = false } }
        do {
            // Resolve once before dispatch. Later pointer changes cannot move this attempt.
            let destination = try await api.chiefConversation().conversationId
            guard generation == self.generation else { return false }
            let id = UUID().uuidString
            let message = ConversationSend(conversationId: destination, content: content, mode: "queue",
                senderMessageId: id, sentAtUnixMilliseconds: Int64(created.timeIntervalSince1970 * 1000))
            let work = PendingWork(id: id, created: created, kind: .message, owner: .chief,
                message: message, draftID: Self.attemptDraftID)
            if let previous = attempt, previous.state == .failed,
               previous.message?.conversationId == nil, destination == nil {
                // A confirmed refusal of creation permits an explicit new creation attempt.
                try LocalStore.shared.outgoing.replace(server: server, id: previous.id, with: work)
                try LocalStore.shared.outgoing.releaseHold(server: server, id: id, destination: .newConversation)
            } else {
                try LocalStore.shared.outgoing.enqueue(work, server: server)
            }
            refreshAttempt()
            await Outbox.shared.drain(api: api)
            guard generation == self.generation else { return false }
            refreshAttempt()
            return attempt.map { [.sent, .serverQueued].contains($0.state) } ?? false
        } catch { if generation == self.generation { self.error = error.localizedDescription }; return false }
    }
    /// Receipt and held-prompt reads only. Missing evidence never admits another POST.
    func checkDelivery() async {
        guard let api, let work = attempt, work.state == .uncertain, !busy, storageError == nil else { return }
        let server = LocalStore.server(api.baseURL)
        let generation = self.generation
        busy = true; error = nil
        defer { if generation == self.generation { busy = false } }
        do {
            let destination: String?
            if let captured = work.message?.conversationId { destination = captured }
            else { destination = try await api.chiefConversation().conversationId }
            guard generation == self.generation, let destination else { return }
            let receipt = try await api.conversationReceipts(destination, messageIDs: [work.id]).first(where: { $0.senderMessageId == work.id })
            guard generation == self.generation else { return }
            if let receipt {
                let state: PendingWork.State
                switch receipt.fate {
                case "prompt": state = .sent
                case "prompt_refused": state = .failed
                case "prompt_discarded": state = .changed
                default: state = .uncertain
                }
                try LocalStore.shared.outgoing.settle(server: server, id: work.id, state: state,
                    note: state == .sent ? "Sent" : state == .uncertain ? "Delivery uncertain" : "Not sent",
                    conversationID: destination)
            } else {
                let configuration = try await api.conversationConfiguration(.chief, id: destination)
                guard generation == self.generation else { return }
                if configuration.heldPrompts.contains(where: { $0.senderMessageId == work.id }) {
                    try LocalStore.shared.outgoing.settle(server: server, id: work.id,
                        state: .serverQueued, note: "Queued on server", conversationID: destination)
                }
            }
            refreshAttempt()
        } catch { if generation == self.generation { self.error = error.localizedDescription } }
    }
}
