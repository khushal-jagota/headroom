import Foundation

extension APIClient {
    func ticketWorkerType(_ workerType: String) async throws -> TicketWorkerType {
        let response: TicketWorkerTypes = try await get("/api/worker-types/\(workerType)/manifest")
        guard let manifest = response.workerTypes.first(where: { $0.workerType == workerType }) else {
            throw APIError(status: 0, message: "This worker type is unavailable.")
        }
        return manifest
    }

    func setTicketBacklogged(_ ticket: TicketDetail, backlogged: Bool) async throws {
        struct Body: Encodable { let expected_record_sha256: String }
        _ = try await ticketWrite("/api/tickets/\(ticket.id)/\(backlogged ? "backlog" : "resume")",
                                  method: "POST", body: Body(expected_record_sha256: ticket.recordSha256))
    }

    /// A reviewer-only change deliberately omits ceiling, as on the web.
    func setTicketApprovalLimit(_ id: String, ceiling: String?, holder: Principal) async throws {
        struct Body: Encodable { let ceiling: String?; let ceiling_holder: Principal }
        _ = try await ticketWrite("/api/tickets/\(id)", method: "PATCH",
                                  body: Body(ceiling: ceiling, ceiling_holder: holder))
    }

    func requestTicketRecap(_ id: String) async throws -> TicketRecapDelivery {
        struct Body: Encodable { let target: Principal }
        let data = try await ticketWrite("/api/recap-requests", method: "POST",
                                         body: Body(target: Principal(kind: "ticket", id: id)))
        return try Self.decoder().decode(TicketRecapDelivery.self, from: data)
    }

    private func ticketWrite<Body: Encodable>(_ path: String, method: String, body: Body) async throws -> Data {
        var request = URLRequest(url: try url(path))
        request.httpMethod = method
        request.httpBody = try JSONEncoder().encode(body)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 25
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return data
    }
}
