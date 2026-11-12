import Foundation

struct PendingWork: Codable, Identifiable, Sendable {
    enum Kind: String, Codable { case message, approval, sendBack, voice }
    enum State: String, Codable { case recording, waiting, sending, uncertain, serverQueued, sent, failed, changed }
    let id: String
    let created: Date
    let kind: Kind
    var state: State = .waiting
    var note: String = "Waiting for connection"
    var owner: ConversationOwner?
    var message: ConversationSend?
    var ticketID: String?
    var proposal: TicketProposal?
    var approval: ReviewApproval?
    var guidance: String?
    var audioFile: String?
    var draftID: String?
    var completed: Date?
    var priorMessageIDs: [String]?
    var ticketTitle: String?
    var proposalFieldLabel: String?
    var displayText: String {
        message?.content.compactMap { $0.text ?? $0.fileName }.joined(separator: "\n") ?? (kind == .approval ? "Approve" : kind == .sendBack ? "Send back" : "Voice recording")
    }
}

