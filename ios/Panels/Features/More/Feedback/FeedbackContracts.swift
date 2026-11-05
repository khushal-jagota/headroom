import Foundation

struct FeedbackResponse: Decodable, Sendable {
    let openCount: Int
    let open: [FeedbackNote]
    let handledGroups: [FeedbackHandledGroup]

    var handledCount: Int { handledGroups.reduce(0) { $0 + $1.notes.count } }
    var orderedHandledGroups: [FeedbackHandledGroup] {
        handledGroups.filter { $0.ticket != nil } + handledGroups.filter { $0.ticket == nil }
    }
}

struct FeedbackNote: Decodable, Identifiable, Sendable {
    let id: String
    let text: String
    let pageAddress: String?
    let pageLabel: String?
    let state: String
    let ticketId: String?
    let createdAt: Double
    let updatedAt: Double
    let handledAt: Double?
}

struct FeedbackHandledGroup: Decodable, Sendable {
    let ticket: FeedbackTicket?
    let notes: [FeedbackNote]
}

struct FeedbackTicket: Decodable, Sendable {
    let id: String
    let title: String
    let stage: String
    let ticketStatus: String
    let awaitingApproval: Bool?
    let awaitingAnswer: Bool?
    let assigned: Bool?
    let awaitingReply: Bool?
    let agentState: String?
    let conversationSignal: ConversationSignal?
}

enum FeedbackAction: String, Sendable { case dismiss, reopen }

protocol FeedbackServing: Sendable {
    func feedback() async throws -> FeedbackResponse
    func updateFeedback(_ id: String, action: FeedbackAction) async throws -> FeedbackNote
}
