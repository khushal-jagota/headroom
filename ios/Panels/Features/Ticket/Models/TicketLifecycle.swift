import Foundation

/// The existing worker-type manifest is the authority for vocabulary and ordering.
struct TicketWorkerTypes: Decodable, Sendable {
    let workerTypes: [TicketWorkerType]
}

struct TicketWorkerType: Decodable, Sendable {
    struct Stage: Decodable, Identifiable, Sendable {
        let id: String
        let label: String
        let gatingField: String?
        let isTerminal: Bool
        let ownershipMode: String?
    }
    struct Field: Decodable, Identifiable, Sendable {
        let id: String
        let label: String
    }
    let workerType: String
    let label: String
    let stages: [Stage]
    let fields: [Field]
    var advance: [String: String]? = nil
}

struct TicketRecapDelivery: Decodable, Sendable {
    let fate: String
    let refusalReason: String?
}
