import Foundation

/// Mounted screens name the windows they show, like the web owner's setDemand.
/// The phone's request is their union, bounded the way the web bounds it.
/// Review's history for one app run, as web/src/routes/ReviewRoute.svelte keeps it:
/// the entries whose every card was skipped, and the ones "Back to review" sends last.
struct ReviewHistory: Equatable, Sendable {
    var skipped: [RecordKey] = []
    var priority: [RecordKey] = []
}

extension ReviewItem {
    /// The review-window entry this card belongs to, as the server keys Review history:
    /// a Ticket, or a Manager's or the Chief's conversation.
    var historyKey: RecordKey {
        if let ticket = owningTicketID { return RecordKey(kind: "ticket", id: ticket) }
        return RecordKey(kind: "conversation", id: conversationId ?? "")
    }
}

enum NavigationScope {
    static let reviewWindowLimit = 30
    /// Review is always in scope: it drives the Sidebar badge and opens without a wait.
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
    /// `reviewHistory` is Review's skipped and returned entries, as the web sends them.
    static func request(demands: [String: [ViewWindowRequest]], selected: ViewOwner? = nil, reviewHistory: ReviewHistory = ReviewHistory(), previous: ViewRequest) -> ViewRequest {
        var windows: [ViewWindowRequest] = []
        var position: [String: Int] = [:]
        for window in baseWindows + demands.keys.sorted().flatMap({ demands[$0]! }) {
            var window = window
            window.limit = min(max(0, window.limit), maximumWindowRows)
            if let index = position[window.identity] { windows[index] = window }
            else { position[window.identity] = windows.count; windows.append(window) }
        }
        func stable(_ window: ViewWindowRequest) -> Bool {
            // Review and the Sidebar's lists are always in scope on the phone; their pages
            // keep their size rather than shrinking as other screens add windows.
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
        next.reviewSkippedKeys = reviewHistory.skipped
        next.reviewPriorityKeys = reviewHistory.priority
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

    /// A settled window can carry lean rows. Its exact member order owns this list.
    func workspaceSettledManagers(in snapshot: ViewSnapshot) -> [ManagerSummary]? {
        guard let window = snapshot.window("settled") else { return nil }
        var rows: [String: ViewManagerValue] = [:]
        var knowledge: [String: ConversationKnowledge] = [:]
        for record in snapshot.content.records {
            switch record {
            case .manager(let id, let row): rows[id] = row
            case .conversation(let id, let value): knowledge[id] = ConversationKnowledge(value)
            default: break
            }
        }
        return window.members.compactMap { member in
            guard member.kind == "manager", let row = rows[member.id], row.settled else { return nil }
            let facts = row.conversationId.flatMap { knowledge[$0] }
            return ManagerSummary(id: member.id, title: row.title, projectId: row.projectId ?? "", project: "",
                                  settled: true, createdAt: row.createdAt, updatedAt: row.updatedAt,
                                  awaitingReply: facts?.messageForOwner ?? false, awaitingAnswer: facts?.awaitingAnswer ?? false,
                                  awaitingApproval: false, awaitingAgentApproval: false, agentState: "idle",
                                  conversationSignal: NavigationSignal(facts).conversationSignal)
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

    /// The queue in review-window order, one card per purpose: each entry's proposal,
    /// then its unread runs, then its ask. A saved view knows no runtime facts, so a
    /// member with nothing else waiting is its owner's ask.
    func reviewQueue(in snapshot: ViewSnapshot) -> ReviewResponse {
        let entries: [NavigationReviewEntry]
        let window = snapshot.window("review")
        if let window {
            let byMember = Dictionary(review.map { ($0.member, $0) }, uniquingKeysWith: { first, _ in first })
            entries = window.members.compactMap { member in
                byMember[member] ?? reviewAskCandidates[member].map { NavigationReviewEntry(member: member, waitingSince: $0.waitingSince, ask: $0) }
            }
        } else { entries = review }
        let items = entries.flatMap { entry -> [ReviewItem] in
            (entry.proposal.map { [ReviewItem(reviewItemType: "proposal", ticketId: $0.ticketId, field: $0.field, title: $0.title, waitingSince: $0.waitingSince, proposalRevision: $0.proposalRevision)] } ?? [])
                + entry.messages.map(\.item) + (entry.ask.map { [$0.item] } ?? [])
        }
        return ReviewResponse(items: items, runningWorkerCount: snapshot.content.counts.runningConversations,
                              unloadedCount: window.map { max(0, $0.total - $0.members.count) } ?? 0)
    }
}

extension NavigationPresentation {
    /// Who reviews a proposal now, when the view shows it still waiting on the same
    /// revision but held by someone other than the owner. Nil when it no longer waits.
    func movedReviewer(for item: ReviewItem, in snapshot: ViewSnapshot) -> String? {
        guard item.isProposal, proposalWaits(item, in: snapshot) == true else { return nil }
        var row: ViewTicketValue?
        var managerTitles: [String: String] = [:]
        for record in snapshot.content.records {
            switch record {
            case .ticket(let id, let value) where id == item.ticketId: row = value
            case .manager(let id, let value): managerTitles[id] = value.title
            default: break
            }
        }
        guard let holder = row?.ceilingHolder, holder != ViewPrincipal(kind: "owner", id: "owner") else { return nil }
        switch holder.kind {
        case "chief": return chief?.label ?? "Chief of Staff"
        case "manager": return managerTitles[holder.id] ?? "the Manager"
        default: return "the Worker"
        }
    }
}

extension NavigationPresentation {
    /// Whether this exact proposal still waits for a decision, from anyone. Nil when the
    /// view does not carry its Ticket.
    func proposalWaits(_ item: ReviewItem, in snapshot: ViewSnapshot) -> Bool? {
        guard item.isProposal else { return nil }
        for case .ticket(let id, let row) in snapshot.content.records where id == item.ticketId {
            return row.integrityError == nil && !row.isBacklogged && row.ticketStatus == "awaiting_approval"
                && row.proposalCreatedAt == item.waitingSince && (item.proposalRevision.map { $0 == row.proposalRevision } ?? true)
        }
        return nil
    }
}

extension NavigationReviewMessage {
    var item: ReviewItem {
        var item = ReviewItem(reviewItemType: "message", title: title, waitingSince: waitingSince, conversationId: conversationId,
                              ownerKind: ownerKind, ownerId: ownerId, ownerReadThroughSequence: ownerReadThroughSequence, addressCurrent: addressCurrent)
        item.addressId = addressId
        item.recap = recap
        item.latestOwnerMessageSequence = latestOwnerMessageSequence
        return item
    }
}

extension NavigationReviewAsk {
    var item: ReviewItem {
        var item = ReviewItem(reviewItemType: "ask", title: title, waitingSince: waitingSince, conversationId: conversationId,
                              ownerKind: ownerKind, ownerId: ownerId)
        item.recap = recap
        item.pendingAsk = pendingAsk?.state
        return item
    }
}

extension NavigationTicket {
    var summary: TicketSummary {
        TicketSummary(locale: locale, machineId: machineId, id: id, title: title, workerType: workerType, stage: stage, ticketStatus: ticketStatus, isBacklogged: isBacklogged,
                      blocked: blocked, recapPreview: recapPreview, activityAt: activityAt,
                      projectId: projectId, project: project, manager: manager, managerId: managerId, managerTitle: managerTitle,
                      awaitingReply: awaitingReply ?? false, awaitingAnswer: awaitingAnswer ?? false, awaitingApproval: awaitingApproval,
                      awaitingAgentApproval: awaitingAgentApproval, assigned: assigned, agentState: agentState ?? "idle",
                      conversationSignal: signal.conversationSignal)
    }
}
