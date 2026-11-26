import Foundation

// Subset of conversation/views/contracts.py, shared with the web projection.
enum ConversationOwner: Codable, Hashable, Sendable {
    case chief, manager(String), ticket(String), goal(String)
    var path: String? {
        switch self {
        case .chief: return "/api/chief/conversation"
        case .manager(let id): return "/api/managers/\(id)/conversation"
        case .goal: return nil
        case .ticket(let id): return "/api/tickets/\(id)/conversation"
        }
    }
}
enum ConversationLens: String, CaseIterable, Sendable { case focus, full }
struct ConversationSnapshot: Codable, Sendable {
    let page: ConversationPage
    let status: ConversationStatus
    /// The tail's cursor: a following changes frame continues from this sequence.
    var coverage: ConversationCoverage? = nil
}
struct ConversationCoverage: Codable, Equatable, Sendable { let throughSequence: Int }
struct ConversationPage: Codable, Sendable {
    let items: [ConversationItem]
    let older: ConversationCursor?
    var projectionVersion: Int? = nil
    var scope: ConversationCursor.Scope? = nil
    var newer: ConversationCursor? = nil
}
struct ConversationCursor: Codable, Sendable {
    let projectionVersion: Int
    let lens: String
    let scope: Scope
    let order: Order
    struct Scope: Codable, Hashable, Sendable {
        let kind: String
        let key: String?
        private enum CodingKeys: String, CodingKey { case kind, key }
        func encode(to encoder: any Encoder) throws {
            var values = encoder.container(keyedBy: CodingKeys.self)
            try values.encode(kind, forKey: .kind)
            if let key { try values.encode(key, forKey: .key) }
            else { try values.encodeNil(forKey: .key) }
        }
    }
    struct Order: Codable, Equatable, Sendable { let sequence: Int; let slot: Int }
}
struct ConversationItem: Equatable, Codable, Identifiable, Sendable {
    let kind: String
    let key: String
    let senderMessageId: String?
    let turnKey: String?
    let sourceSequence: Int?
    let body: ConversationDeliveredBody?
    var order: ConversationCursor.Order? = nil
    var rowKind: String? = nil
    var workGroupKey: String? = nil
    var foldRule: String? = nil
    var settled: Bool? = nil
    var stopped: Bool? = nil
    var startedAtUnixMilliseconds: Int64? = nil
    var ending: String? = nil
    var durationSeconds: Double? = nil
    var toolCallCount: Int? = nil
    var foldedMessageCount: Int? = nil
    var latestMessageKey: String? = nil
    var isLatest: Bool? = nil
    var entryCount: Int? = nil
    var latestEntryKey: String? = nil
    var id: String { senderMessageId ?? key }
    var historyScope: ConversationCursor.Scope? {
        guard kind == "turn" || kind == "work_group" else { return nil }
        return .init(kind: kind, key: key)
    }
}
struct ConversationDeliveredBody: Equatable, Codable, Sendable {
    let kind: String
    let value: ConversationBody?
    let reference: ConversationBodyReference?
}
struct ConversationBodyReference: Equatable, Codable, Sendable {
    let bodyId: String
    let version: String
    let byteCount: Int
    let mediaType: String
}
struct ConversationBodyChunk: Decodable, Sendable {
    let text: String
    let nextOffset: Int?
}
struct ConversationBody: Equatable, Codable, Sendable {
    let kind: String
    let content: [Piece]?
    let title: String?
    let detail: String?
    let state: String?
    let ending: String?
    let errorSummary: String?
    let refusalDetail: String?
    let reason: String?
    let questions: [Question]?
    var senderLabel: String? = nil
    var answerFate: String? = nil
    var toOwner: Bool? = nil
    var taskId: String? = nil
    var taskDescription: String? = nil
    var taskStatus: String? = nil
    var model: String? = nil
    var reasoningEffort: String? = nil
    var replyText: String? = nil
    var status: String? = nil
    var toolCallId: String? = nil
    var toolKind: String? = nil
    var startedDetail: String? = nil
    var cappedDetailSequence: Int? = nil
    var automaticCompactionResult: String? = nil
    var answeredOptionLabel: String? = nil
    var answers: [Answer]? = nil
    var failureDetail: String? = nil
    var deadReason: String? = nil
    var name: String? = nil
    var provider: String? = nil
    var use: String? = nil
    var help: String? = nil
    var helpUrl: String? = nil
    var requestId: String? = nil
    var delivery: RequestDelivery? = nil
    struct Answer: Equatable, Codable, Sendable { let questionId: String; let answers: [String] }
    struct RequestDelivery: Equatable, Codable, Sendable { let status: String; let refusalReason: String? }
    var answerSentence: String? { answerSentence(machineName: nil) }
    func answerSentence(machineName: String?) -> String? {
        switch answerFate {
        case "saved": "The home saved your answer. Delivery to \(machineName ?? "the worker machine") is pending."
        case "applied": "\(machineName ?? "The worker machine") applied your answer."
        case "unconfirmed": "The home saved your answer. The original request is gone, so the app cannot confirm delivery."
        default: nil
        }
    }
    struct Piece: Equatable, Codable, Sendable {
        let piece: String
        let text: String?
        let fileName: String?
        var storedFileId: String? = nil
        var mediaType: String? = nil
        var byteCount: Int? = nil
        var width: Int? = nil
        var height: Int? = nil
        var isImage: Bool { piece == "image" || mediaType?.hasPrefix("image/") == true }
        var isVideo: Bool { mediaType?.hasPrefix("video/") == true }
    }
    struct Question: Equatable, Codable, Sendable { let question: String }
    var text: String {
        if let content { return content.compactMap(\.text).joined(separator: "\n") }
        switch kind {
        case "agent_turn_started": return reason ?? ""
        case "background_task_started": return "Background task started · " + (taskDescription ?? "Task")
        case "background_task_ended": return "Background task " + (taskStatus ?? "ended") + " · " + (taskDescription ?? "Task")
        case "model_changed": return [model, reasoningEffort].compactMap { $0 }.joined(separator: " · ")
        case "context_compacted": return "Context compacted"
        case "command_reply": return replyText ?? ""
        case "turn_stopped": return "Stopped without a recorded ending"
        case "turn_ended":
            if ending == "failed" { return errorSummary ?? "The turn failed." }
            if ending == "interrupted" { return "Stopped" }
            return automaticCompactionResult == "not_compacted" ? "Automatic compaction did not complete." : ""
        case "credential_request": return [name, provider, use, help].compactMap { $0 }.joined(separator: "\n")
        default: break
        }
        return title ?? questions?.map(\.question).joined(separator: "\n") ?? errorSummary ?? detail ?? (kind == "deferred" ? "Long message — open on the web to read in full" : "")
    }
    var isPrompt: Bool { kind.hasPrefix("prompt") }
    var isOwnerPrompt: Bool { isPrompt && (senderLabel == nil || ["You", "owner"].contains(senderLabel!)) }
}
struct ConversationStatus: Equatable, Codable, Sendable {
    let authority: String
    let turnRunning: Bool?
    let liveOutput: ConversationLiveOutput?
    let rest: Rest
    /// A change of either needs a fresh tail snapshot, as on the web.
    var stoppedWithoutEnding: Bool? = nil
    var runtimeEpoch: String? = nil
    var pendingPermission: RequestIdentity? = nil
    var pendingUserInput: RequestIdentity? = nil
    var backgroundTasks: [BackgroundTask]? = nil
    var diagnosticsExpired: Bool? = nil
    var liveToolKey: String? = nil
    var liveToolProgress: ConversationBodyReference? = nil

