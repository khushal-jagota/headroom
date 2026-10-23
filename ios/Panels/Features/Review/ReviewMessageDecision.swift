import Foundation
import Observation

/// Only message_to_owner events belong to the complete unread run, as on the web.
struct ReviewMessageEvent: Decodable, Identifiable, Sendable {
    let sequence: Int
    let kind: String
    let payload: Payload
    var id: Int { sequence }
    struct Payload: Decodable, Sendable {
        let text: String?
        let content: [Piece]?
        var pieces: [Piece] { content ?? text.map { [Piece(piece: "text", text: $0)] } ?? [] }
    }
    struct Piece: Decodable, Sendable {
        let piece: String
        var text: String? = nil
        var storedFileId: String? = nil
        var mediaType: String? = nil
        var fileName: String? = nil
    }
    static func unread(_ events: [Self], after: Int) -> [Self] {
        events.filter { $0.kind == "message_to_owner" && $0.sequence > after }.sorted { $0.sequence < $1.sequence }
    }
}

extension APIClient {
    func reviewMessages(_ id: String, after: Int) async throws -> [ReviewMessageEvent] {
        struct Response: Decodable, Sendable { let events: [ReviewMessageEvent] }
        let response: Response = try await get("/api/conversation/conversations/\(id)/events", query: [.init(name: "after", value: String(after))])
        return ReviewMessageEvent.unread(response.events, after: after)
    }
    func markReviewRead(_ id: String, through: Int) async throws {
        struct Body: Encodable { let throughSequence: Int }
        _ = try await reviewWrite("/api/conversation/conversations/\(id)/owner-read", body: Body(throughSequence: through))
    }
    func currentConversation(_ owner: ConversationOwner) async throws -> String? {
        switch owner {
        case .chief: return try await chiefConversation().conversationId
        case .ticket(let id): return try await ticket(id).conversationId
        case .manager(let id): return try await manager(id).manager.conversationId
        }
    }
}

@MainActor @Observable final class ReviewMessageDecision {
    let composer = ConversationModel()
    var events: [ReviewMessageEvent] = []
    var loaded = false
    var historical = false
    var busy = false
    var error: String?
    var attemptID: String?
    private var api: APIClient?
    private var item: ReviewItem?
    var through: Int { events.last?.sequence ?? 0 }
    var attempt: PendingWork? {
        guard let api, let attemptID else { return nil }
        return Outbox.shared.entries(LocalStore.server(api.baseURL)).first { $0.id == attemptID }
    }
    var accepted: Bool { attempt.map { [.sent, .serverQueued].contains($0.state) } ?? false }
    var pending: Bool { attempt.map { [.waiting, .sending, .uncertain].contains($0.state) } ?? false }

    func load(item: ReviewItem, api: APIClient) async {
        guard !loaded, !busy, let id = item.conversationId, let owner = item.owner else { return }
        busy = true; defer { busy = false }
        self.api = api; self.item = item
        composer.conversationID = id
        composer.restore(owner: owner, api: api, draftID: "review-message:\(id)")
        composer.deliveryMode = "queue"
        do {
            let configuration = try await api.conversationConfiguration(owner, id: id)
            let messages = try await api.reviewMessages(id, after: configuration.ownerReadThroughSequence ?? 0)
            let current = try await api.currentConversation(owner)
            try Task.checkCancellation()
            events = messages; historical = current != id
            composer.applyConfiguration(configuration)
            let saved = Outbox.shared.entries(LocalStore.server(api.baseURL)).last {
                $0.message?.conversationId == id && $0.message?.ownerReadThroughSequence != nil &&
                ($0.message?.ownerReadThroughSequence ?? 0) >= (messages.last?.sequence ?? Int.max)
            }
            attemptID = saved?.id
            if saved == nil, let delivered = Outbox.shared.entries(LocalStore.server(api.baseURL)).last(where: {
                $0.message?.conversationId == id && $0.message?.ownerReadThroughSequence != nil && [.sent, .serverQueued].contains($0.state)
            }), let message = delivered.message,
               composer.draft == message.content.compactMap(\.text).joined(separator: "\n"),
               (try? JSONEncoder().encode(composer.attachments)) == (try? JSONEncoder().encode(message.content.filter { $0.piece != "text" })) {
                clearAcceptedDraft()
            }
            // Outbox content remains the authority if the app closed during a send.
            if let message = saved?.message {
                composer.draft = message.content.compactMap(\.text).joined(separator: "\n")
                composer.attachments = message.content.filter { $0.piece != "text" }
            }
            loaded = true; error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    func send() async -> Bool {
        guard let api, let item, let owner = item.owner, let id = item.conversationId,
              loaded, through > 0, !historical, !busy, !pending else { return false }
        if let attempt, [.failed, .changed].contains(attempt.state) {
            // Explicit refusal retries get a new identity; unknown sends keep theirs.
            if attempt.state == .failed {
                Outbox.shared.retry(attempt.id, api: api)
                attemptID = Outbox.shared.entries(LocalStore.server(api.baseURL)).last { $0.priorMessageIDs?.contains(attempt.id) == true }?.id
                return false
            }
        }
        let text = composer.draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty || !composer.attachments.isEmpty else { return false }
        let messageID = UUID().uuidString
        var message = ConversationSend(conversationId: id, content: (text.isEmpty ? [] : [.init(text: text)]) + composer.attachments,
            senderMessageId: messageID, sentAtUnixMilliseconds: Int64(Date().timeIntervalSince1970 * 1000), ownerReadThroughSequence: through)
        message.mode = "queue"
        let work = PendingWork(id: messageID, created: .now, kind: .message, owner: owner, message: message)
        do {
            try Outbox.shared.enqueue(work, api: api)
            attemptID = messageID
            await Outbox.shared.drain(api: api)
            return accepted
        } catch { self.error = error.localizedDescription; return false }
    }
    func markRead() async -> Bool {
        guard let api, let id = item?.conversationId, loaded, through > 0, !busy, !pending else { return false }
        busy = true; defer { busy = false }
        do { try await api.markReviewRead(id, through: through); return true }
        catch { self.error = error.localizedDescription; return false }
    }
    func clearAcceptedDraft() { composer.draft = ""; composer.attachments = [] }
}
