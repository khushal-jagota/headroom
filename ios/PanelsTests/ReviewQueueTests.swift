import Foundation
import XCTest
@testable import Panels

/// The phone's Review queue, one card per purpose. Pure model checks: no server, no
/// SwiftUI. The shell feeds the same model from the navigation view.
final class ReviewQueueTests: XCTestCase {
    private let proposal = ReviewItem(reviewItemType: "proposal", ticketId: "a", field: "plan", title: "A", waitingSince: 1, proposalRevision: 1)
    private let ticketMessage = ReviewItem(reviewItemType: "message", title: "A", waitingSince: 2, conversationId: "a-conversation", ownerKind: "ticket", ownerId: "a")
    private let chiefMessage = ReviewItem(reviewItemType: "message", title: "Chief", waitingSince: 3, conversationId: "chief", ownerKind: "chief", ownerId: "chief")
    private let ask = ReviewItem(reviewItemType: "ask", title: "Manager", waitingSince: 4, conversationId: "manager", ownerKind: "manager", ownerId: "m")
    private func reconcile(_ queue: inout ReviewQueue, _ live: [ReviewItem], refusals: [ReviewRefusal] = [],
                           moved: [String: String] = [:], fresh: Int? = nil) {
        queue.reconcile(live, refusals: refusals, moved: { moved[$0.id] }, freshSession: fresh)
    }

    /// Risk: a card read or handled elsewhere stays on screen, as the held-card rule let it.
    /// The shown card is always in the latest window; only its own exit holds it.
    func testShownCardFollowsTheLatestWindow() {
        var queue = ReviewQueue()
        let all = [proposal, ticketMessage, chiefMessage, ask]
        reconcile(&queue, all)
        XCTAssertNil(queue.shown, "The app opens on the summary")
        queue.returnToCards(); reconcile(&queue, all)
        XCTAssertEqual(queue.shown?.id, proposal.id)
        // A later window that inserts older work leaves the shown card in place.
        let older = ReviewItem(reviewItemType: "message", title: "Older", waitingSince: 0, conversationId: "older", ownerKind: "chief", ownerId: "chief")
        reconcile(&queue, [older] + all)
        XCTAssertEqual(queue.shown?.id, proposal.id)
        // Decided elsewhere: the window drops it and the next card shows at once.
        reconcile(&queue, [ticketMessage, chiefMessage, ask])
        XCTAssertEqual(queue.shown?.id, ticketMessage.id)
        // Handled here: the card stays only while its exit runs, though its saved work hid it.
        queue.resolve(ticketMessage)
        reconcile(&queue, [chiefMessage, ask])
        XCTAssertEqual(queue.shown?.id, ticketMessage.id)
        queue.finishExit(); reconcile(&queue, [chiefMessage, ask])
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
        // An answered ask stays hidden until the window drops it.
        queue.resolve(chiefMessage); queue.finishExit(); reconcile(&queue, [ask])
        queue.resolve(ask); queue.finishExit(); reconcile(&queue, [ask])
        XCTAssertNil(queue.shown, "After the last card, the summary")
        XCTAssertEqual(queue.counts(queue.cards([ask], refusals: []), unloaded: 0), ReviewSummary(newCount: 0, skippedCount: 0))
        reconcile(&queue, [])
        XCTAssertTrue(queue.answered.isEmpty)
    }

    /// Risk: the summary shows at the wrong moments, or a return to the app moves Khushal
    /// off a card that is still waiting. A Sidebar visit and a card that left while the app
    /// was away show the summary; a plain return keeps him where he was.
    func testSummaryAfterAVisitAndAfterTheOpenCardLeftWhileAway() {
        var queue = ReviewQueue()
        let all = [proposal, chiefMessage]
        queue.visit(0)
        queue.returnToCards(); reconcile(&queue, all)
        queue.visit(0)
        XCTAssertEqual(queue.shown?.id, proposal.id, "Remounting on the same visit keeps the card")
        queue.visit(1); reconcile(&queue, all)
        XCTAssertNil(queue.shown, "Another destination and back shows the summary")

        queue.returnToCards(); reconcile(&queue, all)
        queue.leaveApp(session: 4)
        reconcile(&queue, [chiefMessage], fresh: 4)
        XCTAssertEqual(queue.shown?.id, chiefMessage.id, "The old session's window is not the return")
        queue.leaveApp(session: 4)
        reconcile(&queue, [chiefMessage], fresh: nil)
        XCTAssertEqual(queue.shown?.id, chiefMessage.id, "Offline, nothing fresh arrives and he stays")
        reconcile(&queue, [chiefMessage], fresh: 5)
        XCTAssertEqual(queue.shown?.id, chiefMessage.id, "The card is still there: he stays on it")
        queue.leaveApp(session: 5)
        reconcile(&queue, [proposal], fresh: 6)
        XCTAssertNil(queue.shown, "The card left while away: the summary")
    }

