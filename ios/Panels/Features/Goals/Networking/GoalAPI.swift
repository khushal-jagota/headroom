import Foundation

extension APIClient {
    func holderPage(_ holder: GoalHolder) async throws -> HolderPage { try await get(holder.pagePath) }
    func holderReadings(_ holder: GoalHolder) async throws -> GoalReadings { try await get(holder.pagePath + "/readings") }
    func saveHolderPage(_ holder: GoalHolder, patch: GoalJSON) async throws -> HolderPage {
        try await goalRequest(holder.pagePath, method: "PATCH", body: patch)
    }

    func goals() async throws -> GoalsResponse { try await get("/api/goals") }
    func goalProjects() async throws -> GoalJSON {
        try await get("/api/projects", query: [.init(name: "detail", value: "full")])
    }
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
        var request = try self.request(try url(path)); request.httpMethod = method; request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(Response.self, from: data)
    }
}
