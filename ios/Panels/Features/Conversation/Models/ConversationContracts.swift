import Foundation

// Subset of conversation/views/contracts.py, shared with the web projection.
enum ConversationOwner: Hashable, Sendable {
    case chief, manager(String), ticket(String)
    var path: String {
        switch self {
        case .chief: return "/api/chief/conversation"
        case .manager(let id): return "/api/managers/\(id)/conversation"
        case .ticket(let id): return "/api/tickets/\(id)/conversation"
        }
    }
}
enum ConversationLens: String, CaseIterable, Sendable { case focus, full }
struct ConversationSnapshot: Decodable, Sendable {
    let page: ConversationPage
    let status: ConversationStatus
}
struct ConversationPage: Decodable, Sendable {
    let items: [ConversationItem]
    let older: ConversationCursor?
}
struct ConversationCursor: Codable, Sendable {
    let projectionVersion: Int
    let lens: String
    let scope: Scope
    let order: Order
    struct Scope: Codable, Sendable { let kind: String; let key: String? }
    struct Order: Codable, Sendable { let sequence: Int; let slot: Int }
}
struct ConversationItem: Decodable, Identifiable, Sendable {
    let kind: String
    let key: String
    let senderMessageId: String?
    let turnKey: String?
    let sourceSequence: Int?
    let body: ConversationDeliveredBody?
    var id: String { senderMessageId ?? key }
}
struct ConversationDeliveredBody: Decodable, Sendable {
    let kind: String
    let value: ConversationBody?
    let reference: ConversationBodyReference?
}
struct ConversationBodyReference: Codable, Sendable {
    let bodyId: String
    let version: String
    let byteCount: Int
    let mediaType: String
}
struct ConversationBodyChunk: Decodable, Sendable {
    let text: String
    let nextOffset: Int?
}
struct ConversationBody: Decodable, Sendable {
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
    struct Piece: Decodable, Sendable { let piece: String; let text: String?; let fileName: String? }
    struct Question: Decodable, Sendable { let question: String }
    var text: String {
        if let content { return content.map { $0.text ?? "[\($0.fileName ?? $0.piece)]" }.joined(separator: "\n") }
        return title ?? questions?.map(\.question).joined(separator: "\n") ?? errorSummary ?? detail ?? (kind == "deferred" ? "Long message — open on the web to read in full" : "")
    }
    var isPrompt: Bool { kind.hasPrefix("prompt") }
}
struct ConversationStatus: Decodable, Sendable {
    let authority: String
    let turnRunning: Bool?
    let liveOutput: ConversationLiveOutput?
    let rest: Rest
    struct Rest: Decodable, Sendable {
        let latestHappened: Happened?
        let latestOwnerMessageSequence: Int?
        let latestTool: Happened?
        let permissionTitleExcerpt: String?
        let planProgress: Plan
    }
    struct Happened: Decodable, Sendable { let kind: String; let excerpt: String?; let title: String? }
    struct Plan: Decodable, Sendable { let currentTextExcerpt: String? }
    var line: String {
        if authority != "current" { return "Connection status unavailable" }
        if let title = rest.permissionTitleExcerpt { return title }
        if turnRunning == true { return rest.planProgress.currentTextExcerpt ?? rest.latestTool?.title ?? "Working…" }
        return rest.latestHappened?.excerpt ?? "Conversation"
    }
}
struct ConversationSend: Encodable, Sendable {
    let conversationId: String?
    let content: [Piece]
    let senderLabel = "You"
    var mode = "steer"
    var backendKey: String? = nil
    var model: String? = nil
    var reasoningEffort: String? = nil
    let senderMessageId: String
    let sentAtUnixMilliseconds: Int64
    struct Piece: Encodable, Sendable {
        var piece = "text"
        var text: String? = nil
        var data: String? = nil
        var mediaType: String? = nil
        var fileName: String? = nil
    }
}
struct ConversationDelivery: Decodable, Sendable {
    let conversationId: String?
    let fate: String
    let refusalReason: String?
    var reason: String? = nil
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
protocol ConversationServing: Sendable {
    func conversationSnapshot(_ id: String, lens: ConversationLens, transcript: Bool) async throws -> ConversationSnapshot
    func conversationLiveText(_ id: String, reference: ConversationBodyReference) async throws -> String
    func conversationReceipts(_ id: String, messageIDs: [String]) async throws -> [ConversationReceipt]
    func conversationPage(_ id: String, cursor: ConversationCursor) async throws -> ConversationPage
    func conversationBody(_ id: String, reference: ConversationBodyReference) async throws -> ConversationBody
    func sendConversation(_ owner: ConversationOwner, message: ConversationSend) async throws -> ConversationDelivery
}

// Existing conversation detail and owner start-values contracts.
struct ConversationConfiguration: Decodable, Sendable {
    let backendKey: String
    let model: String?
    let reasoningEffort: String?
    var heldPrompts: [HeldPrompt] = []
    let latestSequence: Int?
    let ownerReadThroughSequence: Int?
    struct HeldPrompt: Decodable, Sendable {
        let heldPromptId: String
        let senderMessageId: String?
        let content: [ConversationBody.Piece]
        var text: String { content.compactMap(\.text).joined(separator: "\n") }
    }
    enum CodingKeys: String, CodingKey { case backendKey, model, reasoningEffort, heldPrompts, latestSequence, ownerReadThroughSequence }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        latestSequence = try values.decodeIfPresent(Int.self, forKey: .latestSequence)
        ownerReadThroughSequence = try values.decodeIfPresent(Int.self, forKey: .ownerReadThroughSequence)
        backendKey = try values.decode(String.self, forKey: .backendKey)
        model = try values.decodeIfPresent(String.self, forKey: .model)
        reasoningEffort = try values.decodeIfPresent(String.self, forKey: .reasoningEffort)
        heldPrompts = try values.decodeIfPresent([HeldPrompt].self, forKey: .heldPrompts) ?? []
    }
}
struct ConversationRow: Identifiable {
    let id: String
    let text: String
    let own: Bool
    let note: String?
    var retryID: String? = nil
    var item: ConversationItem? = nil
}

struct ConversationReceipt: Decodable, Sendable {
    let senderMessageId: String
    let fate: String
}
extension ConversationServing {
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

struct ConversationLiveOutput: Decodable, Sendable {
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
