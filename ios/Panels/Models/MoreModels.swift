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

// conversation/deploy_interruptions/contracts.py is the wire authority.
struct DeploymentInterruptionSummary: Decodable, Sendable {
    let accounts: [Account]
    struct Account: Decodable, Identifiable, Sendable {
        let deploymentId: String
        let captureState: String
        let detail: String?
        let entries: [DeploymentInterruption]
        var id: String { deploymentId }
    }
}
struct DeploymentInterruption: Decodable, Identifiable, Sendable {
    let interruptionId: String
    let conversationId: String?
    let displayName: String
    let ownerKind: String
    let ownerId: String?
    let recoveryState: String
    let detail: String
    let canRestart: Bool
    var id: String { interruptionId }
    var recoveryLabel: String {
        switch recoveryState {
        case "awaiting_action": "Awaiting action"
        case "unable_to_resume": "Unable to resume"
        default: recoveryState.capitalized
        }
    }
}
struct DeploymentRestartResult: Decodable, Sendable {
    let started: Bool
    let deliveryFate: String?
    let notStartedBecause: String?
    var message: String {
        if started { return "Restart requested. The account confirms when the agent resumes." }
        if deliveryFate == "queued" { return "Restart queued. The agent has not resumed yet." }
        return notStartedBecause ?? "The restart did not confirm resumption. Check the conversation."
    }
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
