import Foundation

struct ReviewResponse: Codable, Sendable { let items: [ReviewItem]; let runningWorkerCount: Int }
struct ReviewProposalItem: Codable, Sendable {
    let ticketId: String
    let field: String
    let title: String
    let waitingSince: Double
    var item: ReviewItem {
        ReviewItem(reviewItemType: "proposal", ticketId: ticketId, field: field, title: title, waitingSince: waitingSince)
    }
}

struct ReviewItem: Codable, Identifiable, Sendable {
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
    var proposal: ReviewProposalItem? = nil
    var messages: [ReviewItem] = []
    var isTicket: Bool { reviewItemType == "ticket" }
    var components: [ReviewItem] { isTicket ? (proposal.map { [$0.item] } ?? []) + messages : [self] }
    func keepingComponents(_ keep: (ReviewItem) -> Bool) -> ReviewItem? {
        guard isTicket else { return keep(self) ? self : nil }
        var result = self
        if let proposal, !keep(proposal.item) { result.proposal = nil }
        result.messages = messages.filter(keep)
        return result.proposal == nil && result.messages.isEmpty ? nil : result
    }
    var isMessage: Bool { reviewItemType == "message" }
    var id: String {
        if isTicket { return "ticket:\(ticketId):\(components.map(\.id).joined(separator: "|"))" }
        return isMessage ? "message:\(conversationId ?? ""):\(waitingSince)" : "\(ticketId):\(field):\(waitingSince)"
    }
    var owner: ConversationOwner? {
        switch ownerKind {
        case "chief": return .chief
        case "manager": return ownerId.map(ConversationOwner.manager)
        case "ticket": return ownerId.map(ConversationOwner.ticket)
        default: return nil
        }
    }
    enum CodingKeys: String, CodingKey { case reviewItemType, ticketId, field, title, waitingSince, conversationId, addressId, ownerKind, ownerId, recap, proposal, messages }
    init(reviewItemType: String, ticketId: String = "", field: String = "", title: String, waitingSince: Double, conversationId: String? = nil, ownerKind: String? = nil, ownerId: String? = nil) {
        self.reviewItemType = reviewItemType; self.ticketId = ticketId; self.field = field; self.title = title; self.waitingSince = waitingSince
        self.conversationId = conversationId; self.ownerKind = ownerKind; self.ownerId = ownerId
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        reviewItemType = try c.decode(String.self, forKey: .reviewItemType)
        title = try c.decode(String.self, forKey: .title)
        waitingSince = try c.decode(Double.self, forKey: .waitingSince)
        ticketId = try c.decodeIfPresent(String.self, forKey: .ticketId) ?? ""
        field = try c.decodeIfPresent(String.self, forKey: .field) ?? ""
        conversationId = try c.decodeIfPresent(String.self, forKey: .conversationId)
        addressId = try c.decodeIfPresent(String.self, forKey: .addressId)
        ownerKind = try c.decodeIfPresent(String.self, forKey: .ownerKind)
        ownerId = try c.decodeIfPresent(String.self, forKey: .ownerId)
        recap = try c.decodeIfPresent(String.self, forKey: .recap)
        proposal = try c.decodeIfPresent(ReviewProposalItem.self, forKey: .proposal)
        messages = try c.decodeIfPresent([ReviewItem].self, forKey: .messages) ?? []
    }
}
