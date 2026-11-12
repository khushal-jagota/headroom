import Foundation

// Wire contract: src/planner/navigation/views/contracts.py and contracts.py.
// Only the subset the phone reads is decoded; unknown fields are ignored and
// unknown record kinds are kept by identity so a patch can still evict them.
enum NavigationViewVersion {
    static let projection = 7
    static let wire = 3
    /// The server orders titles only for its configured locales.
    static let locale = "en-US"
}

struct ViewOwner: Codable, Hashable, Sendable {
    let kind: String
    let id: String
    static let chief = ViewOwner(kind: "chief", id: "chief")
    static func manager(_ id: String) -> ViewOwner { ViewOwner(kind: "manager", id: id) }
}

struct RecordKey: Codable, Hashable, Sendable {
    let kind: String
    let id: String
}

/// Server-defined membership. Every key is sent, with explicit nulls, because the
/// server refuses a request whose field set differs from the contract.
struct ViewWindowRequest: Codable, Equatable, Sendable {
    var section: String
    var owner: ViewOwner?
    var group = "all"
    var childFilter = "all"
    var anchor: String?
    var direction = "after"
    var limit = 30

    init(section: String, owner: ViewOwner? = nil, anchor: String? = nil, direction: String = "after", limit: Int = 30) {
        self.section = section; self.owner = owner; self.anchor = anchor; self.direction = direction; self.limit = limit
    }
    /// The server's window identity: one window per section, owner, group and filter.
    var identity: String { [section, owner?.kind ?? "", owner?.id ?? "", group, childFilter].joined(separator: "\u{1f}") }

    enum CodingKeys: String, CodingKey { case section, owner, group, childFilter, anchor, direction, limit }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(section, forKey: .section)
        if let owner { try c.encode(owner, forKey: .owner) } else { try c.encodeNil(forKey: .owner) }
        try c.encode(group, forKey: .group)
        try c.encode(childFilter, forKey: .childFilter)
        if let anchor { try c.encode(anchor, forKey: .anchor) } else { try c.encodeNil(forKey: .anchor) }
        try c.encode(direction, forKey: .direction)
        try c.encode(limit, forKey: .limit)
    }
}

struct ViewRequest: Encodable, Equatable, Sendable {
    var projectionVersion = NavigationViewVersion.projection
    var scopeGeneration = 0
    var locale = NavigationViewVersion.locale
    var selected: ViewOwner?
    var selectedConversationId: String?
    var reviewConversationIds: [String] = []
    var reviewSkippedKeys: [RecordKey] = []
    var reviewPriorityKeys: [RecordKey] = []
    var windows: [ViewWindowRequest] = []

    enum CodingKeys: String, CodingKey {
        case projectionVersion, scopeGeneration, locale, selected, selectedConversationId
        case reviewConversationIds, reviewSkippedKeys, reviewPriorityKeys, windows
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(projectionVersion, forKey: .projectionVersion)
        try c.encode(scopeGeneration, forKey: .scopeGeneration)
        try c.encode(locale, forKey: .locale)
        if let selected { try c.encode(selected, forKey: .selected) } else { try c.encodeNil(forKey: .selected) }
        if let selectedConversationId { try c.encode(selectedConversationId, forKey: .selectedConversationId) } else { try c.encodeNil(forKey: .selectedConversationId) }
        try c.encode(reviewConversationIds, forKey: .reviewConversationIds)
        try c.encode(reviewSkippedKeys, forKey: .reviewSkippedKeys)
        try c.encode(reviewPriorityKeys, forKey: .reviewPriorityKeys)
        try c.encode(windows, forKey: .windows)
    }
    /// Compact JSON, as the `view` query parameter of the snapshot and events doors.
    func encoded() throws -> String {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return String(decoding: try encoder.encode(self), as: UTF8.self)
    }
}

// MARK: Records

struct ViewPrincipal: Codable, Hashable, Sendable { let kind: String; let id: String }
struct ViewContentReference: Codable, Sendable { let version: String; let utf8Bytes: Int }
struct ViewIntegrityIssue: Codable, Sendable { let reason: String }

