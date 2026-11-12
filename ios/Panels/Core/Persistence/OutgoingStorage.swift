import Foundation
import SwiftData

/// Original installed schema. Keep the entity and property names for on-disk admission.
enum PhoneOriginalSchema: VersionedSchema {
    static var versionIdentifier: Schema.Version { Schema.Version(1, 0, 0) }
    static var models: [any PersistentModel.Type] { [LocalRecord.self] }
    @Model final class LocalRecord {
        @Attribute(.unique) var key: String
        var server: String
        var kind: String
        var payload: Data
        var updated: Date
        init(key: String, server: String, kind: String, payload: Data, updated: Date) {
            self.key = key; self.server = server; self.kind = kind; self.payload = payload; self.updated = updated
        }
    }
}

enum PhoneRenamedSchema: VersionedSchema {
    static var versionIdentifier: Schema.Version { Schema.Version(2, 0, 0) }
    static var models: [any PersistentModel.Type] { [LocalRecord.self] }
}

enum PhoneOutgoingSchema: VersionedSchema {
    static var versionIdentifier: Schema.Version { Schema.Version(3, 0, 0) }
    static var models: [any PersistentModel.Type] { [LocalRecord.self, OutgoingMetadata.self] }
}

enum PhoneStoreMigration: SchemaMigrationPlan {
    static var schemas: [any VersionedSchema.Type] { [PhoneOriginalSchema.self, PhoneRenamedSchema.self, PhoneOutgoingSchema.self] }
    static var stages: [MigrationStage] {
        [.lightweight(fromVersion: PhoneOriginalSchema.self, toVersion: PhoneRenamedSchema.self),
         .lightweight(fromVersion: PhoneRenamedSchema.self, toVersion: PhoneOutgoingSchema.self)]
    }
}

/// Queryable scheduling and presentation facts. The original payload stays once in LocalRecord.
@Model final class OutgoingMetadata {
    #Index<OutgoingMetadata>([\.state], [\.server, \.expiresAt], [\.audioFile], [\.server, \.state], [\.server, \.kind], [\.server, \.conversationID], [\.server, \.sequence], [\.server, \.lane, \.sequence], [\.server, \.ownerKey], [\.server, \.draftID])
    @Attribute(.unique) var key: String
    var server: String
    var identity: String
    var ownerKey: String?
    var conversationID: String?
    var ownerReadThroughSequence: Int?
    var ticketID: String?
    var draftID: String?
    var audioFile: String?
    var expiresAt: Date?
    var lane: String
    var kind: String
    var state: String
    var note: String = "Waiting for connection"
    var omitCreationSettings: Bool = false
    var omitModel: Bool = false
    var omitEffort: Bool = false
    var created: Date
    var completed: Date?
    var sequence: Int64
    var provenance: String
    var scheduled: Bool
    var predecessorID: String?
    var replacementID: String?
    var holdReason: String?
    var failure: String?
    var proposalField: String?
    var proposalRevision: Int?
    var proposalCreatedAt: Double?

