import Foundation

/// Mounted screens name the windows they show, like the web owner's setDemand.
/// The phone's request is their union, bounded the way the web bounds it.
enum NavigationScope {
    static let reviewWindowLimit = 30
    /// Review is always in scope: it drives the tab badge and opens without a wait.
    static var baseWindows: [ViewWindowRequest] { [ViewWindowRequest(section: "review", limit: reviewWindowLimit)] }
    static let rowBudget = 256
    /// A detail page's children window, whose members key its reloads. A manager's
    /// children come most recently updated first; Chief's oldest first, so Chief follows
    /// its active page, as its web child page does.
    static func children(of owner: ViewOwner) -> ViewWindowRequest {
        var window = ViewWindowRequest(section: "children", owner: owner, limit: owner.kind == "chief" ? 30 : maximumWindowRows)
        if owner.kind == "chief" { window.childFilter = "active" }
        return window
    }
    static let maximumWindowRows = 60

    /// Mirrors refreshDemand in web/src/lib/navigation/views/owner.ts. Parent lists and
    /// Review keep their pages; other windows share what remains one row per window per round.
    /// `selected` is the one owner whose record a detail screen keys its reloads on.
    static func request(demands: [String: [ViewWindowRequest]], selected: ViewOwner? = nil, previous: ViewRequest) -> ViewRequest {
        var windows: [ViewWindowRequest] = []
        var position: [String: Int] = [:]
        for window in baseWindows + demands.keys.sorted().flatMap({ demands[$0]! }) {
            var window = window
            window.limit = min(max(0, window.limit), maximumWindowRows)
            if let index = position[window.identity] { windows[index] = window }
            else { position[window.identity] = windows.count; windows.append(window) }
        }
        func stable(_ window: ViewWindowRequest) -> Bool {
            // Review is always mounted on the phone, beside any other screen; its page
            // keeps its size rather than shrinking as Workspace cards appear.
            window.section == "review" || window.section == "rail_parents" || window.section == "settled"
                || (window.section == "children" && window.owner?.kind == "chief")
        }
        let desired = windows.map(\.limit)
        let stableIndexes = windows.indices.filter { stable(windows[$0]) }
        var fixed = stableIndexes.reduce(0) { $0 + windows[$1].limit }
        if fixed > rowBudget {
            for index in stableIndexes { windows[index].limit = 0 }
            fill(&windows, stableIndexes, desired: desired, available: rowBudget)
            fixed = stableIndexes.reduce(0) { $0 + windows[$1].limit }
        }
        let elastic = windows.indices.filter { !stable(windows[$0]) && windows[$0].limit > 0 }
        for index in elastic { windows[index].limit = 0 }
        fill(&windows, elastic, desired: desired, available: max(0, rowBudget - fixed))
        var next = previous
        next.windows = windows
        next.selected = selected
        guard next != previous else { return previous }
        next.scopeGeneration = previous.scopeGeneration + 1
        return next
    }

    private static func fill(_ windows: inout [ViewWindowRequest], _ indexes: [Int], desired: [Int], available: Int) {
        var remaining = available
        for _ in 0..<maximumWindowRows {
            for index in indexes where remaining > 0 && windows[index].limit < desired[index] {
                windows[index].limit += 1; remaining -= 1
            }
        }
    }
}

// MARK: Reload identities

extension ViewRecord {
    /// The facts a detail read shows for this record. Conversation marks are left out:
    /// screens take them from the view, so a working or unread change needs no detail read.
    var stamp: String? {
        switch self {
        case .ticket(_, let t):
            let holder = t.ceilingHolder.map { $0.kind + ":" + $0.id } ?? ""
            let parts: [String] = [String(t.updatedAt), t.title, t.recapPreview, t.stage, t.ticketStatus, String(t.isBacklogged),
                                   String(t.blocked ?? false), t.proposalField ?? "", String(t.proposalRevision ?? -1),
                                   String(t.proposalCreatedAt ?? 0), t.gatingField ?? "", t.stageOwnership ?? "", holder,
                                   t.conversationId ?? "", t.managerId ?? "", t.integrityError?.reason ?? ""]
            return parts.joined(separator: "\u{1f}")
        case .manager(_, let m):
            return [String(m.updatedAt), m.title, String(m.settled), m.recapPreview ?? "", m.conversationId ?? "", m.projectId ?? ""].joined(separator: "\u{1f}")
        case .chief(let c):
            return [c.label, c.conversationId ?? ""].joined(separator: "\u{1f}")
        default: return nil
        }
    }
}