    /// Risk: skip keys and the summary disagree with what the phone shows. Skip is per
    /// card; an entry's key is sent only once all its cards are skipped; the summary
    /// counts cards, entries beyond the window count as new, and the return puts skipped
    /// entries last.
    func testSkipKeysSummaryStatesAndReturnOrder() {
        var queue = ReviewQueue()
        let all = [proposal, ticketMessage, chiefMessage]
        queue.returnToCards(); reconcile(&queue, all)
        queue.skip(proposal, in: all); reconcile(&queue, all)
        XCTAssertTrue(queue.history.skipped.isEmpty, "The Ticket's message still waits")
        XCTAssertEqual(queue.shown?.id, ticketMessage.id)
        queue.skip(ticketMessage, in: all); reconcile(&queue, all)
        XCTAssertEqual(queue.history.skipped, [RecordKey(kind: "ticket", id: "a")])
        queue.skip(chiefMessage, in: all); reconcile(&queue, all)
        XCTAssertEqual(queue.history.skipped, [RecordKey(kind: "ticket", id: "a"), RecordKey(kind: "conversation", id: "chief")])
        XCTAssertNil(queue.shown)

        let skippedOnly = queue.counts(all, unloaded: 0)
        XCTAssertEqual([skippedOnly.headline, skippedOnly.detail, skippedOnly.returnTitle], ["3 skipped.", "Nothing new.", "Review skipped"])
        let both = queue.counts(all, unloaded: 2)
        XCTAssertEqual([both.headline, both.detail, both.returnTitle], ["5 to review.", "2 new, 3 skipped.", "Back to review"])
        let fresh = ReviewQueue().counts([proposal, ask], unloaded: 0)
        XCTAssertEqual([fresh.headline, fresh.detail, fresh.returnTitle], ["2 new.", "Ready to review.", "Back to review"])
        let clear = ReviewQueue().counts([], unloaded: 0)
        XCTAssertEqual(clear.headline, "All clear."); XCTAssertEqual(clear.detail, "Nothing to review."); XCTAssertNil(clear.returnTitle)

        // A replaced proposal is a new card even though the old one was skipped.
        let replaced = ReviewItem(reviewItemType: "proposal", ticketId: "a", field: "plan", title: "A", waitingSince: 1, proposalRevision: 2)
        let newer = ReviewItem(reviewItemType: "proposal", ticketId: "b", field: "plan", title: "B", waitingSince: 5, proposalRevision: 1)
        let window = [replaced, ticketMessage, chiefMessage, newer]
        XCTAssertEqual(queue.counts(window, unloaded: 0).newCount, 2)
        // The return offers every card again, new work first and skipped entries last.
        queue.returnToCards()
        XCTAssertEqual(queue.history.priority, queue.history.skipped)
        reconcile(&queue, window)
        XCTAssertEqual(queue.cards(window, refusals: []).map(\.id), [newer.id, replaced.id, ticketMessage.id, chiefMessage.id])
        XCTAssertEqual(queue.shown?.id, newer.id)
        XCTAssertEqual(queue.counts(window, unloaded: 0).skippedCount, 0)
    }

