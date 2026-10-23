import Foundation

// Read projections of conversation/usage/contracts.py and conversation/contract.py.
struct UsageSummary: Decodable, Sendable {
    let days: Int
    let timezone: String
    let observedAt: String?
    let totals: UsageMetrics
    let providers: [UsageMetrics]
    let periods: [UsagePeriod]
    let sources: [UsageSource]
}
struct UsageMetrics: Decodable, Sendable {
    let provider: String?
    let totalTokens: Double?
    let inputTokens: Double?
    let cachedInputTokens: Double?
    let cacheCreationTokens: Double?
    let outputTokens: Double?
    let costUsd: Double?
    let estimatedCostUsd: Double?
    let providerReportedCostUsd: Double?
    let unknownCostRecords: Int
    let recordCount: Int
}
struct UsagePeriod: Decodable, Sendable {
    let start: String
    let label: String
    let totalTokens: Double?
    let costUsd: Double?
    let providers: [UsageMetrics]
}
struct UsageSource: Decodable, Sendable {
    let provider: String
    let state: String
    let issues: [String]
}
struct UsageBackends: Decodable, Sendable { let backends: [UsageBackend] }
struct UsageBackend: Decodable, Sendable, Identifiable {
    var id: String { backendKey }
    let backendKey: String
    let installed: Bool
    let version: String?
    let cachedUsage: AllowanceUsage?
    let diagnoses: [String]
}
struct AllowanceUsage: Decodable, Sendable {
    let observedAt: String
    let windows: [AllowanceWindow]
}
struct AllowanceWindow: Decodable, Sendable {
    let kind: String
    let usedPercent: Double
    let resetsAt: String
    let modelId: String?
}

extension APIClient {
    func usage(days: Int) async throws -> UsageSummary {
        try await get("/api/conversation/usage/summary", query: [.init(name: "days", value: String(days)), .init(name: "timezone", value: TimeZone.current.identifier)])
    }
    func usageBackends() async throws -> UsageBackends { try await get("/api/conversation/backends") }
}

struct UsageBackendRefresh: Decodable, Sendable {
    let backends: [UsageBackend]
    let usageOutcomes: [UsageRefreshOutcome]
    var failure: String? {
        let failures = usageOutcomes.filter { $0.outcome == "failed" }.map {
            $0.detail ?? "\($0.backendKey.capitalized) limits could not be refreshed."
        }
        return failures.isEmpty ? nil : failures.joined(separator: " ")
    }
}
struct UsageRefreshOutcome: Decodable, Sendable {
    let backendKey: String
    let outcome: String
    let detail: String?
}

extension APIClient {
    func refreshUsageBackends() async throws -> UsageBackendRefresh {
        let (data, response) = try await usageRefreshRequest("/api/conversation/backends/refresh")
        try Self.validate(response, data: data)
        return try Self.decoder().decode(UsageBackendRefresh.self, from: data)
    }
    func refreshRecordedUsage() async throws {
        let (data, response) = try await usageRefreshRequest("/api/conversation/usage/refresh")
        try Self.validate(response, data: data)
    }
    private func usageRefreshRequest(_ path: String) async throws -> (Data, URLResponse) {
        var request = URLRequest(url: try url(path))
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        return try await session.data(for: request)
    }
}

enum UsagePresentation {
    static func number(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        return value.formatted(.number.notation(.compactName).precision(.fractionLength(0...1)))
    }
    static func cost(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        return value.formatted(.currency(code: "USD"))
    }
    static func date(_ text: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: text) ?? ISO8601DateFormatter().date(from: text)
    }
    static func fraction(_ percent: Double) -> Double { min(1, max(0, percent / 100)) }
}
