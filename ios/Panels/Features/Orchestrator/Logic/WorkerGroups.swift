import Foundation

/// The facts already supplied by the two existing worker endpoints.
protocol GroupedWorker: Identifiable where ID == String {
    var title: String { get }
    var stage: String { get }
    var ticketStatus: String { get }
    var isBacklogged: Bool { get }
    var awaitingApproval: Bool { get }
    var awaitingAnswer: Bool { get }
    var assigned: Bool { get }
    var awaitingAgentApproval: Bool { get }
    var agentState: String { get }
    var waitingToCloseout: Bool? { get }
    var activityAt: Double? { get }
    var conversationSignal: ConversationSignal? { get }
}
extension ManagerTicket: GroupedWorker {}
extension TicketSummary: GroupedWorker {}

enum WorkerGroup: String, CaseIterable, Identifiable {
    case approval, answer, yours, errored, agent, consequences, agentApproval, empty, blocked, backlogged, done
    var id: String { rawValue }
    var title: String {
        switch self {
        case .approval: "Needs your approval"
        case .answer: "Needs your answer"
        case .yours: "Yours"
        case .errored: "Errored"
        case .agent: "Agent"
        case .consequences: "Waiting on Consequences"
        case .agentApproval: "Awaiting an agent's approval"
        case .empty: "Empty"
        case .blocked: "Blocked"
        case .backlogged: "Backlogged"
        case .done: "Done"
        }
    }
    var initiallyExpanded: Bool { self != .backlogged && self != .done }

    static func classify(_ ticket: some GroupedWorker) -> Self {
        // Page sections are exclusive: history wins over a retained backlog flag.
        if ticket.stage == "done" { return .done }
        if ticket.isBacklogged { return .backlogged }
        if ticket.awaitingApproval { return .approval }
        if ticket.awaitingAnswer { return .answer }
        if ticket.assigned { return .yours }
        // Replies remain unread dots; the complete page has no Messages bucket.
        if ticket.agentState == "errored" { return .errored }
        if ticket.ticketStatus == "agent" || ticket.agentState == "working" { return .agent }
        if ticket.waitingToCloseout == true { return .consequences }
        if ticket.awaitingAgentApproval { return .agentApproval }
        if ticket.ticketStatus == "blocked" { return .blocked }
        return .empty
    }

    static func ordered<T: GroupedWorker>(_ rows: [T]) -> [T] {
        rows.sorted {
            if ($0.activityAt ?? 0) != ($1.activityAt ?? 0) { return ($0.activityAt ?? 0) > ($1.activityAt ?? 0) }
            let order = $0.title.localizedCaseInsensitiveCompare($1.title)
            return order == .orderedSame ? $0.id < $1.id : order == .orderedAscending
        }
    }
}