    /// Risk: a refused decision disappears when its proposal left the window for a reason
    /// other than being decided, as the old restore needed the exact proposal queued.
    func testRefusalComesBackToTheFrontOnce() {
        var queue = ReviewQueue()
        let gone = ReviewItem(reviewItemType: "proposal", ticketId: "z", field: "plan", title: "Z", waitingSince: 9, proposalRevision: 3)
        queue.returnToCards(); reconcile(&queue, [chiefMessage, ask])
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
        let refusal = ReviewRefusal(item: gone, note: "Not sent · the reviewer changed.", summaryID: "op-1")
        reconcile(&queue, [chiefMessage, ask], refusals: [refusal])
        XCTAssertEqual(queue.shown?.id, gone.id, "The refused card comes to the front")
        XCTAssertEqual(queue.cards([chiefMessage, ask], refusals: [refusal]).first?.id, gone.id)
        queue.skip(gone, in: queue.cards([chiefMessage, ask], refusals: [refusal]))
        reconcile(&queue, [chiefMessage, ask], refusals: [refusal])
        XCTAssertEqual(queue.shown?.id, chiefMessage.id, "Each refusal comes forward once")
        // A refusal that lands while a card's exit runs comes forward once the exit ends.
        queue.resolve(chiefMessage)
        let late = ReviewRefusal(item: gone, note: "Not sent · changed.", summaryID: "op-3")
        reconcile(&queue, [ask], refusals: [late])
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
        queue.finishExit(); reconcile(&queue, [ask], refusals: [late])
        XCTAssertEqual(queue.shown?.id, gone.id)
        // A refused card still in the window moves to the front and is listed once.
        let liveRefusal = ReviewRefusal(item: ask, note: "Refused", summaryID: "op-2")
        XCTAssertEqual(queue.cards([chiefMessage, ask], refusals: [liveRefusal]).map(\.id), [ask.id, chiefMessage.id])
        // On the summary, a refusal waits in the queue; the summary stays.
        var away = ReviewQueue()
        reconcile(&away, [chiefMessage], refusals: [refusal])
        XCTAssertNil(away.shown)
        XCTAssertEqual(away.counts(away.cards([chiefMessage], refusals: [refusal]), unloaded: 0).newCount, 2)
    }

    /// Risk: a proposal that moved to another reviewer vanishes with no word, or its
    /// notice lingers after it was decided. The notice is the one card kept outside the
    /// window, and Skip moves on.
    func testReviewerMoveKeepsTheProposalAsANotice() {
        var queue = ReviewQueue()
        queue.returnToCards(); reconcile(&queue, [proposal, chiefMessage])
        reconcile(&queue, [chiefMessage], moved: [proposal.id: "Chief of Staff"])
        XCTAssertEqual(queue.shown?.id, proposal.id)
        XCTAssertEqual(queue.notice, ReviewNotice(item: proposal, reviewer: "Chief of Staff"))
        XCTAssertEqual(queue.counts(queue.cards([chiefMessage], refusals: []), unloaded: 0).newCount, 1, "The notice is not counted")
        reconcile(&queue, [chiefMessage], moved: [proposal.id: "Wren"])
        XCTAssertEqual(queue.notice?.reviewer, "Wren")
        queue.skip(proposal, in: [chiefMessage]); reconcile(&queue, [chiefMessage], moved: [proposal.id: "Wren"])
        XCTAssertNil(queue.notice)
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
        // Decided elsewhere: nothing waits, so no notice.
        var decided = ReviewQueue()
        decided.returnToCards(); reconcile(&decided, [proposal, chiefMessage])
        reconcile(&decided, [chiefMessage])
        XCTAssertNil(decided.notice)
        XCTAssertEqual(decided.shown?.id, chiefMessage.id)
    }

    /// Risk: the refusal card is built from the wrong saved decision: an earlier refusal
    /// after a later waiting decision, or a refusal with no proposal identity.
    @MainActor func testRefusalsComeFromEachProposalsLatestDecision() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalStore(url: directory.appendingPathComponent("review.store"))
        let outbox = Outbox(store: store)
        let gone = TicketProposal(field: "plan", body: "Gone", proposedBy: "worker", createdAt: 9, revision: 3)
        let retried = TicketProposal(field: "plan", body: "Again", proposedBy: "worker", createdAt: 1, revision: 1)
        try outbox.save(PendingWork(id: "refused", created: .now, kind: .approval, state: .failed, note: "Not sent · moved", ticketID: "z", proposal: gone), server: "a")
        try outbox.save(PendingWork(id: "first", created: .now, kind: .approval, state: .failed, note: "Not sent", ticketID: "a", proposal: retried), server: "a")
        try outbox.save(PendingWork(id: "second", created: .now, kind: .approval, ticketID: "a", proposal: retried), server: "a")
        let refusals = ReviewSavedWork.refusals(outbox.reviewSummaries("a"), live: [proposal]) { $0.ticketID == "z" ? "Moved Ticket" : nil }
        XCTAssertEqual(refusals.map(\.item.id), ["z:plan:9.0:revision:3"])
        XCTAssertEqual(refusals.first?.item.title, "Moved Ticket")
        XCTAssertEqual(refusals.first?.note, "Not sent · moved")
    }
}
