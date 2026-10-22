import Foundation

struct IdeasResponse: Codable, Sendable { let ideas: [Idea] }
struct Idea: Codable, Identifiable, Sendable {
    let id: String
    let title: String
    let body: String
    let projectId: String?
    let project: String?
    let createdAt: Double
    let updatedAt: Double
}
struct DeploymentStatus: Codable, Sendable {
    let state: String
    let deployedSha: String?
    let targetSha: String?
    let outcome: String?
    let detail: String?
    let validUntil: String?
}
