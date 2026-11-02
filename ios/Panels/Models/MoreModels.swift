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
    var installationConfirmed: Bool? = nil
    var workPaused: Bool? = nil
    var unknownDisplayUntil: String? = nil
}

// Match the web's expiry rules: stale progress becomes reconnecting, and a
// successful deployment only stays visible until its server-provided expiry.
enum DeploymentStatusPresentation: String {
    case deploying = "Deploying", reconnecting = "Reconnecting", problem = "Problem", deployed = "Deployed", unknown = "Unconfirmed"

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
        case "preparing", "restarting":
            return fresh ? .deploying : unconfirmed(status, connected: connected, now: now)
        case "back_up":
            let successExpiry = status.successDisplayUntil.flatMap { value in
                let format = ISO8601DateFormatter()
                format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                return format.date(from: value) ?? ISO8601DateFormatter().date(from: value)
            }
            let successFresh = successExpiry.map { now < $0 } ?? false
            return connected ? (fresh && successFresh ? .deployed : nil) : .reconnecting
        case "idle": return connected ? nil : .reconnecting
        default: return unconfirmed(status, connected: connected, now: now)
        }
    }
    private static func unconfirmed(_ status: DeploymentStatus, connected: Bool, now: Date) -> Self? {
        guard status.installationConfirmed == true else { return .unknown }
        guard connected else { return .reconnecting }
        if let value = status.unknownDisplayUntil,
           let expiry = ISO8601DateFormatter().date(from: value) ?? fractionalDate(value), now >= expiry { return nil }
        return .unknown
    }
    private static func fractionalDate(_ value: String) -> Date? {
        let format = ISO8601DateFormatter(); format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return format.date(from: value)
    }
    static func detail(_ status: DeploymentStatus?, state: Self?) -> String? {
        guard let status else { return nil }
        if state == .unknown {
            if status.installationConfirmed == true {
                return "The requested app is installed and available. Its final report is unconfirmed. No action is needed from you."
            }
            if status.state == "unknown", let detail = status.detail { return detail }
            return status.workPaused == true
                ? "Installation is unconfirmed. Work remains paused. Chief review is required."
                : "Installation is unconfirmed. Chief review is required."
        }
        return state == .deploying || state == .problem ? status.detail : nil
    }
}
