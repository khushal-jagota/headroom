import Foundation

struct Principal: Codable, Hashable, Sendable { let kind: String; let id: String }
struct ConversationSignal: Codable, Sendable { let working: Bool; let messageForOwner: Bool }
struct PageFacts: Codable, Sendable {
    let matchCount: Int
    let returnCount: Int
    let limit: Int
    let offset: Int
    let complete: Bool
    let nextOffset: Int?
}
struct TicketsResponse: Codable, Sendable { let tickets: [TicketSummary]; let page: PageFacts }
struct ManagersResponse: Codable, Sendable { let managers: [ManagerSummary]; let page: PageFacts }
struct TicketSummary: Codable, Identifiable, Sendable {
    let id: String
    let title: String
    let workerType: String?
    let stage: String
    let ticketStatus: String
    let isBacklogged: Bool
    let blocked: Bool?
    let recapPreview: String
    let waitingToCloseout: Bool?
    let activityAt: Double?
    let projectId: String?
    let project: String?
    let manager: Principal?
    let managerId: String?
    let managerTitle: String?
    let awaitingReply: Bool
    let awaitingAnswer: Bool
    let awaitingApproval: Bool
    let awaitingAgentApproval: Bool
    let assigned: Bool
    var agentState: String
    var conversationSignal: ConversationSignal?
}
/// A detail read's worker row whose working and unread marks the navigation view keeps current.
protocol LiveMarkedWorker {
    var id: String { get }
    var agentState: String { get set }
    var conversationSignal: ConversationSignal? { get set }
}
extension TicketSummary: LiveMarkedWorker {}
struct ManagerSummary: Codable, Identifiable, Sendable {
    let id: String
    let title: String
    let projectId: String
    let project: String
    let settled: Bool?
    let createdAt: Double
    let updatedAt: Double
    let awaitingReply: Bool
    let awaitingAnswer: Bool
    let awaitingApproval: Bool
    let awaitingAgentApproval: Bool
    let agentState: String
    let conversationSignal: ConversationSignal?
}
