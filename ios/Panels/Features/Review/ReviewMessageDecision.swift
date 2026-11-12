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
        case .goal(let id):
            struct GoalConversation: Decodable, Sendable { let conversationId: String? }
            let goal: GoalConversation = try await get("/api/goals/\(id)")
            return goal.conversationId
        }
    }
}

@MainActor @Observable final class ReviewMessageDecision {
    let composer = ConversationModel()
    @ObservationIgnored private let savedWork: ((String) -> [PendingWork])?
    @ObservationIgnored private let refreshSavedWork: (String) -> Void
    init(savedWork: ((String) -> [PendingWork])? = nil,
         refreshSavedWork: @escaping (String) -> Void = { _ in }) {
        self.savedWork = savedWork
        self.refreshSavedWork = refreshSavedWork
    }
    private func saved(_ server: String) -> [PendingWork] {
        if let savedWork { return savedWork(server) }
        guard let id = item?.conversationId else { return [] }
        let summaries = (try? LocalStore.shared.outgoing.replySummaries(server: server, conversationID: id)) ?? []
        let relevant = summaries.last { !["sent", "serverQueued", "changed"].contains($0.state) || ($0.ownerReadThroughSequence ?? 0) >= through }
        let accepted = summaries.last { ["sent", "serverQueued"].contains($0.state) }
        let ids = Set([relevant?.id, accepted?.id].compactMap { $0 })
        return summaries.filter { ids.contains($0.id) }.compactMap { Outbox.shared.work(server, id: $0.id) }
    }
    var events: [ReviewMessageEvent] = []
    var loaded = false
    var historical = false
    var busy = false
    private var loading = false
    var error: String?
    var attemptID: String?
    private var api: APIClient?
    private var item: ReviewItem?
    /// The view facts the loaded run was read for. The view carries no sequence that
    /// moves when a message joins an unread run, so these only say when a read-ahead is
    /// plainly out of date. The shown card reads its run again (see `load(shownAt:)`).
    private var loadedFreshness: String?
    /// When the read behind `events` started.
    private var readStarted: ContinuousClock.Instant?
    static func freshness(_ item: ReviewItem) -> String { "\(item.ownerReadThroughSequence)|\(item.addressCurrent)" }
    func isCurrent(for item: ReviewItem) -> Bool { loaded && loadedFreshness == Self.freshness(item) }
    var through: Int { events.last?.sequence ?? 0 }
    private(set) var attempt: PendingWork?
    @ObservationIgnored private var attemptFact: OutgoingStore.Summary?
    func refreshAttempt() {
        guard let api, let attemptID else { attempt = nil; return }
        let server = LocalStore.server(api.baseURL)
        if let savedWork { attempt = savedWork(server).first { $0.id == attemptID }; return }
        let fact = try? LocalStore.shared.outgoing.summary(server: server, id: attemptID)
        guard fact != attemptFact else { return }
        attemptFact = fact
        attempt = Outbox.shared.work(server, id: attemptID)
    }

    var accepted: Bool { attempt.map { [.sent, .serverQueued].contains($0.state) } ?? false }
    var pending: Bool { attempt.map { [.waiting, .sending, .uncertain].contains($0.state) } ?? false }

