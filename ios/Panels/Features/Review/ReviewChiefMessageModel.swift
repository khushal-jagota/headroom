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
    var blocked: Bool { busy || storageError != nil }
    /// Only a refusal shows. A saved message looks sent at once.
    var note: String? {
        guard let attempt, attempt.state == .failed else { return nil }
        return composer.draft.isEmpty && composer.attachments.isEmpty
            ? "The message did not reach Chief. Open Chief to review the saved message."
            : "The message did not reach Chief. Your draft is ready to retry."
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
            if let summary = summaries.last {
                guard let saved = try LocalStore.shared.outgoing.work(server: server, id: summary.id) else {
                    throw OutgoingStore.StoreError.missingWork
                }
                attempt = saved
            } else { attempt = nil }
            storageError = nil
            if let attempt, attempt.state != .failed { clearAcceptedDraft(attempt) }
            // A refused photo puts its draft back in the saved composer.
            else if attempt?.state == .failed { composer.reloadDraft() }
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
               saved == content.filter({ $0.piece != "text" }) {
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
        guard !blocked, !composer.preparingPhoto else { return false }
        let generation = self.generation
        busy = true; error = nil
        defer { if generation == self.generation { busy = false } }
        let content: [ConversationSend.Piece]
        do { content = try await composer.preparedContent() }
        catch is CancellationError { return false }
        catch { self.error = error.localizedDescription; return false }
        guard generation == self.generation, !content.isEmpty else { return false }
        let created = Date.now
        do {
            // No conversation ID: the server sends it to Chief's current conversation,
            // or starts one. A resend with the same ID lands there once.
            let id = UUID().uuidString
            let message = ConversationSend(conversationId: nil, content: content, mode: "queue",
                senderMessageId: id, sentAtUnixMilliseconds: Int64(created.timeIntervalSince1970 * 1000))
            let work = PendingWork(id: id, created: created, kind: .message, owner: .chief,
                message: message, draftID: Self.attemptDraftID)
            // The draft clears in the same save. A refused photo puts it back.
            let server = LocalStore.server(api.baseURL)
            let outgoing = try LocalStore.shared.outgoing
            try outgoing.enqueue(work, server: server, capturedDraft: outgoing.captureDraft(server: server, id: Self.chiefDraftID))
            composer.reloadDraft()
            refreshAttempt()
            Task { await Outbox.shared.drain(api: api) }
            return true
        } catch { if generation == self.generation { self.error = error.localizedDescription }; return false }
    }
}