/// Ordinary and broken Ticket values share one shape; fields a broken Ticket
/// lacks are optional and `integrityError` tells them apart.
struct ViewTicketValue: Codable, Sendable {
    let title: String
    let recap: String
    let recapPreview: String
    let recapReference: ViewContentReference?
    let workerType: String?
    let stage: String
    let ticketStatus: String
    let isBacklogged: Bool
    let createdAt: Double
    let updatedAt: Double
    let projectId: String?
    let managerKind: String
    let managerId: String?
    let conversationId: String?
    let integrityError: ViewIntegrityIssue?
    var blocked: Bool?
    var ceilingHolder: ViewPrincipal?
    var proposalField: String?
    var proposalRevision: Int?
    var proposalCreatedAt: Double?
    var gatingField: String?
    var stageOwnership: String?
    var terminal: Bool?
    var workerStepClaimOut: Bool?
}

/// A settled-only manager row carries no recap or project; `isLean` marks it.
struct ViewManagerValue: Codable, Sendable {
    let title: String
    let createdAt: Double
    let updatedAt: Double
    let settled: Bool
    let conversationId: String?
    var recap: String?
    var recapPreview: String?
    var projectId: String?
    var isLean: Bool { recap == nil }
}

struct ViewChiefValue: Codable, Sendable { let label: String; let conversationId: String? }
struct ViewProjectValue: Codable, Sendable { let name: String }

struct ViewAttention: Codable, Sendable {
    struct Signal: Codable, Sendable { let working: Bool; let messageForOwner: Bool }
    struct Current: Codable, Sendable { let signal: Signal; let awaitingAnswer: Bool; let lastTurnFailed: Bool }
    struct Durable: Codable, Sendable { let messageForOwner: Bool; let lastTurnFailed: Bool }
    let current: Current?
    let durable: Durable
}

struct ViewConversationValue: Codable, Sendable {
    let firstUnreadOwnerMessageAt: Double?
    let ownerReadThroughSequence: Int
    let errorAcknowledgedThroughSequence: Int
    let attention: ViewAttention
}

struct ViewAddressValue: Codable, Sendable {
    let ownerKind: String
    let ownerId: String
    let conversationId: String
    let current: Bool
    let parentKind: String?
    let parentId: String?
}

enum ViewRecord: Codable, Sendable {
    case ticket(String, ViewTicketValue)
    case manager(String, ViewManagerValue)
    case chief(ViewChiefValue)
    case conversation(String, ViewConversationValue)
    case address(String, ViewAddressValue)
    case project(String, ViewProjectValue)
    /// Goals and any later kind: retained by identity, not interpreted.
    case other(RecordKey)

    var key: RecordKey {
        switch self {
        case .ticket(let id, _): RecordKey(kind: "ticket", id: id)
        case .manager(let id, _): RecordKey(kind: "manager", id: id)
        case .chief: RecordKey(kind: "chief", id: "chief")
        case .conversation(let id, _): RecordKey(kind: "conversation", id: id)
        case .address(let id, _): RecordKey(kind: "address", id: id)
        case .project(let id, _): RecordKey(kind: "project", id: id)
        case .other(let key): key
        }
    }

    enum CodingKeys: String, CodingKey { case kind, id, value }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let kind = try c.decode(String.self, forKey: .kind)
        let id = try c.decode(String.self, forKey: .id)
        switch kind {
        case "ticket": self = .ticket(id, try c.decode(ViewTicketValue.self, forKey: .value))
        case "manager": self = .manager(id, try c.decode(ViewManagerValue.self, forKey: .value))
        case "chief": self = .chief(try c.decode(ViewChiefValue.self, forKey: .value))
        case "conversation": self = .conversation(id, try c.decode(ViewConversationValue.self, forKey: .value))
        case "address": self = .address(id, try c.decode(ViewAddressValue.self, forKey: .value))
        case "project": self = .project(id, try c.decode(ViewProjectValue.self, forKey: .value))
        default: self = .other(RecordKey(kind: kind, id: id))
        }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(key.kind, forKey: .kind)
        try c.encode(key.id, forKey: .id)
        switch self {
        case .ticket(_, let value): try c.encode(value, forKey: .value)
        case .manager(_, let value): try c.encode(value, forKey: .value)
        case .chief(let value): try c.encode(value, forKey: .value)
        case .conversation(_, let value): try c.encode(value, forKey: .value)
        case .address(_, let value): try c.encode(value, forKey: .value)
        case .project(_, let value): try c.encode(value, forKey: .value)
        case .other: break
        }
    }

}

