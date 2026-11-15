import Foundation

struct TicketDetail: Codable, Identifiable, Sendable {
    var locale: String? = nil
    let id: String
    let title: String
    let workerType: String?
    var workflow: TicketWorkerType? = nil
    let employeeBackend: String
    let employeeLaunchModel: String?
    let employeeLaunchReasoningEffort: String?
    let employeeConfigurationEditable: Bool
    let stage: String
    let ticketStatus: String
    let isBacklogged: Bool
    let blocked: Bool
    let blockerSummary: BlockerSummary?
    let ceiling: String
    let ceilingHolder: Principal
    let manager: Principal
    let deadline: String?
    let projectId: String?
    let project: String?
    let managerId: String?
    let managerTitle: String?
    let conversationId: String?
    let recap: String
    let recordSha256: String
    let fieldValues: [String: String]
    let fieldSummaries: [String: FieldSummary]?
    let fieldApprovals: [String: FieldApproval]
    let pendingProposal: TicketProposal?
    let agentState: String?
    let conversationSignal: ConversationSignal?
    let assigned: Bool?
    let awaitingApproval: Bool?
}
struct BlockerSummary: Codable, Sendable { let blockedBy: [TicketBlocker] }
struct TicketBlocker: Codable, Sendable { let ticketId: String; let title: String; let stage: String; let href: String }
struct FieldSummary: Codable, Sendable { let byteCount: Int; let revision: String }
struct FieldApproval: Codable, Sendable { let method: String; let principal: Principal?; let displayLabel: String }
struct TicketProposal: Codable, Sendable { let field: String; let body: String; let proposedBy: String; let createdAt: Double; let revision: Int }
struct TicketFieldBody: Codable, Sendable { let ticketId: String; let field: String; let value: String; let revision: String }

struct TicketBackendCatalog: Decodable, Sendable {
    let backends: [TicketBackend]
}
struct TicketBackend: Decodable, Identifiable, Sendable {
    struct Model: Decodable, Identifiable, Sendable {
        let modelId: String
        let displayName: String?
        let enabled: Bool
        let reasoningEffortOptions: [String]?
        var id: String { modelId }
    }
    let backendKey: String
    let installed: Bool
    let availableModels: [Model]
    let reasoningEffortOptions: [String]
    let defaultModelId: String?
    let defaultReasoningEffort: String?
    var id: String { backendKey }
}


struct TicketIntegrityIssue: Codable, Sendable { let reason: String }
// A broken Ticket has only diagnostic fields, never valid lifecycle controls.
struct TicketRead: Decodable, Sendable {
    let detail: TicketDetail
    enum CodingKeys: String, CodingKey { case integrityError }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let issue = try c.decodeIfPresent(TicketIntegrityIssue.self, forKey: .integrityError) {
            throw APIError(status: 0, message: "Broken Ticket: " + issue.reason)
        }
        detail = try TicketDetail(from: decoder)
    }
}
