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
    let id: String
    let title: String
    let workerType: String?
    let stage: String
    let ticketStatus: String
    let isBacklogged: Bool
    let blocked: Bool?
    let recapPreview: String
    let waitingToCloseout: Bool?
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
    let agentState: String
    let conversationSignal: ConversationSignal?
    enum CodingKeys: String, CodingKey { case integrityError, locale, id, title, workerType, stage, ticketStatus, isBacklogged, blocked, recapPreview, waitingToCloseout, activityAt, projectId, project, manager, managerId, managerTitle, awaitingReply, awaitingAnswer, awaitingApproval, awaitingAgentApproval, assigned, agentState, conversationSignal }
}
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
        locale == "macos" && health?.activityKnown != true
    }
    static func accessibilityLabel(_ title: String, locale: String?, health: ExecutorHealth?) -> String {
        let machine = name(locale).map { ", runs on " + $0 } ?? ""
        return title + machine + (activityUnknown(locale, health: health) ? ", current activity on Mac is unknown" : "")
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
        id = try required(String.self, .id, broken: "")
        title = try required(String.self, .title, broken: "")
        workerType = try c.decodeIfPresent(String.self, forKey: .workerType)
        stage = try required(String.self, .stage, broken: "")
        ticketStatus = try required(String.self, .ticketStatus, broken: "")
        isBacklogged = try required(Bool.self, .isBacklogged, broken: false)
        blocked = try c.decodeIfPresent(Bool.self, forKey: .blocked)
        recapPreview = try required(String.self, .recapPreview, broken: issue?.reason ?? "")
        waitingToCloseout = try c.decodeIfPresent(Bool.self, forKey: .waitingToCloseout)
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
