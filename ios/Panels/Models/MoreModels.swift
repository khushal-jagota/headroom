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
    var successDisplayUntil: String? = nil
}

// Match the web's expiry rules: stale progress becomes reconnecting, and a
// successful deployment only stays visible until its server-provided expiry.
enum DeploymentStatusPresentation: String {
    case deploying = "Deploying", reconnecting = "Reconnecting", problem = "Problem", deployed = "Deployed"

    static func resolve(_ status: DeploymentStatus?, connected: Bool, readFailed: Bool, now: Date) -> Self? {
        guard let status else { return .reconnecting }
        if status.state == "problem" || status.outcome == "failed" || status.outcome == "rolled_back" { return .problem }
        if readFailed { return .problem }
        let expiry = status.validUntil.flatMap { value in
            let format = ISO8601DateFormatter()
            format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            return format.date(from: value) ?? ISO8601DateFormatter().date(from: value)
        }
        let fresh = expiry.map { now < $0 } ?? false
        switch status.state {
        case "preparing", "restarting": return fresh ? .deploying : .reconnecting
        case "back_up":
            let successExpiry = status.successDisplayUntil.flatMap { value in
                let format = ISO8601DateFormatter()
                format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                return format.date(from: value) ?? ISO8601DateFormatter().date(from: value)
            }
            let successFresh = successExpiry.map { now < $0 } ?? false
            return connected ? (fresh && successFresh ? .deployed : nil) : .reconnecting
        case "idle": return connected ? nil : .reconnecting
        default: return .reconnecting
        }
    }
}
