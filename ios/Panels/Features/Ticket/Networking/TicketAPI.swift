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

    func renameTicket(_ id: String, title: String) async throws {
        struct Body: Encodable { let title: String }
        _ = try await ticketWrite("/api/tickets/\(id)", method: "PATCH", body: Body(title: title))
    }

    func editTicketRecap(_ id: String, recap: String) async throws {
        struct Body: Encodable { let recap: String }
        _ = try await ticketWrite("/api/tickets/\(id)", method: "PATCH", body: Body(recap: recap))
    }

    func editTicketField(_ id: String, field: String, body: String) async throws {
        struct Body: Encodable { let field_values: [String: String] }
        _ = try await ticketWrite("/api/tickets/\(id)", method: "PATCH", body: Body(field_values: [field: body]))
    }

    func completeTicketField(_ id: String, field: String, body: String) async throws {
        struct Body: Encodable { let body: String }
        _ = try await ticketWrite("/api/tickets/\(id)/complete/\(field)", method: "POST", body: Body(body: body))
    }

    func acceptTicketProposal(_ ticket: TicketDetail, proposal: TicketProposal, ceiling: String, holder: Principal, editedBody: String) async throws {
        struct Body: Encodable {
            let next_ceiling: String
            let next_holder: Principal
            let expected_proposal_revision: Int
            let edited_body: String?
        }
        _ = try await ticketWrite("/api/tickets/\(ticket.id)/accept/\(proposal.field)", method: "POST",
                                  body: Body(next_ceiling: ceiling, next_holder: holder,
                                             expected_proposal_revision: proposal.revision,
                                             edited_body: editedBody == proposal.body ? nil : editedBody))
    }

    func returnTicketProposal(_ id: String, message: String, expectedProposalRevision: Int) async throws {
        struct Body: Encodable { let message: String; let expected_proposal_revision: Int }
        _ = try await ticketWrite("/api/tickets/\(id)/reject", method: "POST", body: Body(message: message, expected_proposal_revision: expectedProposalRevision))
    }

    func removeTicketBlocker(_ id: String, blockerID: String) async throws {
        struct Body: Encodable {}
        _ = try await ticketWrite("/api/collections/blockers/\(id)/\(blockerID)", method: "DELETE", body: Body())
    }

    func setTicketWorkerConfiguration(_ id: String, backend: String, model: String?, effort: String?) async throws {
        // Explicit null resets a previous override to the backend default.
        var body: [String: String?] = ["employee_backend": backend]
        body["employee_launch_model"] = .some(model)
        body["employee_launch_reasoning_effort"] = .some(effort)
        _ = try await ticketWrite("/api/tickets/\(id)/employee-configuration", method: "PUT", body: body)
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
