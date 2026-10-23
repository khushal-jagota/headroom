import Foundation

struct ReviewResponse: Codable, Sendable { let items: [ReviewItem]; let runningWorkerCount: Int }
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
