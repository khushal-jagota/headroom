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
        return ManagersResponse(managers: rows.sorted { $0.updatedAt == $1.updatedAt ? $0.id < $1.id : $0.updatedAt > $1.updatedAt }, page: page.page)
    }
}

enum WorkspaceAction { case recap, backlog, resume, settle }

extension APIClient {
    func workspaceAction(_ action: WorkspaceAction, route: AppRoute) async throws {
        let kind: String
        let id: String
        switch route {
        case .chief: kind = "chief"; id = "chief"
        case .orchestrator(let value): kind = "manager"; id = value
        case .ticket(let value): kind = "ticket"; id = value
        default: throw APIError(status: 0, message: "This item has no workspace actions.")
        }
        let path: String
        var method = "POST"
        var body: [String: Any] = [:]
        switch action {
        case .recap:
            path = "/api/recap-requests"
            body = ["target": ["kind": kind, "id": id]]
        case .backlog, .resume:
            guard kind == "ticket" else { throw APIError(status: 0, message: "Only tickets can be backlogged.") }
            path = "/api/tickets/\(id)/\(action == .backlog ? "backlog" : "resume")"
        case .settle:
            guard kind == "manager" else { throw APIError(status: 0, message: "Only orchestrators can be settled.") }
            path = "/api/managers/\(id)/settled"
            method = "PATCH"; body = ["settled": true]
        }
        var request = URLRequest(url: try url(path))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        request.timeoutInterval = 25
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        if action == .recap {
            struct Fate: Decodable { let fate: String; let reason: String? }
            let fate = try Self.decoder().decode(Fate.self, from: data)
            guard ["started", "queued", "steered"].contains(fate.fate) else {
                throw APIError(status: 0, message: fate.reason ?? "The recap request was \(fate.fate).")
            }
        }
    }

    func workspaceStageSentence(_ ticket: TicketSummary) async throws -> String {
        struct Manifest: Decodable, Sendable {
            struct Worker: Decodable, Sendable {
                struct Stage: Decodable, Sendable { let id: String; let label: String }
                let stages: [Stage]
            }
            let workerTypes: [Worker]
        }
        let manifest: Manifest = try await get("/api/worker-types/\(ticket.workerType)/manifest")
        guard let label = manifest.workerTypes.first?.stages.first(where: { $0.id == ticket.stage })?.label else { return "" }
        if ticket.ticketStatus == "blocked" { return "Blocked at \(label)." }
        if ticket.awaitingApproval { return "Awaiting your approval at \(label)." }
        if ticket.awaitingAnswer { return "Awaiting your answer at \(label)." }
        if ticket.assigned { return "Awaiting you at \(label)." }
        if ticket.agentState == "errored" { return "The last turn failed at \(label)." }
        if ticket.awaitingAgentApproval { return "Awaiting an agent's approval at \(label)." }
        if ticket.conversationSignal?.working == true { return "Currently doing \(label)." }
        return "At \(label)."
    }
}
