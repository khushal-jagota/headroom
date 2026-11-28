import Foundation
import XCTest
@testable import Panels

/// The skips saved on the phone and the two passes, as the web keeps them
/// (web/tests/review-skips.test.ts). Pure checks of the list, then the session over a fake
/// store and a fake read of the end of the queue. No server, no SwiftUI.
final class ReviewSkipsTests: XCTestCase {
    private let proposal = ReviewItem(reviewItemType: "proposal", ticketId: "a", field: "plan", title: "A", waitingSince: 1, proposalRevision: 1)
    private func message(_ conversation: String, owner: String = "a", since: Double = 2, latest: Int?) -> ReviewItem {
        var item = ReviewItem(reviewItemType: "message", title: "A", waitingSince: since, conversationId: conversation, ownerKind: "ticket", ownerId: owner)
        item.latestOwnerMessageSequence = latest
        return item
    }
    private func ask(_ conversation: String, since: Double) -> ReviewItem {
        ReviewItem(reviewItemType: "ask", title: "M", waitingSince: since, conversationId: conversation, ownerKind: "manager", ownerId: "m")
    }
    private let ticketA = RecordKey(kind: "ticket", id: "a")

    /// Risk: a skip ends too early or too late. It ends only when its own card shows a newer
    /// part; a lost part or an older view never ends it; and the phone skips per card.
    func testASkipEndsOnlyWhenItsCardGainsSomething() {
        let read = message("a-c", latest: 5)
        var list = ReviewSkipList().skipping(proposal, at: 1, whole: false).skipping(read, at: 2, whole: true)
        XCTAssertTrue(list.isSkipped(proposal)); XCTAssertTrue(list.isSkipped(read))
        // A newer proposal revision, a newer message and a new conversation are new.
        let revised = ReviewItem(reviewItemType: "proposal", ticketId: "a", field: "plan", title: "A", waitingSince: 1, proposalRevision: 2)
        XCTAssertFalse(list.isSkipped(revised))
        XCTAssertFalse(list.isSkipped(message("a-c", latest: 6)))
        XCTAssertFalse(list.isSkipped(message("a-other", latest: 1)))
        // Per card: the Ticket's new message leaves its skipped proposal skipped.
        XCTAssertTrue(list.isSkipped(proposal))
        // An older view of the same card is no change.
        XCTAssertTrue(list.isSkipped(message("a-c", latest: 4)))
        let older = ReviewItem(reviewItemType: "proposal", ticketId: "a", field: "plan", title: "A", waitingSince: 0.5, proposalRevision: 1)
        XCTAssertTrue(list.isSkipped(older))
        // A message the view gave no sequence for: a later known sequence is new.
        list = list.skipping(message("a-n", latest: nil), at: 3, whole: true)
        XCTAssertTrue(list.isSkipped(message("a-n", latest: nil)))
        XCTAssertFalse(list.isSkipped(message("a-n", latest: 1)))
        // An ask raised later is new.
        let asked = ReviewSkipList().skipping(ask("m-c", since: 10), at: 1, whole: true)
        XCTAssertTrue(asked.isSkipped(ask("m-c", since: 10)))
        XCTAssertFalse(asked.isSkipped(ask("m-c", since: 11)))
        // A skip made from an older view keeps the newer parts.
        let merged = ReviewSkipList().skipping(message("a-c", latest: 9), at: 1, whole: true).skipping(message("a-c", latest: 3), at: 2, whole: true)
        XCTAssertTrue(merged.isSkipped(message("a-c", latest: 9)))
        let revisedSkip = ReviewSkipList().skipping(revised, at: 1, whole: true).skipping(proposal, at: 2, whole: true)
        XCTAssertTrue(revisedSkip.isSkipped(revised))
    }

