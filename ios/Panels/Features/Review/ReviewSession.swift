import Foundation
import Observation

/// A refused decision, reply or mark read. Its card comes back to the front with the
/// server's reason and the saved note.
struct ReviewRefusal: Equatable, Sendable {
    let item: ReviewItem
    let note: String
    /// One refused attempt; each comes to the front once.
    let refusalID: String
}

/// A proposal that moved to another reviewer while it was on screen.
struct ReviewNotice: Equatable, Sendable {
    let item: ReviewItem
    /// Who reviews it now, as the card says it.
    let reviewer: String
}

/// Review's two passes: the cards that are not skipped, and the skipped ones.
enum ReviewPass: Equatable, Sendable { case new, skipped }

/// The summary screen's words, from the phone's own cards.
struct ReviewSummary: Equatable, Sendable {
    static let newPassTitle = "Review new"
    static let skippedPassTitle = "Review skipped"
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
    /// The passes the summary offers, new first.
    var passes: [ReviewPass] { (newCount > 0 ? [.new] : []) + (skippedCount > 0 ? [.skipped] : []) }
    /// The other pass, for the top bar of a card in `pass`.
    func otherPass(from pass: ReviewPass) -> (pass: ReviewPass, title: String)? {
        switch pass {
        case .new: skippedCount > 0 ? (.skipped, "\(Self.skippedPassTitle) (\(skippedCount))") : nil
        case .skipped: newCount > 0 ? (.new, Self.newPassTitle) : nil
        }
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
    /// The card running its exit after the owner handled it here.
    private(set) var leaving: ReviewItem?
    private(set) var notice: ReviewNotice?
    /// Cards skipped in this pass. They stay hidden until the next pass starts.
    private(set) var skipped: Set<String> = []
    /// The phone's saved skips, which the session keeps.
    var saved = ReviewSkipList()
    /// The cards of each entry this session read, in any window: the skipped keys come from them.
    var known: [RecordKey: [ReviewItem]] = [:]
    private(set) var pass = ReviewPass.new
    /// The skips when the skipped pass began. The pass holds the cards they cover.
    private(set) var passSkips = ReviewSkipList()
    /// The entries the server is told are skipped, and the skipped entries it puts last.
    private(set) var history = ReviewHistory()
    /// Cards handled here, hidden until the saved work or the window catches up, so a
    /// handled card is never shown again before then. A refusal brings it back.
    private(set) var handled: Set<String> = []
    private(set) var seenRefusals: Set<String> = []
    /// The card on screen when the app went to the background, and the connection
    /// session then. The first fresh window of a later session decides whether it is still there.
    private(set) var away: (cardID: String, session: Int)?

    static func == (lhs: ReviewQueue, rhs: ReviewQueue) -> Bool {
        lhs.summary == rhs.summary && lhs.pinned == rhs.pinned && lhs.leaving == rhs.leaving && lhs.notice == rhs.notice
            && lhs.skipped == rhs.skipped && lhs.history == rhs.history && lhs.handled == rhs.handled
            && lhs.saved == rhs.saved && lhs.known == rhs.known && lhs.pass == rhs.pass && lhs.passSkips == rhs.passSkips
            && lhs.seenRefusals == rhs.seenRefusals && lhs.away?.cardID == rhs.away?.cardID && lhs.away?.session == rhs.away?.session
    }

    /// The queue in order: refused decisions first, then the window's cards, with the
    /// skipped entries last, as the server places them.
    func cards(_ live: [ReviewItem], refusals: [ReviewRefusal]) -> [ReviewItem] {
        let refusedIDs = Set(refusals.map(\.item.id))
        let window = live.filter { !handled.contains($0.id) && !refusedIDs.contains($0.id) }
        let returned = Set(history.priority)
        let ordered = window.filter { !returned.contains($0.historyKey) } + window.filter { returned.contains($0.historyKey) }
        var seen: Set<String> = []
        return (refusals.map(\.item).filter { !handled.contains($0.id) } + ordered).filter { seen.insert($0.id).inserted }
    }

    /// The card on screen; nil is the summary.
    var shown: ReviewItem? {
        guard !summary else { return nil }
        return leaving ?? notice?.item ?? pinned
    }

    /// Skipped by its saved skip. A card skipped in this pass that changed since is new, though
    /// it stays hidden in this pass.
    func isSkipped(_ item: ReviewItem) -> Bool { saved.isSkipped(item) }

    /// Not part of this pass: skipped in it; in the new pass, skipped before; in the skipped
    /// pass, not skipped when the pass began.
    func hidden(_ item: ReviewItem) -> Bool {
        if skipped.contains(item.id) { return true }
        switch pass {
        case .new: return saved.isSkipped(item)
        case .skipped: return !passSkips.isSkipped(item)
        }
    }

    /// Counts from the phone's own cards in the loaded window. Entries beyond it count by the
    /// server's skipped count, less the skipped entries the window holds among the keys the
    /// server counted (`sentSkipped`, else the current ones); the rest are new.
    func counts(_ cards: [ReviewItem], unloaded: Int, serverSkipped: Int? = nil, windowEntries: Set<RecordKey> = [],
                sentSkipped: [RecordKey]? = nil) -> ReviewSummary {
        let skippedCount = cards.filter(isSkipped).count
        let loadedSkippedEntries = (sentSkipped ?? history.skipped).filter(windowEntries.contains).count
        let unloadedSkipped = serverSkipped.map { min(unloaded, max(0, $0 - loadedSkippedEntries)) } ?? 0
        return ReviewSummary(newCount: cards.count - skippedCount + unloaded - unloadedSkipped, skippedCount: skippedCount + unloadedSkipped)
    }

    /// Brings the queue up to the latest window.
    /// - `moved`: who reviews a proposal now, when it waits for someone else.
    /// - `freshSession`: the connection session whose first fresh window this is, if any.
    /// - `window`: the window's cards before saved work hides any; nil when it is `live`.
    /// - `holding`: a pass waits for the window it asked for, so no card is picked and the
    ///   summary does not show.
    mutating func reconcile(_ live: [ReviewItem], refusals: [ReviewRefusal], moved: (ReviewItem) -> String?, freshSession: Int?,
                            window: [ReviewItem]? = nil, holding: Bool = false) {
        // A handled card stays hidden until saved work hides it, so a refusal can bring it
        // back; an answered ask until the window itself drops it, as its answer can settle
        // first. An ask with no time shares its identity with the next ask, so its hold
        // ends with its answer. A new refusal brings its card back at once.
        let liveIDs = Set(live.map(\.id))
        let heldAsks = Set((window ?? live).filter { $0.isAsk && $0.waitingSince != ReviewItem.unknownAskTime }.map(\.id))
        handled = handled.filter { liveIDs.contains($0) || heldAsks.contains($0) }
        if leaving == nil { handled.subtract(refusals.filter { !seenRefusals.contains($0.refusalID) }.map(\.item.id)) }
        let cards = cards(live, refusals: refusals)
        defer { recordSkippedEntries(cards) }
        if let away, let freshSession, freshSession > away.session {
            self.away = nil
            // A card that left while the app was away gives way to the summary.
            if !summary && !cards.contains(where: { $0.id == away.cardID }) { showSummary() }
        }
        // A refusal that lands during an exit comes forward once the exit ends.
        if leaving != nil { return }
        let newRefusal = refusals.first { !seenRefusals.contains($0.refusalID) }
        seenRefusals.formUnion(refusals.map(\.refusalID))
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
            // The card on screen stays until the owner acts on it here, even when it changed.
            if let fresh = cards.first(where: { $0.id == current.id }), !skipped.contains(current.id) { pinned = fresh; return }
            if current.isProposal, !cards.contains(where: { $0.id == current.id }), let reviewer = moved(current) {
                notice = ReviewNotice(item: current, reviewer: reviewer)
                pinned = nil
                return
            }
        }
        if holding { pinned = nil; return }
        pinned = cards.first { !hidden($0) }
        if pinned == nil { showSummary() }
    }

