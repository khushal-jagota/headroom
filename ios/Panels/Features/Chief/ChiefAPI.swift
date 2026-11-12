import Foundation

struct ChiefRoster: Decodable, Sendable {
    let chiefOfStaff: ChiefRosterEntry
}
struct ChiefRosterEntry: Decodable, Sendable {
    let label: String
    let recap: String
    let conversationSignal: ConversationSignal?
}

extension APIClient {
    func chiefRoster() async throws -> ChiefRoster { try await get("/api/workers") }

    /// Keep the loaded Chief history window across changes; never substitute the
    /// app-wide active window, which may omit this Chief's workers entirely.
    func holderTickets(_ holder: GoalHolder, filter: TicketFilter, through lastOffset: Int = 0) async throws -> TicketsResponse {
        let principal = Principal(kind: holder.kind.rawValue, id: holder.id ?? "chief")
        var page = try await tickets(manager: principal, filter: filter)
        var rows = page.tickets
        while let offset = page.page.nextOffset, offset <= lastOffset {
            page = try await tickets(offset: offset, manager: principal, filter: filter)
            let ids = Set(rows.map(\.id))
            rows += page.tickets.filter { !ids.contains($0.id) }
        }
        return TicketsResponse(tickets: rows, page: page.page)
    }
}

enum ChiefResources {
    static let roster = ResourceRegistration<ChiefRoster>(key: "/api/workers", versioned: false)
    static let settings = ResourceRegistration<ChiefDetail>(key: "/api/workers/chief-of-staff/settings")
    static let deployment = ResourceRegistration<DeploymentStatus>(key: "/api/deployment-status")
    static let executorHealth = ResourceRegistration<ExecutorHealth>(key: "/api/executor/health", versioned: false)
}

enum HolderTicketResources {
    static func page(_ holder: GoalHolder, filter: TicketFilter, offset: Int) -> ResourceRegistration<TicketsResponse> {
        var parts = URLComponents()
        parts.path = "/api/tickets"
        var query = [URLQueryItem(name: "detail", value: "summary"), .init(name: "limit", value: "30"), .init(name: "offset", value: String(offset))]
        switch filter {
        case .active: query.append(.init(name: "is_backlogged", value: "false"))
        case .backlogged: query.append(.init(name: "is_backlogged", value: "true"))
        case .done: query += [.init(name: "include_terminal", value: "true"), .init(name: "stage", value: "done")]
        }
        query.append(.init(name: "manager_kind", value: holder.kind.rawValue))
        if let id = holder.id { query.append(.init(name: "manager_id", value: id)) }
        parts.queryItems = query
        return ResourceRegistration(key: parts.string!, versioned: false)
    }
}