    struct RequestIdentity: Equatable, Codable, Sendable {
        let kind: String
        let requestId: String
        let rowKey: String
        let sourceSequence: Int
    }
    struct BackgroundTask: Equatable, Codable, Sendable {
        let taskId: String
        let taskDescription: String
    }
    struct LatestTurn: Equatable, Codable, Sendable {
        let startedAtUnixMilliseconds: Int64?
    }
    var hasBackgroundTaskAfterTheTurn: Bool {
        turnRunning == false && backgroundTasks?.isEmpty == false
    }
    struct Rest: Equatable, Codable, Sendable {
        let latestHappened: Happened?
        let latestOwnerMessageSequence: Int?
        let latestTool: Happened?
        let permissionTitleExcerpt: String?
        let planProgress: Plan
        var latestOwnerMessagePreview: String? = nil
        var latestTurn: LatestTurn? = nil
    }
    struct Happened: Equatable, Codable, Sendable {
        let kind: String
        let excerpt: String?
        let title: String?
        var senderLabel: String? = nil
    }
    struct Plan: Equatable, Codable, Sendable { let currentTextExcerpt: String? }
    var line: String {
        if authority != "current" { return "Connection status unavailable" }
        if let title = rest.permissionTitleExcerpt { return title }
        if turnRunning == true { return rest.planProgress.currentTextExcerpt ?? rest.latestTool?.title ?? "Working…" }
        return rest.latestHappened?.excerpt ?? "Conversation"
    }
}
struct ConversationSend: Codable, Sendable {
    let conversationId: String?
    let content: [Piece]
    var senderLabel = "You"
    var mode = "steer"
    var backendKey: String? = nil
    var model: String? = nil
    var reasoningEffort: String? = nil
    // Presence distinguishes an explicit reset from an unchanged omitted effort.
    var reasoningEffortIsSet = false
    let senderMessageId: String
    let sentAtUnixMilliseconds: Int64
    var ownerReadThroughSequence: Int? = nil
    /// A resend names the refused row it sends again, so the view folds that row away.
    var reconcilesSequence: Int? = nil
    struct Piece: Codable, Equatable, Sendable {
        var piece = "text"
        var text: String? = nil
        var data: String? = nil
        var mediaType: String? = nil
        var fileName: String? = nil
        var localVideo: SavedVideo? = nil
        var uploadId: String? = nil
        var preparationError: String? = nil
    }
}
extension ConversationSend {
    enum CodingKeys: String, CodingKey {
        case conversationId, content, senderLabel, mode, backendKey, model, reasoningEffort
        case senderMessageId, sentAtUnixMilliseconds, ownerReadThroughSequence, reconcilesSequence
    }
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        conversationId = try c.decodeIfPresent(String.self, forKey: .conversationId)
        content = try c.decode([Piece].self, forKey: .content)
        senderLabel = try c.decodeIfPresent(String.self, forKey: .senderLabel) ?? "You"
        mode = try c.decodeIfPresent(String.self, forKey: .mode) ?? "steer"
        backendKey = try c.decodeIfPresent(String.self, forKey: .backendKey)
        model = try c.decodeIfPresent(String.self, forKey: .model)
        reasoningEffort = try c.decodeIfPresent(String.self, forKey: .reasoningEffort)
        reasoningEffortIsSet = c.contains(.reasoningEffort)
        senderMessageId = try c.decode(String.self, forKey: .senderMessageId)
        sentAtUnixMilliseconds = try c.decode(Int64.self, forKey: .sentAtUnixMilliseconds)
        ownerReadThroughSequence = try c.decodeIfPresent(Int.self, forKey: .ownerReadThroughSequence)
        reconcilesSequence = try c.decodeIfPresent(Int.self, forKey: .reconcilesSequence)
    }
    func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(conversationId, forKey: .conversationId)
        try c.encode(content, forKey: .content); try c.encode(senderLabel, forKey: .senderLabel)
        try c.encode(mode, forKey: .mode); try c.encodeIfPresent(backendKey, forKey: .backendKey)
        try c.encodeIfPresent(model, forKey: .model)
        if let reasoningEffort { try c.encode(reasoningEffort, forKey: .reasoningEffort) }
        else if reasoningEffortIsSet { try c.encodeNil(forKey: .reasoningEffort) }
        try c.encode(senderMessageId, forKey: .senderMessageId)
        try c.encode(sentAtUnixMilliseconds, forKey: .sentAtUnixMilliseconds)
        try c.encodeIfPresent(ownerReadThroughSequence, forKey: .ownerReadThroughSequence)
        try c.encodeIfPresent(reconcilesSequence, forKey: .reconcilesSequence)
    }
}

