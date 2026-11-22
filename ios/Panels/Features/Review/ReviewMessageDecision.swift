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

private struct ReviewMessageEvents: Decodable, Sendable { let events: [ReviewMessageEvent] }

extension APIClient {
    func reviewMessages(_ id: String, after: Int) async throws -> [ReviewMessageEvent] {
        let response: ReviewMessageEvents = try await get("/api/conversation/conversations/\(id)/events", query: [.init(name: "after", value: String(after))])
        return ReviewMessageEvent.unread(response.events, after: after)
    }
    /// The same run as the phone last read it, saved on the phone.
    @MainActor func savedReviewMessages(_ id: String, after: Int) -> [ReviewMessageEvent]? {
        let response: ReviewMessageEvents? = cached("/api/conversation/conversations/\(id)/events", query: [.init(name: "after", value: String(after))])
        return response.map { ReviewMessageEvent.unread($0.events, after: after) }
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
    let composer: ConversationModel
    @ObservationIgnored private let savedWork: ((String) -> [PendingWork])?
    @ObservationIgnored private let refreshSavedWork: (String) -> Void
    init(composer: ConversationModel = ConversationModel(), savedWork: ((String) -> [PendingWork])? = nil,
         refreshSavedWork: @escaping (String) -> Void = { _ in }) {
        self.composer = composer
        self.savedWork = savedWork
        self.refreshSavedWork = refreshSavedWork
    }
    private func saved(_ server: String) -> [PendingWork] {
        if let savedWork { return savedWork(server) }
        guard let id = item?.conversationId else { return [] }
        let summaries = (try? LocalStore.shared.outgoing.replySummaries(server: server, conversationID: id)) ?? []
        let relevant = summaries.last { $0.state != "sent" || ($0.ownerReadThroughSequence ?? 0) >= through }
        let accepted = summaries.last { $0.state == "sent" }
        let ids = Set([relevant?.id, accepted?.id].compactMap { $0 })
        return summaries.filter { ids.contains($0.id) }.compactMap { Outbox.shared.work(server, id: $0.id) }
    }
    var events: [ReviewMessageEvent] = []
    var loaded = false
    private var addressCurrent: Bool?
    var historical: Bool { !(addressCurrent ?? item?.addressCurrent ?? true) }
    /// Address authority changes independently of the frozen displayed unread run.
    /// A message read that started earlier must not replace this current authority.
    func updateAddressCurrent(_ current: Bool) { addressCurrent = current }
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
    static func freshness(_ item: ReviewItem) -> String { "\(item.ownerReadThroughSequence)" }
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

    /// A saved reply counts at once. Only a refusal brings it back to the card.
    var accepted: Bool { attempt.map { $0.state != .failed } ?? false }

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
        var messages: [ReviewMessageEvent]
        var fromSavedCopy = false
        do {
            messages = try await api.reviewMessages(id, after: item.ownerReadThroughSequence)
        } catch {
            // Offline, the run the phone saved when it last read it stands in, so Mark read
            // and a reply work on it. The next connection reads it again.
            guard !Task.isCancelled, error is URLError, let cached = api.savedReviewMessages(id, after: item.ownerReadThroughSequence) else {
                if !Task.isCancelled, !(error is URLError) { self.error = error.localizedDescription }
                return
            }
            messages = cached; fromSavedCopy = true
        }
        guard !Task.isCancelled else { return }
        events = messages
        refreshSavedWork(LocalStore.server(api.baseURL))
        let saved = self.saved(LocalStore.server(api.baseURL)).last {
            $0.message?.conversationId == id && $0.message?.ownerReadThroughSequence != nil &&
            ($0.state != .sent || ($0.message?.ownerReadThroughSequence ?? 0) >= (messages.last?.sequence ?? Int.max))
        }
        attemptID = saved?.id; refreshAttempt()
        if saved == nil, let delivered = self.saved(LocalStore.server(api.baseURL)).last(where: {
            $0.message?.conversationId == id && $0.message?.ownerReadThroughSequence != nil && $0.state == .sent
        }), let message = delivered.message,
           composer.draft == message.content.compactMap(\.text).joined(separator: "\n"),
           (try? JSONEncoder().encode(composer.attachments)) == (try? JSONEncoder().encode(message.content.filter { $0.piece != "text" })) {
            clearAcceptedDraft()
        }
        // Outbox content remains the authority if the app closed during a send, and a
        // refused reply brings its words back to an empty composer.
        if let saved, let message = saved.message,
           saved.state != .failed || (composer.draft.isEmpty && composer.attachments.isEmpty) {
            composer.draft = message.content.compactMap(\.text).joined(separator: "\n")
            composer.attachments = message.content.filter { $0.piece != "text" }
        }
        loaded = true; loadedFreshness = fromSavedCopy ? nil : Self.freshness(item); readStarted = started; error = nil
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
              loaded, through > 0, !historical, !busy, !accepted else { return false }
        guard !composer.preparingPhoto else { return false }
        busy = true; defer { busy = false }
        // A reply that gave up may already be on the server, so Try Again sends it as it
        // was, under its own ID, as the conversation's Try Again does.
        if let attempt, attempt.state == .failed,
           (try? LocalStore.shared.outgoing.summary(server: LocalStore.server(api.baseURL), id: attempt.id))??.gaveUp == true {
            guard await Outbox.shared.retry(attempt.id, api: api) == attempt.id else {
                error = Outbox.shared.store.saveError
                return false
            }
            refreshSavedWork(LocalStore.server(api.baseURL)); refreshAttempt()
            return accepted
        }
        let text = composer.draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty || !composer.attachments.isEmpty else { return false }
        let content: [ConversationSend.Piece]
        do { content = try await composer.preparedContent() }
        catch is CancellationError { return false }
        catch { self.error = error.localizedDescription; return false }
        guard !historical, self.item?.conversationId == id, self.item?.owner == owner else { return false }
        if let attempt, attempt.state == .failed {
            // Replace a refusal with the current draft under a new ID, retaining its read boundary.
            guard let replacement = await Outbox.shared.retry(attempt.id, api: api, content: content) else {
                error = Outbox.shared.store.saveError
                return false
            }
            attemptID = replacement
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
            refreshSavedWork(LocalStore.server(api.baseURL)); refreshAttempt()
            return accepted
        } catch { self.error = error.localizedDescription; return false }
    }
    /// Saved in the outbox; the card moves on at once.
    func markRead() async -> Bool {
        await settled()
        guard let api, let id = item?.conversationId, loaded, through > 0, !busy, !accepted else { return false }
        do { try Outbox.shared.enqueueMarkRead(owner: item?.owner, conversationID: id, through: through, api: api); return true }
        catch { self.error = error.localizedDescription; return false }
    }
    func clearAcceptedDraft() { composer.draft = ""; composer.attachments = [] }
}
