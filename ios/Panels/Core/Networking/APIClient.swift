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
struct ServerReadFailure: LocalizedError, Sendable {
    let isServerUnreachable: Bool
    /// A passing transport failure: no network, a dropped connection, a timeout or a
    /// cancel. TLS, ATS and malformed URL or response errors do not pass by themselves.
    let cannotReach: Bool
    let message: String
    /// A failed read emits no local connection note. The shared connection owner
    /// decides whether sustained loss requires the full-screen cover.
    var note: String? { cannotReach ? nil : message }
    /// The host lookups are here too: the server is reached over Tailscale, and when
    /// Tailscale drops, its MagicDNS name stops resolving. That is the same as offline.
    static let passingCodes: Set<URLError.Code> = [.notConnectedToInternet, .dataNotAllowed, .internationalRoamingOff,
        .networkConnectionLost, .timedOut, .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed, .cancelled]
    static func isPassing(_ error: any Error) -> Bool {
        if error is CancellationError { return true }
        let error = error as NSError
        return error.domain == NSURLErrorDomain && passingCodes.contains(URLError.Code(rawValue: error.code))
    }
    var errorDescription: String? { message }
    static func note(_ error: any Error) -> String? { ServerReadFailure(error).note }

    init(_ error: any Error) {
        if let failure = error as? ServerReadFailure { self = failure; return }
        if let error = error as? URLError {
            isServerUnreachable = [.cannotFindHost, .dnsLookupFailed, .cannotConnectToHost, .notConnectedToInternet].contains(error.code)
        } else {
            isServerUnreachable = false
        }
        cannotReach = Self.isPassing(error)
        message = error.localizedDescription
    }
}

/// Shared reads and Ticket machine catalogue transport.
struct APIClient: Sendable {
    let baseURL: URL
    var session: URLSession = .shared
    var serverConnection: ServerConnection? = nil

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
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue(NativeBuildGate.build, forHTTPHeaderField: "X-Panels-iOS-Build")
        request.setValue(try NativeClientIdentity.header(), forHTTPHeaderField: "X-Panels-Client")
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

    /// All ordinary transports announce actual network contact before decoding or
    /// HTTP validation. Saved responses never pass through this boundary.
    func data(for request: URLRequest, navigation: Bool = false) async throws -> (Data, URLResponse) {
        let attempt = await serverConnection?.attempt(server: baseURL)
        do {
            let result = try await session.data(for: request)
            await serverConnection?.answered(attempt, response: result.1)
            return result
        } catch {
            if navigation, !Task.isCancelled { await serverConnection?.failed(error, attempt: attempt) }
            throw error
        }
    }

    func download(for request: URLRequest) async throws -> (URL, URLResponse) {
        let attempt = await serverConnection?.attempt(server: baseURL)
        let result = try await session.download(for: request)
        await serverConnection?.answered(attempt, response: result.1)
        return result
    }

    func upload(for request: URLRequest, fromFile file: URL) async throws -> (Data, URLResponse) {
        let attempt = await serverConnection?.attempt(server: baseURL)
        let result = try await session.upload(for: request, fromFile: file)
        await serverConnection?.answered(attempt, response: result.1)
        return result
    }

    func bytes(for request: URLRequest) async throws -> (URLSession.AsyncBytes, URLResponse, ServerConnection.Attempt?) {
        let attempt = await serverConnection?.attempt(server: baseURL)
        let (bytes, response) = try await session.bytes(for: request)
        await serverConnection?.answered(attempt, response: response)
        // A redirected stream cannot use its later lines as contact with this server.
        let sameServer = response.url.map { LocalStore.server($0) == LocalStore.server(baseURL) } == true
        return (bytes, response, response is HTTPURLResponse && sameServer ? attempt : nil)
    }

    func responseData(_ path: String, query: [URLQueryItem] = []) async throws -> (Data, URL) {
        var request = try self.request(try url(path, query: query))
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 25
        let (data, response) = try await self.data(for: request)
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
    func artifacts(owner: ArtifactOwner, directory: String = "", offset: Int = 0) async throws -> ArtifactPage {
        try await get("/api/artifact-catalog/\(owner.kind.rawValue)/\(owner.id)", query: [.init(name: "directory", value: directory), .init(name: "offset", value: String(offset))])
    }
    /// The first page of a file list as this phone last read it.
    @MainActor func cachedArtifacts(owner: ArtifactOwner, directory: String = "") -> ArtifactPage? {
        cached("/api/artifact-catalog/\(owner.kind.rawValue)/\(owner.id)", query: [.init(name: "directory", value: directory), .init(name: "offset", value: "0")])
    }
}

enum TicketFilter: String, CaseIterable, Sendable { case active, backlogged, done }

// Cached data is explicit: preflight checks and receipt requests always use the network.
extension APIClient {
    static func cacheable(_ path: String) -> Bool {
        !path.contains("/receipts") && !path.contains("/body") && !path.contains("/pages") &&
        (path == "/api/workers" || path.hasPrefix("/api/tickets") ||
         path.hasPrefix("/api/managers") || path.hasPrefix("/api/worker-types") ||
         path == "/api/workers/chief-of-staff/settings" || path == "/api/chief/conversation" || path == "/api/drivers" ||
         path.hasPrefix("/api/conversation/conversations/") || path.hasPrefix("/api/artifact-catalog/ticket/"))
    }
    @MainActor func cached<T: Decodable>(_ path: String, query: [URLQueryItem] = []) -> T? {
        cachedRead(path, query: query)?.value
    }
    /// A saved response with the time it was saved.
    @MainActor func cachedRead<T: Decodable>(_ path: String, query: [URLQueryItem] = []) -> (value: T, saved: Date)? {
        guard let url = try? url(path, query: query),
              let row = try? LocalStore.shared.record(server: LocalStore.server(baseURL), kind: "cache", id: url.absoluteString),
              let value = try? Self.decoder().decode(T.self, from: row.payload) else { return nil }
        return (value, row.updated)
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
        let (data, response) = try await self.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(TicketBackendCatalog.self, from: data)
    }
}

extension APIClient {
    /// A driver's icon, through the shared request factory so the build header and the
    /// update gate apply. The URL carries the icon's content version.
    func driverIcon(_ target: URL) async throws -> Data {
        var request = try self.request(target)
        request.timeoutInterval = 25
        let (data, response) = try await self.data(for: request)
        try Self.validate(response, data: data)
        return data
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

/// An installation identifier, independent of notification permission and credentials.
private enum NativeClientIdentity {
    private static let deviceID: String = {
        let key = "panels.client.deviceID"
        if let saved = UserDefaults.standard.string(forKey: key) { return saved }
        let created = UUID().uuidString
        UserDefaults.standard.set(created, forKey: key)
        return created
    }()

    private struct Identity: Encodable {
        let kind = "ios"
        let device_id: String
        let version: String
        let build: Int?
    }

    static func header() throws -> String {
        let identity = Identity(device_id: deviceID, version: NativeBuildGate.build, build: Int(NativeBuildGate.build))
        return String(decoding: try JSONEncoder().encode(identity), as: UTF8.self)
    }
}
