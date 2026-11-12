import Foundation
import SwiftData
import Observation
import SwiftData
import os
import XCTest
@testable import Panels

@MainActor final class OfflineTests: XCTestCase {
    func testCacheEvictionCannotEvictDraftOrOutboxAndServerPartitionsSurviveReopen() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("test.store")
        let store = LocalStore(url: url)
        try store.put(server: "a", kind: "draft", id: "text", data: Data("Keep my words".utf8))
        try store.put(server: "a", kind: "outbox", id: "send", data: Data("same identity".utf8))
        for index in 0..<100 { try store.put(server: "a", kind: "cache", id: String(index), data: Data([1])) }
        let reopened = LocalStore(url: url)
        XCTAssertEqual(reopened.draft(server: "a", id: "text"), "Keep my words")
        XCTAssertNil(reopened.draft(server: "b", id: "text"))
        XCTAssertEqual(try reopened.records(server: "a", kind: "cache").count, LocalStore.cacheLimit)
        XCTAssertEqual(try reopened.record(server: "a", kind: "outbox", id: "send")?.payload, Data("same identity".utf8))
    }
    func testBackgroundCacheRefreshesIdenticalPayloadAndReadsLatestAcrossContexts() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("test.store")
        let store = LocalStore(url: url)
        store.saveDraft("Keep", server: "a", id: "draft")
        let revision = store.workRevision
        try await store.cacheResponse(server: "a", id: "same", data: Data([1]), updated: Date(timeIntervalSince1970: 1))
        XCTAssertEqual(try store.record(server: "a", kind: "cache", id: "same")?.payload, Data([1]))
        store.saveError = "Couldn’t save on this phone: temporary failure"
        try await store.cacheResponse(server: "a", id: "same", data: Data([1]), updated: Date(timeIntervalSince1970: 3))
        XCTAssertNil(store.saveError)
        XCTAssertEqual(try store.record(server: "a", kind: "cache", id: "same")?.updated, Date(timeIntervalSince1970: 3))
        try await store.cacheResponse(server: "a", id: "same", data: Data([2]), updated: Date(timeIntervalSince1970: 4))
        XCTAssertEqual(try store.record(server: "a", kind: "cache", id: "same")?.payload, Data([2]))
        XCTAssertNil(try store.record(server: "b", kind: "cache", id: "same"))
        XCTAssertEqual(store.workRevision, revision)
        let reopened = LocalStore(url: url)
        XCTAssertEqual(try reopened.record(server: "a", kind: "cache", id: "same")?.payload, Data([2]))
        XCTAssertEqual(reopened.draft(server: "a", id: "draft"), "Keep")
    }
    func testBackgroundCacheEvictsByRefreshedAgeAndBytesWithoutTouchingWork() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalStore(url: directory.appendingPathComponent("test.store"))
        store.saveDraft("Keep", server: "a", id: "draft")
        for index in 0..<LocalStore.cacheLimit {
            try await store.cacheResponse(server: "a", id: String(index), data: Data([1]), updated: Date(timeIntervalSince1970: Double(index)))
        }
        try await store.cacheResponse(server: "a", id: "0", data: Data([1]), updated: Date(timeIntervalSince1970: 100))
        try await store.cacheResponse(server: "b", id: "new", data: Data([1]), updated: Date(timeIntervalSince1970: 101))
        XCTAssertNotNil(try store.record(server: "a", kind: "cache", id: "0"))
        XCTAssertNil(try store.record(server: "a", kind: "cache", id: "1"))
        try await store.cacheResponse(server: "b", id: "large", data: Data(repeating: 2, count: LocalStore.cacheByteLimit), updated: Date(timeIntervalSince1970: 102))
        XCTAssertNil(try store.record(server: "a", kind: "cache", id: "0"))
        XCTAssertNil(try store.record(server: "b", kind: "cache", id: "new"))
        try await store.cacheResponse(server: "b", id: "too-large", data: Data(count: LocalStore.cacheByteLimit + 1))
        XCTAssertNil(try store.record(server: "b", kind: "cache", id: "too-large"))
        XCTAssertEqual(store.draft(server: "a", id: "draft"), "Keep")
    }
    func testCachedReadsAndPlainDraftsDoNotInvalidateOutgoingWorkButEnqueueAndRemovalDo() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalStore(url: directory.appendingPathComponent("test.store"))
        let outbox = Outbox(store: store)
        let changes = OSAllocatedUnfairLock(initialState: 0)
        withObservationTracking { _ = outbox.entries("a") } onChange: { changes.withLock { $0 += 1 } }
        try store.put(server: "a", kind: "cache", id: "read", data: Data([1]))
        try store.put(server: "a", kind: "cache", id: "read", data: Data([2]))
        try store.remove(server: "a", kind: "cache", id: "read")
        XCTAssertEqual(changes.withLock { $0 }, 0)
        let message = ConversationSend(conversationId: "c", content: [.init(text: "Keep this")], senderMessageId: "stable", sentAtUnixMilliseconds: 1)
        let work = PendingWork(id: "stable", created: .now, kind: .message, owner: .chief, message: message)
        try store.put(server: "a", kind: "outbox", id: work.id, data: JSONEncoder().encode(work), draft: ("draft", ""))
        XCTAssertEqual(changes.withLock { $0 }, 1)
        XCTAssertEqual(outbox.entries("a").map(\.id), ["stable"])
        withObservationTracking { _ = outbox.entries("a") } onChange: { changes.withLock { $0 += 1 } }
        try store.remove(server: "a", kind: "outbox", id: work.id)
        XCTAssertEqual(changes.withLock { $0 }, 2)
        XCTAssertTrue(outbox.entries("a").isEmpty)
        // A key press saves the draft. It must not wake every saved-work observer.
        withObservationTracking { _ = store.workRevision } onChange: { changes.withLock { $0 += 1 } }
        store.saveDraft("Keep the typed words", server: "a", id: "text")
        XCTAssertEqual(changes.withLock { $0 }, 2)
        XCTAssertEqual(store.draft(server: "a", id: "text"), "Keep the typed words")
    }
    func testClearFinishedRemovesSettledWorkAtItsWindowInOneSaveAndKeepsTheRest() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalStore(url: directory.appendingPathComponent("test.store"))
        let outbox = Outbox(store: store)
        let now = Date(), day: TimeInterval = 24 * 60 * 60
        func work(_ id: String, _ state: PendingWork.State, age: TimeInterval, completedAge: TimeInterval? = nil) -> PendingWork {
            PendingWork(id: id, created: now.addingTimeInterval(-age), kind: .message, state: state, completed: completedAge.map { now.addingTimeInterval(-$0) })
        }
        let rows = [
            work("old-sent", .sent, age: 3 * day, completedAge: day), work("recent-sent", .sent, age: 3 * day, completedAge: day - 60),
            // Changed work never records completion, so its creation time counts.
            work("old-changed", .changed, age: day), work("recent-changed", .changed, age: day - 60),
            work("old-uncertain", .uncertain, age: 7 * day), work("recent-uncertain", .uncertain, age: 7 * day - 60),
            work("failed", .failed, age: 30 * day), work("waiting", .waiting, age: 30 * day), work("sending", .sending, age: 30 * day),
            work("queued", .serverQueued, age: 30 * day), work("recording", .recording, age: 30 * day),
        ]
        for row in rows { try outbox.save(row, server: "a") }
        try outbox.save(work("other-server", .sent, age: 3 * day, completedAge: 2 * day), server: "b")
        let revision = store.workRevision
        outbox.clearFinished("a", now: now)
        XCTAssertEqual(store.workRevision, revision + 1)
        XCTAssertEqual(Set(outbox.entries("a").map(\.id)), ["recent-sent", "recent-changed", "recent-uncertain", "failed", "waiting", "sending", "queued", "recording"])
        XCTAssertEqual(outbox.entries("b").map(\.id), ["other-server"])
        outbox.clearFinished("a", now: now)
        XCTAssertEqual(store.workRevision, revision + 1)
        for index in 0...OutgoingStore.expiryBatchLimit { try outbox.save(work("batch-\(index)", .sent, age: 3 * day), server: "a") }
        let started = ContinuousClock.now
        outbox.clearFinished("a", now: now)
        let elapsed = started.duration(to: .now).components
        print("EXPIRY_BATCH rows=\(OutgoingStore.expiryBatchLimit) seconds=\(elapsed.seconds) attoseconds=\(elapsed.attoseconds)")
        XCTAssertEqual(outbox.entries("a").filter { $0.id.hasPrefix("batch-") }.count, 1)
        outbox.clearFinished("a", now: now)
        XCTAssertTrue(outbox.entries("a").allSatisfy { !$0.id.hasPrefix("batch-") })
    }
    func testEnqueueAndComposerClearShareDurableSave() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("test.store")
        let store = LocalStore(url: url)
        store.saveDraft("Unsent", server: "a", id: "draft")
        let attachments = Data("[{\"name\":\"photo.jpg\",\"bytes\":\"AQID\"}]".utf8)
        try store.put(server: "a", kind: "attachments", id: "draft", data: attachments)
        let outgoing = try store.outgoing
        let capture = try outgoing.captureDraft(server: "a", id: "draft")
        let work = pending("stable", conversation: "c")
        try outgoing.enqueue(work, server: "a", capturedDraft: capture)
        let reopened = LocalStore(url: url)
        XCTAssertEqual(reopened.draft(server: "a", id: "draft"), "")
        XCTAssertEqual(try reopened.record(server: "a", kind: "attachments", id: "draft")?.payload, Data("[]".utf8))
        let saved = try XCTUnwrap(reopened.outgoing.work(server: "a", id: "stable"))
        XCTAssertEqual(saved.message?.senderMessageId, "stable")
        XCTAssertEqual(saved.message?.sentAtUnixMilliseconds, 123)
        XCTAssertEqual(saved.displayText, "stable")
        // A captured composer version never clears newer words or attachments.
        store.saveDraft("Captured", server: "a", id: "draft")
        let older = try outgoing.captureDraft(server: "a", id: "draft")
        store.saveDraft("Newer words", server: "a", id: "draft")
        try store.put(server: "a", kind: "attachments", id: "draft", data: attachments)
        try outgoing.enqueue(pending("next", conversation: "c"), server: "a", capturedDraft: older)
        XCTAssertEqual(store.draft(server: "a", id: "draft"), "Newer words")
        XCTAssertEqual(try store.record(server: "a", kind: "attachments", id: "draft")?.payload, attachments)
        let revision = store.outgoingRevision("a")
        XCTAssertThrowsError(try outgoing.enqueue(work, server: "a", capturedDraft: try outgoing.captureDraft(server: "a", id: "draft")))
        // Rejected duplicate intent leaves all transaction participants unchanged.
        XCTAssertEqual(store.outgoingRevision("a"), revision)
        let afterRejection = LocalStore(url: url)
        XCTAssertEqual(afterRejection.draft(server: "a", id: "draft"), "Newer words")
        XCTAssertEqual(try afterRejection.record(server: "a", kind: "attachments", id: "draft")?.payload, attachments)
        XCTAssertEqual(try afterRejection.outgoing.summary(server: "a", id: "next")?.predecessorID, "stable")
    }
    private func pending(_ id: String, conversation: String? = nil, created: Date = .now) -> PendingWork {
        PendingWork(id: id, created: created, kind: .message, owner: .chief,
            message: ConversationSend(conversationId: conversation, content: [.init(text: id)], backendKey: "backend", model: "model", reasoningEffort: "high", senderMessageId: id, sentAtUnixMilliseconds: 123))
    }
    func testReplacementKeepsLogicalPositionAndLateReceiptsKeepTheirOwnIdentityAcrossRestart() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("test.store")
        let store = LocalStore(url: url), server = "a"
        let outgoing = try store.outgoing
        try outgoing.enqueue(pending("A"), server: server)
        try outgoing.enqueue(pending("B"), server: server)
        let rawA = try XCTUnwrap(store.record(server: server, kind: "outbox", id: "A")?.payload)
        let rawB = try XCTUnwrap(store.record(server: server, kind: "outbox", id: "B")?.payload)
        XCTAssertTrue(try outgoing.canSubmit(server: server, id: "A"))
        XCTAssertFalse(try outgoing.canSubmit(server: server, id: "B"))
        try outgoing.beginAttempt(server: server, id: "A")
        try outgoing.settle(server: server, id: "A", state: .uncertain, note: "Delivery uncertain")
        XCTAssertThrowsError(try outgoing.replace(server: server, id: "A", with: pending("B")))
        XCTAssertEqual(try outgoing.summary(server: server, id: "A")?.scheduled, true)
        XCTAssertEqual(try outgoing.summary(server: server, id: "B")?.predecessorID, "A")
        var aliased = pending("A2")
        aliased.priorMessageIDs = ["A"]
        XCTAssertThrowsError(try outgoing.replace(server: server, id: "A", with: aliased))
        try outgoing.replace(server: server, id: "A", with: pending("A2"))
        let position = try outgoing.summary(server: server, id: "A")?.sequence
        XCTAssertEqual(try outgoing.summary(server: server, id: "A2")?.sequence, position)
        XCTAssertEqual(try outgoing.summary(server: server, id: "B")?.predecessorID, "A2")
        XCTAssertEqual(try outgoing.summary(server: server, id: "A")?.scheduled, false)
        XCTAssertEqual(try outgoing.summary(server: server, id: "A2")?.holdReason, "destinationUnresolved")
        XCTAssertFalse(try outgoing.canSubmit(server: server, id: "A2"))
        XCTAssertNil(try outgoing.work(server: server, id: "A2")?.priorMessageIDs)
        // The original succeeds late. It must remain visible without binding B to its destination.
        try outgoing.settle(server: server, id: "A", state: .sent, note: "Sent", conversationID: "original-conversation")
        XCTAssertNil(try outgoing.work(server: server, id: "B")?.message?.conversationId)
        XCTAssertFalse(try outgoing.canSubmit(server: server, id: "B"))
        XCTAssertThrowsError(try outgoing.replace(server: server, id: "A2", with: pending("B")))
        let reopened = LocalStore(url: url)
        let resumed = try reopened.outgoing
        XCTAssertEqual(try resumed.work(server: server, id: "A")?.state, .sent)
        XCTAssertEqual(try resumed.summary(server: server, id: "B")?.predecessorID, "A2")
        try resumed.releaseHold(server: server, id: "A2", destination: .newConversation)
        try resumed.beginAttempt(server: server, id: "A2")
        var explicit = pending("explicit")
        explicit.message?.model = "different-model"; explicit.message?.reasoningEffort = nil; explicit.message?.reasoningEffortIsSet = true
        try resumed.enqueue(explicit, server: server)
        try resumed.enqueue(pending("revert"), server: server)
        try resumed.settle(server: server, id: "A2", state: .serverQueued, note: "Queued on server", conversationID: "replacement-conversation")
        XCTAssertTrue(try resumed.canSubmit(server: server, id: "B"))
        let bound = try XCTUnwrap(resumed.work(server: server, id: "B")?.message)
        XCTAssertEqual(bound.conversationId, "replacement-conversation")
        XCTAssertNil(bound.backendKey); XCTAssertNil(bound.model); XCTAssertNil(bound.reasoningEffort)
        let explicitBound = try XCTUnwrap(resumed.work(server: server, id: "explicit")?.message)
        XCTAssertEqual(explicitBound.model, "different-model")
        XCTAssertNil(explicitBound.reasoningEffort); XCTAssertTrue(explicitBound.reasoningEffortIsSet)
        let reverted = try XCTUnwrap(resumed.work(server: server, id: "revert")?.message)
        XCTAssertEqual(reverted.model, "model")
        XCTAssertEqual(reverted.reasoningEffort, "high")
        try resumed.beginAttempt(server: server, id: "B")
        try resumed.settle(server: server, id: "A2", state: .sent, note: "Sent", conversationID: "different-late-value")
        XCTAssertEqual(try resumed.work(server: server, id: "B")?.message?.conversationId, "replacement-conversation")
        XCTAssertEqual(try reopened.record(server: server, kind: "outbox", id: "A")?.payload, rawA)
        XCTAssertEqual(try reopened.record(server: server, kind: "outbox", id: "B")?.payload, rawB)
        XCTAssertTrue(try resumed.summaries(server: "other", owner: .chief).isEmpty)
    }
    func testExpiredPredecessorLeavesDurableExplicitHoldAndNoReceiptShadow() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("test.store")
        let store = LocalStore(url: url), now = Date()
        let outgoing = try store.outgoing
        for (id, conversation) in [("unknown", nil), ("known", "conversation")] as [(String, String?)] {
            try outgoing.enqueue(pending(id, conversation: conversation, created: now.addingTimeInterval(-8 * 86400)), server: "a")
            try outgoing.enqueue(pending(id + "-successor", conversation: conversation), server: "a")
            try outgoing.enqueue(pending(id + "-tail", conversation: conversation), server: "a")
            try outgoing.beginAttempt(server: "a", id: id)
            try outgoing.settle(server: "a", id: id, state: .uncertain, note: "Delivery uncertain")
        }
        try outgoing.expire(server: "a", now: now)
        let reopened = LocalStore(url: url), resumed = try reopened.outgoing
        for id in ["unknown", "known"] {
            XCTAssertNil(try resumed.summary(server: "a", id: id))
            XCTAssertNil(try reopened.record(server: "a", kind: "outbox", id: id))
            XCTAssertFalse(try resumed.canSubmit(server: "a", id: id + "-successor"))
            XCTAssertThrowsError(try resumed.settle(server: "a", id: id, state: .sent, note: "Late"))
        }
        XCTAssertEqual(try resumed.summary(server: "a", id: "unknown-successor")?.holdReason, "destinationUnresolved")
        XCTAssertEqual(try resumed.summary(server: "a", id: "known-successor")?.holdReason, "predecessorExpired")
        XCTAssertThrowsError(try resumed.releaseHold(server: "a", id: "unknown-successor", destination: .conversation("")))
        XCTAssertFalse(try resumed.canSubmit(server: "a", id: "unknown-successor"))
        XCTAssertThrowsError(try resumed.releaseHold(server: "a", id: "known-successor", destination: .newConversation))
        XCTAssertThrowsError(try resumed.releaseHold(server: "a", id: "known-successor", destination: .conversation("other")))
        try resumed.releaseHold(server: "a", id: "known-successor", destination: .conversation("conversation"))
        try resumed.releaseHold(server: "a", id: "unknown-successor", destination: .newConversation)
        XCTAssertTrue(try resumed.canSubmit(server: "a", id: "known-successor"))
        XCTAssertTrue(try resumed.canSubmit(server: "a", id: "unknown-successor"))
        let releasedLane = try resumed.summary(server: "a", id: "unknown-successor")?.lane
        XCTAssertEqual(try resumed.summary(server: "a", id: "unknown-tail")?.lane, releasedLane)
        XCTAssertFalse(try resumed.canSubmit(server: "a", id: "unknown-tail"))
        try resumed.beginAttempt(server: "a", id: "unknown-successor")
        try resumed.settle(server: "a", id: "unknown-successor", state: .sent, note: "Sent", conversationID: "chosen-new-start")
        let afterRelease = LocalStore(url: url)
        XCTAssertTrue(try afterRelease.outgoing.canSubmit(server: "a", id: "unknown-tail"))
        XCTAssertEqual(try afterRelease.outgoing.work(server: "a", id: "unknown-tail")?.message?.conversationId, "chosen-new-start")
    }

}

