import Foundation
import XCTest
@testable import Panels

@MainActor final class ReviewSavedWorkTests: XCTestCase {
    private func temporaryStore() throws -> (LocalStore, URL) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        return (LocalStore(url: directory.appendingPathComponent("review.store")), directory)
    }
    private func milliseconds(_ operation: () -> Void) -> Double {
        let start = ContinuousClock.now
        operation()
        let elapsed = start.duration(to: .now).components
        return Double(elapsed.seconds) * 1_000 + Double(elapsed.attoseconds) / 1e15
    }
    private func cards(_ count: Int) -> [ReviewItem] {
        (0..<count).map { index in
            ReviewItem(reviewItemType: "proposal", ticketId: "ticket-\(index)", field: "brief", title: "Ticket", waitingSince: 1, proposalRevision: 2)
        }
    }
    func testQueueReadCountAndHostTimingsAcrossRetainedWorkloads() throws {
        for retained in [0, 100, 1_000] {
            let (store, directory) = try temporaryStore()
            defer { try? FileManager.default.removeItem(at: directory) }
            let outbox = Outbox(store: store)
            for index in 0..<retained {
                let message = ConversationSend(conversationId: "history", content: [.init(text: "Retained outgoing message \(index)")], senderMessageId: "receipt-\(index)", sentAtUnixMilliseconds: Int64(index))
                try outbox.save(PendingWork(id: "receipt-\(index)", created: Date(timeIntervalSince1970: Double(index)), kind: .message, state: .sent, message: message), server: "a")
            }
            var reads = 0
            let read: (String) -> [OutgoingStore.Summary] = { server in reads += 1; return outbox.reviewSummaries(server) }
            let snapshot = ReviewSavedWork(outbox: outbox, read: read)
            let cold = milliseconds { snapshot.refresh("a") }
            XCTAssertEqual(reads, 1)
            for partCount in [1, 30] {
                let queue = cards(partCount)
                reads = 0
                var baseline: [ReviewItem] = []
                let before = milliseconds {
                    // The old Review getter performed this full read inside each card.
                    baseline = queue.filter { item in
                        reads += 1
                        let records = (try? store.records(server: "a", kind: "outbox")) ?? []
                        let decoded: [PendingWork] = records.compactMap { try? JSONDecoder().decode(PendingWork.self, from: $0.payload) }
                        let decisions = decoded.filter { $0.kind == .approval || $0.kind == .sendBack }
                        return !decisions.contains { $0.ticketID == item.ticketId && $0.proposal?.field == item.field && $0.proposal?.createdAt == item.waitingSince && $0.state != .failed }
                    }
                }
                XCTAssertEqual(reads, partCount)
                reads = 0
                var result: [ReviewItem] = []
                let after = milliseconds { result = ReviewSavedWork.cards(queue, decisions: snapshot.decisions("a")) }
                XCTAssertEqual(result.map(\.id), baseline.map(\.id))
                for _ in 0..<20 {
                    snapshot.refresh("a")
                    _ = ReviewSavedWork.cards(queue, decisions: snapshot.decisions("a"))
                }
                XCTAssertEqual(reads, 0)
                print(String(format: "REVIEW_BENCH retained=%d parts=%d before_ms=%.3f before_reads=%d cold_snapshot_ms=%.3f cold_reads=1 warm_queue_ms=%.3f warm_reads=0 repeated_reads=0", retained, partCount, before, partCount, cold, after))
            }
        }
    }
    func testSnapshotInvalidatesOnlyForChangedOutgoingWorkAndKeepsServersSeparate() throws {
        let (store, directory) = try temporaryStore()
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = Outbox(store: store)
        var reads = 0
        let snapshot = ReviewSavedWork(outbox: outbox) { server in reads += 1; return outbox.reviewSummaries(server) }
        let proposal = TicketProposal(field: "brief", body: "Read this", proposedBy: "worker", createdAt: 1, revision: 2)
        var work = PendingWork(id: "decision", created: .now, kind: .approval, ticketID: "ticket-0", proposal: proposal)
        try outbox.save(work, server: "a")
        snapshot.refresh("a")
        XCTAssertEqual(reads, 1)
        XCTAssertTrue(ReviewSavedWork.cards(cards(1), decisions: snapshot.decisions("a")).isEmpty)
        let original = cards(1)[0]
        let replacement = ReviewItem(reviewItemType: "proposal", ticketId: original.ticketId, field: original.field,
            title: original.title, waitingSince: original.waitingSince, proposalRevision: 3)
        let unknown = ReviewItem(reviewItemType: "proposal", ticketId: original.ticketId, field: original.field,
            title: original.title, waitingSince: original.waitingSince)
        XCTAssertNotEqual(original.id, replacement.id, "Same-second replacement has its own identity")
        XCTAssertEqual(ReviewSavedWork.cards([replacement], decisions: snapshot.decisions("a")).first?.id, replacement.id)
        XCTAssertEqual(ReviewSavedWork.cards([unknown], decisions: snapshot.decisions("a")).count, 1)
        XCTAssertEqual(ReviewSavedWork.pendingDecisionTickets(in: cards(1), decisions: snapshot.decisions("a")), [original.ticketId])
        XCTAssertTrue(ReviewSavedWork.pendingDecisionTickets(in: [replacement], decisions: snapshot.decisions("a")).isEmpty)
        XCTAssertTrue(ReviewSavedWork.pendingDecisionTickets(in: [unknown], decisions: snapshot.decisions("a")).isEmpty)
        XCTAssertTrue(ReviewSavedWork.pendingDecisionTickets(in: [], decisions: snapshot.decisions("a")).isEmpty)
        XCTAssertTrue(snapshot.decisions("b").isEmpty)
        let revision = store.outgoingRevision("a")
        try outbox.save(work, server: "a")
        store.saveDraft("Typed", server: "a", id: "draft")
        try store.remove(server: "a", kind: "draft", id: "draft")
        try store.put(server: "a", kind: "cache", id: "cache", data: Data([1]))
        try store.remove(server: "a", kind: "outbox", id: "absent")
        try outbox.save(work, server: "b")
        XCTAssertEqual(store.outgoingRevision("a"), revision)
        snapshot.refresh("a")
        XCTAssertEqual(reads, 1)
        work.state = .failed
        try outbox.save(work, server: "a")
        snapshot.refresh("a")
        XCTAssertEqual(reads, 2)
        XCTAssertEqual(ReviewSavedWork.cards(cards(1), decisions: snapshot.decisions("a")).count, 1)
        snapshot.refresh("b")
        XCTAssertEqual(reads, 3)
        XCTAssertEqual(snapshot.decisions("b").first?.state, "waiting")
        XCTAssertTrue(snapshot.decisions("a").isEmpty)
        snapshot.refresh("a")
        XCTAssertEqual(reads, 4)
        XCTAssertEqual(snapshot.decisions("a").first?.state, "failed")
        try store.remove(server: "a", kind: "outbox", id: work.id)
        snapshot.refresh("a")
        XCTAssertEqual(reads, 5)
        XCTAssertTrue(snapshot.decisions("a").isEmpty)
    }
    /// A saved mark read or reply takes its messages off Review at once. A message that
    /// arrives after the server's read point catches up shows again, and a refusal brings it back.
    func testHandledMessagesLeaveReviewAtOnceUntilTheServerCatchesUp() throws {
        let (store, directory) = try temporaryStore()
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = Outbox(store: store)
        let snapshot = ReviewSavedWork(outbox: outbox)
        func card(readThrough: Int) -> [ReviewItem] {
            [ReviewItem(reviewItemType: "message", title: "Chief", waitingSince: 1, conversationId: "c", ownerKind: "chief", ownerId: "chief", ownerReadThroughSequence: readThrough)]
        }
        try store.outgoing.enqueueMarkRead(server: "a", owner: .chief, conversationID: "c", through: 9)
        snapshot.refresh("a")
        XCTAssertTrue(ReviewSavedWork.cards(card(readThrough: 4), decisions: snapshot.decisions("a")).isEmpty, "Hidden while the read waits")
        XCTAssertEqual(ReviewSavedWork.cards(card(readThrough: 9), decisions: snapshot.decisions("a")).count, 1, "A newer message after the read shows again")
        let id = try XCTUnwrap(snapshot.decisions("a").first?.id)
        try store.outgoing.settle(server: "a", id: id, state: .failed, note: "Refused")
        snapshot.refresh("a")
        XCTAssertEqual(ReviewSavedWork.cards(card(readThrough: 4), decisions: snapshot.decisions("a")).count, 1, "A refusal brings it back")
    }
}