    /// Risk: the server counts or orders the wrong entries, or skips of cards that left are
    /// never removed. Known entries count when every card is skipped, unknown ones as they
    /// were skipped; the cap drops the entries the phone has not seen first, oldest first.
    func testSkippedKeysAndTheLimit() {
        let other = message("chief", owner: "x", latest: 1)
        let list = ReviewSkipList().skipping(proposal, at: 1, whole: false).skipping(other, at: 2, whole: true)
        let x = other.historyKey
        XCTAssertEqual(list.applyingKeys(known: [:]), [x], "Unknown entries count as they were skipped")
        XCTAssertEqual(list.applyingKeys(known: [ticketA: [proposal]]), [ticketA, x], "The Ticket's other card has gone")
        XCTAssertEqual(list.applyingKeys(known: [ticketA: [proposal, message("a-c", latest: 1)]]), [x])
        XCTAssertEqual(list.applyingKeys(known: [x: [message("chief", owner: "x", latest: 2)]]), [], "A changed card is not skipped")
        XCTAssertEqual(list.keeping([x]).keys, [x])

        var many = ReviewSkipList()
        for index in 0..<4 { many = many.skipping(message("c\(index)", owner: "t\(index)", latest: 1), at: Double(index), whole: true) }
        let seen: Set<RecordKey> = [RecordKey(kind: "ticket", id: "t0")]
        XCTAssertEqual(Set(many.limited(seen: seen, to: 2).keys.map(\.id)), ["t0", "t3"])
    }

    /// Risk: a skip is lost on relaunch, one server's skips apply to another, a stored value
    /// this build did not write breaks Review, or the sweep of old lists removes the current one.
    @MainActor func testListsAreKeptPerInstallationAndSurviveRelaunch() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let local = LocalStore(url: directory.appendingPathComponent("skips.store"))
        let list = ReviewSkipList().skipping(message("a-n", latest: nil), at: 1, whole: true)
        ReviewSkipStore.local(server: "https://one", in: local).write(list, for: "install-1")
        let relaunched = ReviewSkipStore.local(server: "https://one", in: local)
        XCTAssertEqual(relaunched.list("install-1"), list)
        XCTAssertTrue(relaunched.list("install-2").isEmpty, "Another installation at the same address")
        XCTAssertTrue(ReviewSkipStore.local(server: "https://two", in: local).list("install-1").isEmpty, "Another server")
        try local.put(server: "https://one", kind: ReviewSkipStore.kind, id: "install-1", data: Data("{\"version\":9}".utf8))
        XCTAssertTrue(relaunched.list("install-1").isEmpty, "A list this build did not write reads as no skips")
        XCTAssertTrue(ReviewSkipList(stored: Data("garbage".utf8)).isEmpty)