@MainActor final class OutboxRecoveryTests: XCTestCase {
    private var directory: URL!
    private var store: LocalStore!
    private var api: APIClient!
    private let server = "https://offline-test.invalid"
    override func setUp() async throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = LocalStore(url: directory.appendingPathComponent("test.store"))
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [OfflineProtocol.self]
        api = APIClient(baseURL: URL(string: server)!, session: URLSession(configuration: configuration))
#if SWIFT_PACKAGE
        let url = Bundle.module.url(forResource: "ticket-full", withExtension: "json", subdirectory: "Fixtures")!
#else
        let url = Bundle(for: Self.self).url(forResource: "ticket-full", withExtension: "json")!
#endif
        OfflineProtocol.state.reset(ticket: try Data(contentsOf: url))
    }
    override func tearDown() async throws { try? FileManager.default.removeItem(at: directory) }
    /// Recent creation times, ordered by `at`. Older uncertain work is past its cleanup window.
    private func message(_ id: String, at: TimeInterval = 1, readThrough: Int? = nil) -> PendingWork {
        PendingWork(id: id, created: Date(timeIntervalSinceNow: at - 60), kind: .message, owner: .ticket("ticket"),
            message: ConversationSend(conversationId: "conversation", content: [.init(text: id)], senderMessageId: id, sentAtUnixMilliseconds: Int64(at * 1000), ownerReadThroughSequence: readThrough))
    }
    func testWaitingMessagePostsWithoutReceiptOrHeldMessageReads() async throws {
        let outbox = Outbox(store: store)
        try outbox.save(message("new", readThrough: 12), server: server)

        await outbox.drain(api: api)

        XCTAssertEqual(OfflineProtocol.state.requests, ["POST /api/tickets/ticket/conversation/send"])
        XCTAssertEqual(outbox.entries(server).first?.state, .sent)
    }
    func testMissingReceiptNeverReadmitsAttemptedWorkAfterRestart() async throws {
        let outbox = Outbox(store: store)
        var work = message("held", readThrough: 12)
        work.state = .sending
        try outbox.save(work, server: server)
        OfflineProtocol.state.heldMessageID = "held"

        await outbox.drain(api: api)
        XCTAssertEqual(outbox.entries(server).first?.state, .uncertain)
        let reopened = Outbox(store: LocalStore(url: directory.appendingPathComponent("test.store")))
        await reopened.drain(api: api)

        XCTAssertEqual(OfflineProtocol.state.requests, [
            "GET /api/conversation/conversations/conversation/view/receipts",
            "GET /api/conversation/conversations/conversation/view/receipts"
        ])
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty)
        XCTAssertEqual(reopened.entries(server).first?.state, .uncertain)
    }
    func testOfflineRelaunchDrainsInOrderOnceAndPreservesIdentity() async throws {
        let outbox = Outbox(store: store)
        try outbox.save(message("first"), server: server)
        try outbox.save(message("second", at: 2), server: server)
        // Work persisted while no foreground drainer ran remains proven unattempted.
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty)
        let reopened = Outbox(store: LocalStore(url: directory.appendingPathComponent("test.store")))
        OfflineProtocol.state.offline = false
        await reopened.drain(api: api)
        await reopened.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.writes, ["first", "second"])
        XCTAssertEqual(reopened.entries(server).map(\.state), [.sent, .sent])
    }
    func testLaneSlotsReleaseQueuedSuccessorAndProgressWhileAnotherLaneStalls() async throws {
        let outbox = Outbox(store: store)
        func work(_ id: String, _ lane: String, _ state: PendingWork.State = .waiting) -> PendingWork {
            PendingWork(id: id, created: .now, kind: .message, state: state, owner: .ticket(lane),
                message: ConversationSend(conversationId: lane, content: [.init(text: id)], senderMessageId: id, sentAtUnixMilliseconds: 1))
        }
        try outbox.save(work("queued", "queued", .serverQueued), server: server)
        try outbox.save(work("B", "queued"), server: server)
        try outbox.save(work("stall", "stalled"), server: server)
        try outbox.save(work("quick", "quick"), server: server)
        OfflineProtocol.state.holdLanes = true
        let draining = Task { await outbox.drain(api: api) }
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        while !OfflineProtocol.state.writes.contains("quick"), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(5)) }
        let progressedBeforeRelease = OfflineProtocol.state.writes
        let peaks = OfflineProtocol.state.peaks
        // Always release both blocked requests before assertions, including a failure.
        OfflineProtocol.state.releaseHeldRequests()
        await draining.value
        XCTAssertTrue(progressedBeforeRelease.contains("B"), "Queued acceptance releases B without A's eventual receipt")
        XCTAssertTrue(progressedBeforeRelease.contains("quick"), "A stalled lane cannot occupy the free server slot")
        XCTAssertLessThanOrEqual(peaks.server, 2)
        XCTAssertEqual(peaks.server, 2)
        XCTAssertLessThanOrEqual(peaks.lane, 1)
        XCTAssertEqual(outbox.work(server, id: "queued")?.state, .serverQueued)
    }
    func testLostResponseReconcilesReceiptWithoutResending() async throws {
        let outbox = Outbox(store: store)
        try outbox.save(message("lost"), server: server)
        OfflineProtocol.state.loseResponse = true
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.entries(server).first?.state, .uncertain)
        let reopened = Outbox(store: LocalStore(url: directory.appendingPathComponent("test.store")))
        await reopened.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.requests, [
            "POST /api/tickets/ticket/conversation/send",
            "GET /api/conversation/conversations/conversation/view/receipts"
        ])
        XCTAssertEqual(OfflineProtocol.state.writes, ["lost"])
        XCTAssertEqual(reopened.entries(server).first?.state, .sent)
    }
    func testChangedProposalHoldsBothDecisionKindsAndKeepsGuidance() async throws {
        let detail = try APIClient.decoder().decode(TicketDetail.self, from: OfflineProtocol.state.ticket)
        let proposal = try XCTUnwrap(detail.pendingProposal)
        let outbox = Outbox(store: store)
        let approval = ReviewApproval(expectedProposalRevision: proposal.revision, editedBody: "Edit", nextCeiling: "done", nextHolder: .init(kind: "owner", id: "owner"))
        try outbox.enqueue(PendingWork(id: "approve", created: .now, kind: .approval, ticketID: detail.id, proposal: proposal, approval: approval), api: api)
        try outbox.enqueue(PendingWork(id: "return", created: .now, kind: .sendBack, ticketID: detail.id, proposal: proposal, guidance: "Keep these words"), api: api)
        OfflineProtocol.state.bumpRevision()
        await outbox.drain(api: api)
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty)
        XCTAssertEqual(outbox.entries(server).map(\.state), [.changed, .changed])
        let frozen = try XCTUnwrap(outbox.entries(server).last?.protectedDecisionBody)
        XCTAssertEqual(try APIClient.decoder().decode(DecisionCommand.self, from: frozen).message, "Keep these words")
        XCTAssertEqual(OfflineProtocol.state.decisionOutcomes, 2)
    }
    func testLegacyDecisionNeverGainsProtectedOrLegacyReplayAuthority() async throws {
        let detail = try APIClient.decoder().decode(TicketDetail.self, from: OfflineProtocol.state.ticket)
        let outbox = Outbox(store: store)
        try outbox.save(PendingWork(id: "return", created: .now, kind: .sendBack, ticketID: detail.id, proposal: detail.pendingProposal, guidance: "Change it"), server: server)
        OfflineProtocol.state.loseResponse = true
        await outbox.drain(api: api)
        let reopened = Outbox(store: LocalStore(url: directory.appendingPathComponent("test.store")))
        await reopened.drain(api: api)
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty)
        XCTAssertTrue(OfflineProtocol.state.decisionBodies.isEmpty)
        XCTAssertNil(reopened.entries(server).first?.protectedDecisionBody)
        XCTAssertEqual(reopened.entries(server).first?.guidance, "Change it")
        XCTAssertEqual(reopened.entries(server).first?.state, .uncertain)
    }
    private func protectedDecision(_ id: String, kind: PendingWork.Kind = .approval) throws -> PendingWork {
        let detail = try APIClient.decoder().decode(TicketDetail.self, from: OfflineProtocol.state.ticket)
        let proposal = try XCTUnwrap(detail.pendingProposal)
        return PendingWork(id: id, created: .now, kind: kind, ticketID: detail.id, proposal: proposal,
            approval: kind == .approval ? ReviewApproval(expectedProposalRevision: proposal.revision, editedBody: "Edited once", nextCeiling: "needs_implementation", nextHolder: .init(kind: "owner", id: "owner")) : nil,
            guidance: kind == .sendBack ? "Keep these exact words" : nil)
    }
    func testProtectedDecisionLostAfterCommitRestartsFromReceiptWithoutAnotherOutcome() async throws {
        let outbox = Outbox(store: store)
        let original = try protectedDecision("committed")
        OfflineProtocol.state.loseDecisionAfterCommit = true
        try outbox.enqueue(original, api: api)
        await outbox.drain(api: api)
        let frozen = try XCTUnwrap(outbox.work(server, id: original.id))
        XCTAssertNil(frozen.proposal); XCTAssertNil(frozen.approval); XCTAssertNil(frozen.guidance)
        XCTAssertNotNil(frozen.protectedDecisionBody)
        XCTAssertEqual(frozen.protectedProposalCreatedAt, original.proposal?.createdAt)
        XCTAssertEqual(frozen.state, .uncertain)
        let raw = try XCTUnwrap(store.record(server: server, kind: "outbox", id: original.id)?.payload)
        OfflineProtocol.state.bumpRevision() // Recovery must precede any current-proposal read.
        let reopenedStore = LocalStore(url: directory.appendingPathComponent("test.store"))
        let reopened = Outbox(store: reopenedStore)
        await reopened.drain(api: api)
        await reopened.drain(api: api)
        XCTAssertEqual(reopened.work(server, id: original.id)?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.decisionOutcomes, 1)
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.count, 1)
        XCTAssertEqual(try reopenedStore.record(server: server, kind: "outbox", id: original.id)?.payload, raw)
        XCTAssertFalse(OfflineProtocol.state.requests.contains("GET /api/tickets/\(original.ticketID!)"))
    }
    func testSupportedNullRetriesOnlyOriginalDecisionIDAndFrozenBytesAfterRestart() async throws {
        let outbox = Outbox(store: store)
        let original = try protectedDecision("null-retry", kind: .sendBack)
        OfflineProtocol.state.loseDecisionBeforeCommit = true
        try outbox.enqueue(original, api: api)
        await outbox.drain(api: api)
        let body = try XCTUnwrap(outbox.work(server, id: original.id)?.protectedDecisionBody)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .uncertain)
        XCTAssertEqual(try store.outgoing.summary(server: server, id: original.id)?.provenance, "attempted")
        let reopened = Outbox(store: LocalStore(url: directory.appendingPathComponent("test.store")))
        await reopened.drain(api: api)
        XCTAssertEqual(reopened.work(server, id: original.id)?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.map { $0.0 }, [original.id, original.id])
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.map { $0.1 }, [body, body])
        XCTAssertEqual(OfflineProtocol.state.decisionOutcomes, 1)
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty, "No legacy decision endpoint is available as fallback")
    }
    func testUnsupportedDecisionServerRestoresCardAndDraftAfterTransientPreflight() async throws {
        let outbox = Outbox(store: store)
        let original = try protectedDecision("unsupported")
        store.saveDraft("Kept local guidance", server: server, id: "draft")
        OfflineProtocol.state.lookupStatus = 503
        OfflineProtocol.state.decisionLookup = Data(#"{"error":{"message":"Temporary failure"}}"#.utf8)
        try outbox.enqueue(original, api: api)
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .waiting)
        XCTAssertEqual(try store.outgoing.summary(server: server, id: original.id)?.provenance, "unattempted")
        OfflineProtocol.state.lookupStatus = 200
        OfflineProtocol.state.decisionLookup = Data("{}".utf8)
        await outbox.drain(api: api)
        let saved = try XCTUnwrap(outbox.work(server, id: original.id))
        XCTAssertEqual(saved.state, .changed)
        XCTAssertTrue(saved.note.contains("compatible server"))
        XCTAssertEqual(store.draft(server: server, id: "draft"), "Kept local guidance")
        XCTAssertTrue(OfflineProtocol.state.decisionBodies.isEmpty)
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty)
        let item = ReviewItem(reviewItemType: "proposal", ticketId: original.ticketID!, field: original.proposal!.field, title: "Still needs review", waitingSince: original.proposal!.createdAt)
        let card = ReviewCard(id: "card", ticketId: original.ticketID!, parts: [item])
        let snapshot = ReviewSavedWork(outbox: outbox); snapshot.refresh(server)
        XCTAssertEqual(ReviewSavedWork.cards([card], decisions: snapshot.decisions(server)).map(\.id), [card.id])
    }
    func testAttemptedDecisionMalformedMismatchedAndAmbiguousResponsesStayUncertain() async throws {
        let outbox = Outbox(store: store)
        let original = try protectedDecision("ambiguous")
        OfflineProtocol.state.loseDecisionBeforeCommit = true
        try outbox.enqueue(original, api: api)
        await outbox.drain(api: api)
        OfflineProtocol.state.decisionLookup = Data("{}".utf8)
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .uncertain)
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.count, 1)
        let command = try APIClient.decoder().decode(DecisionCommand.self, from: XCTUnwrap(outbox.work(server, id: original.id)?.protectedDecisionBody))
        let wrong = DecisionReceipt(operationId: "different-operation", ticketId: original.ticketID!, decision: command.decision, field: command.field,
            expectedProposalRevision: command.expectedProposalRevision, outcome: "applied",
            transition: DecisionTransition(stage: "needs_implementation", ceiling: "needs_implementation", ceilingHolder: .init(kind: "owner", id: "owner"), ticketStatus: "ready", pendingProposalRevision: 16), error: nil, createdAt: 100)
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        OfflineProtocol.state.decisionLookup = try encoder.encode(DecisionOperationResponse(receiptContract: 1, receipt: wrong))
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .uncertain)
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.count, 1)
        OfflineProtocol.state.decisionLookup = nil // Explicit supported null permits only this same operation to retry.
        OfflineProtocol.state.decisionPost = Data(#"{"error":{"message":"Conflict without a receipt"}}"#.utf8)
        OfflineProtocol.state.decisionStatus = 409
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .uncertain)
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.map { $0.0 }, [original.id, original.id])
        XCTAssertEqual(try store.outgoing.summary(server: server, id: original.id)?.provenance, "attempted")
        XCTAssertEqual(OfflineProtocol.state.decisionOutcomes, 0)
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty)
    }
    func testTranscriptionPersistsDraftBeforeDeletingAudioAndDoesNotAppendTwice() async throws {
        let file = "test-\(UUID()).m4a"
        let url = LocalStore.directory.appendingPathComponent(file)
        try FileManager.default.createDirectory(at: LocalStore.directory, withIntermediateDirectories: true)
        try Data([1, 2, 3]).write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }
        store.saveDraft("Typed", server: server, id: "guidance")
        let outbox = Outbox(store: store)
        try outbox.save(PendingWork(id: "audio", created: .now, kind: .voice, audioFile: file, draftID: "guidance"), server: server)
        await outbox.drain(api: api)
        let reopened = LocalStore(url: directory.appendingPathComponent("test.store"))
        await Outbox(store: reopened).drain(api: api)
        XCTAssertEqual(reopened.draft(server: server, id: "guidance"), "Typed\n\nRecorded words")
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
        XCTAssertEqual(OfflineProtocol.state.writes, ["voice"])
        let unowned = "expired-\(UUID()).m4a", shared = "shared-\(UUID()).m4a"
        let unownedURL = LocalStore.directory.appendingPathComponent(unowned), sharedURL = LocalStore.directory.appendingPathComponent(shared)
        try Data([4]).write(to: unownedURL); try Data([5]).write(to: sharedURL)
        defer { try? FileManager.default.removeItem(at: unownedURL); try? FileManager.default.removeItem(at: sharedURL) }
        let past = Date(timeIntervalSinceNow: -8 * 86400)
        try outbox.save(PendingWork(id: "expired-audio", created: past, kind: .voice, state: .uncertain, audioFile: unowned, draftID: "old"), server: server)
        try outbox.save(PendingWork(id: "expired-shared", created: past, kind: .voice, state: .uncertain, audioFile: shared, draftID: "old"), server: server)
        try outbox.save(PendingWork(id: "retained-audio", created: past, kind: .voice, state: .recording, audioFile: shared, draftID: "kept"), server: server + "-foreign")
        outbox.clearFinished(server)
        XCTAssertFalse(FileManager.default.fileExists(atPath: unownedURL.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: sharedURL.path))
        XCTAssertNil(outbox.work(server, id: "expired-audio"))
        XCTAssertNil(outbox.work(server, id: "expired-shared"))
        XCTAssertNotNil(outbox.work(server + "-foreign", id: "retained-audio"))
    }
}