// MARK: Windows and frames

/// A whole window is replaced atomically. Never infer a global total from members.
struct ViewWindow: Codable, Sendable {
    let windowId: String
    let request: ViewWindowRequest
    let members: [RecordKey]
    let total: Int
    let previous: String?
    let next: String?
}

struct ViewCounts: Codable, Equatable, Sendable {
    let runningConversations: Int
    let outClaims: Int
    let review: Int
    let unread: Int
    let settled: Int
}

struct ViewContinuity: Codable, Equatable, Sendable {
    let scope: String
    let schemaVersion: Int
    let semantics: String
    let epoch: String
    let revision: String
    func sameDomain(_ other: ViewContinuity) -> Bool {
        scope == other.scope && schemaVersion == other.schemaVersion && semantics == other.semantics && epoch == other.epoch
    }
}

struct ViewAuthority: Codable, Equatable, Sendable {
    struct Availability: Codable, Equatable, Sendable { let state: String; let generation: String; let reason: String? }
    struct Permit: Codable, Equatable, Sendable { let scope: String; let runtimeEpoch: String; let installationId: String; let generation: String }
    let deliverySessionId: String
    let availability: Availability
    let permit: Permit
}

struct ViewContent: Codable, Sendable {
    var records: [ViewRecord]
    var windows: [ViewWindow]
    var counts: ViewCounts
}

/// One coherent bounded view: records, window membership and global counts.
struct ViewSnapshot: Codable, Sendable {
    let wireVersion: Int
    let projectionVersion: Int
    let viewId: String
    let scopeGeneration: Int
    let orderingGeneration: String
    var coverage: ViewContinuity
    var contentVersion: String
    let mode: String
    let authority: ViewAuthority
    var content: ViewContent

    func window(_ section: String, owner: ViewOwner? = nil) -> ViewWindow? {
        content.windows.first { $0.request.section == section && $0.request.owner == owner && $0.request.group == "all" && $0.request.childFilter == "all" }
    }
}

extension ViewSnapshot {
    /// Saved display keeps durable attention, never current runtime authority.
    var savedDisplay: ViewSnapshot {
        var snapshot = self
        snapshot.content.records = content.records.map { record in
            guard case .conversation(let id, let value) = record else { return record }
            return .conversation(id, ViewConversationValue(firstUnreadOwnerMessageAt: value.firstUnreadOwnerMessageAt,
                ownerReadThroughSequence: value.ownerReadThroughSequence,
                errorAcknowledgedThroughSequence: value.errorAcknowledgedThroughSequence,
                attention: ViewAttention(current: nil, durable: value.attention.durable)))
        }
        return ViewSnapshot(wireVersion: wireVersion, projectionVersion: projectionVersion, viewId: viewId,
            scopeGeneration: scopeGeneration, orderingGeneration: orderingGeneration, coverage: coverage,
            contentVersion: contentVersion, mode: "saved", authority: authority, content: snapshot.content)
    }
}

struct ViewPatch: Codable, Sendable {
    let viewId: String
    let scopeGeneration: Int
    let orderingGeneration: String
    let coverage: ViewContinuity
    let previousCoverage: ViewContinuity
    let contentVersion: String
    let previousContentVersion: String
    let authority: ViewAuthority
    let upserts: [ViewRecord]
    let evictions: [RecordKey]
    let windows: [ViewWindow]
    let counts: ViewCounts
}

struct ViewCheckpoint: Codable, Sendable {
    let viewId: String
    let scopeGeneration: Int
    let orderingGeneration: String
    let coverage: ViewContinuity
    let previousCoverage: ViewContinuity
    let contentVersion: String
    let authority: ViewAuthority
}

enum ViewFrame: Decodable, Sendable {
    case snapshot(ViewSnapshot)
    case patch(ViewPatch)
    case checkpoint(ViewCheckpoint)
    case reset(reason: String)

