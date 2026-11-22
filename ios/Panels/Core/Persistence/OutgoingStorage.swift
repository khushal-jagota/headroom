import Foundation
import SwiftData

/// Queryable scheduling and presentation facts. The original payload stays once in LocalRecord.
@Model final class OutgoingMetadata {
    #Index<OutgoingMetadata>([\.server, \.expiresAt], [\.audioFile], [\.server, \.state], [\.server, \.kind], [\.server, \.conversationID], [\.server, \.lane, \.sequence], [\.server, \.ownerKey], [\.server, \.draftID])
    @Attribute(.unique) var key: String
    var server: String
    var identity: String
    var ownerKey: String?
    var conversationID: String?
    /// A Review reply's read boundary, or a mark read's read point.
    var ownerReadThroughSequence: Int?
    var ticketID: String?
    var draftID: String?
    var audioFile: String?
    var expiresAt: Date?
    var lane: String
    var kind: String
    var state: String
    var note: String = ""
    /// Set on a never-sent message that inherits a conversation its lane just created.
    var omitCreationSettings: Bool = false
    var omitModel: Bool = false
    var omitEffort: Bool = false
    var created: Date
    var completed: Date?
    var sequence: Int64
    /// The phone has sent this at least once. An ask answer checks its receipt first.
    var attempted: Bool = false
    /// Attempts the server answered without settling the item: 5xx, 408, 429, an
    /// unreadable reply, or an ask that still waits. A passing transport failure does
    /// not count. Kept on disk, so a phone opened briefly still reaches the bound.
    var unsettledAttempts: Int = 0
    var proposalField: String?
    var proposalRevision: Int?
    var proposalCreatedAt: Double?

    init(key: String, server: String, work: PendingWork, sequence: Int64) {
        let owner = work.owner.map(Self.ownerKey)
        let destination: String?, through: Int?
        switch work.kind {
        case .markRead: destination = work.readConversationID; through = work.readThroughSequence
        case .askAnswer: destination = work.askAnswer?.conversationID; through = nil
        default: destination = work.message?.conversationId; through = work.message?.ownerReadThroughSequence
        }
        let command = work.protectedDecisionBody.flatMap { try? APIClient.decoder().decode(DecisionCommand.self, from: $0) }
        self.key = key; self.server = server
        identity = work.id
        ownerKey = owner
        conversationID = destination
        ownerReadThroughSequence = through
        kind = work.kind.rawValue
        state = work.state.rawValue
        note = work.note
        created = work.created; completed = work.completed
        self.sequence = sequence
        ticketID = work.ticketID; draftID = work.draftID; audioFile = work.audioFile
        lane = Self.lane(kind: work.kind, conversationID: destination, ownerKey: owner, ticketID: work.ticketID, draftID: work.draftID, identity: work.id)
        proposalField = command?.field ?? work.proposal?.field
        proposalRevision = command?.expectedProposalRevision ?? work.proposal?.revision
        proposalCreatedAt = work.protectedProposalCreatedAt ?? work.proposal?.createdAt
    }

    /// One request at a time per lane. All of an owner's messages and answers share one
    /// lane, so they go out in the order they were saved, whichever screen saved them.
    static func lane(kind: PendingWork.Kind, conversationID: String?, ownerKey: String?, ticketID: String?, draftID: String?, identity: String) -> String {
        switch kind {
        case .markRead: return "read:\(conversationID ?? identity)"
        case .approval, .sendBack: return "ticket:\(ticketID ?? identity)"
        case .voice: return "draft:\(draftID ?? identity)"
        case .message, .askAnswer:
            if let ownerKey { return "owner:\(ownerKey)" }
            return "conversation:\(conversationID ?? identity)"
        }
    }

    static func ownerKey(_ owner: ConversationOwner) -> String {
        switch owner {
        case .chief: "chief"
        case .manager(let id): "manager:\(id)"
        case .ticket(let id): "ticket:\(id)"
        case .goal(let id): "goal:\(id)"
        }
    }
}

