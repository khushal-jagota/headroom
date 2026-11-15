import Foundation

enum FeedbackPageDestination: Hashable {
    case workspace, review, route(AppRoute)

    static func parse(_ address: String?) -> Self? {
        guard let address, address.hasPrefix("#/"),
              !address.contains(where: { $0.isWhitespace }) else { return nil }
        let path = address.dropFirst().split(separator: "?", maxSplits: 1)[0]
        let parts = path.split(separator: "/").map { String($0).removingPercentEncoding ?? String($0) }
        switch parts {
        case ["workspace"]: return .workspace
        case ["review"]: return .review
        case ["workspace", "chief-of-staff"]: return .route(.chief)
        case let p where p.count == 3 && p[0] == "workspace" && ["manager", "item"].contains(p[1]):
            return .route(.orchestrator(p[2]))
        case let p where p.count == 4 && p[0] == "workspace" && ["manager", "item"].contains(p[1]):
            return .route(.ticket(p[3]))
        case let p where p.count == 2 && p[0] == "workspace" && p[1] != "utility":
            return .route(.ticket(p[1]))
        default:
            let utility: String?
            if parts.count == 1 { utility = parts.first }
            else if parts.count == 3 && parts[0] == "workspace" && parts[1] == "utility" { utility = parts[2] }
            else { utility = nil }
            switch utility {
            case "ideas": return .route(.ideas)
            case "feedback": return .route(.feedback)
            case "backends", "usage": return .route(.usage)
            case "vps": return .route(.vps)
            case "settings": return .route(.settings)
            default: return nil
            }
        }
    }
}

extension FeedbackTicket {
    var stateLabel: String {
        if stage == "done" { return "Done" }
        if ticketStatus == "blocked" { return "Blocked" }
        if awaitingApproval == true { return "Needs your approval" }
        if awaitingAnswer == true { return "Needs your answer" }
        if assigned == true { return "Yours" }
        if awaitingReply == true { return "Messages" }
        if agentState == "errored" { return "Errored" }
        if ticketStatus == "agent" || agentState == "working" { return "Agent" }
        if ticketStatus == "errored" { return "Errored" }
        if ticketStatus == "empty" && stage == "needs_consequences" { return "Waiting on Consequences" }
        let label = stage.replacingOccurrences(of: "_", with: " ")
        return label.prefix(1).uppercased() + label.dropFirst()
    }
}