struct ConversationDelivery: Decodable, Sendable {
    let conversationId: String?
    let fate: String
    let refusalReason: String?
    var reason: String? = nil
    static let unconfirmedNote = "Delivery unconfirmed"
    static func refusalText(_ reason: String?) -> String {
        switch reason {
        case "backend_did_not_start": "agent could not start"
        case "model_resolution_failed": "model is unavailable"
        case "session_did_not_load": "conversation could not load"
        case "write_to_backend_failed": "agent could not receive the message"
        case "no_such_conversation": "conversation is unavailable"
        case "no_running_turn_to_steer_into": "no turn is running"
        case "backend_cannot_steer", "running_turn_cannot_accept_steer", "message_cannot_be_steered": "try Queue instead"
        case "running_turn_changed_before_steer": "the running turn changed"
        case "backend_rejected_steer": "agent could not accept steering"
        default: reason?.replacingOccurrences(of: "_", with: " ") ?? "agent could not receive the message"
        }
    }
}
struct ConversationReadBoundary: Equatable, Sendable {
    let conversationID: String
    let throughSequence: Int
}

protocol ConversationServing: Sendable {
    func markConversationRead(_ id: String, through: Int) async throws -> Int
    func conversationSnapshot(_ id: String, lens: ConversationLens, transcript: Bool) async throws -> ConversationSnapshot
    func conversationLiveText(_ id: String, reference: ConversationBodyReference) async throws -> String
    func conversationReceipts(_ id: String, messageIDs: [String]) async throws -> [ConversationReceipt]
    func conversationPage(_ id: String, cursor: ConversationCursor) async throws -> ConversationPage
    func conversationScope(_ id: String, lens: ConversationLens, scope: ConversationCursor.Scope) async throws -> ConversationPage
    func conversationBody(_ id: String, reference: ConversationBodyReference) async throws -> ConversationBody
    func sendConversation(_ owner: ConversationOwner, message: ConversationSend) async throws -> ConversationDelivery
}

