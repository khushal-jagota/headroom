import Foundation

struct WorkspaceChiefSignalResponse: Decodable, Sendable {
    struct Chief: Decodable, Sendable { let conversationSignal: ConversationSignal? }
    let chiefOfStaff: Chief
}

extension APIClient {
    func workspaceChiefSignal() async throws -> ConversationSignal? {
        let response: WorkspaceChiefSignalResponse = try await get("/api/workers")
        return response.chiefOfStaff.conversationSignal
    }

    func workspaceSettledWindow(through lastOffset: Int = 0) async throws -> ManagersResponse {
        var page = try await managers(settled: true)
        var rows = page.managers
        while let offset = page.page.nextOffset, offset <= lastOffset {
            page = try await managers(offset: offset, settled: true)
            let existing = Set(rows.map(\.id))
            rows += page.managers.filter { !existing.contains($0.id) }
        }
        return ManagersResponse(managers: rows, page: page.page)
    }
}
