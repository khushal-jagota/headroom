import Foundation

struct APIError: LocalizedError, Sendable {
    let status: Int
    let message: String
    var isUpdateRequired: Bool { status == 426 && message == NativeBuildGate.message }
    var isChangedProposal: Bool {
        status == 400 && message.hasPrefix("the visible proposal changed; read it again before ")
    }
    var errorDescription: String? { isChangedProposal ? "This changed, review again" : message }
}

/// A failed request is not proof that the server itself cannot be reached.
struct ServerReadFailure: Sendable {
    let isServerUnreachable: Bool
    let message: String

    init(_ error: any Error) {
        if let error = error as? URLError {
            isServerUnreachable = [.cannotFindHost, .dnsLookupFailed, .cannotConnectToHost, .notConnectedToInternet].contains(error.code)
        } else {
            isServerUnreachable = false
        }
        message = error.localizedDescription
    }
}

/// Shared reads and Ticket machine catalogue transport.
struct APIClient: Sendable {
    static let defaultBaseURL = URL(string: "https://panels-vps.tail687c57.ts.net")!
    let baseURL: URL
    var session: URLSession = .shared

    static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    func url(_ path: String, query: [URLQueryItem] = []) throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false),
              ["https", "http"].contains(components.scheme), components.host != nil else {
            throw APIError(status: 0, message: "Enter a valid server URL in Settings.")
        }
        components.path = path
        components.queryItems = query.isEmpty ? nil : query
        components.fragment = nil
        guard let url = components.url else { throw URLError(.badURL) }
        return url
    }

    /// Every native transport goes through this factory, including uploads and streams.
    func request(_ target: URL) throws -> URLRequest {
        guard !NativeBuildGate.requiresUpdate(baseURL) else {
            throw APIError(status: 426, message: NativeBuildGate.message)
        }
        var request = URLRequest(url: target)
        request.setValue(NativeBuildGate.build, forHTTPHeaderField: "X-Panels-iOS-Build")
        return request
    }

    func get<T: Decodable & Sendable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        let (data, url) = try await responseData(path, query: query)
        let value = try Self.decoder().decode(T.self, from: data)
        if Self.cacheable(path) {
            await LocalStore.shared.enqueueCacheResponse(server: LocalStore.server(baseURL), id: url.absoluteString, data: data)
        }
        return value
    }

    func responseData(_ path: String, query: [URLQueryItem] = []) async throws -> (Data, URL) {
        var request = try self.request(try url(path, query: query))
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 25
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return (data, request.url!)
    }

    static func validate(_ response: URLResponse, data: Data = Data()) throws {
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        if http.statusCode == 426 {
            struct Envelope: Decodable { struct Detail: Decodable { let code: String }; let error: Detail }
            let code = (try? JSONDecoder().decode(Envelope.self, from: data))?.error.code
            // Streaming transports have no error body until after headers. 426 is reserved
            // for the native gate on this server, including stream admission.
            if code == "ios_update_required" || data.isEmpty {
                if let url = http.url { NativeBuildGate.requireUpdate(url) }
                throw APIError(status: 426, message: NativeBuildGate.message)
            }
        }
        guard (200..<300).contains(http.statusCode) else {
            struct Envelope: Decodable { struct Detail: Decodable { let message: String }; let error: Detail }
            let message = (try? JSONDecoder().decode(Envelope.self, from: data))?.error.message
            throw APIError(status: http.statusCode, message: message ?? "Server returned HTTP \(http.statusCode).")
        }
    }

    func managers(offset: Int = 0, settled: Bool? = false) async throws -> ManagersResponse {
        var query = [URLQueryItem(name: "detail", value: "summary"), .init(name: "limit", value: "30"), .init(name: "offset", value: String(offset))]
        if let settled { query.append(.init(name: "settled", value: String(settled))) }
        return try await get("/api/managers", query: query)
    }
    func tickets(offset: Int = 0, manager: Principal? = nil, filter: TicketFilter = .active) async throws -> TicketsResponse {
        var query = [URLQueryItem(name: "detail", value: "summary"), .init(name: "limit", value: "30"), .init(name: "offset", value: String(offset))]
        switch filter {
        case .active: query.append(.init(name: "is_backlogged", value: "false"))
        case .backlogged: query.append(.init(name: "is_backlogged", value: "true"))
        case .done: query += [.init(name: "include_terminal", value: "true"), .init(name: "stage", value: "done")]
        }
        if let manager {
            query.append(.init(name: "manager_kind", value: manager.kind))
            if manager.kind == "manager" { query.append(.init(name: "manager_id", value: manager.id)) }
        }
        return try await get("/api/tickets", query: query)
    }
    func ticket(_ id: String) async throws -> TicketDetail {
        let result: TicketRead = try await get("/api/tickets", query: [.init(name: "detail", value: "full"), .init(name: "id", value: id)])
        return result.detail
    }
    func ticketField(_ id: String, field: String) async throws -> TicketFieldBody { try await get("/api/tickets/\(id)/workspace/fields/\(field)") }
    func manager(_ id: String) async throws -> ManagerDetail { try await get("/api/managers/\(id)/workspace") }
    func chief() async throws -> ChiefDetail { try await get("/api/workers/chief-of-staff/settings") }
    func chiefConversation() async throws -> ConversationReference { try await get("/api/chief/conversation") }
    func ideas() async throws -> IdeasResponse { try await get("/api/ideas") }
    func deployment() async throws -> DeploymentStatus { try await get("/api/deployment-status") }
    func artifacts(owner: ArtifactOwner, directory: String = "", offset: Int = 0) async throws -> ArtifactPage {
        try await get("/api/artifact-catalog/\(owner.kind.rawValue)/\(owner.id)", query: [.init(name: "directory", value: directory), .init(name: "offset", value: String(offset))])
    }
}