// Existing conversation detail and owner start-values contracts.
struct ConversationConfiguration: Decodable, Sendable {
    let machineId: String?
    let backendKey: String
    /// The backend can join a running turn. Older servers omit it; then Steer stays offered.
    let supportsSteer: Bool
    let model: String?
    let reasoningEffort: String?
    var heldPrompts: [HeldPrompt] = []
    let latestSequence: Int?
    let ownerReadThroughSequence: Int?
    struct HeldPrompt: Equatable, Decodable, Sendable {
        let heldPromptId: String
        let senderMessageId: String?
        let content: [ConversationBody.Piece]
        var queueReason: String? = nil
        var text: String { content.compactMap(\.text).joined(separator: "\n") }
        /// Why the server held this message, in the owner's words.
        var note: String {
            switch queueReason {
            case "attachment": "Queued · attachments wait for the turn to end"
            case "run_change": "Queued · a model change waits for the turn to end"
            case "steer_refused": "Queued · could not join the running turn"
            case "command_needs_its_own_turn": "Queued · a command needs its own turn"
            default: "Queued"
            }
        }
    }
    enum CodingKeys: String, CodingKey { case machineId, backendKey, supportsSteer, model, reasoningEffort, heldPrompts, latestSequence, ownerReadThroughSequence }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        machineId = try values.decodeIfPresent(String.self, forKey: .machineId)
        latestSequence = try values.decodeIfPresent(Int.self, forKey: .latestSequence)
        ownerReadThroughSequence = try values.decodeIfPresent(Int.self, forKey: .ownerReadThroughSequence)
        backendKey = try values.decode(String.self, forKey: .backendKey)
        supportsSteer = try values.decodeIfPresent(Bool.self, forKey: .supportsSteer) ?? true
        model = try values.decodeIfPresent(String.self, forKey: .model)
        reasoningEffort = try values.decodeIfPresent(String.self, forKey: .reasoningEffort)
        heldPrompts = try values.decodeIfPresent([HeldPrompt].self, forKey: .heldPrompts) ?? []
    }
}
struct ConversationRow: Identifiable, Equatable {
    let id: String
    let text: String
    let own: Bool
    let note: String?
    var senderLabel: String? = nil
    var previewText: String { (own ? "You: " : senderLabel.map { $0 + ": " } ?? "") + (text.isEmpty ? (pieces.compactMap(\.fileName) + attachments.compactMap(\.fileName)).joined(separator: ", ") : text) }
    var retryID: String? = nil
    var item: ConversationItem? = nil
    enum Presentation: Equatable { case message, narration, turn, workGroup, tool, system }
    var body: ConversationBody? = nil
    var pieces: [ConversationBody.Piece] = []
    var presentation: Presentation = .message
    var breakBefore = false
    var scopeExpanded = false
    var scopeLoading = false
    /// Attachments still on the phone, shown as previews until the server has the message.
    var attachments: [ConversationSend.Piece] = []
}