    /// The skipped keys the server is told about: each Ticket or conversation whose every
    /// card is skipped, from the current cards, else from the cards this session read, else
    /// as it was skipped.
    private mutating func recordSkippedEntries(_ cards: [ReviewItem]) {
        var current: [RecordKey: [ReviewItem]] = [:]
        for card in cards { current[card.historyKey, default: []].append(card) }
        var keys: [RecordKey] = []
        for key in cards.map(\.historyKey) where !keys.contains(key) && current[key]!.allSatisfy(isSkipped) { keys.append(key) }
        let elsewhere = known.filter { current[$0.key] == nil }
        keys += saved.applyingKeys(known: elsewhere).filter { current[$0] == nil }
        history.skipped = keys
    }

    /// Every card of the entry would be skipped once `item` is.
    func skipsWholeEntry(_ item: ReviewItem, in cards: [ReviewItem]) -> Bool {
        cards.filter { $0.historyKey == item.historyKey && $0.id != item.id }.allSatisfy(isSkipped)
    }

    /// The skipped entries the server puts last, fixed at the start of the app run and at each pass start.
    mutating func fixSkippedOrder(_ cohort: [RecordKey]) { history.priority = cohort }

    mutating func showSummary() {
        summary = true; pinned = nil; notice = nil; away = nil; leaving = nil
    }

