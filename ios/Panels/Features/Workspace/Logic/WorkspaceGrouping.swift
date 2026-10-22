import Foundation

// Mirrors workspaceRail.ts and workItemPresentation.ts. Unread is an independent mark.
enum WorkspaceSection: String, CaseIterable, Identifiable {
    case approval, answer, yours, errored, agent, consequences, agentApproval, other
    var id: Self { self }
    var title: String {
        switch self {
        case .approval: "Needs your approval"
        case .answer: "Needs your answer"
        case .yours: "Yours"
        case .errored: "Errored"
        case .agent: "Agent"
        case .consequences: "Waiting on Consequences"
        case .agentApproval: "Awaiting an agent’s approval"
        case .other: "Other"
        }
    }
    var needsAttention: Bool { self == .approval || self == .answer || self == .errored }
}

struct WorkspaceTicketGroup: Identifiable {
    let section: WorkspaceSection
    let tickets: [TicketSummary]
    var id: WorkspaceSection { section }
}

enum WorkspaceGrouping {
    static func section(for ticket: TicketSummary) -> WorkspaceSection? {
        guard !ticket.isBacklogged, ticket.blocked != true, ticket.ticketStatus != "blocked", ticket.stage != "done" else { return nil }
        if ticket.awaitingApproval { return .approval }
        if ticket.awaitingAnswer { return .answer }
        if ticket.assigned { return .yours }
        if ticket.agentState == "errored" { return .errored }
        if ticket.ticketStatus == "agent" || ticket.agentState == "working" { return .agent }
        if ticket.waitingToCloseout == true { return .consequences }
        if ticket.awaitingAgentApproval { return .agentApproval }
        return .other
    }

    static func groups(_ tickets: [TicketSummary], previous: [WorkspaceTicketGroup] = []) -> [WorkspaceTicketGroup] {
        WorkspaceSection.allCases.compactMap { section in
            let fresh = tickets.filter { self.section(for: $0) == section }
            guard !fresh.isEmpty else { return nil }
            let byID = Dictionary(fresh.map { ($0.id, $0) }, uniquingKeysWith: { _, latest in latest })
            let survivors = (previous.first { $0.section == section }?.tickets ?? []).compactMap { byID[$0.id] }
            let survivorIDs = Set(survivors.map(\.id))
            let arrivals = fresh.filter { !survivorIDs.contains($0.id) }.sorted {
                let titleOrder = $0.title.localizedCaseInsensitiveCompare($1.title)
                return titleOrder == .orderedSame ? $0.id < $1.id : titleOrder == .orderedAscending
            }
            return WorkspaceTicketGroup(section: section, tickets: survivors + arrivals)
        }
    }
}
