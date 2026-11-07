import Foundation

/// Conversation facts from one current view, as web/src/lib/navigation/knowledge.ts
/// accepts them. `nil` is unknown: it never becomes an idle or read fact.
struct ConversationKnowledge: Equatable, Sendable {
    let working: Bool?
    let awaitingAnswer: Bool?
    let messageForOwner: Bool?
    let lastTurnFailed: Bool?

    init(_ value: ViewConversationValue) {
        working = value.attention.current?.signal.working
        awaitingAnswer = value.attention.current?.awaitingAnswer
        messageForOwner = value.attention.durable.messageForOwner
        lastTurnFailed = value.attention.durable.lastTurnFailed
    }
}

/// A partial conversation signal: only known facts are present.
struct NavigationSignal: Equatable, Sendable {
    var working: Bool?
    var messageForOwner: Bool?
    init(_ knowledge: ConversationKnowledge?) { working = knowledge?.working; messageForOwner = knowledge?.messageForOwner }
}

struct NavigationTicket: Sendable {
    let id: String
    let title: String
    let workerType: String?
    let stage: String
    let ticketStatus: String
    let isBacklogged: Bool
    let blocked: Bool
    let terminal: Bool
    let createdAt: Double
    let activityAt: Double
    let conversationId: String?
    let signal: NavigationSignal
    let awaitingReply: Bool?
    let awaitingAnswer: Bool?
    let awaitingApproval: Bool
    let awaitingAgentApproval: Bool
    let assigned: Bool
    let agentState: String?
    let waitingToCloseout: Bool
    let gatingField: String?
    let recap: String
    let recapPreview: String
    let manager: Principal
    let managerId: String?
    let managerTitle: String?
    let projectId: String?
    let project: String?
}

struct NavigationManager: Sendable {
    let id: String
    let title: String
    let createdAt: Double
    let updatedAt: Double
    let settled: Bool
    let projectId: String
    let project: String?
    let recap: String
    let recapPreview: String
    let conversationId: String?
    let signal: NavigationSignal
    let awaitingReply: Bool?
    let awaitingAnswer: Bool?
    let agentState: String?
}

struct NavigationChief: Sendable {
    let label: String
    let conversationId: String?
    let signal: NavigationSignal
}

/// Review cards as web/src/lib/types.ts NavigationReviewItem: a Ticket card holds its
/// proposal and its unread conversations; other owners' messages stand alone.
struct NavigationReviewProposal: Equatable, Sendable {
    let ticketId: String
    let field: String
    let title: String
    let waitingSince: Double
    let proposalField: String
    let proposalRevision: Int
}
struct NavigationReviewMessage: Equatable, Sendable {
    let conversationId: String
    let addressId: String
    let addressCurrent: Bool
    let ownerKind: String
    let ownerId: String
    let title: String
    let waitingSince: Double
    let recap: String
    let ownerReadThroughSequence: Int
    var latestSequence = 0
}
enum NavigationReviewCard: Equatable, Sendable {
    case ticket(id: String, title: String, waitingSince: Double, proposal: NavigationReviewProposal?, messages: [NavigationReviewMessage])
    case message(NavigationReviewMessage)

    var waitingSince: Double {
        switch self { case .ticket(_, _, let waiting, _, _): waiting; case .message(let message): message.waitingSince }
    }
    /// The review window's member key for this card.
    var member: RecordKey {
        switch self {
        case .ticket(let id, _, _, _, _): RecordKey(kind: "ticket", id: id)
        case .message(let message): RecordKey(kind: "address", id: message.addressId)
        }
    }
    fileprivate var sortType: String { if case .message = self { "message" } else { "ticket" } }
    fileprivate var sortIdentity: String {
        switch self { case .ticket(let id, _, _, _, _): id; case .message(let message): message.conversationId }
    }
}

/// Port of web/src/lib/navigation/presentation.ts navigationWorkspacePresentation for
/// the facts the phone shows. One coherent view in, derived rows out; no reads.
struct NavigationPresentation: Sendable {
    let tickets: [NavigationTicket]
    let ticketsById: [String: NavigationTicket]
    let managers: [NavigationManager]
    let managersById: [String: NavigationManager]
    let chief: NavigationChief?
    let review: [NavigationReviewCard]