        // Lists untouched for 30 days go, except the one in use.
        relaunched.write(list, for: "install-1")
        ReviewSkipStore.local(server: "https://two", in: local).write(list, for: "install-9")
        for row in try local.records(kind: ReviewSkipStore.kind) { row.updatedAt = .now.addingTimeInterval(-31 * 24 * 60 * 60) }
        XCTAssertEqual(relaunched.list("install-1"), list)
        XCTAssertEqual(try local.records(kind: ReviewSkipStore.kind).map(\.server), ["https://one"])
    }

    // MARK: The session

    @MainActor private final class Saved { var lists: [String: ReviewSkipList] = [:] }
    @MainActor private final class Reads { var calls: [(limit: Int, cohort: [RecordKey])] = []; var answer: [ReviewItem]? = []; var hang = false }
    @MainActor private func session(_ saved: Saved, _ reads: Reads, wait: Duration = .seconds(3)) -> ReviewSession {
        ReviewSession(skipStores: { _ in
            ReviewSkipStore(load: { saved.lists[$0]?.encoded() }, save: { installation, data in saved.lists[installation] = ReviewSkipList(stored: data) })
        }, readTail: { _, limit, cohort, _ in
            reads.calls.append((limit, cohort))
            if reads.hang { try? await Task.sleep(for: .seconds(30)) }
            return reads.answer
        }, entryWait: wait)
    }
    @MainActor private func review(_ items: [ReviewItem], unloaded: Int = 0, total: Int? = nil, current: Bool = true, whole: Bool = true, tail: Bool = false,
                                   serverSkipped: Int? = nil, sent: [RecordKey]? = nil) -> ReviewResponse {
        ReviewResponse(items: items, runningWorkerCount: 0, unloadedCount: unloaded, queueTotal: total ?? Set(items.map(\.historyKey)).count + unloaded,
                       serverSkippedCount: serverSkipped, holdsWholeQueue: whole,
                       fromTail: tail, installationId: "install", current: current, sentSkippedKeys: sent)
    }
    @MainActor private func store(_ items: [ReviewItem], unloaded: Int = 0, total: Int? = nil, current: Bool = true, whole: Bool = true,
                                  serverSkipped: Int? = nil, sent: [RecordKey]? = nil) -> AppStore {
        let store = AppStore(baseURL: URL(string: "http://127.0.0.1:1")!)
        store.review = review(items, unloaded: unloaded, total: total, current: current, whole: whole, serverSkipped: serverSkipped, sent: sent)
        return store
    }
    /// Lets the session's own tasks run.
    @MainActor private func settle() async { try? await Task.sleep(for: .milliseconds(100)) }

    /// Risk: a skip lasts one app run, or the passes mix. A skip survives a relaunch; the new
    /// pass leaves it out, and the skipped pass holds only it.
    @MainActor func testASkipSurvivesARelaunchAndThePassesSplitTheQueue() async {
        let saved = Saved(), reads = Reads()
        let chief = message("chief", owner: "x", since: 3, latest: 1)
        let items = [proposal, chief]
        let first = session(saved, reads), app = store(items)
        first.update(store: app, savedWork: ReviewSavedWork(), freshSession: 1)
        await first.startPass(.new, store: app)
        XCTAssertEqual(first.shown?.id, proposal.id)
        first.skip(proposal, store: app)
        XCTAssertEqual(first.shown?.id, chief.id)

        let relaunched = session(saved, reads), again = store(items)
        relaunched.update(store: again, savedWork: ReviewSavedWork(), freshSession: 1)
        XCTAssertEqual(relaunched.summary.passes, [.new, .skipped])
        await relaunched.startPass(.new, store: again)
        XCTAssertEqual(relaunched.shown?.id, chief.id)
        XCTAssertEqual(relaunched.upcoming(3).map(\.id), [chief.id])
        await relaunched.startPass(.skipped, store: again)
        XCTAssertEqual(relaunched.shown?.id, proposal.id)
        XCTAssertEqual(relaunched.upcoming(3).map(\.id), [proposal.id])
        XCTAssertTrue(reads.calls.isEmpty, "One window holds the whole queue: no read of its end")
    }

    /// Risk (web faults F1 and F3): the check starts before a read of this run, reads more
    /// than the skips it has not loaded, or a changed card past the window still counts as
    /// skipped and goes last.
    @MainActor func testTheEntryCheckReadsOnlyTheSkipsNotLoadedAfterTheFirstFreshWindow() async {
        let saved = Saved(), reads = Reads()
        let far = message("far", owner: "f", since: 8, latest: 1), farther = message("farther", owner: "g", since: 9, latest: 1)
        saved.lists["install"] = ReviewSkipList().skipping(proposal, at: 1, whole: true).skipping(far, at: 2, whole: true).skipping(farther, at: 3, whole: true)
        let review = session(saved, reads), app = store([proposal], unloaded: 2, whole: false)
        review.update(store: app, savedWork: ReviewSavedWork(), freshSession: nil)
        await settle()
        XCTAssertTrue(reads.calls.isEmpty, "A saved view starts nothing")
        // The tail shows `far` with a new message: it is new again.
        reads.answer = [message("far", owner: "f", since: 8, latest: 2), farther]
        review.update(store: app, savedWork: ReviewSavedWork(), freshSession: 1)
        await settle()
        XCTAssertEqual(reads.calls.map(\.cohort), [[far.historyKey, farther.historyKey]])
        XCTAssertEqual(reads.calls.first?.limit, 2)
        XCTAssertEqual(review.queue.history.priority, [ticketA, farther.historyKey])
        XCTAssertEqual(review.queue.history.skipped, [ticketA, farther.historyKey])
        review.update(store: app, savedWork: ReviewSavedWork(), freshSession: 2)
        await settle()
        XCTAssertEqual(reads.calls.count, 1, "The check runs once per app run")
    }

    /// Risk (F1): a slow read leaves the skipped order unset, or the wait gives up with an
    /// empty cohort. It gives up after its wait with the cohort the phone knows.
    @MainActor func testTheEntryCheckGivesUpWithTheKnownCohort() async throws {
        let saved = Saved(), reads = Reads()
        reads.hang = true
        let far = message("far", owner: "f", since: 8, latest: 1)
        saved.lists["install"] = ReviewSkipList().skipping(far, at: 2, whole: true)
        let review = session(saved, reads, wait: .milliseconds(50)), app = store([proposal], unloaded: 1, whole: false)
        review.update(store: app, savedWork: ReviewSavedWork(), freshSession: 1)
        XCTAssertEqual(review.queue.history.priority, [])
        for _ in 0..<100 where review.queue.history.priority.isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertEqual(review.queue.history.priority, [far.historyKey])
    }

    /// Risk (F4 and F5): a pass starts from an old check, or the skipped order moves during a
    /// pass. Each pass start reads the end for every skip, and a skip in a pass leaves the
    /// order as the pass began. Risk: the skipped cards sit only at the end of the queue, so a
    /// pass that picked from the top window bounced to the summary. The pass waits for the
    /// window it asked for.
    @MainActor func testEachPassStartReadsTheEndAgainAndWaitsForItsWindow() async {
        let saved = Saved(), reads = Reads()
        let far = message("far", owner: "f", since: 8, latest: 1)
        saved.lists["install"] = ReviewSkipList().skipping(proposal, at: 1, whole: true).skipping(far, at: 2, whole: true)
        let chief = message("chief", owner: "x", since: 3, latest: 1)
        // 40 entries: more than one window from the top holds.
        let session = session(saved, reads), app = store([chief], unloaded: 39, total: 40, whole: false)
        reads.answer = [proposal, far]
        session.update(store: app, savedWork: ReviewSavedWork(), freshSession: 1)
        await settle()
        XCTAssertEqual(reads.calls.map(\.cohort), [[ticketA, far.historyKey]])
        XCTAssertEqual(session.queue.history.priority, [ticketA, far.historyKey])
        // `far` changed since the summary was read.
        let changed = message("far", owner: "f", since: 8, latest: 5)
        reads.answer = [proposal, changed]
        await session.startPass(.skipped, store: app)
        XCTAssertEqual(reads.calls.last?.cohort, [ticketA, far.historyKey])
        XCTAssertEqual(session.queue.history.priority, [ticketA], "The changed card is new and takes its place by age")
        XCTAssertEqual(app.viewRequest.windows.first, NavigationScope.reviewTail(60))
        XCTAssertTrue(session.holding, "The top window holds no skipped card: wait for the end")
        XCTAssertNil(session.shown)
        // The window from the end holds every entry, so nothing is left unloaded.
        app.review = review([proposal, changed], unloaded: 0, total: 40, whole: false, tail: true)
        session.update(store: app, savedWork: ReviewSavedWork(), freshSession: 1)
        XCTAssertFalse(session.holding)
        XCTAssertEqual(session.shown?.id, proposal.id)
        session.skip(proposal, store: app)
        XCTAssertEqual(session.queue.history.priority, [ticketA])
        XCTAssertNil(session.shown, "The changed card is not part of the skipped pass")
        XCTAssertEqual([session.summary.newCount, session.summary.skippedCount], [1, 1])
        // A second skipped pass stays on the end of the queue: the queue's size decides.
        await session.startPass(.skipped, store: app)
        XCTAssertEqual(app.viewRequest.windows.first, NavigationScope.reviewTail(60))
        XCTAssertFalse(session.holding)
        XCTAssertEqual(session.shown?.id, proposal.id)
        await session.startPass(.new, store: app)
        XCTAssertEqual(reads.calls.count, 2, "The window from the end holds every skip: no read")
        XCTAssertEqual(app.viewRequest.windows.first, NavigationScope.baseWindows[0])
        XCTAssertTrue(session.holding)
        app.review = review([chief], unloaded: 39, total: 40, whole: false)
        session.update(store: app, savedWork: ReviewSavedWork(), freshSession: 1)
        XCTAssertEqual(session.shown?.id, chief.id)
    }

    /// Risk: the window a pass asked for never arrives, as offline, and the pass waits forever.
    /// After the wait of a read it picks from what it has.
    @MainActor func testAPassStopsWaitingForItsWindowAfterTheWait() async throws {
        let saved = Saved(), reads = Reads()
        reads.answer = nil
        saved.lists["install"] = ReviewSkipList().skipping(proposal, at: 1, whole: true)
        let chief = message("chief", owner: "x", since: 3, latest: 1)
        let session = session(saved, reads, wait: .milliseconds(50)), app = store([proposal, chief], unloaded: 38, total: 40, whole: false)
        session.update(store: app, savedWork: ReviewSavedWork(), freshSession: 1)
        await session.startPass(.skipped, store: app)
        XCTAssertTrue(session.holding)
        XCTAssertNil(session.shown)
        for _ in 0..<100 where session.holding { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertFalse(session.holding)
        XCTAssertEqual(session.shown?.id, proposal.id, "The skipped card the top window holds")
    }

    /// Risk: a Sidebar Review selection made while a pass start reads the end of the queue is
    /// undone when the read ends, and the cards open over the summary.
    @MainActor func testASummarySelectionDuringThePassReadKeepsTheSummary() async throws {
        let saved = Saved(), reads = Reads()
        reads.hang = true
        saved.lists["install"] = ReviewSkipList().skipping(proposal, at: 1, whole: true)
        let chief = message("chief", owner: "x", since: 3, latest: 1)
        let session = session(saved, reads, wait: .milliseconds(200)), app = store([proposal, chief], unloaded: 38, total: 40, whole: false)
        session.update(store: app, savedWork: ReviewSavedWork(), freshSession: 1)
        await settle()
        let order = session.queue.history.priority
        let start = Task { await session.startPass(.new, store: app) }
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(session.startingPass)
        session.showSummary()
        await start.value
        XCTAssertNil(session.shown)
        XCTAssertTrue(session.queue.summary)
        XCTAssertFalse(session.holding)
        XCTAssertEqual(session.queue.history.priority, order)
        XCTAssertEqual(app.viewRequest.windows.first, NavigationScope.baseWindows[0])
    }

    /// Risk: a skip of a card that left the queue is kept forever, or one that is still
    /// there is dropped. Only a read of the whole queue from the top in this run, where the
    /// server counted fewer skips than it was sent, drops it.
    @MainActor func testOnlyAReadOfTheWholeQueueDropsTheSkipsOfCardsThatLeft() async {
        let saved = Saved(), reads = Reads()
        let gone = message("gone", owner: "g", latest: 1)
        saved.lists["install"] = ReviewSkipList().skipping(proposal, at: 1, whole: true).skipping(gone, at: 2, whole: true)
        let sent = [ticketA, gone.historyKey]
        let session = session(saved, reads)
        session.update(store: store([proposal], current: false, serverSkipped: 1, sent: sent), savedWork: ReviewSavedWork(), freshSession: 1)
        session.update(store: store([proposal], serverSkipped: 1, sent: sent), savedWork: ReviewSavedWork(), freshSession: nil)
        XCTAssertEqual(saved.lists["install"]?.keys.count, 2, "A saved view, or no stream, is not a read of this run")
        session.update(store: store([proposal], whole: false, serverSkipped: 1, sent: sent), savedWork: ReviewSavedWork(), freshSession: 1)
        XCTAssertEqual(saved.lists["install"]?.keys.count, 2, "One window of a larger queue")
        session.update(store: store([proposal], serverSkipped: 2, sent: sent), savedWork: ReviewSavedWork(), freshSession: 1)
        XCTAssertEqual(saved.lists["install"]?.keys.count, 2, "The server still counts every key sent")
        session.update(store: store([proposal], serverSkipped: 1, sent: sent), savedWork: ReviewSavedWork(), freshSession: 1)
        XCTAssertEqual(saved.lists["install"]?.keys, [ticketA])
    }

    /// Risk (web fault F2): the read of the end is taken for a read of the top, or the check
    /// tests more than the direction and refuses a good answer. Through the real client: the
    /// request holds only Review's window from the end, with the cohort last, and an answer
    /// whose window is not from the end is no answer.
    @MainActor func testTheReadOfTheEndTestsOnlyTheDirection() async throws {
        let base = URL(string: "https://tail.invalid")!
        let store = AppStore(baseURL: base, session: TailProtocol.session)
        TailProtocol.direction = "before"
        let cohort = [ticketA], skipped = [RecordKey(kind: "conversation", id: "c")]
        let items = try await store.readReviewTail(limit: 7, cohort: cohort, skipped: skipped)
        XCTAssertEqual(items?.count, 0)
        let sent = try XCTUnwrap(TailProtocol.lastView)
        let windows = try XCTUnwrap(sent["windows"] as? [[String: Any]])
        XCTAssertEqual(windows.count, 1)
        XCTAssertEqual(windows.first?["section"] as? String, "review")
        XCTAssertEqual(windows.first?["direction"] as? String, "before")
        XCTAssertEqual(windows.first?["limit"] as? Int, 7)
        XCTAssertEqual((sent["review_priority_keys"] as? [[String: String]])?.first?["id"], "a")
        XCTAssertEqual((sent["review_skipped_keys"] as? [[String: String]])?.first?["id"], "c")
        // The server answered with its own anchor and limit: still an answer from the end.
        TailProtocol.answerLimit = 3
        let normalized = try await store.readReviewTail(limit: 7, cohort: cohort, skipped: skipped)
        XCTAssertNotNil(normalized)
        TailProtocol.direction = "after"
        let fromTop = try await store.readReviewTail(limit: 7, cohort: cohort, skipped: skipped)
        XCTAssertNil(fromTop)
    }
}

