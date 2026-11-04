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
    var integrityError: TicketIntegrityIssue? = nil
    var locale: String? = nil
    let id: String
    let title: String
    let recap: String
    let recapPreview: String
    let stage: String
    let ticketStatus: String
    let workerType: String?
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
    enum CodingKeys: String, CodingKey { case integrityError, locale, id, title, recap, recapPreview, stage, ticketStatus, workerType, isBacklogged, blocked, hasPendingProposal, awaitingReply, awaitingAnswer, awaitingApproval, awaitingAgentApproval, agentState, assigned, waitingToCloseout, activityAt, conversationSignal }
}

extension ManagerTicket {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let issue = try c.decodeIfPresent(TicketIntegrityIssue.self, forKey: .integrityError)
        func required<T: Decodable>(_ type: T.Type, _ key: CodingKeys, broken: T) throws -> T {
            if issue != nil { return try c.decodeIfPresent(type, forKey: key) ?? broken }
            return try c.decode(type, forKey: key)
        }
        integrityError = issue
        locale = try c.decodeIfPresent(String.self, forKey: .locale)
        id = try required(String.self, .id, broken: "")
        title = try required(String.self, .title, broken: "")
        recap = try required(String.self, .recap, broken: issue?.reason ?? "")
        recapPreview = try required(String.self, .recapPreview, broken: issue?.reason ?? "")
        stage = try required(String.self, .stage, broken: "")
        ticketStatus = try required(String.self, .ticketStatus, broken: "")
        workerType = try c.decodeIfPresent(String.self, forKey: .workerType)
        isBacklogged = try required(Bool.self, .isBacklogged, broken: false)
        blocked = try required(Bool.self, .blocked, broken: false)
        hasPendingProposal = try required(Bool.self, .hasPendingProposal, broken: false)
        awaitingReply = try required(Bool.self, .awaitingReply, broken: false)
        awaitingAnswer = try required(Bool.self, .awaitingAnswer, broken: false)
        awaitingApproval = try required(Bool.self, .awaitingApproval, broken: false)
        awaitingAgentApproval = try required(Bool.self, .awaitingAgentApproval, broken: false)
        agentState = try required(String.self, .agentState, broken: "errored")
        assigned = try required(Bool.self, .assigned, broken: false)
        waitingToCloseout = try c.decodeIfPresent(Bool.self, forKey: .waitingToCloseout)
        activityAt = try c.decodeIfPresent(Double.self, forKey: .activityAt)
        conversationSignal = try c.decodeIfPresent(ConversationSignal.self, forKey: .conversationSignal)
    }
}
