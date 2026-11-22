import Foundation
import Observation

/// A refused decision. Its card comes back to the front with the server's reason and the
/// saved note, even when its proposal no longer waits in the window.
struct ReviewRefusal: Equatable, Sendable {
    let item: ReviewItem
    let note: String
    /// The outbox summary that was refused; each refusal comes to the front once.
    let summaryID: String
}

/// A proposal that moved to another reviewer while it was on screen.
struct ReviewNotice: Equatable, Sendable {
    let item: ReviewItem
    /// Who reviews it now, as the card says it.
    let reviewer: String
}

/// The summary screen's words, from the phone's own cards.
struct ReviewSummary: Equatable, Sendable {
    let newCount: Int
    let skippedCount: Int
    var headline: String {
        if newCount > 0 && skippedCount > 0 { return "\(newCount + skippedCount) to review." }
        if newCount > 0 { return "\(newCount) new." }
        if skippedCount > 0 { return "\(skippedCount) skipped." }
        return "All clear."
    }
    var detail: String {
        if newCount > 0 && skippedCount > 0 { return "\(newCount) new, \(skippedCount) skipped." }
        if newCount > 0 { return "Ready to review." }
        if skippedCount > 0 { return "Nothing new." }
        return "Nothing to review."
    }
    /// The return button's title; nil when nothing waits.
    var returnTitle: String? {
        if newCount > 0 { return "Back to review" }
        return skippedCount > 0 ? "Review skipped" : nil
    }
}

/// Review's queue on the phone, one card per purpose. Every rule here is a function of
/// the latest review window and what happened on this phone during this app run.
///
/// The card on screen is always in the latest window: it leaves when the window drops
/// it, so nothing read or handled elsewhere stays on screen. Three things hold a card
/// that the window no longer has: its own exit while it runs, a refused decision, and a
/// proposal that moved to another reviewer while on screen (a notice until Skip).
struct ReviewQueue: Equatable, Sendable {
    /// The summary screen is up. The app opens on it.
    private(set) var summary = true
    /// The card on screen, kept by identity: a later window does not replace it.
    private(set) var pinned: ReviewItem?
    /// The card running its exit after Khushal handled it here.
    private(set) var leaving: ReviewItem?
    private(set) var notice: ReviewNotice?
    /// Cards skipped this run. Skip is local, like the web.
    private(set) var skipped: Set<String> = []
    /// Entries whose every card was skipped, and the entries "Back to review" sends last.
    private(set) var history = ReviewHistory()
    /// Cards handled here, hidden until the saved work or the window catches up, so a
    /// handled card is never shown again before then. A refusal brings it back.
    private(set) var handled: Set<String> = []
    private(set) var seenRefusals: Set<String> = []
    /// The card on screen when the app went to the background, and the connection
    /// session then. The first fresh window of a later session decides whether it is still there.
    private(set) var away: (cardID: String, session: Int)?
    /// The last time Review became the destination that this queue has seen.
    private(set) var seenVisit: Int?

    static func == (lhs: ReviewQueue, rhs: ReviewQueue) -> Bool {
        lhs.summary == rhs.summary && lhs.pinned == rhs.pinned && lhs.leaving == rhs.leaving && lhs.notice == rhs.notice
            && lhs.skipped == rhs.skipped && lhs.history == rhs.history && lhs.handled == rhs.handled
            && lhs.seenRefusals == rhs.seenRefusals && lhs.away?.cardID == rhs.away?.cardID && lhs.away?.session == rhs.away?.session
            && lhs.seenVisit == rhs.seenVisit
    }

    /// The queue in order: refused decisions first, then the window's cards, with the
    /// entries "Back to review" returned placed last, as the server places them.
    func cards(_ live: [ReviewItem], refusals: [ReviewRefusal]) -> [ReviewItem] {
        let refusedIDs = Set(refusals.map(\.item.id))
        let window = live.filter { !handled.contains($0.id) && !refusedIDs.contains($0.id) }
        let returned = Set(history.priority)
        let ordered = window.filter { !returned.contains($0.historyKey) } + window.filter { returned.contains($0.historyKey) }
        var seen: Set<String> = []
        return (refusals.map(\.item) + ordered).filter { seen.insert($0.id).inserted }
    }

    /// The card on screen; nil is the summary.
    var shown: ReviewItem? {
        guard !summary else { return nil }
        return leaving ?? notice?.item ?? pinned
    }

    /// Counts from the phone's own cards: the loaded window's, and every entry beyond it as new.
    func counts(_ cards: [ReviewItem], unloaded: Int) -> ReviewSummary {
        let skippedCount = cards.filter { skipped.contains($0.id) }.count
        return ReviewSummary(newCount: cards.count - skippedCount + unloaded, skippedCount: skippedCount)
    }

