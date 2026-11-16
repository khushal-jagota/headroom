import Foundation

struct Principal: Codable, Hashable, Sendable { let kind: String; let id: String }
struct ConversationSignal: Codable, Sendable { let working: Bool; let messageForOwner: Bool }
struct PageFacts: Codable, Sendable {
    let matchCount: Int
    let returnCount: Int
    let limit: Int
    let offset: Int
    let complete: Bool
    let nextOffset: Int?
}
struct TicketsResponse: Codable, Sendable { let tickets: [TicketSummary]; let page: PageFacts }
struct ManagersResponse: Codable, Sendable { let managers: [ManagerSummary]; let page: PageFacts }
struct TicketSummary: Codable, Identifiable, Sendable {
    var integrityError: TicketIntegrityIssue? = nil
    var locale: String? = nil
    var machineId: String? = nil
    let id: String
    let title: String
    let workerType: String?
    let stage: String
    let ticketStatus: String
    let isBacklogged: Bool
    let blocked: Bool?
    let recapPreview: String
    let activityAt: Double?
    let projectId: String?
    let project: String?
    let manager: Principal?
    let managerId: String?
    let managerTitle: String?
    let awaitingReply: Bool
    let awaitingAnswer: Bool
    let awaitingApproval: Bool
    let awaitingAgentApproval: Bool
    let assigned: Bool
    var agentState: String
    var conversationSignal: ConversationSignal?
    enum CodingKeys: String, CodingKey { case integrityError, locale, machineId, id, title, workerType, stage, ticketStatus, isBacklogged, blocked, recapPreview, activityAt, projectId, project, manager, managerId, managerTitle, awaitingReply, awaitingAnswer, awaitingApproval, awaitingAgentApproval, assigned, agentState, conversationSignal }
}
/// A detail read's worker row whose working and unread marks the navigation view keeps current.
protocol LiveMarkedWorker {
    var id: String { get }
    var agentState: String { get set }
    var conversationSignal: ConversationSignal? { get set }
}
extension TicketSummary: LiveMarkedWorker {}
struct ManagerSummary: Codable, Identifiable, Sendable {
    let id: String
    let title: String
    let projectId: String
    let project: String
    let settled: Bool?
    let createdAt: Double
    let updatedAt: Double
    let awaitingReply: Bool
    let awaitingAnswer: Bool
    let awaitingApproval: Bool
    let awaitingAgentApproval: Bool
    let agentState: String
    let conversationSignal: ConversationSignal?
}

