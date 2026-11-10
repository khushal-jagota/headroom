import Foundation

// Wire projection of conversation/usage/contracts/usage.py (T3 contract v5).
struct RecordedUsageSummary: Decodable, Sendable {
    let contractVersion: Int
    let readAt: String
    let timeZone: String
    let sinceDay: String
    let untilDay: String
    let buckets: [Bucket]
    let sources: [Source]
    let pricing: Pricing
    struct Tokens: Decodable, Sendable {
        let uncachedInputTokens: Double
        let cachedInputTokens: Double
        let cacheCreationTokens: Double
        let outputTokens: Double
        let reasoningTokens: Double
    }
    struct Bucket: Decodable, Sendable {
        let day: String
        let provider: String
        let totals: Tokens
        let costUsd: Double
        let costSource: String
        let records: Int
        let unpricedRecords: Int
    }
    struct Source: Decodable, Sendable {
        struct Fingerprint: Decodable, Sendable { let hostId: String; let provider: String; let resolvedHomePath: String; let volumeId: String }
        let fingerprint: Fingerprint
        let status: String
        let message: String?
    }
    struct Pricing: Decodable, Sendable { let status: String; let source: String; let fetchedAt: String? }
}
// Presentation data for the existing native Tokens/Cost surfaces.
struct UsageSummary: Sendable {
    let days: Int
    let timezone: String
    let observedAt: String?
    let totals: UsageMetrics
    let providers: [UsageMetrics]
    let periods: [UsagePeriod]
    let sources: [UsageSource]
    let pricingNote: String?
    init(_ wire: RecordedUsageSummary, days: Int) {
        self.days = days; timezone = wire.timeZone; observedAt = wire.readAt
        totals = UsageMetrics(wire.buckets)
        providers = Set(wire.buckets.map(\.provider)).sorted().map { provider in
            UsageMetrics(wire.buckets.filter { $0.provider == provider }, provider: provider)
        }
        let format = DateFormatter(); format.locale = Locale(identifier: "en_US_POSIX")
        var dayCalendar = Calendar(identifier: .gregorian); dayCalendar.timeZone = TimeZone(secondsFromGMT: 0)!
        format.calendar = dayCalendar; format.timeZone = dayCalendar.timeZone
        format.dateFormat = "yyyy-MM-dd"
        let first = format.date(from: wire.sinceDay)
        let dates = (0..<max(1, days)).compactMap { offset in
            first.flatMap { dayCalendar.date(byAdding: .day, value: offset, to: $0) }.map { format.string(from: $0) }
        }
        periods = dates.map { day in
            let buckets = wire.buckets.filter { $0.day == day }
            let value = UsageMetrics(buckets)
            return UsagePeriod(start: day, label: day, totalTokens: value.totalTokens, costUsd: value.costUsd,
                providers: Set(buckets.map(\.provider)).sorted().map { provider in
                    UsageMetrics(buckets.filter { $0.provider == provider }, provider: provider)
                })
        }
        sources = wire.sources.map { source in
            UsageSource(id: [source.fingerprint.hostId, source.fingerprint.provider, source.fingerprint.resolvedHomePath, source.fingerprint.volumeId].joined(separator: "|"),
                        provider: source.fingerprint.provider, host: source.fingerprint.hostId,
                        state: source.status, issues: source.message.map { [$0] } ?? [])
        }
        pricingNote = wire.pricing.status == "fresh" ? nil
            : "Pricing \(wire.pricing.status). Unpriced records are excluded from recorded cost."
    }
}
struct UsageMetrics: Sendable {
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
    init(_ buckets: [RecordedUsageSummary.Bucket], provider: String? = nil) {
        self.provider = provider
        let input = buckets.reduce(0) { $0 + $1.totals.uncachedInputTokens }
        let cached = buckets.reduce(0) { $0 + $1.totals.cachedInputTokens }
        let creation = buckets.reduce(0) { $0 + $1.totals.cacheCreationTokens }
        let output = buckets.reduce(0) { $0 + $1.totals.outputTokens }
        inputTokens = input; cachedInputTokens = cached; cacheCreationTokens = creation; outputTokens = output
        // Reasoning is already part of output; match the web without counting it twice.
        totalTokens = input + cached + creation + output
        costUsd = buckets.reduce(0) { $0 + $1.costUsd }
        estimatedCostUsd = buckets.filter { $0.costSource == "modelPriced" }.reduce(0) { $0 + $1.costUsd }
        providerReportedCostUsd = buckets.filter { $0.costSource == "providerReported" }.reduce(0) { $0 + $1.costUsd }
        unknownCostRecords = buckets.reduce(0) { $0 + $1.unpricedRecords }
        recordCount = buckets.reduce(0) { $0 + $1.records }
    }
}
struct UsagePeriod: Sendable {
    let start: String
    let label: String
    let totalTokens: Double?
    let costUsd: Double?
    let providers: [UsageMetrics]
}
struct UsageSource: Sendable {
    let id: String
    let provider: String
    let host: String
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
    func usage(days: Int, now: Date = .now, timeZone: TimeZone = .current) async throws -> UsageSummary {
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = timeZone
        let format = DateFormatter(); format.calendar = calendar; format.timeZone = timeZone
        format.locale = Locale(identifier: "en_US_POSIX"); format.dateFormat = "yyyy-MM-dd"
        let start = calendar.date(byAdding: .day, value: -(days - 1), to: now)!
        let wire: RecordedUsageSummary = try await get("/api/conversation/usage/summary", query: [
            .init(name: "sinceDay", value: format.string(from: start)),
            .init(name: "untilDay", value: format.string(from: now)),
            .init(name: "timeZone", value: timeZone.identifier), .init(name: "resolution", value: "day")])
        guard wire.contractVersion == 5 else { throw APIError(status: 0, message: "Usage format changed. Update the app to read it.") }
        return UsageSummary(wire, days: days)
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
        let (data, response) = try await usageRefreshRequest("/api/conversation/usage/rates/refresh")
        try Self.validate(response, data: data)
    }
    private func usageRefreshRequest(_ path: String) async throws -> (Data, URLResponse) {
        var request = try self.request(try url(path))
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