    /// Skip is per card. The session saves it.
    mutating func skip(_ item: ReviewItem, in cards: [ReviewItem]) {
        guard shown?.id == item.id else { return }
        skipped.insert(item.id)
        away = nil
        recordSkippedEntries(cards)
        if notice?.item.id == item.id { notice = nil }
        if pinned?.id == item.id { pinned = nil }
    }

    /// Handled here: the card stays only while its exit runs. Only the card on screen is
    /// handled, so a card leaves once: the card and an artifact opened from it can both see
    /// one saved reply, and the later call, after the next card shows, changes nothing.
    mutating func resolve(_ item: ReviewItem) {
        guard shown?.id == item.id else { return }
        leaving = item
        away = nil
        handled.insert(item.id)
    }
    /// The exit of `item` finished. The next reconcile shows the next card. A late finish for
    /// a card that already left changes nothing, so it never touches the next card's exit.
    mutating func finishExit(_ item: ReviewItem) {
        guard leaving?.id == item.id else { return }
        if let leaving, pinned?.id == leaving.id { pinned = nil }
        if let leaving, notice?.item.id == leaving.id { notice = nil }
        leaving = nil
    }

    /// "Review new" or "Review skipped". `cohort` is the skipped entries, which go last.
    mutating func startPass(_ next: ReviewPass, cohort: [RecordKey]) {
        summary = false
        away = nil
        skipped = []
        pass = next
        passSkips = saved
        history.priority = cohort
        pinned = nil; notice = nil
    }

