import Foundation

struct TicketDetail: Codable, Identifiable, Sendable {
    var locale: String? = nil
    let id: String
    let title: String
    let workerType: String?
    var workflow: TicketWorkerType? = nil
    let employeeBackend: String
    let employeeLaunchModel: String?
    let employeeLaunchReasoningEffort: String?
    let employeeConfigurationEditable: Bool
    let stage: String
    let ticketStatus: String
    let isBacklogged: Bool
    let blocked: Bool
    let blockerSummary: BlockerSummary?
    let ceiling: String
    let ceilingHolder: Principal
    let manager: Principal
    let deadline: String?
    let projectId: String?
    let project: String?
    let managerId: String?
    let managerTitle: String?
    let conversationId: String?
    let recap: String
    let recordSha256: String
    let fieldValues: [String: String]
    let fieldSummaries: [String: FieldSummary]?
    let fieldApprovals: [String: FieldApproval]
    let pendingProposal: TicketProposal?
    let agentState: String?
    let conversationSignal: ConversationSignal?
    let assigned: Bool?
    let awaitingApproval: Bool?
}
struct BlockerSummary: Codable, Sendable { let blockedBy: [TicketBlocker] }
struct TicketBlocker: Codable, Sendable { let ticketId: String; let title: String; let stage: String; let href: String }
struct FieldSummary: Codable, Sendable { let byteCount: Int; let revision: String }
struct FieldApproval: Codable, Sendable { let method: String; let principal: Principal?; let displayLabel: String }
struct TicketProposal: Codable, Sendable { let field: String; let body: String; let proposedBy: String; let createdAt: Double; let revision: Int }
struct TicketFieldBody: Codable, Sendable { let ticketId: String; let field: String; let value: String; let revision: String }

struct TicketBackendCatalog: Decodable, Sendable {
    let backends: [TicketBackend]
}
struct TicketBackend: Decodable, Identifiable, Sendable {
    struct Model: Decodable, Identifiable, Sendable {
        let modelId: String
        let displayName: String?
        var resolvedModelId: String? = nil
        var selectedVariant: String? = nil
        let enabled: Bool
        let reasoningEffortOptions: [String]?
        /// The server's readable name for the model this one resolves to. Older servers omit it.
        var resolvedDisplayName: String? = nil
        var id: String { modelId }
    }
    let backendKey: String
    let installed: Bool
    let availableModels: [Model]
    let reasoningEffortOptions: [String]
    let defaultModelId: String?
    let defaultReasoningEffort: String?
    /// The backend reports allowance usage. Older servers omit it.
    var reportsAllowanceUsage: Bool? = nil
    var id: String { backendKey }
    /// The card or any of its models offers a reasoning effort.
    var offersReasoning: Bool {
        !reasoningEffortOptions.isEmpty || availableModels.contains { !($0.reasoningEffortOptions ?? []).isEmpty }
    }
    /// What to call a chosen model: the server's name for what it resolves to, then the
    /// concrete entry's own label, then the resolved id.
    func modelName(_ modelID: String) -> String {
        let selected = availableModels.first { $0.modelId == modelID }
        guard let selected, let resolved = selected.resolvedModelId, resolved != selected.selectedVariant else {
            return selected?.displayName ?? selected?.resolvedModelId ?? modelID
        }
        if let name = selected.resolvedDisplayName { return name }
        let concrete = availableModels.first { $0.modelId == resolved }?.displayName
        if let concrete, concrete != resolved, concrete.lowercased() != selected.selectedVariant?.lowercased() { return concrete }
        return resolved
    }
}

/// The drivers this server runs, from `GET /api/drivers`. An older server has no such
/// route; then the catalog is nil and every reader falls back.
struct DriverCatalog: Decodable, Sendable, Equatable {
    let drivers: [DriverPresentation]
    /// The backend a conversation starts on when nothing chooses one.
    var defaultBackendInstanceId: String? = nil

    func driver(_ key: String) -> DriverPresentation? { drivers.first { $0.driverKind == key } }
    /// The driver's label, else the key capitalised.
    static func label(_ key: String, in catalog: DriverCatalog?) -> String {
        catalog?.driver(key)?.label ?? key.capitalized
    }
    /// The composer's backend: the one chosen, else the server's default, else none.
    static func composerBackend(_ chosen: String?, in catalog: DriverCatalog?) -> String? {
        chosen ?? catalog?.defaultBackendInstanceId
    }
}
struct DriverPresentation: Decodable, Sendable, Equatable {
    struct Icon: Decodable, Sendable, Equatable {
        /// A same-origin path carrying a content version.
        let url: String
        let mediaType: String
        /// A single-colour glyph the app may tint.
        let monochrome: Bool
    }
    let driverKind: String
    let label: String
    /// Optional, so one driver without an icon does not cost the whole list.
    var icon: Icon? = nil
}
enum DriverResources {
    static let catalog = ResourceRegistration<DriverCatalog>(key: "/api/drivers", versioned: false)
}

/// How a backend is drawn. The bundled marks come first, so the built-in drivers look the
/// same with or without the server's list. Another driver shows its PNG icon, tinted when
/// monochrome; the app cannot draw SVG, so an SVG-only driver shows its initials.
enum DriverMark: Equatable, Sendable {
    case bundled(String)
    case remote(URL, template: Bool)
    case initials(String)

    static func resolve(key: String, catalog: DriverCatalog?, bundled: (String) -> Bool, server: URL) -> DriverMark {
        let asset = "conversation-" + key
        if bundled(asset) { return .bundled(asset) }
        if let icon = catalog?.driver(key)?.icon, icon.mediaType == "image/png",
           icon.url.hasPrefix("/"), !icon.url.hasPrefix("//"),
           let url = URL(string: icon.url, relativeTo: server)?.absoluteURL {
            return .remote(url, template: icon.monochrome)
        }
        return .initials(initials(key))
    }
    /// The web's rule: "antigravity" gives "A"; "open-code" gives "OC".
    static func initials(_ key: String) -> String {
        key.split(whereSeparator: { $0 == "-" || $0 == "_" }).prefix(2).compactMap(\.first).map { String($0).uppercased() }.joined()
    }
}


struct TicketIntegrityIssue: Codable, Sendable { let reason: String }
// A broken Ticket has only diagnostic fields, never valid lifecycle controls.
struct TicketRead: Decodable, Sendable {
    let detail: TicketDetail
    enum CodingKeys: String, CodingKey { case integrityError }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let issue = try c.decodeIfPresent(TicketIntegrityIssue.self, forKey: .integrityError) {
            throw APIError(status: 0, message: "Broken Ticket: " + issue.reason)
        }
        detail = try TicketDetail(from: decoder)
    }
}