// Read projection of /api/executor/health and the web's ticketMachines rules.
struct ExecutorHealth: Codable, Sendable {
    struct Identity: Codable, Sendable {
        let executorIncarnation: String
        let connectionGeneration: Int
    }
    let mode: String
    let identity: Identity?
    let connectionState: String
    let lastContactAtMs: Double?
    var activityKnown: Bool {
        mode == "remote" && connectionState == "ready" && (identity?.connectionGeneration ?? 0) > 0
    }
    var contactSentence: String {
        guard let lastContactAtMs else { return "Last contact: unknown." }
        return "Last contact: \(Date(timeIntervalSince1970: lastContactAtMs / 1000).formatted(date: .abbreviated, time: .standard)) · \(TimeZone.current.identifier)."
    }
}
enum TicketMachine {
    static func name(_ locale: String?) -> String? {
        switch locale { case "macos": "Mac"; case "vps": "VPS"; default: nil }
    }
    static func activityUnknown(_ locale: String?, health: ExecutorHealth?) -> Bool {
        switch locale {
        case "vps": false
        case "macos": health?.activityKnown != true
        default: true
        }
    }
    static func conversationActivityUnknown(_ locale: String?, owner: ConversationOwner, health: ExecutorHealth?) -> Bool {
        guard case .ticket = owner else { return false }
        return activityUnknown(locale, health: health)
    }
    static func unknownActivityText(_ locale: String?) -> String {
        locale == "macos" ? "Current activity on Mac is unknown." : "Current activity is unknown."
    }
    static func accessibilityLabel(_ title: String, locale: String?, health: ExecutorHealth?) -> String {
        let machine = name(locale).map { ", runs on " + $0 } ?? ""
        return title + machine + (activityUnknown(locale, health: health) ? (locale == "macos" ? ", current activity on Mac is unknown" : ", current activity is unknown") : "")
    }
    static func signal(_ signal: ConversationSignal?, locale: String?, health: ExecutorHealth?, completed: Bool = false) -> ConversationSignal? {
        signal.map { ConversationSignal(working: $0.working && !completed && !activityUnknown(locale, health: health), messageForOwner: $0.messageForOwner) }
    }
}
extension TicketSummary {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let issue = try c.decodeIfPresent(TicketIntegrityIssue.self, forKey: .integrityError)
        func required<T: Decodable>(_ type: T.Type, _ key: CodingKeys, broken: T) throws -> T {
            if issue != nil { return try c.decodeIfPresent(type, forKey: key) ?? broken }
            return try c.decode(type, forKey: key)
        }
        integrityError = issue
        locale = try c.decodeIfPresent(String.self, forKey: .locale)
        machineId = try c.decodeIfPresent(String.self, forKey: .machineId)
        id = try required(String.self, .id, broken: "")
        title = try required(String.self, .title, broken: "")
        workerType = try c.decodeIfPresent(String.self, forKey: .workerType)
        stage = try required(String.self, .stage, broken: "")
        ticketStatus = try required(String.self, .ticketStatus, broken: "")
        isBacklogged = try required(Bool.self, .isBacklogged, broken: false)
        blocked = try c.decodeIfPresent(Bool.self, forKey: .blocked)
        recapPreview = try required(String.self, .recapPreview, broken: issue?.reason ?? "")
        activityAt = try c.decodeIfPresent(Double.self, forKey: .activityAt)
        projectId = try c.decodeIfPresent(String.self, forKey: .projectId)
        project = try c.decodeIfPresent(String.self, forKey: .project)
        manager = try c.decodeIfPresent(Principal.self, forKey: .manager)
        managerId = try c.decodeIfPresent(String.self, forKey: .managerId)
        managerTitle = try c.decodeIfPresent(String.self, forKey: .managerTitle)
        awaitingReply = try required(Bool.self, .awaitingReply, broken: false)
        awaitingAnswer = try required(Bool.self, .awaitingAnswer, broken: false)
        awaitingApproval = try required(Bool.self, .awaitingApproval, broken: false)
        awaitingAgentApproval = try required(Bool.self, .awaitingAgentApproval, broken: false)
        assigned = try required(Bool.self, .assigned, broken: false)
        agentState = try required(String.self, .agentState, broken: "errored")
        conversationSignal = try c.decodeIfPresent(ConversationSignal.self, forKey: .conversationSignal)
    }
}


/// Read subset of the existing machine catalogue, shared by Workspace rows and previews.
struct WorkspaceMachinesResponse: Codable, Sendable {
    let machines: [WorkspaceMachine]
}

struct WorkspaceMachine: Codable, Identifiable, Sendable {
    let machineId: String
    let name: String
    let icon: String
    let color: String
    let isHome: Bool
    let available: Bool
    let runningWorkers: Int?
    let waitingTickets: Int
    var id: String { machineId }
}

/// Current machine-scoped health contract. Legacy health callers keep their own DTO.
struct WorkspaceMachineHealth: Codable, Sendable {
    struct Refusal: Codable, Sendable { let reason: String }
    let mode: String
    let presence: String?
    let sinceMs: Double?
    let lastContactAtMs: Double?
    let refusal: Refusal?

    var activityKnown: Bool { mode == "remote" && presence == "present" }

    /// Mirrors macTicketActivityLabel: durable agent ownership survives an absent live turn.
    func activityLabel(for ticket: TicketSummary) -> String? {
        guard ticket.locale == "macos" else { return nil }
        if presence == "refused" { return refusal?.reason }
        guard ["asleep", "not_heard_from", "never"].contains(presence ?? "") else { return nil }
        if ticket.conversationSignal?.working == true { return "Paused: the Mac is away" }
        return ticket.ticketStatus == "agent" || ticket.agentState == "working" ? "Waiting for the Mac" : nil
    }

    static func signal(for ticket: TicketSummary, health: WorkspaceMachineHealth?) -> ConversationSignal? {
        let known: Bool
        switch ticket.locale {
        case "vps": known = true
        case "macos": known = health?.activityKnown == true
        default: known = false
        }
        return ticket.conversationSignal.map {
            ConversationSignal(working: $0.working && ticket.stage != "done" && ticket.stage != "cancelled" && known,
                               messageForOwner: $0.messageForOwner)
        }
    }
}
