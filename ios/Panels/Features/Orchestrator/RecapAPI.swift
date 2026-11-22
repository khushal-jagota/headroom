import Foundation

struct RecapDelivery: Decodable, Sendable {
    let fate: String
    let refusalReason: String?
    var accepted: Bool { ["started", "queued", "injected"].contains(fate) }
    var note: String? {
        switch fate {
        case "refused": "Not delivered · \(refusalReason?.replacingOccurrences(of: "_", with: " ") ?? "request refused")"
        default: nil
        }
    }
}

extension APIClient {
    func requestRecap(target: Principal) async throws -> RecapDelivery {
        struct Body: Encodable { let target: Principal }
        var request = try self.request(try url("/api/recap-requests"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(Body(target: target))
        request.timeoutInterval = 25
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(RecapDelivery.self, from: data)
    }
}

extension APIClient {
    func setManagerSettled(_ id: String, settled: Bool) async throws {
        struct Body: Encodable { let settled: Bool }
        try await writeAgentFields("/api/managers/\(id)/settled", method: "PATCH", body: Body(settled: settled))
    }
    private func writeAgentFields<Body: Encodable>(_ path: String, method: String, body: Body) async throws {
        var request = try self.request(try url(path))
        request.httpMethod = method; request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
    }
}

enum ManagerResources {
    // The broader workspace has no provider. Its active fallback stays explicit.
    static func detail(_ id: String) -> ResourceRegistration<ManagerDetail> {
        ResourceRegistration(key: "/api/managers/\(id)/workspace", versioned: false)
    }
}

struct ManagerDocumentContent: Decodable, Sendable {
    let goal: String
    let recap: String
}
extension ManagerResources {
    static func content(_ id: String) -> ResourceRegistration<ManagerDocumentContent> {
        ResourceRegistration(key: "/api/managers/\(id)/workspace-content")
    }
}
