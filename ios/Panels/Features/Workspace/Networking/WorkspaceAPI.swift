import Foundation

extension APIClient {
    func workspaceMachines() async throws -> WorkspaceMachinesResponse {
        try await get("/api/machines")
    }

    func workspaceMachineHealth(machineId: String) async throws -> WorkspaceMachineHealth {
        try await get("/api/executor/health", query: [URLQueryItem(name: "machine_id", value: machineId)])
    }


}

enum WorkspaceAction { case recap, backlog, resume, settle, reopen }

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
        case .settle, .reopen:
            guard kind == "manager" else { throw APIError(status: 0, message: "Only orchestrators can be settled.") }
            path = "/api/managers/\(id)/settled"
            method = "PATCH"; body = ["settled": action == .settle]
        }
        var request = try self.request(try url(path))
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
        let detail = try await self.ticket(ticket.id)
        guard let label = detail.workflow?.stages.first(where: { $0.id == ticket.stage })?.label else { return "" }
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


enum WorkspaceResources {
    static let machines = ResourceRegistration<WorkspaceMachinesResponse>(
        key: "/api/machines", versioned: false, signalRefreshInterval: ResourceLimits.fallbackSeconds)

    static func machineHealth(_ id: String) -> ResourceRegistration<WorkspaceMachineHealth> {
        var parts = URLComponents()
        parts.path = "/api/executor/health"
        parts.queryItems = [URLQueryItem(name: "machine_id", value: id)]
        return ResourceRegistration(key: parts.string!, versioned: false, signalRefreshInterval: ResourceLimits.fallbackSeconds)
    }
}
