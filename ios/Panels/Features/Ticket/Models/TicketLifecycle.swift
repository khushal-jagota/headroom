import Foundation

/// A ticket stores its own workflow; preset manifests only supply older fixtures.
struct TicketWorkerTypes: Codable, Sendable {
    let workerTypes: [TicketWorkerType]
}

struct TicketWorkerType: Codable, Sendable {
    struct Stage: Codable, Identifiable, Sendable {
        let id: String
        let label: String
        let gatingField: String?
        let isTerminal: Bool
        let ownershipMode: String?
    }
    struct Field: Codable, Identifiable, Sendable {
        let id: String
        let label: String
    }
    let workerType: String?
    let label: String
    let stages: [Stage]
    let fields: [Field]
    var ceilingRange: [String]? = nil
    var advance: [String: String]? = nil
}

struct TicketRecapDelivery: Codable, Sendable {
    let fate: String
    let refusalReason: String?
}
