import Foundation

/// The Review queue, derived from the navigation view's review window.
struct ReviewResponse: Sendable {
    /// One card per purpose, in review-window order.
    let items: [ReviewItem]
    let runningWorkerCount: Int
    /// Entries beyond the loaded review window still count toward the badge and the summary.
    var unloadedCount = 0
    /// Every entry in the queue, loaded or not.
    var queueTotal = 0
    /// The server's count of skipped entries in the whole queue, when the view carries it.
    var serverSkippedCount: Int? = nil
    /// The window read the queue from the top, and it holds every entry.
    var holdsWholeQueue = false
    /// The window was read from the end of the queue.
    var fromTail = false
    /// The server installation the view came from; the phone's skips are kept under it.
    var installationId: String? = nil
    /// The view came from the server in this app run, not from the copy saved on the phone.
    var current = false
    /// The skipped keys of the request the server answered with this view; nil for a saved copy.
    var sentSkippedKeys: [RecordKey]? = nil
}

/// One native card with one purpose: a proposal, one conversation's unread run, or an ask.
/// The cards of one window entry keep their own identities and timestamps.
struct ReviewItem: Identifiable, Equatable, Sendable {
    let reviewItemType: String
    var ticketId: String = ""
    var field: String = ""
    let title: String
    let waitingSince: Double
    let proposalRevision: Int?
    var conversationId: String? = nil
    var addressId: String? = nil
    var ownerKind: String? = nil
    var ownerId: String? = nil
    var recap: String? = nil
    /// The read boundary the view reported; the unread run starts after it.
    var ownerReadThroughSequence = 0
    /// False when the owner has moved on to another conversation: read only.
    var addressCurrent = true
    /// The newest message to the owner when the view was built: the Mark read bound for a
    /// message card that never read its run.
    var latestOwnerMessageSequence: Int? = nil
    /// The ask the view carried, for an ask card that cannot read its conversation.
    var pendingAsk: ReviewAskState? = nil
    var isProposal: Bool { reviewItemType == "proposal" }
    var isMessage: Bool { reviewItemType == "message" }
    var isAsk: Bool { reviewItemType == "ask" }
    /// An ask whose record holds no time waits last, under this time. Every such ask in a
    /// conversation shares one identity, so a new one cannot be told from an answered one.
    static let unknownAskTime: Double = 9_007_199_254_740_991
    var id: String {
        switch reviewItemType {
        case "message": "message:\(conversationId ?? ""):\(waitingSince)"
        case "ask": "ask:\(conversationId ?? ""):\(waitingSince)"
        default: "\(ticketId):\(field):\(waitingSince)" + (proposalRevision.map { ":revision:\($0)" } ?? "")
        }
    }
    /// The Ticket this card belongs to, if any; it names the card's Open ticket link.
    var owningTicketID: String? {
        if isProposal { return ticketId }
        return ownerKind == "ticket" ? ownerId : nil
    }
    var owner: ConversationOwner? {
        switch ownerKind {
        case "chief": return .chief
        case "manager": return ownerId.map(ConversationOwner.manager)
        case "ticket": return ownerId.map(ConversationOwner.ticket)
        case "goal": return ownerId.map(ConversationOwner.goal)
        default: return nil
        }
    }
    init(reviewItemType: String, ticketId: String = "", field: String = "", title: String, waitingSince: Double, proposalRevision: Int? = nil, conversationId: String? = nil, ownerKind: String? = nil, ownerId: String? = nil, ownerReadThroughSequence: Int = 0, addressCurrent: Bool = true) {
        self.reviewItemType = reviewItemType; self.ticketId = ticketId; self.field = field; self.title = title; self.waitingSince = waitingSince
        self.proposalRevision = proposalRevision.flatMap { $0 > 0 ? $0 : nil }
        self.conversationId = conversationId; self.ownerKind = ownerKind; self.ownerId = ownerId
        self.ownerReadThroughSequence = ownerReadThroughSequence; self.addressCurrent = addressCurrent
    }
}

/// The ask an agent's running turn waits on, read from its conversation as the web's
/// ReviewAskCard reads it (`GET /api/conversation/conversations/{id}`).
struct ReviewAskState: Decodable, Equatable, Sendable {
    struct PermissionOption: Codable, Equatable, Sendable { let optionId: String; let label: String; let optionKind: String }
    struct Permission: Codable, Equatable, Sendable {
        let askId: String
        let title: String
        let detail: String?
        let options: [PermissionOption]
    }
    struct Option: Codable, Equatable, Sendable { let label: String; let description: String? }
    struct Question: Codable, Equatable, Sendable {
        let questionId: String
        let header: String?
        let question: String
        let options: [Option]
        let multiSelect: Bool
        let allowOther: Bool
        /// The owner may answer in words.
        var takesWords: Bool { allowOther || options.isEmpty }
    }
    struct UserInput: Codable, Equatable, Sendable { let requestId: String; let questions: [Question] }
    let isRunning: Bool
    let pendingPermissionAsk: Permission?
    let pendingUserInput: UserInput?
    var waiting: Bool { pendingPermissionAsk != nil || pendingUserInput != nil }
    /// Which ask this is: its permission ask ID or its question's request ID.
    var askIdentity: String? { pendingPermissionAsk?.askId ?? pendingUserInput?.requestId }
}