    init(record: LocalRecord, work: PendingWork?, sequence: Int64) {
        let savedIdentity = work?.id ?? record.key.components(separatedBy: "\u{1f}").last ?? record.key
        let savedOwner = work?.owner.map(Self.ownerKey)
        let savedConversation = work?.message?.conversationId
        let savedTicket = work?.ticketID, savedDraft = work?.draftID
        let savedLane: String
        if let savedConversation { savedLane = "conversation:\(savedConversation)" }
        else if let savedTicket { savedLane = "ticket:\(savedTicket)" }
        else if let savedDraft { savedLane = "draft:\(savedDraft)" }
        else { savedLane = "start:\(savedOwner ?? "unknown"):\(savedIdentity)" }
        key = record.key; server = record.server
        identity = savedIdentity; ownerKey = savedOwner; conversationID = savedConversation
        ownerReadThroughSequence = work?.message?.ownerReadThroughSequence
        ticketID = savedTicket; draftID = savedDraft; lane = savedLane
        audioFile = work?.audioFile
        if let work {
            switch work.state {
            case .sent, .changed: expiresAt = (work.completed ?? work.created).addingTimeInterval(86400)
            case .uncertain: expiresAt = (work.completed ?? work.created).addingTimeInterval(7 * 86400)
            default: expiresAt = nil
            }
        } else { expiresAt = nil }
        kind = work?.kind.rawValue ?? "unknown"
        note = work?.note ?? "Saved work needs attention"
        state = work?.state.rawValue ?? "storageError"
        created = work?.created ?? record.updatedAt; completed = work?.completed
        self.sequence = sequence
        provenance = "unknown"
        scheduled = work.map { ![.sent, .changed].contains($0.state) } ?? false
        let command = work?.protectedDecisionBody.flatMap { try? APIClient.decoder().decode(DecisionCommand.self, from: $0) }
        proposalField = command?.field ?? work?.proposal?.field
        proposalRevision = command?.expectedProposalRevision ?? work?.proposal?.revision
        proposalCreatedAt = work?.protectedProposalCreatedAt ?? work?.proposal?.createdAt
        failure = work == nil ? "Saved outgoing work could not be decoded. Its original bytes remain on this phone." : nil
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

/// Add metadata once, without rewriting any saved intent or making it dispatchable.
@MainActor enum OutgoingStoreMigration {
    static let markerID = "outgoing-v1"
    private static func storageError(_ context: ModelContext) throws -> String? {
        var query = FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.state == "storageError" })
        query.fetchLimit = 1
        return try context.fetch(query).first?.failure
    }
    static func backfill(_ context: ModelContext, beforeSave: (() throws -> Void)? = nil) throws -> String? {
        do {
            let markerKey = LocalStore.key("", "outgoingMigration", markerID)
            var marker = FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.key == markerKey })
            marker.fetchLimit = 1
            if try !context.fetch(marker).isEmpty { return try storageError(context) }
            let indexed = try context.fetch(FetchDescriptor<OutgoingMetadata>())
            for row in indexed where row.provenance == "unknown" && ["waiting", "sending"].contains(row.state) {
                row.state = "uncertain"; row.note = "Delivery uncertain · check before another attempt"; row.expiresAt = row.created.addingTimeInterval(7 * 86400)
            }
            let keys = Set(indexed.map(\.key))
            let rows = try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.kind == "outbox" }))
            let pending = rows.filter { !keys.contains($0.key) }.map { row in
                (row, try? JSONDecoder().decode(PendingWork.self, from: row.payload))
            }.sorted {
                let lhs = $0.1?.created ?? $0.0.updatedAt, rhs = $1.1?.created ?? $1.0.updatedAt
                return lhs == rhs ? $0.0.key < $1.0.key : lhs < rhs
            }
            var sequence = indexed.map(\.sequence).max() ?? 0
            for (record, work) in pending {
                sequence += 1
                let metadata = OutgoingMetadata(record: record, work: work, sequence: sequence)
                if ["waiting", "sending"].contains(metadata.state) {
                    metadata.state = "uncertain"; metadata.note = "Delivery uncertain · check before another attempt"; metadata.expiresAt = metadata.created.addingTimeInterval(7 * 86400)
                }
                context.insert(metadata)
            }
            context.insert(LocalRecord(key: markerKey, server: "", kind: "outgoingMigration", payload: Data([1]), updated: .now))
            if context.hasChanges { try beforeSave?(); try context.save() }
            return try storageError(context)
        } catch {
            context.rollback()
            throw error
        }
    }
}

