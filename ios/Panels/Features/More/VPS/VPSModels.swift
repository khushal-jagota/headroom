import Foundation

struct VPSMeasurement: Decodable, Sendable { let value: Double?; let unit: String; let reason: String? }
struct VPSLatest: Decodable, Sendable {
    let sequence: Int?
    let sample: VPSSample?
    let collection: VPSCollection
}
struct VPSCollection: Decodable, Sendable { let availability: String; let reason: String?; let cadenceSeconds: Double }
struct VPSSample: Decodable, Sendable {
    let timestamp: Double
    let coreCount: Int
    let metrics: [String: VPSMeasurement]
    let services: [VPSService]
    func value(_ key: String) -> Double? { metrics[key]?.value }
}
struct VPSService: Decodable, Sendable, Identifiable {
    var id: String { identity }
    let identity: String
    let name: String
    let state: String?
    let processCount: Int
    let metrics: [String: VPSMeasurement]
}
struct VPSProcess: Decodable, Sendable, Identifiable {
    struct Identity: Decodable, Sendable { let bootId: String; let pid: Int; let startTicks: Int }
    let identity: Identity
    let name: String
    let metrics: [String: VPSMeasurement]
    var id: String { "\(identity.bootId):\(identity.pid):\(identity.startTicks)" }
}
struct VPSProcesses: Decodable, Sendable {
    let processes: [VPSProcess]
    let total: Int
    let nextOffset: Int?
    let availability: String
    let reason: String?
}
struct VPSHistory: Decodable, Sendable {
    struct Point: Decodable, Sendable { let timestamp: Double; let value: Double?; let bootId: String }
    let points: [Point]
}
enum VPSWindow: String, CaseIterable {
    case minutes = "5 min", day = "24 hours", month = "30 days"
    var seconds: Double { switch self { case .minutes: 300; case .day: 86400; case .month: 2592000 } }
    var resolution: String { switch self { case .minutes: "raw"; case .day: "minute"; case .month: "coarse" } }
}
extension APIClient {
    func vpsLatest() async throws -> VPSLatest { try await get("/api/vps-metrics/latest") }
    func vpsProcesses(sequence: Int?, offset: Int = 0) async throws -> VPSProcesses {
        var query: [URLQueryItem] = [.init(name: "limit", value: "100"), .init(name: "offset", value: String(offset))]
        if let sequence { query.append(.init(name: "sequence", value: String(sequence))) }
        return try await get("/api/vps-metrics/processes", query: query)
    }
    func vpsHistory(window: VPSWindow) async throws -> VPSHistory {
        let end = Date().timeIntervalSince1970
        return try await get("/api/vps-metrics/history", query: [.init(name: "signal", value: "cpu_percent"), .init(name: "start", value: String(end - window.seconds)), .init(name: "end", value: String(end)), .init(name: "resolution", value: window.resolution)])
    }
}
enum VPSPresentation {
    static func bytes(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        return ByteCountFormatter.string(fromByteCount: Int64(max(0, min(value, Double(Int64.max / 2)))), countStyle: .decimal)
    }
    static func percent(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        return "\(value.formatted(.number.precision(.fractionLength(0...1))))%"
    }
    static func used(total: Double?, available: Double?) -> Double? {
        guard let total, let available, total > 0 else { return nil }
        return max(0, total - available)
    }
    // Process/service CPU is one core; the machine figure is already total capacity.
    static func cpuShare(_ percent: Double?, cores: Int) -> Double? {
        guard let percent, cores > 0 else { return nil }
        return percent / Double(cores)
    }
    static func stale(_ latest: VPSLatest, now: Double) -> Bool {
        guard let sample = latest.sample else { return true }
        return latest.collection.availability == "unavailable" || now - sample.timestamp > latest.collection.cadenceSeconds * 2
    }
}
