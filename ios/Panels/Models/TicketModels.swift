import Foundation

struct TicketDetail: Codable, Identifiable, Sendable {
    let id: String
    let title: String
    let workerType: String
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
}
struct BlockerSummary: Codable, Sendable { let blockedBy: [TicketBlocker] }
struct TicketBlocker: Codable, Sendable { let ticketId: String; let title: String; let stage: String; let href: String }
struct FieldSummary: Codable, Sendable { let byteCount: Int; let revision: String }
struct FieldApproval: Codable, Sendable { let method: String; let principal: Principal?; let displayLabel: String }
struct TicketProposal: Codable, Sendable { let field: String; let body: String; let proposedBy: String; let createdAt: Double; let revision: Int }
struct TicketFieldBody: Codable, Sendable { let ticketId: String; let field: String; let value: String; let revision: String }
