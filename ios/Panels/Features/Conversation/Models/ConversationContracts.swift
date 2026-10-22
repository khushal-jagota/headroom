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
    let rest: Rest
    struct Rest: Decodable, Sendable {
        let latestHappened: Happened?
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
    let mode = "steer"
    let senderMessageId: String
    let sentAtUnixMilliseconds: Int64
    struct Piece: Encodable, Sendable { let piece = "text"; let text: String }
}
struct ConversationDelivery: Decodable, Sendable {
    let conversationId: String?
    let fate: String
    let refusalReason: String?
}
protocol ConversationServing: Sendable {
    func conversationSnapshot(_ id: String, lens: ConversationLens, transcript: Bool) async throws -> ConversationSnapshot
    func conversationPage(_ id: String, cursor: ConversationCursor) async throws -> ConversationPage
    func conversationBody(_ id: String, reference: ConversationBodyReference) async throws -> ConversationBody
    func sendConversation(_ owner: ConversationOwner, message: ConversationSend) async throws -> ConversationDelivery
}