    /// The view supplies the read boundary and whether the address is still the
    /// owner's current conversation, so the unread run is the only read. A read
    /// already in flight is awaited rather than repeated.
    ///
    /// `shownAt` is when the card came on screen. A run read before then reads again,
    /// because messages can join it after a read-ahead. The earlier run stays on screen
    /// meanwhile, and Mark read and a reply wait for the new read.
    func load(item: ReviewItem, api: APIClient, shownAt: ContinuousClock.Instant? = nil) async {
        await settled()
        guard !Task.isCancelled else { return }
        if isCurrent(for: item), let readStarted, shownAt.map({ readStarted >= $0 }) ?? true { return }
        guard !busy, let id = item.conversationId, let owner = item.owner else { return }
        let first = !loaded
        loading = true; if first { busy = true }
        defer { loading = false; if first { busy = false } }
        self.api = api; self.item = item
        let started = ContinuousClock.now
        refreshSavedWork(LocalStore.server(api.baseURL))
        if !loaded {
            composer.conversationID = id
            composer.restore(owner: owner, api: api, draftID: "review-message:\(id)", savedWork: self.saved(LocalStore.server(api.baseURL)))
            composer.deliveryMode = "queue"
        }
        do {
            let messages = try await api.reviewMessages(id, after: item.ownerReadThroughSequence)
            try Task.checkCancellation()
            events = messages; historical = !item.addressCurrent
            refreshSavedWork(LocalStore.server(api.baseURL))
            let saved = self.saved(LocalStore.server(api.baseURL)).last {
                $0.message?.conversationId == id && $0.message?.ownerReadThroughSequence != nil &&
                (![.sent, .serverQueued, .changed].contains($0.state) || ($0.message?.ownerReadThroughSequence ?? 0) >= (messages.last?.sequence ?? Int.max))
            }
            attemptID = saved?.id; refreshAttempt()
            if saved == nil, let delivered = self.saved(LocalStore.server(api.baseURL)).last(where: {
                $0.message?.conversationId == id && $0.message?.ownerReadThroughSequence != nil && [.sent, .serverQueued].contains($0.state)
            }), let message = delivered.message,
               composer.draft == message.content.compactMap(\.text).joined(separator: "\n"),
               (try? JSONEncoder().encode(composer.attachments)) == (try? JSONEncoder().encode(message.content.filter { $0.piece != "text" })) {
                clearAcceptedDraft()
            }
            // Outbox content remains the authority if the app closed during a send.
            if let saved, saved.state != .failed, let message = saved.message {
                composer.draft = message.content.compactMap(\.text).joined(separator: "\n")
                composer.attachments = message.content.filter { $0.piece != "text" }
            }
            loaded = true; loadedFreshness = Self.freshness(item); readStarted = started; error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    /// Waits for a read in flight, so a reply or Mark read uses the newest run.
    private func settled() async {
        while loading {
            do { try await Task.sleep(for: .milliseconds(40)) } catch { return }
        }
    }
    func send() async -> Bool {
        await settled()
        guard let api, let item, let owner = item.owner, let id = item.conversationId,
              loaded, through > 0, !historical, !busy, !pending else { return false }
        let text = composer.draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty || !composer.attachments.isEmpty else { return false }
        let content: [ConversationSend.Piece] = (text.isEmpty ? [] : [.init(text: text)]) + composer.attachments
        if let attempt, attempt.state == .failed {
            // Replace a terminal refusal with the current draft, retaining its read boundary.
            guard let replacement = Outbox.shared.retry(attempt.id, api: api, content: content) else {
                error = Outbox.shared.store.saveError
                return false
            }
            attemptID = replacement
            await Outbox.shared.drain(api: api)
            refreshSavedWork(LocalStore.server(api.baseURL)); refreshAttempt()
            return accepted
        }
        let messageID = UUID().uuidString
        var message = ConversationSend(conversationId: id, content: content,
            senderMessageId: messageID, sentAtUnixMilliseconds: Int64(Date().timeIntervalSince1970 * 1000), ownerReadThroughSequence: through)
        message.mode = "queue"
        let work = PendingWork(id: messageID, created: .now, kind: .message, owner: owner, message: message)
        do {
            try Outbox.shared.enqueue(work, api: api)
            attemptID = messageID
            await Outbox.shared.drain(api: api)
            refreshSavedWork(LocalStore.server(api.baseURL)); refreshAttempt()
            return accepted
        } catch { self.error = error.localizedDescription; return false }
    }
    func markRead() async -> Bool {
        await settled()
        guard let api, let id = item?.conversationId, loaded, through > 0, !busy, !pending else { return false }
        busy = true; defer { busy = false }
        do { try await api.markReviewRead(id, through: through); return true }
        catch { self.error = error.localizedDescription; return false }
    }
    func clearAcceptedDraft() { composer.draft = ""; composer.attachments = [] }
}
