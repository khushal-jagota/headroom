import Foundation

/// The served lifecycle determines approval gates; Review never assumes coding stages.
struct ReviewWorkerManifest: Decodable, Sendable {
    struct Stage: Decodable, Identifiable, Sendable { let id: String; let label: String }
    struct Field: Decodable, Sendable { let id: String; let label: String }
    let workerType: String
    let stages: [Stage]
    let advance: [String: String]
    let fields: [Field]
    let ceilingRange: [String]

    func approvalStages(after stage: String) -> [Stage] {
        guard let next = advance[stage], let start = stages.firstIndex(where: { $0.id == next }) else { return [] }
        return Array(stages[start...]).filter { ceilingRange.contains($0.id) }
    }
}

struct ReviewApproval: Encodable, Sendable {
    let expectedProposalRevision: Int
    let editedBody: String?
    let nextCeiling: String
    let nextHolder: Principal
}

extension APIClient {
    func reviewManifest(_ workerType: String) async throws -> ReviewWorkerManifest {
        struct Response: Decodable, Sendable { let workerTypes: [ReviewWorkerManifest] }
        let response: Response = try await get("/api/worker-types/\(workerType)/manifest")
        guard let manifest = response.workerTypes.first(where: { $0.workerType == workerType }) else {
            throw APIError(status: 0, message: "This worker type is unavailable.")
        }
        return manifest
    }

    func approveReview(_ ticketID: String, field: String, approval: ReviewApproval) async throws {
        try await reviewWrite("/api/tickets/\(ticketID)/accept/\(field)", body: approval)
    }

    func returnReview(_ ticketID: String, message: String) async throws {
        struct Body: Encodable { let message: String }
        try await reviewWrite("/api/tickets/\(ticketID)/reject", body: Body(message: message))
    }

    private func reviewWrite<T: Encodable>(_ path: String, body: T) async throws {
        var request = URLRequest(url: try url(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 25
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
    }
}