    /// Brings the queue up to the latest window.
    /// - `moved`: who reviews a proposal now, when it waits for someone else.
    /// - `freshSession`: the connection session whose first fresh window this is, if any.
    mutating func reconcile(_ live: [ReviewItem], refusals: [ReviewRefusal], moved: (ReviewItem) -> String?, freshSession: Int?) {
        let liveIDs = Set(live.map(\.id))
        handled.formIntersection(liveIDs)
        let cards = cards(live, refusals: refusals)
        if let away, let freshSession, freshSession > away.session {
            self.away = nil
            // A card that left while the app was away gives way to the summary.
            if !summary && !cards.contains(where: { $0.id == away.cardID }) { showSummary() }
        }
        // A refusal that lands during an exit comes forward once the exit ends.
        if leaving != nil { return }
        let newRefusal = refusals.first { !seenRefusals.contains($0.summaryID) }
        seenRefusals.formUnion(refusals.map(\.summaryID))
        if summary { return }
        if let newRefusal {
            notice = nil
            pinned = newRefusal.item
            return
        }
        if let current = notice {
            if let back = cards.first(where: { $0.id == current.item.id }) { notice = nil; pinned = back; return }
            if let reviewer = moved(current.item) { notice = ReviewNotice(item: current.item, reviewer: reviewer); return }
            notice = nil; pinned = nil
        }
        if let current = pinned {
            if let fresh = cards.first(where: { $0.id == current.id }), !skipped.contains(current.id) { pinned = fresh; return }
            if current.isProposal, !cards.contains(where: { $0.id == current.id }), let reviewer = moved(current) {
                notice = ReviewNotice(item: current, reviewer: reviewer)
                pinned = nil
                return
            }
        }
        pinned = cards.first { !skipped.contains($0.id) }
        if pinned == nil { showSummary() }
    }

    private mutating func showSummary() {
        summary = true; pinned = nil; notice = nil; away = nil
    }

    /// Skip is per card. When every card of one Ticket or conversation is skipped, its key
    /// joins the skipped history the server is told about.
    mutating func skip(_ item: ReviewItem, in cards: [ReviewItem]) {
        skipped.insert(item.id)
        away = nil
        let key = item.historyKey
        let entry = cards.filter { $0.historyKey == key }
        if !entry.isEmpty, entry.allSatisfy({ skipped.contains($0.id) }), !history.skipped.contains(key) {
            history.skipped.append(key)
        }
        if notice?.item.id == item.id { notice = nil }
        if pinned?.id == item.id { pinned = nil }
    }

    /// Handled here: the card stays only while its exit runs.
    mutating func resolve(_ item: ReviewItem) {
        leaving = item
        away = nil
        handled.insert(item.id)
    }
    /// The exit finished. The next reconcile shows the next card.
    mutating func finishExit() {
        if let leaving, pinned?.id == leaving.id { pinned = nil }
        if let leaving, notice?.item.id == leaving.id { notice = nil }
        leaving = nil
    }

    /// "Back to review" and "Review skipped", as the web returns: every skipped card is
    /// offered again, and the skipped entries go last.
    mutating func returnToCards() {
        summary = false
        away = nil
        skipped = []
        history.priority = history.skipped
        pinned = nil; notice = nil
    }

    /// Each time Review becomes the destination again, it opens on the summary.
    mutating func visit(_ visit: Int) {
        if let seenVisit, seenVisit != visit { showSummary() }
        seenVisit = visit
    }

    /// The app went to the background during `session`.
    mutating func leaveApp(session: Int) {
        away = shown.map { ($0.id, session) }
    }
}

/// Review's queue and loaded cards. The shell owns it and keeps it current, so skips,
/// the shown card and the cards read ahead survive a visit to another destination and
/// stay ready offline. A new server starts a new session.
@MainActor @Observable final class ReviewSession {
    private(set) var queue = ReviewQueue()
    /// The queue in order, as the summary and the read-ahead see it.
    private(set) var cards: [ReviewItem] = []
    private(set) var unloadedCount = 0
    private(set) var runningWorkerCount = 0
    /// Each refused card's reason, by card.
    private(set) var refusalNotes: [String: String] = [:]
    @ObservationIgnored let decisions = ReviewProposalDecisions()
    @ObservationIgnored let messageDecisions = ReviewMessageDecisions()
    @ObservationIgnored let askDecisions = ReviewAskDecisions()
    @ObservationIgnored private var live: [ReviewItem] = []
    @ObservationIgnored private var refusals: [ReviewRefusal] = []
    @ObservationIgnored private var moved: (ReviewItem) -> String? = { _ in nil }

    /// Counts finished exits. A card that comes back under the same identity, such as a
    /// refused reply, is a fresh card on screen, not the one that faded out.
    private(set) var exits = 0
    var shown: ReviewItem? { queue.shown }
    var notice: ReviewNotice? { queue.notice }
    var summary: ReviewSummary { queue.counts(cards, unloaded: unloadedCount) }
    /// The window has been read at least once, so the summary's counts mean something.
    private(set) var loaded = false

