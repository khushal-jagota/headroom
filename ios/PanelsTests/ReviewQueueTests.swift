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
        queue.finishExit(ticketMessage); reconcile(&queue, [chiefMessage, ask])
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
        // A card handled here stays hidden while nothing else hides it, such as an answered
        // ask or a message whose run was already read, so it never comes back before then.
        queue.resolve(chiefMessage); queue.finishExit(chiefMessage); reconcile(&queue, [chiefMessage, ask])
        XCTAssertEqual(queue.shown?.id, ask.id)
        queue.resolve(ask); queue.finishExit(ask); reconcile(&queue, [chiefMessage, ask])
        XCTAssertNil(queue.shown, "After the last card, the summary")
        XCTAssertEqual(queue.counts(queue.cards([chiefMessage, ask], refusals: []), unloaded: 0), ReviewSummary(newCount: 0, skippedCount: 0))
        // Once the saved work or the window hides it, a refusal can bring it back.
        reconcile(&queue, [ask])
        XCTAssertEqual(queue.handled, [ask.id])
        XCTAssertEqual(queue.cards([chiefMessage, ask], refusals: []).map(\.id), [chiefMessage.id])
    }

    /// Risk: the summary shows at the wrong moments, or a return to the app moves the owner
    /// off a card that is still waiting. A Sidebar selection and a card that left while the app
    /// was away show the summary; a plain return keeps him where he was.
    func testSummarySelectionAndAfterTheOpenCardLeftWhileAway() {
        var queue = ReviewQueue()
        let all = [proposal, chiefMessage]
        queue.returnToCards(); reconcile(&queue, all)
        XCTAssertEqual(queue.shown?.id, proposal.id)
        queue.showSummary(); reconcile(&queue, all)
        XCTAssertNil(queue.shown, "An explicit Review selection shows the summary")

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
        // A card handled after the return is not "left while away" when the stream reconnects.
        queue.returnToCards(); reconcile(&queue, all)
        queue.leaveApp(session: 6)
        queue.resolve(proposal); queue.finishExit(proposal); reconcile(&queue, [chiefMessage])
        reconcile(&queue, [chiefMessage], fresh: 7)
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
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
        let refusal = ReviewRefusal(item: gone, note: "Not sent · the reviewer changed.", refusalID: "op-1")
        reconcile(&queue, [chiefMessage, ask], refusals: [refusal])
        XCTAssertEqual(queue.shown?.id, gone.id, "The refused card comes to the front")
        XCTAssertEqual(queue.cards([chiefMessage, ask], refusals: [refusal]).first?.id, gone.id)
        queue.skip(gone, in: queue.cards([chiefMessage, ask], refusals: [refusal]))
        reconcile(&queue, [chiefMessage, ask], refusals: [refusal])
        XCTAssertEqual(queue.shown?.id, chiefMessage.id, "Each refusal comes forward once")
        // A refusal that lands while a card's exit runs comes forward once the exit ends.
        queue.resolve(chiefMessage)
        let late = ReviewRefusal(item: gone, note: "Not sent · changed.", refusalID: "op-3")
        reconcile(&queue, [ask], refusals: [late])
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
        queue.finishExit(chiefMessage); reconcile(&queue, [ask], refusals: [late])
        XCTAssertEqual(queue.shown?.id, gone.id)
        // A refused card still in the window moves to the front and is listed once.
        let liveRefusal = ReviewRefusal(item: ask, note: "Refused", refusalID: "op-2")
        XCTAssertEqual(queue.cards([chiefMessage, ask], refusals: [liveRefusal]).map(\.id), [ask.id, chiefMessage.id])
        // On the summary, a refusal waits in the queue; the summary stays.
        var away = ReviewQueue()
        reconcile(&away, [chiefMessage], refusals: [refusal])
        XCTAssertNil(away.shown)
        XCTAssertEqual(away.counts(away.cards([chiefMessage], refusals: [refusal]), unloaded: 0).newCount, 2)
    }

    /// Risk: a card handled here comes back before saved work or the window hides it: a
    /// refusal already seen forcing a handled card forward (review fault 1), or an answered
    /// ask flashing back when its answer settles before the window drops it (fault 6).
    func testHandledCardsStayHiddenUntilSavedWorkOrTheWindowCatchesUp() {
        var queue = ReviewQueue()
        queue.returnToCards()
        let refusal = ReviewRefusal(item: chiefMessage, note: "Refused", refusalID: "read@1")
        queue.reconcile([chiefMessage, ask], refusals: [refusal], moved: { _ in nil }, freshSession: nil)
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
        queue.resolve(chiefMessage); queue.finishExit(chiefMessage)
        queue.reconcile([chiefMessage, ask], refusals: [refusal], moved: { _ in nil }, freshSession: nil)
        XCTAssertEqual(queue.shown?.id, ask.id, "A refusal already seen cannot bring back a card handled since")
        XCTAssertFalse(queue.cards([chiefMessage, ask], refusals: [refusal]).contains { $0.id == chiefMessage.id })
        // A new refusal of the same card brings it back.
        let again = ReviewRefusal(item: chiefMessage, note: "Refused again", refusalID: "read@2")
        queue.reconcile([chiefMessage, ask], refusals: [again], moved: { _ in nil }, freshSession: nil)
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
        // The answered ask: saved work hides it while the answer waits, then the answer is
        // sent while the window still holds the ask.
        queue.skip(chiefMessage, in: queue.cards([chiefMessage, ask], refusals: [again]))
        queue.reconcile([chiefMessage, ask], refusals: [again], moved: { _ in nil }, freshSession: nil)
        XCTAssertEqual(queue.shown?.id, ask.id)
        queue.resolve(ask); queue.finishExit(ask)
        queue.reconcile([chiefMessage], refusals: [again], moved: { _ in nil }, freshSession: nil, window: [chiefMessage, ask])
        queue.reconcile([chiefMessage, ask], refusals: [again], moved: { _ in nil }, freshSession: nil)
        XCTAssertFalse(queue.cards([chiefMessage, ask], refusals: [again]).contains { $0.id == ask.id }, "Hidden until the window drops it")
        queue.reconcile([chiefMessage], refusals: [again], moved: { _ in nil }, freshSession: nil)
        XCTAssertTrue(queue.handled.isEmpty)
    }

    /// Risk: the skipped keys the server is told about go stale (review fault 7): an entry
    /// with a new card stays "skipped", or one whose last unskipped card was handled is missed.
    func testSkippedKeysFollowTheCurrentCards() {
        var queue = ReviewQueue()
        queue.returnToCards(); reconcile(&queue, [proposal, ticketMessage, chiefMessage])
        queue.skip(proposal, in: [proposal, ticketMessage, chiefMessage]); reconcile(&queue, [proposal, ticketMessage, chiefMessage])
        XCTAssertTrue(queue.history.skipped.isEmpty)
        // The Ticket's message is read elsewhere: its entry is now fully skipped.
        reconcile(&queue, [proposal, chiefMessage])
        XCTAssertEqual(queue.history.skipped, [RecordKey(kind: "ticket", id: "a")])
        // A new message on the same Ticket: the entry waits again.
        let newer = ReviewItem(reviewItemType: "message", title: "A", waitingSince: 6, conversationId: "a-other", ownerKind: "ticket", ownerId: "a")
        reconcile(&queue, [proposal, chiefMessage, newer])
        XCTAssertTrue(queue.history.skipped.isEmpty)
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
    /// Risk: a refusal card is built from the wrong saved attempt. A proposal's card comes
    /// from its latest decision, and one outside the window only while the view shows it
    /// still waiting. A message card comes only from its conversation's latest reply or
    /// Mark read, until the server's read point passes it; an older refused row cannot
    /// force a handled card back (review fault 1), and the same item refused again after
    /// Try Again is a new refusal (fault 3). Fault 5: an unknown proposal is not shown.
    @MainActor func testRefusalsComeFromEachCardsLatestAttempt() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalStore(url: directory.appendingPathComponent("review.store"))
        let outbox = Outbox(store: store)
        let gone = TicketProposal(field: "plan", body: "Gone", proposedBy: "worker", createdAt: 9, revision: 3)
        let retried = TicketProposal(field: "plan", body: "Again", proposedBy: "worker", createdAt: 1, revision: 1)
        try outbox.save(PendingWork(id: "refused", created: .now, kind: .approval, state: .failed, note: "Not sent · moved", ticketID: "z", proposal: gone), server: "a")
        try outbox.save(PendingWork(id: "first", created: .now, kind: .approval, state: .failed, note: "Not sent", ticketID: "a", proposal: retried), server: "a")
        try outbox.save(PendingWork(id: "second", created: .now, kind: .approval, ticketID: "a", proposal: retried), server: "a")
        let refusals = ReviewSavedWork.refusals(outbox.reviewSummaries("a"), live: [proposal], title: { $0.ticketID == "z" ? "Moved Ticket" : nil }, waits: { _ in true })
        XCTAssertEqual(refusals.map(\.item.id), ["z:plan:9.0:revision:3"])
        XCTAssertEqual(refusals.first?.item.title, "Moved Ticket")
        XCTAssertEqual(refusals.first?.note, "Not sent · moved")
        // Decided, replaced or not carried by the view: nothing is shown or counted.
        XCTAssertTrue(ReviewSavedWork.refusals(outbox.reviewSummaries("a"), live: [], title: { _ in nil }, waits: { _ in false }).isEmpty)
        XCTAssertTrue(ReviewSavedWork.refusals(outbox.reviewSummaries("a"), live: [], title: { _ in nil }).isEmpty)

        // A refused reply brings its message card forward.
        let reply = ConversationSend(conversationId: "chief", content: [.init(text: "Refused reply")], senderMessageId: "reply", sentAtUnixMilliseconds: 1, ownerReadThroughSequence: 3)
        var refusedReply = PendingWork(id: "reply", created: .now, kind: .message, state: .failed, note: "Not sent · refused", owner: .chief, message: reply)
        refusedReply.completed = Date(timeIntervalSince1970: 100)
        try outbox.save(refusedReply, server: "a")
        let withReply = ReviewSavedWork.refusals(outbox.reviewSummaries("a"), live: [chiefMessage, proposal], title: { _ in nil })
        XCTAssertEqual(withReply.first?.item.id, chiefMessage.id)
        XCTAssertEqual(withReply.first?.note, "Not sent · refused")
        // A later read point, past what the reply answered, settles it.
        var later = chiefMessage; later.ownerReadThroughSequence = 3
        XCTAssertFalse(ReviewSavedWork.refusals(outbox.reviewSummaries("a"), live: [later], title: { _ in nil }).contains { $0.item.isMessage })
        // Refused again after Try Again kept its identity: a new refusal.
        refusedReply.completed = Date(timeIntervalSince1970: 200)
        try outbox.save(refusedReply, server: "a")
        let again = ReviewSavedWork.refusals(outbox.reviewSummaries("a"), live: [chiefMessage], title: { _ in nil })
        XCTAssertNotEqual(again.first?.refusalID, withReply.first?.refusalID)
        // A newer Mark read settles the refused reply, so the handled card stays handled.
        try store.outgoing.enqueueMarkRead(server: "a", owner: .chief, conversationID: "chief", through: 4)
        XCTAssertFalse(ReviewSavedWork.refusals(outbox.reviewSummaries("a"), live: [chiefMessage], title: { _ in nil }).contains { $0.item.isMessage })
    }

    /// Risk: the queue freezes for the rest of the run. A reviewer move on the stream closes
    /// the Reviewer sheet before the save returns; a save that then waits for the close
    /// keeps its card leaving forever. Either order must leave exactly once.
    func testSavedDecisionLeavesWhetherTheSheetClosesBeforeOrAfterTheSave() {
        var exit = ReviewCardExit()
        XCTAssertFalse(exit.saved(sheetOpen: true), "An open sheet holds the card until it closes")
        XCTAssertTrue(exit.dismissed())
        XCTAssertFalse(exit.dismissed(), "One close leaves once")
        // The stream closed the sheet first: its close found nothing saved, so the save leaves.
        var raced = ReviewCardExit()
        XCTAssertFalse(raced.dismissed())
        XCTAssertTrue(raced.saved(sheetOpen: false))
        XCTAssertFalse(raced.dismissed())
    }

    /// Risk: the queue freezes for the rest of the run. the owner moves a proposal to Chief,
    /// and the app goes to the background before the Reviewer sheet closes. The card is
    /// leaving, so it is not "the card he was on": a fresh window on the return must not
    /// swap its exit for the summary, which removes the card and its sheet before the sheet
    /// reports closing. The exit finishes and the queue moves on.
    func testACardLeavingWhenTheAppGoesAwayFinishesItsExitOnTheReturn() {
        var queue = ReviewQueue()
        let all = [proposal, ticketMessage, chiefMessage]
        queue.returnToCards(); reconcile(&queue, all)
        XCTAssertEqual(queue.shown?.id, proposal.id)
        queue.resolve(proposal)
        queue.leaveApp(session: 4)
        // The server has not caught up yet: the window still has the moved proposal.
        reconcile(&queue, all, fresh: 5)
        XCTAssertEqual(queue.shown?.id, proposal.id, "The exit still runs on the return")
        queue.finishExit(proposal); reconcile(&queue, all)
        XCTAssertEqual(queue.shown?.id, ticketMessage.id, "The queue moves on")
        XCTAssertNil(queue.leaving)
    }

    /// Risk: a Review selection during an exit leaves a held card or a late callback
    /// changes the summary. The saved decision stays handled, and skip history stays intact.
    func testSummarySelectionAbandonsTheExitAndIgnoresLateCallbacks() {
        var queue = ReviewQueue()
        let all = [proposal, ticketMessage, chiefMessage]
        queue.returnToCards(); reconcile(&queue, all)
        queue.skip(proposal, in: all); reconcile(&queue, all)
        queue.resolve(ticketMessage); reconcile(&queue, all)
        let skipped = queue.skipped
        let history = queue.history
        queue.showSummary()
        queue.finishExit(ticketMessage)
        queue.resolve(ticketMessage)
        queue.skip(ticketMessage, in: all)
        reconcile(&queue, all)
        XCTAssertNil(queue.shown)
        XCTAssertNil(queue.leaving)
        XCTAssertNil(queue.pinned)
        XCTAssertEqual(queue.handled, [ticketMessage.id])
        XCTAssertEqual(queue.skipped, skipped)
        XCTAssertEqual(queue.history, history)
        queue.showSummary(); reconcile(&queue, all)
        XCTAssertNil(queue.shown, "A repeated selection stays on the summary")
    }

    /// Risk: the same card opens again before its old exit callback runs. Identity alone
    /// cannot distinguish the new presentation from the abandoned one.
    @MainActor func testSummarySelectionInvalidatesTheReopenedCardPresentation() {
        let store = AppStore(baseURL: URL(string: "http://127.0.0.1:1")!)
        store.review = ReviewResponse(items: [proposal, chiefMessage], runningWorkerCount: 0)
        let session = ReviewSession()
        session.update(store: store, savedWork: ReviewSavedWork(), freshSession: nil)
        session.returnToCards(store: store)
        let oldPresentation = session.exits
        XCTAssertTrue(session.presents(proposal, at: oldPresentation))
        session.showSummary()
        session.returnToCards(store: store)
        XCTAssertEqual(session.shown?.id, proposal.id)
        XCTAssertFalse(session.presents(proposal, at: oldPresentation))
        XCTAssertTrue(session.presents(proposal, at: session.exits))
    }

    /// Risk: one saved reply moves the queue on twice. The card that sent it and an artifact
    /// opened from it both see the reply land: the artifact's dock finishes the exit at once,
    /// then the card's exit timer resolves and finishes again. The late calls must not bring
    /// the card back or end the next card's exit.
    func testASavedReplySeenByTwoSurfacesLeavesOnce() {
        var queue = ReviewQueue()
        queue.returnToCards()
        reconcile(&queue, [ticketMessage, chiefMessage, ask])
        XCTAssertEqual(queue.shown?.id, ticketMessage.id)
        // The card saves the reply and starts its exit; the artifact's dock sees the same save.
        queue.resolve(ticketMessage)
        queue.resolve(ticketMessage); queue.finishExit(ticketMessage)
        reconcile(&queue, [chiefMessage, ask])
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
        // The next card is handled and leaving when the first card's late calls arrive.
        queue.resolve(chiefMessage)
        queue.resolve(ticketMessage); queue.finishExit(ticketMessage)
        reconcile(&queue, [ask])
        XCTAssertEqual(queue.leaving?.id, chiefMessage.id, "A late finish leaves the next card's exit alone")
        XCTAssertEqual(queue.shown?.id, chiefMessage.id)
        queue.finishExit(chiefMessage); reconcile(&queue, [ask])
        XCTAssertEqual(queue.shown?.id, ask.id)
    }

    /// Risk: an ask with no recorded time shares its identity with the next ask in its
    /// conversation, so a follow-up ask stays hidden behind an answered one.
    func testAnAskWithNoTimeIsNotHeldPastItsAnswer() {
        let timeless = ReviewItem(reviewItemType: "ask", title: "Manager", waitingSince: ReviewItem.unknownAskTime,
                                  conversationId: "manager", ownerKind: "manager", ownerId: "m")
        var queue = ReviewQueue()
        queue.returnToCards()
        queue.reconcile([timeless], refusals: [], moved: { _ in nil }, freshSession: nil)
        queue.resolve(timeless); queue.finishExit(timeless)
        // The answer is sent; the window still carries an ask under the same identity.
        queue.reconcile([], refusals: [], moved: { _ in nil }, freshSession: nil, window: [timeless])
        queue.reconcile([timeless], refusals: [], moved: { _ in nil }, freshSession: nil)
        XCTAssertTrue(queue.cards([timeless], refusals: []).contains { $0.id == timeless.id }, "The follow-up ask shows")
    }
}
