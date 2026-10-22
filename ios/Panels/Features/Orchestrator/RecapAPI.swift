import Foundation

struct RecapDelivery: Decodable, Sendable {
    let fate: String
    let refusalReason: String?
    var accepted: Bool { ["started", "queued", "injected"].contains(fate) }
    var note: String? {
        switch fate {
        case "started", "queued", "injected": nil
        case "refused": "Not delivered · \(refusalReason?.replacingOccurrences(of: "_", with: " ") ?? "request refused")"
        default: "Delivery uncertain · do not resend"
        }
    }
}

extension APIClient {
    func requestRecap(target: Principal) async throws -> RecapDelivery {
        struct Body: Encodable { let target: Principal }
        var request = URLRequest(url: try url("/api/recap-requests"))
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
    func saveManagerGoal(_ id: String, goal: String) async throws {
        struct Body: Encodable { let goal: String }
        try await writeAgentFields("/api/managers/\(id)", method: "PATCH", body: Body(goal: goal))
    }
    func saveAgentRecap(target: Principal, recap: String) async throws {
        struct Body: Encodable { let recap: String }
        let chief = target.kind == "chief"
        try await writeAgentFields(chief ? "/api/workers/chief-of-staff/recap" : "/api/managers/\(target.id)", method: chief ? "PUT" : "PATCH", body: Body(recap: recap))
    }
    func setWorkerBacklog(_ id: String, backlogged: Bool) async throws {
        try await writeAgentFields("/api/tickets/\(id)/\(backlogged ? "backlog" : "resume")", method: "POST", body: [String: String]())
    }
    private func writeAgentFields<Body: Encodable>(_ path: String, method: String, body: Body) async throws {
        var request = URLRequest(url: try url(path))
        request.httpMethod = method; request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
    }
}
