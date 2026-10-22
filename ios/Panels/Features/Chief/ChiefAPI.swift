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
    func chiefWorkers(filter: TicketFilter, through lastOffset: Int = 0) async throws -> TicketsResponse {
        let principal = Principal(kind: "chief", id: "chief")
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
