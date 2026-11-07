import Foundation

/// Mounted screens name the windows they show, like the web owner's setDemand.
/// The phone's request is their union, bounded the way the web bounds it.
enum NavigationScope {
    static let reviewWindowLimit = 30
    /// Review is always in scope: it drives the tab badge and opens without a wait.
    static var baseWindows: [ViewWindowRequest] { [ViewWindowRequest(section: "review", limit: reviewWindowLimit)] }
    static let rowBudget = 256
    static let maximumWindowRows = 60

    /// Mirrors refreshDemand in web/src/lib/navigation/views/owner.ts. Parent lists and
    /// Review keep their pages; other windows share what remains one row per window per round.
    static func request(demands: [String: [ViewWindowRequest]], previous: ViewRequest) -> ViewRequest {
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

    /// The queue in review-window order, each card flattened to its native items.
    func reviewQueue(in snapshot: ViewSnapshot) -> ReviewResponse {
        let cards: [NavigationReviewCard]
        let window = snapshot.window("review")
        if let window {
            let byMember = Dictionary(review.map { ($0.member, $0) }, uniquingKeysWith: { first, _ in first })
            cards = window.members.compactMap { byMember[$0] }
        } else { cards = review }
        let items = cards.flatMap { card -> [ReviewItem] in
            switch card {
            case .message(let message): return [message.item]
            case .ticket(_, _, _, let proposal, let messages):
                return (proposal.map { [ReviewItem(reviewItemType: "proposal", ticketId: $0.ticketId, field: $0.field, title: $0.title, waitingSince: $0.waitingSince)] } ?? [])
                    + messages.map(\.item)
            }
        }
        return ReviewResponse(items: items, runningWorkerCount: snapshot.content.counts.runningConversations,
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
        TicketSummary(id: id, title: title, workerType: workerType, stage: stage, ticketStatus: ticketStatus, isBacklogged: isBacklogged,
                      blocked: blocked, recapPreview: recapPreview, waitingToCloseout: waitingToCloseout, activityAt: activityAt,
                      projectId: projectId, project: project, manager: manager, managerId: managerId, managerTitle: managerTitle,
                      awaitingReply: awaitingReply ?? false, awaitingAnswer: awaitingAnswer ?? false, awaitingApproval: awaitingApproval,
                      awaitingAgentApproval: awaitingAgentApproval, assigned: assigned, agentState: agentState ?? "idle",
                      conversationSignal: signal.conversationSignal)
    }
}