// MARK: Existing native models from one view

extension NavigationSignal {
    /// The phone's two-fact mark; absent when neither fact is known.
    var conversationSignal: ConversationSignal? {
        working == nil && messageForOwner == nil ? nil : ConversationSignal(working: working ?? false, messageForOwner: messageForOwner ?? false)
    }
}

extension NavigationPresentation {
    /// Workspace cards come from the rail windows; nil when this view has none.
    func workspaceManagers(in snapshot: ViewSnapshot) -> [ManagerSummary]? {
        guard let window = snapshot.window("rail_parents") else { return nil }
        return window.members.compactMap { managersById[$0.id] }.map { manager in
            ManagerSummary(id: manager.id, title: manager.title, projectId: manager.projectId, project: manager.project ?? "",
                           settled: manager.settled, createdAt: manager.createdAt, updatedAt: manager.updatedAt,
                           awaitingReply: manager.awaitingReply ?? false, awaitingAnswer: manager.awaitingAnswer ?? false,
                           awaitingApproval: false, awaitingAgentApproval: false, agentState: manager.agentState ?? "idle",
                           conversationSignal: manager.signal.conversationSignal)
        }
    }

    /// Membership comes from each parent's rail window, never from rows another window loaded.
    func workspaceTickets(in snapshot: ViewSnapshot) -> [TicketSummary]? {
        guard snapshot.window("rail_parents") != nil else { return nil }
        var seen: Set<String> = []
        return snapshot.content.windows.filter { $0.request.section == "rail_tickets" && $0.request.group == "all" }
            .flatMap(\.members).compactMap { key in
                guard seen.insert(key.id).inserted, let ticket = ticketsById[key.id] else { return nil }
                return ticket.summary
            }
    }

    /// The queue in review-window order. A Ticket card stays one entry with its parts.
    func reviewQueue(in snapshot: ViewSnapshot) -> ReviewResponse {
        let cards: [NavigationReviewCard]
        let window = snapshot.window("review")
        if let window {
            let byMember = Dictionary(review.map { ($0.member, $0) }, uniquingKeysWith: { first, _ in first })
            cards = window.members.compactMap { byMember[$0] }
        } else { cards = review }
        let queue = cards.map { card -> ReviewCard in
            switch card {
            case .message(let message): return ReviewCard(id: message.item.id, parts: [message.item])
            case .ticket(let id, _, _, let proposal, let messages):
                let parts = (proposal.map { [ReviewItem(reviewItemType: "proposal", ticketId: $0.ticketId, field: $0.field, title: $0.title, waitingSince: $0.waitingSince, proposalRevision: $0.proposalRevision)] } ?? [])
                    + messages.map(\.item)
                return ReviewCard(id: "ticket:\(id)", ticketId: id, parts: parts)
            }
        }
        return ReviewResponse(cards: queue, runningWorkerCount: snapshot.content.counts.runningConversations,
                              unloadedCount: window.map { max(0, $0.total - $0.members.count) } ?? 0)
    }
}

extension NavigationReviewMessage {
    var item: ReviewItem {
        var item = ReviewItem(reviewItemType: "message", title: title, waitingSince: waitingSince, conversationId: conversationId,
                              ownerKind: ownerKind, ownerId: ownerId, ownerReadThroughSequence: ownerReadThroughSequence, addressCurrent: addressCurrent)
        item.addressId = addressId
        item.recap = recap
        return item
    }
}

extension NavigationTicket {
    var summary: TicketSummary {
        TicketSummary(locale: locale, id: id, title: title, workerType: workerType, stage: stage, ticketStatus: ticketStatus, isBacklogged: isBacklogged,
                      blocked: blocked, recapPreview: recapPreview, waitingToCloseout: waitingToCloseout, activityAt: activityAt,
                      projectId: projectId, project: project, manager: manager, managerId: managerId, managerTitle: managerTitle,
                      awaitingReply: awaitingReply ?? false, awaitingAnswer: awaitingAnswer ?? false, awaitingApproval: awaitingApproval,
                      awaitingAgentApproval: awaitingAgentApproval, assigned: assigned, agentState: agentState ?? "idle",
                      conversationSignal: signal.conversationSignal)
    }
}
