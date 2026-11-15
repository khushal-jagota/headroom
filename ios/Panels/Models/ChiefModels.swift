import Foundation

struct ChiefDetail: Codable, Sendable {
    let employeeId: String
    let label: String
    let recap: String
    let skill: ManagedSkill
    let launchDefaults: EmployeeConfiguration
}
struct ManagedSkill: Codable, Sendable { let name: String; let description: String; let markdownBody: String }
struct ConversationReference: Codable, Sendable { let conversationId: String? }
