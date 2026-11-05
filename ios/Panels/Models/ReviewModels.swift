import Foundation

struct ReviewResponse: Codable, Sendable {
    let items: [ReviewItem]
    let runningWorkerCount: Int
    init(items: [ReviewItem], runningWorkerCount: Int) {
        self.items = items; self.runningWorkerCount = runningWorkerCount
    }
    enum CodingKeys: String, CodingKey { case items, runningWorkerCount }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        runningWorkerCount = try c.decode(Int.self, forKey: .runningWorkerCount)
        // The server groups a ticket's proposal and messages. Keep the existing
        // native cards, but decode every child with its own timestamp and identity.
        struct Card: Decodable {
            let reviewItemType: String
            let proposal: ReviewItem?
            let messages: [ReviewItem]?
            let leaf: ReviewItem?
            enum CodingKeys: String, CodingKey { case reviewItemType, proposal, messages }
            init(from decoder: Decoder) throws {
                let c = try decoder.container(keyedBy: CodingKeys.self)
                reviewItemType = try c.decode(String.self, forKey: .reviewItemType)
                if reviewItemType == "ticket" {
                    proposal = try c.decodeIfPresent(ReviewItem.self, forKey: .proposal)
                    messages = try c.decode([ReviewItem].self, forKey: .messages)
                    leaf = nil
                } else {
                    proposal = nil; messages = nil; leaf = try ReviewItem(from: decoder)
                }
            }
        }
        items = try c.decode([Card].self, forKey: .items).flatMap { card in
            if let leaf = card.leaf { return [leaf] }
            return [card.proposal].compactMap { $0 } + (card.messages ?? [])
        }
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
    enum CodingKeys: String, CodingKey { case reviewItemType, ticketId, field, title, waitingSince, conversationId, addressId, ownerKind, ownerId, recap }
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
    }
}
