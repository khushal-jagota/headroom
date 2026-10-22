import Foundation

struct ManagerDetail: Codable, Identifiable, Sendable {
    let id: String
    let title: String
    let project: String
    let projectId: String
    let goal: String
    let recap: String
    let conversationSignal: ConversationSignal?
    let settled: Bool?
    let manager: ManagerAgent
    let tickets: [ManagerTicket]
    let artifacts: [ManagedArtifact]
    let conversationHistory: [ConversationHistoryEntry]
}
struct ManagerAgent: Codable, Sendable { let agentKey: String; let conversationId: String?; let launchConfiguration: EmployeeConfiguration }
struct EmployeeConfiguration: Codable, Sendable { let employeeBackend: String; let employeeLaunchModel: String?; let employeeLaunchReasoningEffort: String? }
struct ConversationHistoryEntry: Codable, Sendable { let conversationId: String; let createdAt: Double }
struct ManagerTicket: Codable, Identifiable, Sendable {
    let id: String
    let title: String
    let recap: String
    let recapPreview: String
    let stage: String
    let ticketStatus: String
    let workerType: String
    let isBacklogged: Bool
    let blocked: Bool
    let hasPendingProposal: Bool
    let awaitingReply: Bool
    let awaitingAnswer: Bool
    let awaitingApproval: Bool
    let awaitingAgentApproval: Bool
    let agentState: String
    let assigned: Bool
    let waitingToCloseout: Bool?
    let activityAt: Double?
    let conversationSignal: ConversationSignal?
}