enum TicketFilter: String, CaseIterable, Sendable { case active, backlogged, done }

// Cached data is explicit: preflight checks and receipt requests always use the network.
extension APIClient {
    static func cacheable(_ path: String) -> Bool {
        !path.contains("/receipts") && !path.contains("/body") && !path.contains("/pages") &&
        (path == "/api/workers" || path.hasPrefix("/api/tickets") ||
         path.hasPrefix("/api/managers") || path.hasPrefix("/api/worker-types") ||
         path == "/api/workers/chief-of-staff/settings" || path == "/api/chief/conversation" ||
         path.hasPrefix("/api/conversation/conversations/"))
    }
    @MainActor func cached<T: Decodable>(_ path: String, query: [URLQueryItem] = []) -> T? {
        guard let url = try? url(path, query: query),
              let row = try? LocalStore.shared.record(server: LocalStore.server(baseURL), kind: "cache", id: url.absoluteString) else { return nil }
        return try? Self.decoder().decode(T.self, from: row.payload)
    }
    @MainActor func cachedTicket(_ id: String) -> TicketDetail? {
        cached("/api/tickets", query: [.init(name: "detail", value: "full"), .init(name: "id", value: id)])
    }
}

extension APIClient {
    func conversationBackends(owner: ConversationOwner) async throws -> TicketBackendCatalog {
        guard case .ticket(let id) = owner else { return try await get("/api/conversation/backends") }
        struct Body: Encodable { let ticketId: String }
        var request = try self.request(try url("/api/conversation/backends"))
        request.httpMethod = "POST"
        request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(Body(ticketId: id))
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(TicketBackendCatalog.self, from: data)
    }
}

extension APIClient {
    func executorHealth() async throws -> ExecutorHealth { try await get("/api/executor/health") }
}

/// A server-scoped latch survives relaunch. A new build automatically gets a fresh key.
/// UserDefaults is thread-safe, so API adapters can use this outside the main actor.
enum NativeBuildGate {
    static let message = "Update Panels from TestFlight"
    static let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "1"
    private static func key(_ url: URL) -> String {
        "panels.updateRequired.\(build).\(url.scheme ?? "https")://\(url.host ?? ""):\(url.port ?? (url.scheme == "http" ? 80 : 443))"
    }
    static func requiresUpdate(_ url: URL) -> Bool { UserDefaults.standard.bool(forKey: key(url)) }
    static func requireUpdate(_ url: URL) { UserDefaults.standard.set(true, forKey: key(url)) }
}
