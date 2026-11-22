import Foundation

/// One saved phone action. The outbox saves it before the screen moves on, and sends it
/// while the app is open. `waiting` looks done on screen; only `failed` (refused, or
/// given up after the server kept answering without settling it) shows a mark.
struct PendingWork: Codable, Identifiable, Sendable {
    enum Kind: String, Codable { case message, approval, sendBack, voice, markRead, askAnswer }
    enum State: String, Codable { case recording, waiting, sent, failed }
    let id: String
    let created: Date
    let kind: Kind
    var state: State = .waiting
    /// Why it failed, shown on the item. Empty until it fails.
    var note: String = ""
    var owner: ConversationOwner?
    var message: ConversationSend?
    var ticketID: String?
    var proposal: TicketProposal?
    var approval: ReviewApproval?
    var guidance: String?
    var protectedDecisionBody: Data?
    var protectedProposalCreatedAt: Double?
    var audioFile: String?
    var draftID: String?
    var decisionDraftUpdatedAt: Date?
    var completed: Date?
    var priorMessageIDs: [String]?
    var rejectedPhotoDraft: RejectedPhotoDraft?
    var ticketTitle: String?
    var proposalFieldLabel: String?
    /// Mark read: the conversation and the read point to move it to.
    var readConversationID: String?
    var readThroughSequence: Int?
    var askAnswer: AskAnswer?
    var displayText: String {
        message?.content.compactMap { $0.text ?? $0.fileName }.joined(separator: "\n") ?? (kind == .approval ? "Approve" : kind == .sendBack ? "Send back" : "Voice recording")
    }
}

/// An answer to an agent's ask. The question and the chosen labels are saved with it,
/// because the ask can be gone by the time the answer is sent.
struct AskAnswer: Codable, Sendable, Equatable {
    struct Question: Codable, Sendable, Equatable {
        let id: String
        let prompt: String
        /// The values the server receives.
        let answers: [String]
        /// What the person chose, as they read it.
        let labels: [String]
    }
    enum Request: Codable, Sendable, Equatable {
        case permission(askID: String, optionID: String)
        case userInput(requestID: String)
    }
    let conversationID: String
    let request: Request
    let questions: [Question]

    /// The ordinary message that carries an answer the ask no longer waits for.
    var lateMessageText: String {
        questions.map { "Answer to \"\($0.prompt)\": \($0.labels.joined(separator: ", "))" }.joined(separator: "\n")
    }
}

/// Only empty post-clear versions are stored. Photo bytes stay once in the message.
struct RejectedPhotoDraft: Codable, Sendable {
    let id: String
    let originalText: String?
    let clearedTextUpdatedAt: Date?
    let clearedAttachmentsUpdatedAt: Date?
}
