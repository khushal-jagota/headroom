import Foundation

/// The Review queue, derived from the navigation view's review window.
struct ReviewResponse: Sendable {
    let items: [ReviewItem]
    let runningWorkerCount: Int
    /// Cards beyond the loaded review window still count toward the badge.
    var unloadedCount = 0
}

/// One native card: a proposal, or one conversation's unread run. A Ticket card's
/// proposal and messages keep their own identities and timestamps.
struct ReviewItem: Identifiable, Sendable {
    let reviewItemType: String
    var ticketId: String = ""
    var field: String = ""
    let title: String
    let waitingSince: Double
    var conversationId: String? = nil
    var addressId: String? = nil
    var ownerKind: String? = nil
    var ownerId: String? = nil
    var recap: String? = nil
    /// The read boundary the view reported; the unread run starts after it.
    var ownerReadThroughSequence = 0
    /// False when the owner has moved on to another conversation: read only.
    var addressCurrent = true
    /// The conversation's latest event in the view; a read-ahead older than it is stale.
    var latestSequence = 0
    var isMessage: Bool { reviewItemType == "message" }
    var id: String { isMessage ? "message:\(conversationId ?? ""):\(waitingSince)" : "\(ticketId):\(field):\(waitingSince)" }
    var owner: ConversationOwner? {
        switch ownerKind {
        case "chief": return .chief
        case "manager": return ownerId.map(ConversationOwner.manager)
        case "ticket": return ownerId.map(ConversationOwner.ticket)
        case "goal": return ownerId.map(ConversationOwner.goal)
        default: return nil
        }
    }
    init(reviewItemType: String, ticketId: String = "", field: String = "", title: String, waitingSince: Double, conversationId: String? = nil, ownerKind: String? = nil, ownerId: String? = nil, ownerReadThroughSequence: Int = 0, addressCurrent: Bool = true) {
        self.reviewItemType = reviewItemType; self.ticketId = ticketId; self.field = field; self.title = title; self.waitingSince = waitingSince
        self.conversationId = conversationId; self.ownerKind = ownerKind; self.ownerId = ownerId
        self.ownerReadThroughSequence = ownerReadThroughSequence; self.addressCurrent = addressCurrent
    }
}
