import Foundation

struct TicketRecapDelivery: Decodable, Sendable {
    let fate: String
    let refusalReason: String?
}