/// Main-actor ownership serializes outgoing payload, metadata and composer transactions.
/// Payload bytes are immutable after insertion. Mutable delivery facts live in metadata.
@MainActor final class OutgoingStore {
    struct DraftCapture {
        struct Value {
            let payload: Data
            let updated: Date
        }
        let id: String
        let text: Value?
        let attachments: Value?
    }
    enum Destination { case conversation(String), newConversation }
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
        let provenance: String
        let scheduled: Bool
        let predecessorID: String?
        let replacementID: String?
        let holdReason: String?
        let proposalField: String?
        let proposalRevision: Int?
        let proposalCreatedAt: Double?
        init(_ value: OutgoingMetadata) {
            id = value.identity; ownerKey = value.ownerKey; conversationID = value.conversationID; ownerReadThroughSequence = value.ownerReadThroughSequence
            ticketID = value.ticketID; draftID = value.draftID; lane = value.lane
            state = value.state; kind = value.kind; note = value.note; sequence = value.sequence
            provenance = value.provenance; scheduled = value.scheduled
            predecessorID = value.predecessorID; replacementID = value.replacementID; holdReason = value.holdReason
            proposalField = value.proposalField; proposalRevision = value.proposalRevision
            proposalCreatedAt = value.proposalCreatedAt
        }
    }
    private let context: ModelContext
    private let beforeSave: (() throws -> Void)?
    private let changed: (String) -> Void
    private let failed: (Error) -> Void
    init(context: ModelContext, beforeSave: (() throws -> Void)? = nil, changed: @escaping (String) -> Void, failed: @escaping (Error) -> Void) {
        self.context = context; self.beforeSave = beforeSave; self.changed = changed; self.failed = failed
    }
    private func transaction<T>(_ server: String, _ body: () throws -> T) throws -> T {
        do {
            let result = try body()
            if context.hasChanges { try beforeSave?(); try context.save(); changed(server) }
            return result
        } catch { context.rollback(); failed(error); throw error }
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
    func summary(server: String, id: String) throws -> Summary? { try metadata(server, id).map(Summary.init) }
    func summaries(server: String, owner: ConversationOwner) throws -> [Summary] {
        let ownerKey = OutgoingMetadata.ownerKey(owner)
        return try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server && $0.ownerKey == ownerKey }, sortBy: [SortDescriptor(\.sequence)])).map(Summary.init)
    }
    func summaries(server: String, draftID: String) throws -> [Summary] {
        try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server && $0.draftID == draftID }, sortBy: [SortDescriptor(\.sequence)])).map(Summary.init)
    }
    func summaries(server: String, conversationID: String) throws -> [Summary] {
        try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server && $0.conversationID == conversationID }, sortBy: [SortDescriptor(\.sequence)])).map(Summary.init)
    }
    func replySummaries(server: String, conversationID: String) throws -> [Summary] {
        try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server && $0.conversationID == conversationID && $0.ownerReadThroughSequence != nil }, sortBy: [SortDescriptor(\.sequence)])).map(Summary.init)
    }
    func allSummaries(server: String) throws -> [Summary] {
        try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server }, sortBy: [SortDescriptor(\.sequence)])).map(Summary.init)
    }
    func decisionSummaries(server: String, ticketID: String) throws -> [Summary] {
        try laneRows(server, "ticket:\(ticketID)").filter { ["approval", "sendBack"].contains($0.kind) }.map(Summary.init)
    }
    func decisionSummaries(server: String) throws -> [Summary] {
        let kinds = ["approval", "sendBack"]
        return try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server && kinds.contains($0.kind) }, sortBy: [SortDescriptor(\.sequence)])).map(Summary.init)
    }
    /// The bounded scheduler reads metadata only. Detail payloads decode on demand.
    func schedulingSummaries(server: String) throws -> [Summary] {
        let states = ["waiting", "sending", "uncertain", "serverQueued"]
        return try context.fetch(FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.server == server && states.contains($0.state) }, sortBy: [SortDescriptor(\.sequence)])).map(Summary.init)
    }
    func work(server: String, id: String) throws -> PendingWork? {
        guard let row = try raw(server, "outbox", id), let metadata = try metadata(server, id) else { return nil }
        var work = try JSONDecoder().decode(PendingWork.self, from: row.payload)
        work.state = PendingWork.State(rawValue: metadata.state) ?? .failed
        work.note = metadata.note; work.completed = metadata.completed
        if let message = work.message, metadata.conversationID != message.conversationId || metadata.omitCreationSettings {
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
    private func insert(_ work: PendingWork, server: String, payload: Data? = nil) throws -> OutgoingMetadata {
        guard try metadata(server, work.id) == nil, try raw(server, "outbox", work.id) == nil else { throw StoreError.duplicateIdentity }
        let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys
        let row = LocalRecord(key: LocalStore.key(server, "outbox", work.id), server: server, kind: "outbox", payload: try payload ?? encoder.encode(work), updated: .now)
        let metadata = OutgoingMetadata(record: row, work: work, sequence: try nextSequence(server))
        metadata.provenance = work.state == .waiting || work.state == .recording ? "unattempted" : "unknown"
        if work.kind == .message, work.message?.conversationId == nil, let ownerKey = metadata.ownerKey {
            // All queued messages behind one unresolved creation keep that exact start lane.
            var query = FetchDescriptor<OutgoingMetadata>(predicate: #Predicate {
                $0.server == server && $0.ownerKey == ownerKey && $0.kind == "message" && $0.conversationID == nil && $0.scheduled
            }, sortBy: [SortDescriptor(\.sequence, order: .reverse)])
            query.fetchLimit = 1
            if let previous = try context.fetch(query).first, !["sent", "changed"].contains(previous.state) { metadata.lane = previous.lane }
        }
        metadata.predecessorID = try laneRows(server, metadata.lane).last(where: { $0.scheduled && !["sent", "changed", "serverQueued"].contains($0.state) })?.identity
        context.insert(row); context.insert(metadata)
        return metadata
    }
    func enqueue(_ work: PendingWork, server: String, capturedDraft: DraftCapture? = nil) throws {
        try transaction(server) {
            _ = try insert(work, server: server)
            if let capturedDraft { try clearCaptured(capturedDraft, server: server) }
        }
    }
    func canSubmit(server: String, id: String) throws -> Bool {
        guard let row = try metadata(server, id), row.scheduled, row.state == "waiting", row.provenance == "unattempted", row.holdReason == nil else { return false }
        guard let predecessor = row.predecessorID else { return true }
        // Missing predecessors require an explicit decision, never an inferred release.
        guard let previous = try metadata(server, predecessor) else { return false }
        return ["sent", "changed", "serverQueued"].contains(previous.state)
    }
    func beginAttempt(server: String, id: String) throws {
        try transaction(server) {
            guard try canSubmit(server: server, id: id), let row = try metadata(server, id) else { throw StoreError.invalidTransition }
            row.provenance = "attempted"; row.state = "sending"; row.note = "Sending…"
        }
    }
    /// Receipts update their exact operation even after a replacement retires its schedule.
    func settle(server: String, id: String, state: PendingWork.State, note: String, conversationID: String? = nil, now: Date = .now) throws {
        try transaction(server) {
            guard let row = try metadata(server, id) else { throw StoreError.missingWork }
            let oldLane = row.lane
            if row.state != state.rawValue { row.state = state.rawValue }
            if row.note != note { row.note = note }
            if [.sent, .changed, .failed].contains(state), row.completed == nil { row.completed = now }
            let deadline: Date?
            switch state {
            case .sent, .changed: deadline = (row.completed ?? row.created).addingTimeInterval(86400)
            case .uncertain: deadline = (row.completed ?? row.created).addingTimeInterval(7 * 86400)
            default: deadline = nil
            }
            if row.expiresAt != deadline { row.expiresAt = deadline }
            if let conversationID, row.conversationID != conversationID {
                row.conversationID = conversationID
                // A late original receipt never retargets a replacement's successors.
                if row.scheduled {
                    var inherited = try work(server: server, id: row.identity)?.message
                    for successor in try laneRows(server, oldLane) where successor.sequence > row.sequence && successor.scheduled && successor.provenance == "unattempted" && successor.conversationID == nil {
                        let chosen = try work(server: server, id: successor.identity)?.message
                        successor.conversationID = conversationID; successor.omitCreationSettings = true
                        successor.omitModel = chosen?.model == inherited?.model
                        successor.omitEffort = chosen?.reasoningEffort == inherited?.reasoningEffort
                        inherited = chosen
                        successor.lane = "conversation:\(conversationID)"
                    }
                    row.lane = "conversation:\(conversationID)"
                }
            }
        }
    }
    /// An explicit resend occupies the old logical position; the original keeps its own receipt.
    func replace(server: String, id: String, with replacement: PendingWork) throws {
        try transaction(server) {
            guard let original = try metadata(server, id), (["uncertain", "failed"].contains(original.state) || (original.state == "waiting" && original.provenance == "unknown")), replacement.state == .waiting else { throw StoreError.invalidTransition }
            guard original.state == "failed" || !(replacement.priorMessageIDs ?? []).contains(id) else { throw StoreError.invalidTransition }
            let next = try insert(replacement, server: server)
            next.sequence = original.sequence; next.lane = original.lane; next.predecessorID = original.predecessorID
            next.conversationID = original.conversationID; next.omitCreationSettings = original.omitCreationSettings; next.omitModel = original.omitModel; next.omitEffort = original.omitEffort
            if original.conversationID == nil {
                next.holdReason = "destinationUnresolved"
                next.note = "Not sent · choose a conversation or start a new one"
            }
            if original.state == "waiting" && original.provenance == "unknown" { original.state = "uncertain"; original.expiresAt = original.created.addingTimeInterval(7 * 86400) }
            original.scheduled = false; original.replacementID = next.identity
            for successor in try laneRows(server, original.lane) where successor.identity != next.identity && successor.predecessorID == id {
                successor.predecessorID = next.identity
            }
        }
    }
    nonisolated static let expiryBatchLimit = 64
    /// The due index and limit bound every recurring foreground cleanup transaction.
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
                for successor in try laneRows(server, row.lane) where successor.predecessorID == row.identity {
                    if !["sent", "changed", "serverQueued"].contains(row.state) {
                        successor.holdReason = successor.conversationID == nil ? "destinationUnresolved" : "predecessorExpired"
                        successor.note = "Not sent · choose how to send this message"
                    }
                    successor.predecessorID = nil
                }
                if let payload = try raw(server, "outbox", row.identity) { context.delete(payload) }
                context.delete(row)
            }
            return Array(files)
        }
    }
    func audioIsUnowned(_ file: String) throws -> Bool {
        var query = FetchDescriptor<OutgoingMetadata>(predicate: #Predicate { $0.audioFile == file })
        query.fetchLimit = 1
        return try context.fetch(query).isEmpty
    }

    func releaseHold(server: String, id: String, destination: Destination) throws {
        try transaction(server) {
            guard let row = try metadata(server, id), row.provenance == "unattempted", row.state == "waiting", row.holdReason != nil else { throw StoreError.invalidTransition }
            let oldLane = row.lane
            switch destination {
            case .conversation(let conversationID):
                guard !conversationID.isEmpty, row.conversationID == nil || row.conversationID == conversationID else { throw StoreError.invalidTransition }
                row.conversationID = conversationID; row.lane = "conversation:\(conversationID)"; row.omitCreationSettings = true
            case .newConversation:
                guard row.conversationID == nil else { throw StoreError.invalidTransition }
                row.lane = "start:\(row.ownerKey ?? "unknown"):\(row.identity)"
            }
            // Carry only the dependent, never-attempted chain. Other work in the old
            // lane or a retired original does not inherit this explicit choice.
            var predecessors: Set<String> = [row.identity]
            var inherited = try work(server: server, id: row.identity)?.message
            for successor in try laneRows(server, oldLane) where successor.identity != row.identity && successor.scheduled && successor.provenance == "unattempted" {
                guard let predecessor = successor.predecessorID, predecessors.contains(predecessor) else { continue }
                let chosen = try work(server: server, id: successor.identity)?.message
                successor.lane = row.lane; successor.conversationID = row.conversationID
                successor.omitCreationSettings = row.omitCreationSettings
                successor.omitModel = row.conversationID != nil && chosen?.model == inherited?.model
                successor.omitEffort = row.conversationID != nil && chosen?.reasoningEffort == inherited?.reasoningEffort
                inherited = chosen
                predecessors.insert(successor.identity)
            }
            row.holdReason = nil; row.predecessorID = nil; row.note = "Waiting for connection"
        }
    }
    /// Compatibility entry point for saved work. Existing intent is never reencoded.
    func saveEncoded(_ data: Data, server: String, id: String, draft: (String, String)? = nil) throws {
        try transaction(server) {
            let decoded = try? JSONDecoder().decode(PendingWork.self, from: data)
            if let row = try metadata(server, id) {
                if let decoded {
                    if row.state != decoded.state.rawValue { row.state = decoded.state.rawValue }
                    if row.note != decoded.note { row.note = decoded.note }
                    if row.completed != decoded.completed { row.completed = decoded.completed }
                    let deadline: Date?
                    switch decoded.state {
                    case .sent, .changed: deadline = (decoded.completed ?? decoded.created).addingTimeInterval(86400)
                    case .uncertain: deadline = (decoded.completed ?? decoded.created).addingTimeInterval(7 * 86400)
                    default: deadline = nil
                    }
                    if row.expiresAt != deadline { row.expiresAt = deadline }
                }
            } else if let decoded {
                _ = try insert(decoded, server: server, payload: data)
            } else {
                let payload = LocalRecord(key: LocalStore.key(server, "outbox", id), server: server, kind: "outbox", payload: data, updated: .now)
                context.insert(payload)
                context.insert(OutgoingMetadata(record: payload, work: nil, sequence: try nextSequence(server)))
            }
            if let (draftID, text) = draft { try writeDraft(text, server: server, id: draftID) }
        }
    }
    private func writeDraft(_ text: String, server: String, id: String) throws {
        if let row = try raw(server, "draft", id) {
            row.payload = Data(text.utf8); row.updatedAt = .now
        } else {
            context.insert(LocalRecord(key: LocalStore.key(server, "draft", id), server: server, kind: "draft", payload: Data(text.utf8), updated: .now))
        }
    }
    func finishVoice(server: String, id: String, transcript: String, now: Date = .now) throws {
        try transaction(server) {
            guard let row = try metadata(server, id), row.kind == "voice", let draftID = row.draftID else { throw StoreError.missingWork }
            guard row.state != "sent" else { return }
            let draft = try raw(server, "draft", draftID).flatMap { String(data: $0.payload, encoding: .utf8) } ?? ""
            let text = draft.isEmpty ? transcript : draft + "\n\n" + transcript
            try writeDraft(text, server: server, id: draftID)
            row.state = "sent"; row.note = "Transcribed"; row.completed = now; row.audioFile = nil; row.expiresAt = now.addingTimeInterval(86400)
        }
    }
    func retryVoice(server: String, id: String) throws {
        try transaction(server) {
            guard let row = try metadata(server, id), row.kind == "voice", ["waiting", "failed", "uncertain", "recording"].contains(row.state) else { throw StoreError.invalidTransition }
            row.provenance = "unattempted"; row.state = "waiting"; row.note = "Waiting for connection"; row.expiresAt = nil
        }
    }
    func remove(server: String, ids: [String]) throws {
        guard !ids.isEmpty else { return }
        try transaction(server) {
            for id in ids {
                guard let row = try metadata(server, id) else { continue }
                for successor in try laneRows(server, row.lane) where successor.predecessorID == id {
                    successor.holdReason = successor.conversationID == nil ? "destinationUnresolved" : "predecessorExpired"
                    successor.predecessorID = nil
                }
                if let payload = try raw(server, "outbox", id) { context.delete(payload) }
                context.delete(row)
            }
        }
    }

    func rejectAdmission(server: String, id: String, note: String) throws {
        try transaction(server) {
            guard let row = try metadata(server, id), row.provenance == "attempted", row.state == "sending" else { throw StoreError.invalidTransition }
            row.provenance = "unattempted"; row.state = "waiting"; row.note = note
        }
    }

}
