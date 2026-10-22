import Foundation

struct APIError: LocalizedError, Sendable {
    let status: Int
    let message: String
    var errorDescription: String? { message }
}

/// All endpoint paths are confined to this adapter. No calls mark conversations read.
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

    func get<T: Decodable & Sendable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        var request = URLRequest(url: try url(path, query: query))
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 25
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(T.self, from: data)
    }

    static func validate(_ response: URLResponse, data: Data = Data()) throws {
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
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
    func ticket(_ id: String) async throws -> TicketDetail { try await get("/api/tickets", query: [.init(name: "detail", value: "full"), .init(name: "id", value: id)]) }
    func ticketField(_ id: String, field: String) async throws -> TicketFieldBody { try await get("/api/tickets/\(id)/workspace/fields/\(field)") }
    func manager(_ id: String) async throws -> ManagerDetail { try await get("/api/managers/\(id)/workspace") }
    func chief() async throws -> ChiefDetail { try await get("/api/workers/chief-of-staff/settings") }
    func chiefConversation() async throws -> ConversationReference { try await get("/api/chief/conversation") }
    func review() async throws -> ReviewResponse { try await get("/api/review") }
    func ideas() async throws -> IdeasResponse { try await get("/api/ideas") }
    func deployment() async throws -> DeploymentStatus { try await get("/api/deployment-status") }
    func artifacts(owner: ArtifactOwner, directory: String = "", offset: Int = 0) async throws -> ArtifactPage {
        try await get("/api/artifact-catalog/\(owner.kind.rawValue)/\(owner.id)", query: [.init(name: "directory", value: directory), .init(name: "offset", value: String(offset))])
    }
}

enum TicketFilter: String, CaseIterable, Sendable { case active, backlogged, done }

// Preserve the user's loaded window when a change arrives. Pages remain bounded requests.
extension APIClient {
    func managerWindow(through lastOffset: Int) async throws -> ManagersResponse {
        var page = try await managers()
        var rows = page.managers
        while let offset = page.page.nextOffset, offset <= lastOffset {
            page = try await managers(offset: offset)
            let ids = Set(rows.map(\.id))
            rows += page.managers.filter { !ids.contains($0.id) }
        }
        return ManagersResponse(managers: rows, page: page.page)
    }
    func ticketWindow(through lastOffset: Int) async throws -> TicketsResponse {
        var page = try await tickets()
        var rows = page.tickets
        while let offset = page.page.nextOffset, offset <= lastOffset {
            page = try await tickets(offset: offset)
            let ids = Set(rows.map(\.id))
            rows += page.tickets.filter { !ids.contains($0.id) }
        }
        return TicketsResponse(tickets: rows, page: page.page)
    }
}