struct ConversationReceipt: Decodable, Sendable {
    let senderMessageId: String
    let fate: String
    var sourceSequence: Int? = nil
}
struct ConversationRetainedRange: Encodable, Sendable {
    let scope: ConversationCursor.Scope
    let first: ConversationCursor.Order
    let last: ConversationCursor.Order
}
extension ConversationServing {
    func conversationScope(_ id: String, lens: ConversationLens, scope: ConversationCursor.Scope) async throws -> ConversationPage {
        throw APIError(status: 501, message: "Conversation history is unavailable.")
    }
    func conversationReceipts(_ id: String, messageIDs: [String]) async throws -> [ConversationReceipt] { [] }
}

/// Recognition may return after Stop or Cancel. Only the current capture may
/// produce draft text, and only finishing releases its buffered words.
struct ConversationSpeechDraft {
    private(set) var generation = 0
    private var words = ""
    mutating func begin() -> Int { cancel(); return generation }
    mutating func receive(_ text: String, generation: Int) {
        guard generation == self.generation else { return }
        words = text
    }
    mutating func finish(generation: Int) -> String? {
        guard generation == self.generation else { return nil }
        let result = words.trimmingCharacters(in: .whitespacesAndNewlines)
        cancel()
        return result
    }
    mutating func cancel() { generation += 1; words = "" }
}

/// The open tail's frames (conversation/views/api.py `_tail`): a snapshot of the newest
/// window, then changes against it. A reset or gap needs a fresh snapshot.
struct ConversationTailChanges: Decodable, Sendable {
    struct ScopeChanges: Decodable, Sendable { let scope: ConversationCursor.Scope; let itemKeys: [String]; let removedKeys: [String] }
    let lens: String
    let fromSequence: Int
    let coverage: ConversationCoverage
    let upserts: [ConversationItem]
    let scopeChanges: [ScopeChanges]
    let removedKeys: [String]
    let invalidatedScopes: [ConversationCursor.Scope]
    let status: ConversationStatus
}
enum ConversationTailFrame: Sendable {
    case snapshot(ConversationSnapshot)
    case changes(ConversationTailChanges)
    case reset
}
/// URLSession's line sequence omits blank lines, so a frame is its `event:` line
/// followed by one `data:` line. Comments are heartbeats; unknown events are ignored.
struct ConversationTailParser {
    private var event = "message"
    mutating func consume(_ line: String) throws -> ConversationTailFrame? {
        if line.hasPrefix("event:") { event = line.dropFirst(6).trimmingCharacters(in: .whitespaces); return nil }
        guard line.hasPrefix("data:") else { return nil }
        defer { event = "message" }
        var payload = line.dropFirst(5)
        if payload.first == " " { payload = payload.dropFirst() }
        let data = Data(payload.utf8)
        switch event {
        case "snapshot": return .snapshot(try APIClient.decoder().decode(ConversationSnapshot.self, from: data))
        case "changes": return .changes(try APIClient.decoder().decode(ConversationTailChanges.self, from: data))
        case "reset": return .reset
        default: return nil
        }
    }
}

struct ConversationLiveOutput: Equatable, Codable, Sendable {
    let turnKey: String
    let reference: ConversationBodyReference
}
extension ConversationServing {
    func conversationLiveText(_ id: String, reference: ConversationBodyReference) async throws -> String { "" }
}

struct ConversationHeldChange: Decodable, Sendable {
    let fate: String?
    let discarded: Bool?
    let refusalReason: String?
    let reason: String?
}
