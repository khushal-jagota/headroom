import Foundation

struct ReviewResponse: Codable, Sendable { let items: [ReviewItem]; let runningWorkerCount: Int }
struct ReviewItem: Codable, Identifiable, Sendable {
    let reviewItemType: String
    let ticketId: String
    let field: String
    let title: String
    let waitingSince: Double
    var id: String { "\(ticketId):\(field):\(waitingSince)" }
}