    init(_ records: [ViewRecord]) {
        var conversations: [String: ViewConversationValue] = [:]
        var knowledge: [String: ConversationKnowledge] = [:]
        var rawTickets: [(String, ViewTicketValue)] = []
        var rawTicketsById: [String: ViewTicketValue] = [:]
        var rawManagers: [(String, ViewManagerValue)] = []
        var rawManagersById: [String: ViewManagerValue] = [:]
        var projects: [String: String] = [:]
        var addresses: [(String, ViewAddressValue)] = []
        var chiefRecord: ViewChiefValue?
        var reviewTicketOrder: [String] = []
        var reviewTickets: [String: (title: String, waitingSince: Double, proposal: NavigationReviewProposal?, messages: [NavigationReviewMessage])] = [:]

        for record in records {
            switch record {
            case .conversation(let id, let value): conversations[id] = value; knowledge[id] = ConversationKnowledge(value)
            case .ticket(let id, let row):
                if rawTicketsById[id] == nil { rawTickets.append((id, row)) } else if let index = rawTickets.firstIndex(where: { $0.0 == id }) { rawTickets[index] = (id, row) }
                rawTicketsById[id] = row
                if row.integrityError == nil, !row.isBacklogged, row.ticketStatus == "awaiting_approval",
                   row.ceilingHolder == ViewPrincipal(kind: "owner", id: "owner"),
                   let gating = row.gatingField, let proposalField = row.proposalField, let created = row.proposalCreatedAt {
                    let proposal = NavigationReviewProposal(ticketId: id, field: gating, title: row.title, waitingSince: created,
                                                            proposalField: proposalField, proposalRevision: row.proposalRevision ?? 0)
                    if reviewTickets[id] == nil { reviewTicketOrder.append(id) }
                    reviewTickets[id] = (row.title, created, proposal, [])
                }
            case .manager(let id, let value):
                if rawManagersById[id] == nil { rawManagers.append((id, value)) } else if let index = rawManagers.firstIndex(where: { $0.0 == id }) { rawManagers[index] = (id, value) }
                rawManagersById[id] = value
            case .project(let id, let value): projects[id] = value.name
            case .chief(let value): chiefRecord = value
            case .address(let id, let value): addresses.append((id, value))
            case .other: break
            }
        }
        func known(_ conversation: String?) -> ConversationKnowledge? { conversation.flatMap { knowledge[$0] } }

        let tickets: [NavigationTicket] = rawTickets.map { id, row in
            let facts = known(row.conversationId)
            let parent = row.managerKind == "chief" ? nil : row.managerId
            let manager = parent.map { Principal(kind: "manager", id: $0) } ?? Principal(kind: "chief", id: "chief")
            let parentRow = parent.flatMap { rawManagersById[$0] }
            // A Ticket's Project is its parent's when it has one, as the web masthead joins it.
            let projectId = parent == nil ? row.projectId : parentRow?.projectId
            let project = projectId.flatMap { projects[$0] }
            if row.integrityError != nil {
                return NavigationTicket(id: id, title: row.title, workerType: row.workerType, stage: row.stage, ticketStatus: "errored",
                    isBacklogged: row.isBacklogged, blocked: false, terminal: false, createdAt: row.createdAt, activityAt: row.updatedAt,
                    conversationId: row.conversationId, signal: NavigationSignal(facts), awaitingReply: facts?.messageForOwner,
                    awaitingAnswer: facts?.awaitingAnswer, awaitingApproval: false, awaitingAgentApproval: false, assigned: false,
                    agentState: "errored", waitingToCloseout: false, gatingField: nil, recap: row.recap, recapPreview: row.recapPreview,
                    manager: manager, managerId: parent, managerTitle: parentRow?.title, projectId: row.projectId, project: project)
            }
            let owner = row.ceilingHolder == ViewPrincipal(kind: "owner", id: "owner")
            let parked = row.ticketStatus == "awaiting_approval"
            return NavigationTicket(id: id, title: row.title, workerType: row.workerType, stage: row.stage, ticketStatus: row.ticketStatus,
                isBacklogged: row.isBacklogged, blocked: row.blocked ?? false, terminal: row.terminal ?? false, createdAt: row.createdAt,
                activityAt: row.updatedAt, conversationId: row.conversationId, signal: NavigationSignal(facts),
                awaitingReply: facts?.messageForOwner, awaitingAnswer: facts?.awaitingAnswer,
                awaitingApproval: parked && owner, awaitingAgentApproval: parked && !owner, assigned: row.stageOwnership == "user",
                agentState: Self.agentState(status: row.ticketStatus, stage: row.stage, facts),
                waitingToCloseout: row.gatingField == "consequences" && row.ticketStatus == "empty", gatingField: row.gatingField,
                recap: row.recap, recapPreview: row.recapPreview, manager: manager, managerId: parent, managerTitle: parentRow?.title,
                projectId: row.projectId, project: project)
        }
        let managers: [NavigationManager] = rawManagers.compactMap { id, row in
            guard !row.isLean, let projectId = row.projectId else { return nil }
            let facts = known(row.conversationId)
            return NavigationManager(id: id, title: row.title, createdAt: row.createdAt, updatedAt: row.updatedAt, settled: row.settled,
                projectId: projectId, project: projects[projectId], recap: row.recap ?? "", recapPreview: row.recapPreview ?? "",
                conversationId: row.conversationId, signal: NavigationSignal(facts), awaitingReply: facts?.messageForOwner,
                awaitingAnswer: facts?.awaitingAnswer, agentState: Self.agentState(status: nil, stage: nil, facts))
        }
        let chief = chiefRecord.map { NavigationChief(label: $0.label, conversationId: $0.conversationId, signal: NavigationSignal(known($0.conversationId))) }

        // Exact address membership; current addresses win, then identity. The canonical
        // address is chosen before the owner filter and never falls back to a former owner.
        var reviewItems: [NavigationReviewCard] = []
        var reviewConversations: Set<String> = []
        let ordered = addresses.sorted { ($0.1.current ? 0 : 1, $0.0) < ($1.1.current ? 0 : 1, $1.0) }
        for (addressId, address) in ordered {
            guard !reviewConversations.contains(address.conversationId),
                  knowledge[address.conversationId]?.messageForOwner == true,
                  let conversation = conversations[address.conversationId],
                  let waiting = conversation.firstUnreadOwnerMessageAt else { continue }
            reviewConversations.insert(address.conversationId)
            let title: String
            let recap: String
            switch address.ownerKind {
            case "ticket":
                guard let owner = rawTicketsById[address.ownerId] else { continue }
                if owner.stage == "done" || owner.isBacklogged { continue }
                title = owner.title; recap = owner.recap
            case "manager":
                guard let owner = rawManagersById[address.ownerId] else { continue }
                if owner.settled { continue }
                title = owner.title; recap = owner.recap ?? ""
            default:
                guard let owner = chiefRecord else { continue }
                title = owner.label; recap = ""
            }
            let message = NavigationReviewMessage(conversationId: address.conversationId, addressId: addressId, addressCurrent: address.current,
                ownerKind: address.ownerKind, ownerId: address.ownerId, title: title, waitingSince: waiting, recap: recap,
                ownerReadThroughSequence: conversation.ownerReadThroughSequence, latestSequence: conversation.latestSequence ?? 0)
            if address.ownerKind == "ticket" {
                if reviewTickets[address.ownerId] == nil {
                    reviewTicketOrder.append(address.ownerId)
                    reviewTickets[address.ownerId] = (title, waiting, nil, [])
                }
                // A card represents the oldest thing waiting; the proposal keeps its own stamp.
                reviewTickets[address.ownerId]!.messages.append(message)
                reviewTickets[address.ownerId]!.waitingSince = min(reviewTickets[address.ownerId]!.waitingSince, waiting)
            } else {
                reviewItems.append(.message(message))
            }
        }
        reviewItems += reviewTicketOrder.map { id in
            let card = reviewTickets[id]!
            return .ticket(id: id, title: card.title, waitingSince: card.waitingSince, proposal: card.proposal, messages: card.messages)
        }
        review = reviewItems.sorted { ($0.waitingSince, $0.sortType, $0.sortIdentity) < ($1.waitingSince, $1.sortType, $1.sortIdentity) }

        self.tickets = tickets
        ticketsById = Dictionary(tickets.map { ($0.id, $0) }, uniquingKeysWith: { _, latest in latest })
        self.managers = managers
        managersById = Dictionary(managers.map { ($0.id, $0) }, uniquingKeysWith: { _, latest in latest })
        self.chief = chief
    }

    /// Existing agent-state precedence; unknown runtime never becomes idle.
    static func agentState(status: String?, stage: String?, _ facts: ConversationKnowledge?) -> String? {
        let working = facts?.working, failed = facts?.lastTurnFailed
        if working == true { return "working" }
        if status == "errored" { return "errored" }
        if failed == true && stage != "done" { return "errored" }
        return working == false && (failed == false || stage == "done") ? "idle" : nil
    }
}
