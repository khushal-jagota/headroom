import Foundation

/// The Review queue, derived from the navigation view's review window.
struct ReviewResponse: Sendable {
    let cards: [ReviewCard]
    let runningWorkerCount: Int
    /// Cards beyond the loaded review window still count toward the badge.
    var unloadedCount = 0
    var items: [ReviewItem] { cards.flatMap(\.parts) }
}

/// One queue entry, shown as one page, as desktop shows one card. A Ticket card holds
/// its proposal first, then each unread conversation; other owners' messages stand alone.
struct ReviewCard: Identifiable, Sendable {
    let id: String
    /// Empty for a message that is not tied to a Ticket.
    var ticketId = ""
    let parts: [ReviewItem]
    var title: String { parts.first?.title ?? "" }
}

/// One native card: a proposal, or one conversation's unread run. A Ticket card's
/// proposal and messages keep their own identities and timestamps.
struct ReviewItem: Identifiable, Sendable {
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
    var isMessage: Bool { reviewItemType == "message" }
    var id: String { isMessage ? "message:\(conversationId ?? ""):\(waitingSince)" : "\(ticketId):\(field):\(waitingSince)" + (proposalRevision.map { ":revision:\($0)" } ?? "") }
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