private final class OfflineState: @unchecked Sendable {
    private let lock = NSLock()
    private var _offline = false
    private var _loseResponse = false
    private var _ticket = Data()
    private var _writes: [String] = []
    private var _requests: [String] = []
    private var _heldMessageID: String?
    private var receipts: Set<String> = []
    private var decisionReceipts: [String: Data] = [:]
    private var recordedDecisions: [(String, Data)] = []
    private var _decisionOutcomes = 0
    private var _decisionLookup: Data?
    private var _decisionPost: Data?
    private var _decisionStatus = 200
    private var _lookupStatus = 200
    private var _loseDecisionBeforeCommit = false
    private var _loseDecisionAfterCommit = false
    var decisionBodies: [(String, Data)] { lock.withLock { recordedDecisions } }
    var decisionOutcomes: Int { lock.withLock { _decisionOutcomes } }
    var decisionLookup: Data? { get { lock.withLock { _decisionLookup } } set { lock.withLock { _decisionLookup = newValue } } }
    var decisionPost: Data? { get { lock.withLock { _decisionPost } } set { lock.withLock { _decisionPost = newValue } } }
    var decisionStatus: Int { get { lock.withLock { _decisionStatus } } set { lock.withLock { _decisionStatus = newValue } } }
    var lookupStatus: Int { get { lock.withLock { _lookupStatus } } set { lock.withLock { _lookupStatus = newValue } } }
    var loseDecisionBeforeCommit: Bool { get { lock.withLock { _loseDecisionBeforeCommit } } set { lock.withLock { _loseDecisionBeforeCommit = newValue } } }
    var loseDecisionAfterCommit: Bool { get { lock.withLock { _loseDecisionAfterCommit } } set { lock.withLock { _loseDecisionAfterCommit = newValue } } }
    func status(_ request: URLRequest) -> Int { lock.withLock { request.url!.path.contains("/decision-operations/") ? (request.httpMethod == "POST" ? _decisionStatus : _lookupStatus) : 200 } }
    private var _holdLanes = false
    private var held: [@Sendable () -> Void] = []
    private var active = 0, peak = 0, lanePeak = 0
    private var activeLanes: [String: Int] = [:]
    var holdLanes: Bool { get { lock.withLock { _holdLanes } } set { lock.withLock { _holdLanes = newValue } } }
    var peaks: (server: Int, lane: Int) { lock.withLock { (peak, lanePeak) } }
    private func lane(_ request: URLRequest) -> String {
        let components = request.url!.path.split(separator: "/").map(String.init)
        if components.count > 2, components[1] == "tickets" { return components[2] }
        return components.count > 3 ? components[3] : request.url!.path
    }
    func begin(_ request: URLRequest, respond: @escaping @Sendable () -> Void) {
        let delayed = lock.withLock {
            let key = lane(request)
            active += 1; peak = max(peak, active)
            activeLanes[key, default: 0] += 1; lanePeak = max(lanePeak, activeLanes[key, default: 0])
            if _holdLanes && (key == "stalled" || (key == "queued" && request.url!.path.hasSuffix("/receipts"))) {
                held.append(respond); return true
            }
            return false
        }
        if !delayed { respond() }
    }
    func end(_ request: URLRequest) { lock.withLock { active -= 1; activeLanes[lane(request), default: 0] -= 1 } }
    func releaseHeldRequests() {
        let callbacks = lock.withLock { _holdLanes = false; let all = held; held = []; return all }
        for callback in callbacks { callback() }
    }
    var offline: Bool { get { lock.withLock { _offline } } set { lock.withLock { _offline = newValue } } }
    var loseResponse: Bool { get { lock.withLock { _loseResponse } } set { lock.withLock { _loseResponse = newValue } } }
    var heldMessageID: String? { get { lock.withLock { _heldMessageID } } set { lock.withLock { _heldMessageID = newValue } } }
    var requests: [String] { lock.withLock { _requests } }
    var ticket: Data { lock.withLock { _ticket } }
    var writes: [String] { lock.withLock { _writes } }
    func reset(ticket: Data) { lock.withLock { _ticket = ticket; _offline = false; _loseResponse = false; _writes = []; _requests = []; _heldMessageID = nil; receipts = []; decisionReceipts = [:]; recordedDecisions = []; _decisionOutcomes = 0; _decisionLookup = nil; _decisionPost = nil; _decisionStatus = 200; _lookupStatus = 200; _loseDecisionBeforeCommit = false; _loseDecisionAfterCommit = false } }
    func bumpRevision() {
        lock.withLock {
            var value = try! JSONSerialization.jsonObject(with: _ticket) as! [String: Any]
            var proposal = value["pending_proposal"] as! [String: Any]
            proposal["revision"] = (proposal["revision"] as! Int) + 1
            value["pending_proposal"] = proposal
            _ticket = try! JSONSerialization.data(withJSONObject: value)
        }
    }
    private func requestBody(_ request: URLRequest) -> Data {
        var data = request.httpBody ?? Data()
        if let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable { let count = stream.read(&buffer, maxLength: buffer.count); if count <= 0 { break }; data.append(buffer, count: count) }
        }
        return data
    }
    func response(_ request: URLRequest) throws -> Data {
        try lock.withLock {
            let path = request.url!.path
            _requests.append("\(request.httpMethod ?? "GET") \(path)")
            if _offline { throw URLError(.notConnectedToInternet) }
            if path.contains("/decision-operations/") {
                let operationID = request.url!.lastPathComponent
                if request.httpMethod != "POST" {
                    return _decisionLookup ?? decisionReceipts[operationID] ?? Data(#"{"receipt_contract":1,"receipt":null}"#.utf8)
                }
                let data = requestBody(request)
                recordedDecisions.append((operationID, data))
                if _loseDecisionBeforeCommit { _loseDecisionBeforeCommit = false; throw URLError(.networkConnectionLost) }
                if let override = _decisionPost { return override }
                if let existing = decisionReceipts[operationID] { return existing }
                let body = try JSONSerialization.jsonObject(with: data) as! [String: Any]
                let ticket = try JSONSerialization.jsonObject(with: _ticket) as! [String: Any]
                let current = ticket["pending_proposal"] as? [String: Any]
                let refused = (body["expected_proposal_revision"] as? Int) != (current?["revision"] as? Int)
                let value: [String: Any] = ["receipt_contract": 1, "receipt": [
                    "operation_id": operationID, "ticket_id": String(request.url!.path.split(separator: "/")[2]),
                    "decision": body["decision"]!, "field": body["field"]!, "expected_proposal_revision": body["expected_proposal_revision"]!,
                    "outcome": refused ? "refused" : "applied", "created_at": 100,
                    "transition": refused ? NSNull() : ["stage": "needs_implementation", "ceiling": "needs_implementation", "ceiling_holder": ["kind": "owner", "id": "owner"], "ticket_status": "ready", "pending_proposal_revision": 16],
                    "error": refused ? ["code": "changed_proposal", "message": "Proposal changed", "detail": [:]] : NSNull()
                ]]
                let response = try JSONSerialization.data(withJSONObject: value)
                decisionReceipts[operationID] = response; _decisionOutcomes += 1
                if _loseDecisionAfterCommit { _loseDecisionAfterCommit = false; throw URLError(.networkConnectionLost) }
                return response
            }
            if path.hasSuffix("/receipts") {
                return try JSONSerialization.data(withJSONObject: ["receipts": receipts.map { ["sender_message_id": $0, "fate": "prompt"] }])
            }
            if path == "/api/conversation/conversations/conversation", let id = _heldMessageID {
                return try JSONSerialization.data(withJSONObject: ["backend_key": "codex", "held_prompts": [
                    ["held_prompt_id": "held-1", "sender_message_id": id, "content": [["piece": "text", "text": "Held"]]]
                ]])
            }
            if request.httpMethod != "POST" { return _ticket }
            var data = request.httpBody ?? Data()
            if let stream = request.httpBodyStream {
                stream.open(); defer { stream.close() }
                var buffer = [UInt8](repeating: 0, count: 4096)
                while stream.hasBytesAvailable { let count = stream.read(&buffer, maxLength: buffer.count); if count <= 0 { break }; data.append(buffer, count: count) }
            }
            let body = try JSONSerialization.jsonObject(with: data) as! [String: Any]
            let id = body["sender_message_id"] as? String ?? (path.hasSuffix("voice-transcriptions") ? "voice" : "decision")
            _writes.append(id); if id != "queued" { receipts.insert(id) }
            if _loseResponse { _loseResponse = false; throw URLError(.networkConnectionLost) }
            if id == "voice" { return Data(#"{"transcript":"Recorded words"}"#.utf8) }
            return try JSONSerialization.data(withJSONObject: ["conversation_id": body["conversation_id"] as? String ?? "conversation", "fate": id == "queued" ? "queued" : "started"])
        }
    }
}
private final class OfflineProtocol: URLProtocol, @unchecked Sendable {
    static let state = OfflineState()
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "offline-test.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() { Self.state.begin(request) { [self] in respond() } }
    private func respond() {
        defer { Self.state.end(request) }
        do {
            let data = try Self.state.response(request)
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: Self.state.status(request), httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data); client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

@MainActor final class OfflinePresentationTests: XCTestCase {
    func testPersistedRetryReplacesRefusedBubbleAfterModelRecreation() throws {
        let api = APIClient(baseURL: URL(string: "https://retry-\(UUID().uuidString.lowercased()).invalid")!)
        let server = LocalStore.server(api.baseURL)
        defer {
            try? LocalStore.shared.remove(server: server, kind: "outbox", id: "new")
            try? LocalStore.shared.remove(server: server, kind: "outbox", id: "old")
            try? LocalStore.shared.remove(server: server, kind: "outbox", ids: ["other-old", "other-new", "late-original", "late-replacement"])
        }
        let old = PendingWork(id: "old", created: .now, kind: .message, state: .failed, owner: .chief,
            message: ConversationSend(conversationId: "c", content: [.init(text: "Words")], senderMessageId: "old", sentAtUnixMilliseconds: 1))
        var retry = PendingWork(id: "new", created: .now, kind: .message, owner: .chief,
            message: ConversationSend(conversationId: "c", content: [.init(text: "Words")], senderMessageId: "new", sentAtUnixMilliseconds: 2))
        retry.priorMessageIDs = ["old"]
        try Outbox.shared.save(old, server: server)
        try Outbox.shared.save(retry, server: server)
        let other = PendingWork(id: "other-old", created: .now, kind: .message, state: .failed, owner: .chief,
            message: ConversationSend(conversationId: "other-conversation", content: [.init(text: "Other destination")], senderMessageId: "other-old", sentAtUnixMilliseconds: 3))
        let otherReplacement = PendingWork(id: "other-new", created: .now, kind: .message, owner: .chief,
            message: ConversationSend(conversationId: "other-conversation", content: [.init(text: "Other destination")], senderMessageId: "other-new", sentAtUnixMilliseconds: 4))
        try Outbox.shared.save(other, server: server)
        try LocalStore.shared.outgoing.replace(server: server, id: other.id, with: otherReplacement)
        let model = ConversationModel()
        model.conversationID = "c"
        model.restore(owner: .chief, api: api)
        XCTAssertEqual(model.rows.count, 1)
        XCTAssertEqual(model.rows.first?.id, "old")
        XCTAssertEqual(model.rows.first?.text, "Words")
        let original = PendingWork(id: "late-original", created: .now, kind: .message, state: .uncertain, owner: .chief,
            message: ConversationSend(conversationId: nil, content: [.init(text: "Independent original")], senderMessageId: "late-original", sentAtUnixMilliseconds: 5))
        let replacement = PendingWork(id: "late-replacement", created: .now, kind: .message, owner: .chief,
            message: ConversationSend(conversationId: nil, content: [.init(text: "Independent replacement")], senderMessageId: "late-replacement", sentAtUnixMilliseconds: 6))
        let storage = try LocalStore.shared.outgoing
        try storage.enqueue(original, server: server)
        try storage.replace(server: server, id: original.id, with: replacement)
        try storage.releaseHold(server: server, id: replacement.id, destination: .newConversation)
        try storage.settle(server: server, id: replacement.id, state: .sent, note: "Sent", conversationID: "c2")
        let secondDestination = ConversationModel()
        secondDestination.conversationID = "c2"
        secondDestination.restore(owner: .chief, api: api)
        XCTAssertEqual(Set(secondDestination.rows.map(\.id)), [original.id, replacement.id], "Unknown original stays inspectable with its replacement")
        try storage.settle(server: server, id: original.id, state: .sent, note: "Sent", conversationID: "c1")
        secondDestination.syncOutbox()
        XCTAssertEqual(secondDestination.rows.map(\.id), [replacement.id], "A known late result cannot appear as Sent in the replacement's different destination")
        let firstDestination = ConversationModel()
        firstDestination.conversationID = "c1"
        firstDestination.restore(owner: .chief, api: api)
        XCTAssertEqual(firstDestination.rows.map(\.id), [original.id])
        XCTAssertEqual(firstDestination.outgoing.first?.note, "Sent")
    }
}

extension OfflineTests {
    func testRequiredUpdateRetainsPendingIdentityAndDraftWithoutReplay() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalStore(url: directory.appendingPathComponent("test.store"))
        let api = APIClient(baseURL: URL(string: "https://update-\(UUID().uuidString).invalid")!)
        let server = LocalStore.server(api.baseURL)
        store.saveDraft("More unsent words", server: server, id: "draft")
        let message = ConversationSend(conversationId: "c", content: [.init(text: "Pending words")], senderMessageId: "stable", sentAtUnixMilliseconds: 123)
        let outbox = Outbox(store: store)
        try outbox.save(PendingWork(id: "stable", created: .now, kind: .message, owner: .chief, message: message), server: server)
        NativeBuildGate.requireUpdate(api.baseURL)
        await outbox.drain(api: api)
        let saved = try XCTUnwrap(outbox.entries(server).first)
        XCTAssertEqual(saved.state, .waiting)
        XCTAssertEqual(saved.message?.senderMessageId, "stable")
        XCTAssertEqual(saved.message?.content.first?.text, "Pending words")
        XCTAssertEqual(store.draft(server: server, id: "draft"), "More unsent words")
    }
}


// Matches the app's original unversioned model. The entity keeps its LocalRecord
// name, while the new model renames only the Core Data-colliding date property.
private enum LegacyPhoneSchema {
    @Model final class LocalRecord {
        @Attribute(.unique) var key: String
        var server: String
        var kind: String
        var payload: Data
        var updated: Date
        init(key: String, server: String, kind: String, payload: Data, updated: Date) {
            self.key = key; self.server = server; self.kind = kind; self.payload = payload; self.updated = updated
        }
    }
}

extension OfflineTests {
    func testLegacyStoreMigrationPreservesDraftAndPendingMessage() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let server = "https://legacy.invalid", foreign = "https://other.invalid"
        let date = Date(timeIntervalSince1970: 1_700_000_001.125)
        let attachment = ConversationSend.Piece(piece: "image", data: "AAECA/8=", mediaType: "image/png", fileName: "saved.png")
        let message = ConversationSend(conversationId: "saved-conversation", content: [.init(text: "Exact legacy words"), attachment], senderMessageId: "legacy-id", sentAtUnixMilliseconds: 123)
        let waiting = PendingWork(id: "legacy-id", created: date, kind: .message, owner: .chief, message: message)
        let sending = PendingWork(id: "sending", created: date, kind: .message, state: .sending, owner: .chief, message: message)
        let approval = PendingWork(id: "approval", created: date, kind: .approval, ticketID: "ticket")
        let voice = PendingWork(id: "voice", created: date, kind: .voice, state: .recording, audioFile: "owned.m4a", draftID: "voice-draft")
        func raw(_ work: PendingWork) throws -> Data {
            var object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(work)) as! [String: Any]
            object["futureLegacyField"] = ["keep": "unknown bytes survive"]
            return try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        }
        let records: [(String, String, String, Data)] = [
            (server, "draft", "draft", Data("Legacy unsent draft 🪶\n".utf8)),
            (server, "attachments", "draft", try JSONEncoder().encode([attachment])),
            (server, "outbox", waiting.id, try raw(waiting)),
            (server, "outbox", sending.id, try raw(sending)),
            (server, "outbox", approval.id, try raw(approval)),
            (server, "outbox", voice.id, try raw(voice)),
            (server, "outbox", "corrupt", Data([0xff, 0x00, 0x7b])),
            (foreign, "outbox", waiting.id, try raw(waiting)),
        ]
        for originalDateName in [true, false] {
            let source = root.appendingPathComponent(originalDateName ? "original" : "installed")
            let copied = root.appendingPathComponent(originalDateName ? "original-copy" : "installed-copy")
            try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
            let sourceURL = source.appendingPathComponent("phone.store")
            // Both sources are actual unversioned installed model schemas. Release all
            // seed contexts before copying the whole coherent directory, including WAL.
            try autoreleasepool {
                let schema = originalDateName ? Schema([LegacyPhoneSchema.LocalRecord.self]) : Schema([LocalRecord.self])
                let container = try ModelContainer(for: schema, configurations: [ModelConfiguration(url: sourceURL)])
                let context = ModelContext(container)
                context.autosaveEnabled = false
                for (origin, kind, id, bytes) in records {
                    if originalDateName {
                        context.insert(LegacyPhoneSchema.LocalRecord(key: LocalStore.key(origin, kind, id), server: origin, kind: kind, payload: bytes, updated: date))
                    } else {
                        context.insert(LocalRecord(key: LocalStore.key(origin, kind, id), server: origin, kind: kind, payload: bytes, updated: date))
                    }
                }
                try context.save()
            }
            let audio = Data([0x6d, 0x34, 0x61, 0x00, 0xff])
            try audio.write(to: source.appendingPathComponent("owned.m4a"))
            try FileManager.default.copyItem(at: source, to: copied)
            let migratedURL = copied.appendingPathComponent("phone.store")
            let migrated = LocalStore(url: migratedURL)
            XCTAssertTrue(migrated.saveError?.contains("original bytes remain") == true, "The corrupt row remains visible as a storage error")
            for (origin, kind, id, bytes) in records {
                let row = try XCTUnwrap(migrated.record(server: origin, kind: kind, id: id))
                XCTAssertEqual(row.key, LocalStore.key(origin, kind, id))
                XCTAssertEqual(row.server, origin); XCTAssertEqual(row.kind, kind)
                XCTAssertEqual(row.payload, bytes); XCTAssertEqual(row.updatedAt, date)
            }
            XCTAssertEqual(try Data(contentsOf: copied.appendingPathComponent("owned.m4a")), audio)
            func metadata() throws -> [OutgoingMetadata] {
                let container = try ModelContainer(for: Schema(versionedSchema: PhoneOutgoingSchema.self), configurations: [ModelConfiguration(url: migratedURL)])
                return try ModelContext(container).fetch(FetchDescriptor<OutgoingMetadata>(sortBy: [SortDescriptor(\.sequence)]))
            }
            let marker = try XCTUnwrap(migrated.record(server: "", kind: "outgoingMigration", id: OutgoingStoreMigration.markerID))
            XCTAssertEqual(marker.payload, Data([1]))
            let markerDate = marker.updatedAt
            let indexed = try metadata()
            XCTAssertEqual(indexed.count, 6)
            XCTAssertEqual(Set(indexed.map(\.provenance)), ["unknown"])
            XCTAssertTrue(indexed.filter { ["legacy-id", "sending", "approval"].contains($0.identity) }.allSatisfy { $0.state == "uncertain" })
            XCTAssertEqual(indexed.first(where: { $0.identity == "corrupt" })?.state, "storageError")
            XCTAssertNotNil(indexed.first(where: { $0.identity == "corrupt" })?.failure)
            XCTAssertEqual(indexed.first(where: { $0.identity == "voice" })?.draftID, "voice-draft")
            XCTAssertEqual(indexed.filter { $0.server == foreign }.count, 1)
            migrated.saveDraft("A new draft", server: server, id: "fresh-draft")
            try migrated.put(server: server, kind: "cache", id: "fresh-cache", data: Data([1]))
            XCTAssertNil(migrated.saveError)
            XCTAssertTrue(migrated.outgoingError?.contains("original bytes remain") == true)
            let identities = indexed.map { ($0.key, $0.sequence) }
            let reopened = LocalStore(url: migratedURL)
            XCTAssertEqual(reopened.draft(server: server, id: "draft"), "Legacy unsent draft 🪶\n")
            let reopenedMarker = try XCTUnwrap(reopened.record(server: "", kind: "outgoingMigration", id: OutgoingStoreMigration.markerID))
            XCTAssertEqual(reopenedMarker.updatedAt, markerDate)
            XCTAssertEqual(reopenedMarker.payload, Data([1]))
            XCTAssertTrue(reopened.outgoingError?.contains("original bytes remain") == true)
            let again = try metadata()
            XCTAssertEqual(again.map(\.key), identities.map { $0.0 })
            XCTAssertEqual(again.map(\.sequence), identities.map { $0.1 })
            XCTAssertEqual(try reopened.records(server: server, kind: "outbox").count, 5)
            XCTAssertEqual(try reopened.records(server: foreign, kind: "outbox").count, 1)
            XCTAssertEqual(try reopened.record(server: server, kind: "outbox", id: "corrupt")?.payload, Data([0xff, 0x00, 0x7b]))
            XCTAssertFalse(try reopened.outgoing.canSubmit(server: server, id: waiting.id))
            XCTAssertThrowsError(try reopened.outgoing.beginAttempt(server: server, id: waiting.id))
            try reopened.outgoing.settle(server: server, id: waiting.id, state: .sent, note: "Late receipt", conversationID: "receipt-conversation")
            let legacyAfterSettlement = try XCTUnwrap(reopened.record(server: server, kind: "outbox", id: waiting.id))
            XCTAssertEqual(legacyAfterSettlement.payload, try raw(waiting))
            XCTAssertEqual(legacyAfterSettlement.updatedAt, date)
            XCTAssertEqual(try reopened.outgoing.summary(server: server, id: waiting.id)?.provenance, "unknown")
            XCTAssertEqual(try reopened.outgoing.work(server: server, id: waiting.id)?.message?.conversationId, "receipt-conversation")
        }
    }
}