/// Main-actor ownership serializes outgoing payload, metadata and composer transactions.
/// Payload bytes are immutable after insertion. Mutable delivery facts live in metadata.
@MainActor final class OutgoingStore {
    struct DraftCapture: Equatable {
        struct Value: Equatable {
            let payload: Data
            let updated: Date
        }
        let id: String
        let text: Value?
        let attachments: Value?
    }
    enum StoreError: Error { case missingWork, duplicateIdentity, invalidTransition }
    struct Summary: Identifiable, Sendable, Equatable {
        let id: String
        let ownerKey: String?
        let conversationID: String?
        let ownerReadThroughSequence: Int?
        let ticketID: String?
        let draftID: String?
        let lane: String
        let state: String
        let kind: String
        let note: String
        let sequence: Int64
        let attempted: Bool
        let unsettledAttempts: Int
        let proposalField: String?
        let proposalRevision: Int?
        let proposalCreatedAt: Double?
        let created: Date
        let completed: Date?
        let expiresAt: Date?
        init(_ value: OutgoingMetadata) {
            id = value.identity; ownerKey = value.ownerKey; conversationID = value.conversationID; ownerReadThroughSequence = value.ownerReadThroughSequence
            ticketID = value.ticketID; draftID = value.draftID; lane = value.lane
            state = value.state; kind = value.kind; note = value.note; sequence = value.sequence
            attempted = value.attempted; unsettledAttempts = value.unsettledAttempts
            proposalField = value.proposalField; proposalRevision = value.proposalRevision
            proposalCreatedAt = value.proposalCreatedAt
            created = value.created; completed = value.completed; expiresAt = value.expiresAt
        }
        var refused: Bool { state == PendingWork.State.failed.rawValue }
        /// Failed without a settling answer from the server, which may already hold it.
        /// Try Again sends it again with the same identity.
        var gaveUp: Bool { refused && unsettledAttempts > 0 }
    }
    nonisolated static let finishedLifetime: TimeInterval = 24 * 60 * 60
    private let context: ModelContext
    private let beforeSave: (() throws -> Void)?
    private let onPayloadDecode: (() -> Void)?
    private let changed: (String) -> Void
    private let failed: (Error) -> Void
    init(context: ModelContext, beforeSave: (() throws -> Void)? = nil, onPayloadDecode: (() -> Void)? = nil, changed: @escaping (String) -> Void, failed: @escaping (Error) -> Void) {
        self.context = context; self.beforeSave = beforeSave; self.onPayloadDecode = onPayloadDecode; self.changed = changed; self.failed = failed
    }
    @discardableResult
    private func transaction<T>(_ server: String, _ body: () throws -> T) throws -> T {
        do {
            let result = try body()
            if context.hasChanges { try beforeSave?(); try context.save(); changed(server) }
            return result
        } catch { context.rollback(); if !(error is StoreError) { failed(error) }; throw error }
    }
    private func metadata(_ server: String, _ id: String) throws -> OutgoingMetadata? {
        let key = LocalStore.key(server, "outbox", id)
        var query = FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.key == key })
        query.fetchLimit = 1
        return try context.fetch(query).first
    }
    private func raw(_ server: String, _ kind: String, _ id: String) throws -> LocalRecord? {
        let key = LocalStore.key(server, kind, id)
        var query = FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.key == key })
        query.fetchLimit = 1
        return try context.fetch(query).first
    }
    private func laneRows(_ server: String, _ lane: String) throws -> [OutgoingMetadata] {
        try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server && $0.lane == lane }, sortBy: [SortDescriptor(\.sequence)]))
    }
    private func nextSequence(_ server: String) throws -> Int64 {
        var query = FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server }, sortBy: [SortDescriptor(\.sequence, order: .reverse)])
        query.fetchLimit = 1
        return (try context.fetch(query).first?.sequence ?? 0) + 1
    }
    private func sorted(_ predicate: Predicate<OutgoingMetadata>) throws -> [Summary] {
        try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: predicate, sortBy: [SortDescriptor(\.sequence)])).map(Summary.init)
    }

    // MARK: Reads

    func summary(server: String, id: String) throws -> Summary? { try metadata(server, id).map(Summary.init) }
    func summaries(server: String, owner: ConversationOwner) throws -> [Summary] {
        let ownerKey = OutgoingMetadata.ownerKey(owner)
        return try sorted(#Predicate { $0.server == server && $0.ownerKey == ownerKey })
    }
    func summaries(server: String, draftID: String) throws -> [Summary] {
        try sorted(#Predicate { $0.server == server && $0.draftID == draftID })
    }
    func summaries(server: String, conversationID: String) throws -> [Summary] {
        try sorted(#Predicate { $0.server == server && $0.conversationID == conversationID })
    }
    func allSummaries(server: String) throws -> [Summary] { try sorted(#Predicate { $0.server == server }) }
    /// The conversation of the owner's newest message, including a start with no known destination.
    func latestConversationID(server: String, owner: ConversationOwner) throws -> String? {
        let ownerKey = OutgoingMetadata.ownerKey(owner)
        var query = FetchDescriptor<OutgoingMetadata>(predicate: #Predicate {
            $0.server == server && $0.ownerKey == ownerKey && $0.kind == "message"
        }, sortBy: [SortDescriptor(\.sequence, order: .reverse)])
        query.fetchLimit = 1
        return try context.fetch(query).first?.conversationID
    }
    /// Messages for one destination, plus the owner's messages that wait for a new conversation.
    func conversationSummaries(server: String, owner: ConversationOwner, conversationID: String?) throws -> [Summary] {
        let ownerKey = OutgoingMetadata.ownerKey(owner)
        let rows = try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: #Predicate {
            $0.server == server && $0.ownerKey == ownerKey && $0.kind == "message"
        }, sortBy: [SortDescriptor(\.sequence)]))
        return rows.filter { $0.conversationID == nil || $0.conversationID == conversationID }.map(Summary.init)
    }
    func replySummaries(server: String, conversationID: String) throws -> [Summary] {
        try sorted(#Predicate { $0.server == server && $0.conversationID == conversationID && $0.kind == "message" && $0.ownerReadThroughSequence != nil })
    }
    func decisionSummaries(server: String, ticketID: String) throws -> [Summary] {
        try decisionSummaries(server: server).filter { $0.ticketID == ticketID }
    }
    func decisionSummaries(server: String) throws -> [Summary] {
        let kinds = ["approval", "sendBack"]
        return try sorted(#Predicate { $0.server == server && kinds.contains($0.kind) })
    }
    /// What Review hides at once: decisions, mark reads and replies that carry a read boundary.
    func reviewSummaries(server: String) throws -> [Summary] {
        let kinds = ["approval", "sendBack", "markRead"]
        return try sorted(#Predicate { $0.server == server && (kinds.contains($0.kind) || $0.ownerReadThroughSequence != nil) })
    }
    /// Each lane's first unfinished row, when it can be sent now. Metadata only.
    func runnable(server: String) throws -> [Summary] {
        let open = ["waiting", "recording"]
        let rows = try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server && open.contains($0.state) }, sortBy: [SortDescriptor(\.sequence)]))
        var seen: Set<String> = []
        return rows.filter { seen.insert($0.lane).inserted && $0.state == "waiting" }.map(Summary.init)
    }
    func work(server: String, id: String) throws -> PendingWork? {
        guard let row = try raw(server, "outbox", id), let metadata = try metadata(server, id) else { return nil }
        onPayloadDecode?()
        var work = try JSONDecoder().decode(PendingWork.self, from: row.payload)
        work.state = PendingWork.State(rawValue: metadata.state) ?? .failed
        work.note = metadata.note; work.completed = metadata.completed
        // A message saved before its conversation existed takes the conversation the first
        // message created, before it is ever sent. Every send of it is then the same body.
        if let message = work.message, metadata.conversationID != message.conversationId {
            work.message = ConversationSend(conversationId: metadata.conversationID, content: message.content,
                senderLabel: message.senderLabel, mode: message.mode,
                backendKey: metadata.omitCreationSettings ? nil : message.backendKey,
                model: metadata.omitModel ? nil : message.model,
                reasoningEffort: metadata.omitEffort ? nil : message.reasoningEffort,
                reasoningEffortIsSet: !metadata.omitEffort && message.reasoningEffortIsSet,
                senderMessageId: message.senderMessageId, sentAtUnixMilliseconds: message.sentAtUnixMilliseconds,
                ownerReadThroughSequence: message.ownerReadThroughSequence)
        }
        return work
    }
    func audioIsUnowned(_ file: String) throws -> Bool {
        var query = FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.audioFile == file })
        query.fetchLimit = 1
        return try context.fetch(query).isEmpty
    }

    // MARK: Drafts

    func captureDraft(server: String, id: String) throws -> DraftCapture {
        func value(_ kind: String) throws -> DraftCapture.Value? {
            try raw(server, kind, id).map { .init(payload: $0.payload, updated: $0.updatedAt) }
        }
        return try DraftCapture(id: id, text: value("draft"), attachments: value("attachments"))
    }
    private func clearCaptured(_ capture: DraftCapture, server: String) throws {
        func clear(_ kind: String, _ captured: DraftCapture.Value?, empty: Data) throws {
            let current = try raw(server, kind, capture.id)
            guard current?.payload == captured?.payload, current?.updatedAt == captured?.updated else { return }
            if let current { current.payload = empty; current.updatedAt = .now }
        }
        try clear("draft", capture.text, empty: Data())
        try clear("attachments", capture.attachments, empty: Data("[]".utf8))
    }
    private func capturePhotoDraft(_ work: PendingWork, server: String, capturedDraft: DraftCapture?) throws -> PendingWork {
        var frozen = work
        if let capturedDraft {
            let capturedStillCurrent = try captureDraft(server: server, id: capturedDraft.id) == capturedDraft
            try clearCaptured(capturedDraft, server: server)
            if capturedStillCurrent, work.message?.content.contains(where: { $0.piece == "image" }) == true {
                let cleared = try captureDraft(server: server, id: capturedDraft.id)
                // Only versions that were cleared by this transaction can be restored.
                if cleared.text?.payload.isEmpty ?? true,
                   cleared.attachments?.payload == nil || cleared.attachments?.payload == Data("[]".utf8) {
                    frozen.rejectedPhotoDraft = RejectedPhotoDraft(id: capturedDraft.id,
                        originalText: capturedDraft.text.flatMap { String(data: $0.payload, encoding: .utf8) },
                        clearedTextUpdatedAt: cleared.text?.updated,
                        clearedAttachmentsUpdatedAt: cleared.attachments?.updated)
                }
            }
        }
        return frozen
    }
    private func restorePhotoDraft(_ work: PendingWork, server: String, restoredAt: Date) throws {
        guard let recovery = work.rejectedPhotoDraft, let message = work.message else { return }
        let current = try captureDraft(server: server, id: recovery.id)
        func matches(_ value: DraftCapture.Value?, updated: Date?, empty: Data) -> Bool {
            if let updated { return value?.updated == updated && value?.payload == empty }
            return value == nil
        }
        guard matches(current.text, updated: recovery.clearedTextUpdatedAt, empty: Data()),
              matches(current.attachments, updated: recovery.clearedAttachmentsUpdatedAt, empty: Data("[]".utf8)) else { return }
        try writeDraft(recovery.originalText ?? message.content.compactMap(\.text).joined(separator: "\n"), server: server, id: recovery.id, updated: restoredAt)
        let payload = try JSONEncoder().encode(message.content.filter { $0.piece != "text" })
        if let row = try raw(server, "attachments", recovery.id) {
            row.payload = payload; row.updatedAt = restoredAt
        } else {
            context.insert(LocalRecord(key: LocalStore.key(server, "attachments", recovery.id), server: server,
                kind: "attachments", payload: payload, updated: restoredAt))
        }
    }
    private func writeDraft(_ text: String, server: String, id: String, updated: Date = .now) throws {
        if let row = try raw(server, "draft", id) {
            row.payload = Data(text.utf8); row.updatedAt = updated
        } else {
            context.insert(LocalRecord(key: LocalStore.key(server, "draft", id), server: server, kind: "draft", payload: Data(text.utf8), updated: updated))
        }
    }

    // MARK: Writes

    @discardableResult
    private func insert(_ work: PendingWork, server: String, sequence: Int64? = nil) throws -> OutgoingMetadata {
        guard try metadata(server, work.id) == nil, try raw(server, "outbox", work.id) == nil else { throw StoreError.duplicateIdentity }
        let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys
        let key = LocalStore.key(server, "outbox", work.id)
        let row = LocalRecord(key: key, server: server, kind: "outbox", payload: try encoder.encode(work), updated: .now)
        let metadata = OutgoingMetadata(key: key, server: server, work: work, sequence: try sequence ?? nextSequence(server))
        if [.sent, .failed].contains(work.state) { metadata.expiresAt = Self.expiry(kind: work.kind.rawValue, state: work.state, completed: work.completed ?? work.created) }
        context.insert(row); context.insert(metadata)
        return metadata
    }
    private static func expiry(kind: String, state: PendingWork.State, completed: Date) -> Date? {
        switch state {
        case .sent: return completed.addingTimeInterval(finishedLifetime)
        // A refused message or recording keeps its retry until the person acts on it.
        case .failed where kind != "message" && kind != "voice": return completed.addingTimeInterval(finishedLifetime)
        default: return nil
        }
    }
    func enqueue(_ work: PendingWork, server: String, capturedDraft: DraftCapture? = nil) throws {
        try transaction(server) {
            try insert(try capturePhotoDraft(work, server: server, capturedDraft: capturedDraft), server: server)
        }
    }
    /// Save a new item, or update the state and note of a saved one. Its bytes stay as saved.
    func save(_ work: PendingWork, server: String) throws {
        try transaction(server) {
            guard let row = try metadata(server, work.id) else { try insert(work, server: server); return }
            if row.state != work.state.rawValue { row.state = work.state.rawValue }
            if row.note != work.note { row.note = work.note }
            if row.completed != work.completed { row.completed = work.completed }
        }
    }
    /// The server only moves a read point forward, so one waiting read per conversation
    /// carries the highest point asked for.
    func enqueueMarkRead(server: String, owner: ConversationOwner?, conversationID: String, through: Int, now: Date = .now) throws {
        try transaction(server) {
            let rows = try summaries(server: server, conversationID: conversationID).filter { $0.kind == "markRead" }
            if rows.contains(where: { !$0.refused && ($0.ownerReadThroughSequence ?? 0) >= through }) { return }
            for row in rows where row.state == "waiting" {
                if let metadata = try metadata(server, row.id) { context.delete(metadata) }
                if let payload = try raw(server, "outbox", row.id) { context.delete(payload) }
            }
            try insert(PendingWork(id: UUID().uuidString, created: now, kind: .markRead, owner: owner,
                readConversationID: conversationID, readThroughSequence: through), server: server)
        }
    }
    /// Saved before each send, so a relaunch knows the item may have reached the server.
    func beginAttempt(server: String, id: String) throws {
        try transaction(server) {
            guard let row = try metadata(server, id), row.state == "waiting" else { throw StoreError.invalidTransition }
            if !row.attempted { row.attempted = true }
        }
    }
    func settle(server: String, id: String, state: PendingWork.State, note: String = "", conversationID: String? = nil, now: Date = .now,
                decisionDraft: (id: String, text: String, updated: Date)? = nil, restoreRejectedPhotoDraft: Bool = false) throws {
        try transaction(server) {
            guard let row = try metadata(server, id) else { throw StoreError.missingWork }
            if restoreRejectedPhotoDraft, let work = try work(server: server, id: id) {
                try restorePhotoDraft(work, server: server, restoredAt: row.completed ?? now)
            }
            if state == .sent, let decisionDraft,
               let draft = try raw(server, "draft", decisionDraft.id), draft.payload == Data(decisionDraft.text.utf8), draft.updatedAt == decisionDraft.updated {
                draft.payload = Data(); draft.updatedAt = .now
            }
            if row.state != state.rawValue { row.state = state.rawValue }
            if row.note != note { row.note = note }
            if row.unsettledAttempts != 0 { row.unsettledAttempts = 0 }
            if [.sent, .failed].contains(state), row.completed == nil { row.completed = now }
            let deadline = Self.expiry(kind: row.kind, state: state, completed: row.completed ?? now)
            if row.expiresAt != deadline { row.expiresAt = deadline }
            if let conversationID, row.conversationID != conversationID { try adopt(conversationID, from: row, server: server) }
        }
    }
    /// One more attempt the server answered without settling. At the bound the item
    /// fails with its note, keeps its count (so Try Again keeps its identity), and its
    /// lane moves on. Returns whether it failed.
    func countUnsettledAttempt(server: String, id: String, bound: Int, note: String, now: Date = .now) throws -> Bool {
        try transaction(server) {
            guard let row = try metadata(server, id), row.state == "waiting" else { throw StoreError.invalidTransition }
            row.unsettledAttempts += 1
            guard row.unsettledAttempts >= bound else { return false }
            row.state = PendingWork.State.failed.rawValue; row.note = note
            if row.completed == nil { row.completed = now }
            row.expiresAt = Self.expiry(kind: row.kind, state: .failed, completed: row.completed ?? now)
            return true
        }
    }
    /// Try Again for an item that keeps its identity: it waits again in its old place.
    func requeue(server: String, id: String) throws {
        try transaction(server) {
            guard let row = try metadata(server, id), row.state == "failed" else { throw StoreError.invalidTransition }
            row.state = PendingWork.State.waiting.rawValue; row.note = ""; row.unsettledAttempts = 0
            row.completed = nil; row.expiresAt = nil
        }
    }
    /// A first message created a conversation: later, never-sent messages behind it go there too.
    private func adopt(_ conversationID: String, from row: OutgoingMetadata, server: String) throws {
        let created = row.conversationID == nil
        var inherited = try work(server: server, id: row.identity)?.message
        row.conversationID = conversationID
        guard created, row.kind == "message" else { return }
        for successor in try laneRows(server, row.lane) where successor.sequence > row.sequence && !successor.attempted && successor.conversationID == nil && successor.kind == "message" {
            let chosen = try work(server: server, id: successor.identity)?.message
            successor.conversationID = conversationID; successor.omitCreationSettings = true
            successor.omitModel = chosen?.model == inherited?.model
            successor.omitEffort = chosen?.reasoningEffort == inherited?.reasoningEffort
            inherited = chosen
        }
    }
    /// A refused message retried by tap: a new identity takes the old one's place in its lane.
    func replace(server: String, id: String, with replacement: PendingWork, capturedDraft: DraftCapture? = nil) throws {
        try transaction(server) {
            guard let original = try metadata(server, id), original.state == "failed", replacement.state == .waiting else { throw StoreError.invalidTransition }
            let next = try insert(try capturePhotoDraft(replacement, server: server, capturedDraft: capturedDraft), server: server, sequence: original.sequence)
            next.lane = original.lane; next.conversationID = original.conversationID
            next.omitCreationSettings = original.omitCreationSettings; next.omitModel = original.omitModel; next.omitEffort = original.omitEffort
            if let payload = try raw(server, "outbox", id) { context.delete(payload) }
            context.delete(original)
        }
    }
    /// The ask stopped waiting before its answer landed. In one transaction the answer is
    /// settled and its words become an ordinary message in that conversation.
    func convertLateAnswer(server: String, id: String, message: PendingWork) throws {
        try transaction(server) {
            guard let row = try metadata(server, id), row.kind == "askAnswer", row.state == "waiting" else { throw StoreError.invalidTransition }
            row.state = PendingWork.State.sent.rawValue; row.completed = .now
            row.expiresAt = Self.expiry(kind: row.kind, state: .sent, completed: .now)
            if try metadata(server, message.id) == nil { try insert(message, server: server, sequence: row.sequence) }
        }
    }
    nonisolated static let expiryBatchLimit = 64
    /// The due index and limit bound every foreground cleanup transaction.
    @discardableResult
    func expire(server: String, now: Date = .now) throws -> [String] {
        let never = Date.distantFuture
        var query = FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server && ($0.expiresAt ?? never) <= now }, sortBy: [SortDescriptor(\.expiresAt)])
        query.fetchLimit = Self.expiryBatchLimit
        let due = try context.fetch(query)
        guard !due.isEmpty else { return [] }
        return try transaction(server) {
            let files = Set(due.compactMap(\.audioFile))
            for row in due {
                if let payload = try raw(server, "outbox", row.identity) { context.delete(payload) }
                context.delete(row)
            }
            return Array(files)
        }
    }
    func finishVoice(server: String, id: String, transcript: String, now: Date = .now) throws {
        try transaction(server) {
            guard let row = try metadata(server, id), row.kind == "voice", let draftID = row.draftID else { throw StoreError.missingWork }
            guard row.state != "sent" else { return }
            let draft = try raw(server, "draft", draftID).flatMap { String(data: $0.payload, encoding: .utf8) } ?? ""
            let text = draft.isEmpty ? transcript : draft + "\n\n" + transcript
            try writeDraft(text, server: server, id: draftID)
            row.state = "sent"; row.note = ""; row.completed = now; row.audioFile = nil; row.expiresAt = now.addingTimeInterval(Self.finishedLifetime)
        }
    }
    func retryVoice(server: String, id: String) throws {
        try transaction(server) {
            guard let row = try metadata(server, id), row.kind == "voice", ["waiting", "failed", "recording"].contains(row.state) else { throw StoreError.invalidTransition }
            row.state = "waiting"; row.note = ""; row.unsettledAttempts = 0; row.expiresAt = nil
        }
    }
    func remove(server: String, ids: [String]) throws {
        guard !ids.isEmpty else { return }
        try transaction(server) {
            for id in ids {
                if let row = try metadata(server, id) { context.delete(row) }
                if let payload = try raw(server, "outbox", id) { context.delete(payload) }
            }
        }
    }
}