    /// The app went to the background during `session`. A card whose exit runs is
    /// handled, not the card the owner was on: its exit finishes on the return. Recorded,
    /// a fresh window would show the summary over the exit and remove the card's sheet
    /// before the sheet reports closing, so the card stays leaving and the queue stops.
    mutating func leaveApp(session: Int) {
        away = shown.flatMap { $0.id == leaving?.id ? nil : ($0.id, session) }
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
    @ObservationIgnored private var window: [ReviewItem] = []
    @ObservationIgnored private var refusals: [ReviewRefusal] = []
    @ObservationIgnored private var moved: (ReviewItem) -> String? = { _ in nil }
    /// The server's count of skipped entries, and the entries the loaded window holds.
    private(set) var serverSkipped: Int?
    private(set) var windowEntries: Set<RecordKey> = []
    /// The skip store for a server address. Tests give their own.
    @ObservationIgnored private let skipStores: (String) -> ReviewSkipStore
    @ObservationIgnored private var skipStore: ReviewSkipStore?
    @ObservationIgnored private var skipScope: (server: String, installation: String)?
    /// One read of the end of the queue: limit, the entries put last, the skipped keys.
    typealias TailRead = @MainActor @Sendable (AppStore, Int, [RecordKey], [RecordKey]) async -> [ReviewItem]?
    @ObservationIgnored private let readTail: TailRead
    /// The check of the saved skips at the start of the app run. It starts at the first
    /// window read from the server in this run and gives up after `entryWait`.
    private enum EntryCheck { case waiting, running, done }
    @ObservationIgnored private var entryCheck = EntryCheck.waiting
    @ObservationIgnored private var entryTask: Task<Void, Never>?
    /// How long a read of the end of the queue may hold up the skipped order.
    @ObservationIgnored private let entryWait: Duration
    /// The most rows one read of the end of the queue asks for.
    static let tailRows = NavigationScope.maximumWindowRows
    /// A pass is starting while its read of the end of the queue runs.
    private(set) var startingPass = false
    /// A pass waits for the Review window it asked for: from the end of the queue (true) or the top.
    private(set) var holding = false
    @ObservationIgnored private var wantsTail: Bool?
    @ObservationIgnored private var holdTask: Task<Void, Never>?
    /// Counts explicit summary selections, so one made during a pass start's read wins.
    @ObservationIgnored private var summarySelections = 0

    init(skipStores: @escaping (String) -> ReviewSkipStore = { ReviewSkipStore.local(server: $0) },
         readTail: @escaping TailRead = { store, limit, cohort, skipped in
             try? await store.readReviewTail(limit: limit, cohort: cohort, skipped: skipped) },
         entryWait: Duration = .seconds(3)) {
        self.skipStores = skipStores
        self.readTail = readTail
        self.entryWait = entryWait
    }

    /// Identifies a card presentation after an exit or an explicit summary selection.
    /// Old callbacks cannot act on a card reopened under the same identity.
    private(set) var exits = 0
    var shown: ReviewItem? { queue.shown }
    var notice: ReviewNotice? { queue.notice }
    var summary: ReviewSummary {
        queue.counts(cards, unloaded: unloadedCount, serverSkipped: serverSkipped, windowEntries: windowEntries, sentSkipped: sentSkipped)
    }
    @ObservationIgnored private var sentSkipped: [RecordKey]?
    @ObservationIgnored private var queueTotal = 0
    var pass: ReviewPass { queue.pass }
    /// The window has been read at least once, so the summary's counts mean something.
    private(set) var loaded = false

    /// The latest window and saved work. `freshSession` is the store's connection session
    /// once its stream follows changes.
    func update(store: AppStore, savedWork: ReviewSavedWork, freshSession: Int?) {
        let server = LocalStore.server(store.baseURL)
        let decided = savedWork.decisions(server)
        window = store.review?.items ?? []
        live = ReviewSavedWork.cards(window, decisions: decided)
        let view = store.view
        let presentation = store.presentation
        refusals = ReviewSavedWork.refusals(decided, live: store.review?.items ?? [], title: { summary in
            presentation?.ticketsById[summary.ticketID ?? ""]?.title ?? Outbox.shared.work(server, id: summary.id)?.ticketTitle
        }, waits: { item in view.flatMap { presentation?.proposalWaits(item, in: $0) } ?? nil })
        refusalNotes = Dictionary(refusals.map { ($0.item.id, $0.note) }, uniquingKeysWith: { first, _ in first })
        moved = { item in view.flatMap { presentation?.movedReviewer(for: item, in: $0) } }
        unloadedCount = store.review?.unloadedCount ?? 0
        runningWorkerCount = store.review?.runningWorkerCount ?? 0
        serverSkipped = store.review?.serverSkippedCount
        queueTotal = store.review?.queueTotal ?? 0
        sentSkipped = store.review?.sentSkippedKeys
        windowEntries = Set(window.map(\.historyKey))
        if holding, let wantsTail, store.review?.fromTail == wantsTail { endHold() }
        loaded = store.review != nil
        loadSkips(server: server, installation: store.review?.installationId)
        // Only a window read in this run says what a card holds now: a copy saved on the
        // phone can be older than a skip.
        let fresh = freshSession != nil && store.review?.current == true
        if fresh, let review = store.review {
            // The whole queue, and the server counted fewer skips than it was sent: a skipped entry left.
            let entryLeft = review.serverSkippedCount.map { $0 < (review.sentSkippedKeys?.count ?? 0) } ?? false
            learn(window, wholeQueue: review.holdsWholeQueue && entryLeft)
        }
        // A refused decision's card acts again on a fresh read of its proposal.
        for refusal in refusals { decisions.dropResolved(refusal.item.id) }
        reconcile(freshSession: freshSession)
        if fresh, entryCheck == .waiting { beginEntryCheck(store: store) }
        store.setReviewHistory(queue.history)
    }

    /// The server's installation names the list, so one server's skips never apply to another.
    private func loadSkips(server: String, installation: String?) {
        guard let installation, skipScope?.server != server || skipScope?.installation != installation else { return }
        let skipStore = skipStores(server)
        self.skipStore = skipStore
        skipScope = (server, installation)
        queue.known = [:]
        queue.saved = skipStore.list(installation)
    }
    private func saveSkips(_ list: ReviewSkipList) {
        let list = list.limited(seen: Set(queue.known.keys))
        guard list != queue.saved else { return }
        queue.saved = list
        if let skipScope { skipStore?.write(list, for: skipScope.installation) }
    }

    /// Cards read in this run. A read of the whole queue from the top drops the skips of
    /// entries that left it. A read of the end never does: it is not the whole queue.
    private func learn(_ items: [ReviewItem], wholeQueue: Bool) {
        let entries = Dictionary(grouping: items, by: \.historyKey)
        for (key, cards) in entries { queue.known[key] = cards }
        if wholeQueue { saveSkips(queue.saved.keeping(Set(entries.keys))) }
    }

    /// At the start of the run, the skipped entries the phone has not loaded sit at the end of
    /// the queue. One read of the end checks exactly those, then the skipped order is fixed.
    private func beginEntryCheck(store: AppStore) {
        entryCheck = .running
        let unknown = queue.saved.keys.filter { queue.known[$0] == nil }
        guard !unknown.isEmpty, unloadedCount > 0 else { settleEntryCheck(store: store); return }
        let skipped = queue.history.skipped, read = readTail, wait = entryWait
        entryTask = Task { [weak self] in
            let tail = await Self.within(wait) { await read(store, min(unknown.count, Self.tailRows), unknown, skipped) }
            guard let self, self.entryCheck == .running else { return }
            if let tail { self.learn(tail, wholeQueue: false) }
            self.settleEntryCheck(store: store)
        }
    }
    private func settleEntryCheck(store: AppStore) {
        entryCheck = .done
        reconcile()
        queue.fixSkippedOrder(queue.history.skipped)
        store.setReviewHistory(queue.history)
    }
    /// The read's answer, or nil once the wait runs out.
    private static func within(_ wait: Duration, _ read: @escaping @MainActor @Sendable () async -> [ReviewItem]?) async -> [ReviewItem]? {
        await withTaskGroup(of: [ReviewItem]?.self) { group in
            group.addTask { await read() }
            group.addTask { try? await Task.sleep(for: wait); return nil }
            let first = await group.next() ?? nil
            group.cancelAll()
            return first
        }
    }

    private func reconcile(freshSession: Int? = nil) {
        queue.reconcile(live, refusals: refusals, moved: moved, freshSession: freshSession, window: window, holding: holding)
        cards = queue.cards(live, refusals: refusals)
    }

    func skip(_ item: ReviewItem, store: AppStore) {
        guard shown?.id == item.id else { return }
        // The card as the window holds it now, not as it was first shown.
        let current = window.first { $0.id == item.id } ?? item
        saveSkips(queue.saved.skipping(current, at: Date.now.timeIntervalSince1970, whole: queue.skipsWholeEntry(item, in: cards)))
        queue.skip(item, in: cards)
        // A card offered again reads its run again, as a freshly opened card does.
        if item.isMessage { messageDecisions.drop(item.id) }
        reconcile()
        store.setReviewHistory(queue.history)
    }
    func resolve(_ item: ReviewItem) { queue.resolve(item) }
    func finishExit(_ item: ReviewItem) {
        guard queue.leaving?.id == item.id else { return }
        if item.isMessage { messageDecisions.drop(item.id) }
        queue.finishExit(item)
        exits += 1
        reconcile()
    }
    /// "Review new" or "Review skipped". A card may have changed since the summary was read,
    /// so a queue larger than one window reads its end again first, for every skip. The
    /// skipped order is then fixed for the pass. Offline, the pass starts with what is known.
    func startPass(_ next: ReviewPass, store: AppStore) async {
        guard !startingPass else { return }
        startingPass = true
        defer { startingPass = false }
        entryCheck = .done
        entryTask?.cancel()
        let selection = summarySelections
        let keys = queue.saved.keys, skipped = queue.history.skipped, read = readTail
        if !keys.isEmpty, unloadedCount > 0,
           let tail = await Self.within(entryWait, { await read(store, min(keys.count, Self.tailRows), keys, skipped) }) {
            learn(tail, wholeQueue: false)
        }
        // A Sidebar Review selection during the read keeps the summary.
        guard selection == summarySelections else { return }
        reconcile()
        queue.startPass(next, cohort: queue.history.skipped)
        exits += 1
        for item in cards where item.isMessage { messageDecisions.drop(item.id) }
        // The skipped entries sit at the end of the queue. When one window from the top cannot
        // hold the whole queue, the skipped pass reads its end. The queue's size decides, not
        // what the window on screen left out: a window from the end can hold the whole queue.
        // Until the window it asks for arrives, the pass picks no card, at most for the wait of a read.
        let tail = next == .skipped && queueTotal > NavigationScope.reviewWindowLimit
        store.setReviewWindow(tail ? NavigationScope.reviewTail(Self.tailRows) : nil)
        if (store.review?.fromTail ?? false) != tail {
            holding = true
            wantsTail = tail
            let wait = entryWait
            holdTask?.cancel()
            holdTask = Task { [weak self] in
                try? await Task.sleep(for: wait)
                guard !Task.isCancelled else { return }
                self?.endHold()
            }
        }
        reconcile()
        store.setReviewHistory(queue.history)
    }
    private func endHold() {
        guard holding else { return }
        holding = false
        wantsTail = nil
        holdTask?.cancel(); holdTask = nil
        reconcile()
    }
    func showSummary() {
        summarySelections += 1
        if holding { holding = false; wantsTail = nil; holdTask?.cancel(); holdTask = nil }
        queue.showSummary()
        exits += 1
        reconcile()
    }
    func presents(_ item: ReviewItem, at presentation: Int) -> Bool {
        exits == presentation && shown?.id == item.id
    }
    func leaveApp(session: Int) { queue.leaveApp(session: session) }

    /// The shown card and the next few waiting ones, which are read ahead.
    func upcoming(_ count: Int) -> [ReviewItem] {
        let shown = self.shown
        let waiting = cards.filter { !queue.hidden($0) && $0.id != shown?.id }
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