    enum CodingKeys: String, CodingKey { case event, reason }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        switch try c.decode(String.self, forKey: .event) {
        case "snapshot": self = .snapshot(try ViewSnapshot(from: decoder))
        case "patch": self = .patch(try ViewPatch(from: decoder))
        case "checkpoint": self = .checkpoint(try ViewCheckpoint(from: decoder))
        case "reset": self = .reset(reason: try c.decode(String.self, forKey: .reason))
        case let other: throw DecodingError.dataCorruptedError(forKey: .event, in: c, debugDescription: "Unknown view frame \(other)")
        }
    }
}

extension ViewFrame {
    /// The scope a frame answers; a reset names no scope the phone checks.
    var scopeGeneration: Int? {
        switch self {
        case .snapshot(let value): value.scopeGeneration
        case .patch(let value): value.scopeGeneration
        case .checkpoint(let value): value.scopeGeneration
        case .reset: nil
        }
    }
}

/// One server-sent frame per `data:` line. URLSession's line sequence omits the blank
/// separator lines; `event:` names repeat the frame's own `event` field and heartbeat
/// comments carry nothing.
enum NavigationViewEventParser {
    static func data(_ line: String) -> String? {
        guard line.hasPrefix("data:") else { return nil }
        let payload = line.dropFirst(5)
        return String(payload.first == " " ? payload.dropFirst() : payload)
    }
}

extension APIClient {
    private func navigationViewURL(_ path: String, _ request: ViewRequest) throws -> URL {
        var components = URLComponents(url: try url(path), resolvingAgainstBaseURL: false)!
        // `+` would read as a space; encode everything outside the unreserved set.
        let unreserved = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        components.percentEncodedQuery = "view=" + (try request.encoded()).addingPercentEncoding(withAllowedCharacters: unreserved)!
        return components.url!
    }

    private static func supported(_ snapshot: ViewSnapshot) -> Bool {
        snapshot.wireVersion == NavigationViewVersion.wire && snapshot.projectionVersion == NavigationViewVersion.projection && snapshot.mode == "current"
    }

    /// One bounded snapshot for the phone's current scope. Returns the raw body too,
    /// so the last coherent view can be restored at launch.
    func navigationViewSnapshot(_ request: ViewRequest) async throws -> (ViewSnapshot, Data) {
        var urlRequest = try self.request(try navigationViewURL("/api/navigation/view/snapshot", request))
        urlRequest.cachePolicy = .reloadIgnoringLocalCacheData
        urlRequest.timeoutInterval = 25
        let (data, response) = try await session.data(for: urlRequest)
        try Self.validate(response, data: data)
        guard case .snapshot(let snapshot) = try Self.decoder().decode(ViewFrame.self, from: data), Self.supported(snapshot)
        else { throw APIError(status: 0, message: "The server sent an unsupported navigation view.") }
        return (snapshot, data)
    }

    /// The scope's events stream: a snapshot first, then patch, checkpoint and reset
    /// frames. Each frame comes with its raw body, so a snapshot is saved as it was read.
    /// The server ends the stream instead of sending a frame when its source changes.
    /// Frames are decoded off the main actor and none is dropped: each patch follows the last.
    func navigationViewEvents(_ request: ViewRequest) -> AsyncThrowingStream<(ViewFrame, Data), Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var urlRequest = try self.request(try navigationViewURL("/api/navigation/view/events", request))
                    urlRequest.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    urlRequest.cachePolicy = .reloadIgnoringLocalCacheData
                    // Longer than the server's 15 s heartbeat, so a silent socket is noticed.
                    urlRequest.timeoutInterval = 45
                    let (bytes, response) = try await session.bytes(for: urlRequest)
                    try Self.validate(response)
                    let decoder = Self.decoder()
                    for try await line in ServerSentLines(bytes) {
                        try Task.checkCancellation()
                        guard let payload = NavigationViewEventParser.data(line) else { continue }
                        let data = Data(payload.utf8)
                        let frame = try decoder.decode(ViewFrame.self, from: data)
                        if case .snapshot(let snapshot) = frame, !Self.supported(snapshot) {
                            throw APIError(status: 0, message: "The server sent an unsupported navigation view.")
                        }
                        continuation.yield((frame, data))
                    }
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
