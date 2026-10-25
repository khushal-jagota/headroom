import Foundation

// Wire contract: planner/resource_versions/contracts.py. Body and revision must
// come from one read; a probe must never be attached to a separately fetched body.
struct ResourceVersions: Decodable, Sendable {
    let epoch: String
    let resources: [Resource]
    struct Resource: Decodable, Sendable { let key: String; let revision: String? }
}
struct ResourceBody<Body: Decodable & Sendable>: Decodable, Sendable {
    let epoch: String
    let key: String
    let revision: String
    let body: Body
}
struct ResourceVersionState {
    private var stamps: [String: Stamp] = [:]
    private struct Stamp: Equatable { let epoch: String; let revision: String }
    func isCurrent(_ key: String, probe: ResourceVersions?) -> Bool {
        guard let probe, let revision = probe.resources.first(where: { $0.key == key })?.revision else { return false }
        return stamps[key] == Stamp(epoch: probe.epoch, revision: revision)
    }
    mutating func accept<T>(_ body: ResourceBody<T>) {
        stamps[body.key] = Stamp(epoch: body.epoch, revision: body.revision)
    }
    mutating func invalidate(_ key: String) { stamps[key] = nil }
}

extension APIClient {
    func resourceVersions(_ keys: [String]) async throws -> ResourceVersions {
        let encoded = String(decoding: try JSONEncoder().encode(keys), as: UTF8.self)
        return try await get("/api/resource-versions", query: [.init(name: "keys", value: encoded)])
    }
    func resourceBody<T: Codable & Sendable>(_ key: String) async throws -> ResourceBody<T> {
        let result: ResourceBody<T> = try await get("/api/resource-body", query: [.init(name: "key", value: key)])
        guard result.key == key else { throw URLError(.badServerResponse) }
        if Self.cacheable(key) {
            let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
            let data = try encoder.encode(result.body)
            try? await LocalStore.shared.cacheResponse(server: LocalStore.server(baseURL), id: try url(key).absoluteString, data: data)
        }
        return result
    }
}