    /// The latest window and saved work. `freshSession` is the store's connection session
    /// once its stream follows changes.
    func update(store: AppStore, savedWork: ReviewSavedWork, freshSession: Int?) {
        let server = LocalStore.server(store.baseURL)
        let decided = savedWork.decisions(server)
        live = ReviewSavedWork.cards(store.review?.items ?? [], decisions: decided)
        let view = store.view
        let presentation = store.presentation
        refusals = ReviewSavedWork.refusals(decided, live: store.review?.items ?? [], title: { summary in
            presentation?.ticketsById[summary.ticketID ?? ""]?.title ?? Outbox.shared.work(server, id: summary.id)?.ticketTitle
        }, waits: { item in view.flatMap { presentation?.proposalWaits(item, in: $0) } ?? nil })
        refusalNotes = Dictionary(refusals.map { ($0.item.id, $0.note) }, uniquingKeysWith: { first, _ in first })
        moved = { item in view.flatMap { presentation?.movedReviewer(for: item, in: $0) } }
        unloadedCount = store.review?.unloadedCount ?? 0
        runningWorkerCount = store.review?.runningWorkerCount ?? 0
        loaded = store.review != nil
        // A refused decision's card acts again on a fresh read of its proposal.
        for refusal in refusals { decisions.dropResolved(refusal.item.id) }
        reconcile(freshSession: freshSession)
        store.setReviewHistory(queue.history)
    }

    private func reconcile(freshSession: Int? = nil) {
        queue.reconcile(live, refusals: refusals, moved: moved, freshSession: freshSession)
        cards = queue.cards(live, refusals: refusals)
    }

    func skip(_ item: ReviewItem, store: AppStore) {
        queue.skip(item, in: cards)
        // A card offered again reads its run again, as a freshly opened card does.
        if item.isMessage { messageDecisions.drop(item.id) }
        reconcile()
        store.setReviewHistory(queue.history)
    }
    func resolve(_ item: ReviewItem) { queue.resolve(item) }
    func finishExit() {
        if let item = queue.leaving, item.isMessage { messageDecisions.drop(item.id) }
        queue.finishExit()
        exits += 1
        reconcile()
    }
    func returnToCards(store: AppStore) {
        queue.returnToCards()
        for item in cards where item.isMessage { messageDecisions.drop(item.id) }
        reconcile()
        store.setReviewHistory(queue.history)
    }
    func visit(_ visit: Int) { queue.visit(visit); reconcile() }
    func leaveApp(session: Int) { queue.leaveApp(session: session) }

    /// The shown card and the next few waiting ones, which are read ahead.
    func upcoming(_ count: Int) -> [ReviewItem] {
        let shown = self.shown
        let waiting = cards.filter { !queue.skipped.contains($0.id) && $0.id != shown?.id }
        return Array(((shown.map { [$0] } ?? []) + waiting).prefix(count))
    }

    /// Drops loaded cards that left the queue.
    func retainLoaded() {
        let ids = Set(cards.map(\.id)).union([shown?.id].compactMap { $0 })
        decisions.retain(ids)
        messageDecisions.retain(ids)
        askDecisions.retain(ids)
    }
}

/// Proposal decisions keyed by card, shared by the visible card and the read-ahead.
/// Plain storage: creating one while the view renders publishes nothing.
@MainActor final class ReviewProposalDecisions {
    private var decisions: [String: ReviewDecision] = [:]
    func decision(for item: ReviewItem) -> ReviewDecision {
        if let existing = decisions[item.id] { return existing }
        let decision = ReviewDecision()
        decisions[item.id] = decision
        return decision
    }
    var all: [ReviewDecision] { Array(decisions.values) }
    func dropResolved(_ id: String) { if decisions[id]?.resolved == true { decisions[id] = nil } }
    func retain(_ ids: Set<String>) { decisions = decisions.filter { ids.contains($0.key) } }
}

/// Message decisions keyed by card, shared by the visible card and the read-ahead.
/// Plain storage: creating one while the view renders publishes nothing.
@MainActor final class ReviewMessageDecisions {
    private var decisions: [String: ReviewMessageDecision] = [:]
    func decision(for item: ReviewItem, savedWork: ReviewSavedWork) -> ReviewMessageDecision {
        if let existing = decisions[item.id] { return existing }
        let decision = ReviewMessageDecision(refreshSavedWork: savedWork.refresh)
        decisions[item.id] = decision
        return decision
    }
    func refreshAttempts() { for decision in decisions.values { decision.refreshAttempt() } }
    func retain(_ ids: Set<String>) { decisions = decisions.filter { ids.contains($0.key) } }
    func drop(_ id: String) { decisions[id] = nil }
}

/// Ask decisions keyed by card, shared by the visible card and the read-ahead.
@MainActor final class ReviewAskDecisions {
    private var decisions: [String: ReviewAskDecision] = [:]
    func decision(for item: ReviewItem) -> ReviewAskDecision {
        if let existing = decisions[item.id] { return existing }
        let decision = ReviewAskDecision()
        decisions[item.id] = decision
        return decision
    }
    func retain(_ ids: Set<String>) { decisions = decisions.filter { ids.contains($0.key) } }
}