/// Answers the view snapshot door with an empty queue whose Review window has the set direction.
private final class TailProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var direction = "before"
    nonisolated(unsafe) static var answerLimit: Int? = nil
    nonisolated(unsafe) static var lastView: [String: Any]?
    static var session: URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [TailProtocol.self]
        return URLSession(configuration: config)
    }
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "tail.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let url = request.url!
        let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "view" }?.value ?? "{}"
        let view = (try? JSONSerialization.jsonObject(with: Data(query.utf8))) as? [String: Any] ?? [:]
        Self.lastView = view
        let window: [String: Any] = ["window_id": "review", "request": ["section": "review", "owner": NSNull(), "group": "all", "child_filter": "all",
                                                                        "anchor": NSNull(), "direction": Self.direction, "limit": Self.answerLimit ?? 7],
                                     "members": [], "total": 40, "previous": NSNull(), "next": NSNull(), "forward_overlap": NSNull(), "backward_overlap": NSNull()]
        let body: [String: Any] = ["event": "snapshot", "wire_version": NavigationViewVersion.wire, "projection_version": NavigationViewVersion.projection,
            "view_id": "v", "scope_generation": view["scope_generation"] as? Int ?? 0, "ordering_generation": "o", "mode": "current", "content_version": "c",
            "coverage": ["scope": "s", "schema_version": 11, "semantics": "x", "epoch": "e", "revision": "1"],
            "authority": ["delivery_session_id": "d", "availability": ["state": "current", "generation": "1", "reason": NSNull()],
                          "permit": ["scope": "s", "runtime_epoch": "r", "installation_id": "i", "generation": "1"]],
            "content": ["records": [], "windows": [window], "review_context": [], "review_summary": ["skipped": 1, "new": 39],
                        "counts": ["running_conversations": 0, "out_claims": 0, "review": 40, "unread": 0, "settled": 0]]]
        let data = try! JSONSerialization.data(withJSONObject: body)
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
