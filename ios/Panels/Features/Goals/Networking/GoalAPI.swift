import Foundation

extension APIClient {
    func goals() async throws -> GoalsResponse { try await get("/api/goals") }
    func goal(_ id: String) async throws -> GoalRecord { try await get("/api/goals/\(id)") }
    func goalReadings(_ id: String) async throws -> GoalReadings { try await get("/api/goals/\(id)/readings") }
    func saveGoal(_ id: String, patch: GoalJSON) async throws -> GoalRecord {
        try await goalRequest("/api/goals/\(id)", method: "PATCH", body: patch)
    }
    func manualState(_ id: String, date: String? = nil) async throws -> GoalManualState {
        try await get("/api/measures/\(id)/manual-state", query: date.map { [.init(name: "date", value: $0)] } ?? [])
    }
    func writeManual(_ id: String, command: GoalManualCommand) async throws -> GoalManualResult {
        try await goalRequest("/api/measures/\(id)/manual-writes", method: "POST", body: command)
    }
    func goalRequest<Body: Encodable, Response: Decodable & Sendable>(_ path: String, method: String, body: Body) async throws -> Response {
        var request = URLRequest(url: try url(path)); request.httpMethod = method; request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(Response.self, from: data)
    }
}
