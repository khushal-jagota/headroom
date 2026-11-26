import Foundation
import SwiftData
import Observation
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
        try store.outgoing.enqueue(pending("send"), server: "a")
        for index in 0..<100 { try store.put(server: "a", kind: "cache", id: String(index), data: Data([1])) }
        let reopened = LocalStore(url: url)
        XCTAssertEqual(reopened.draft(server: "a", id: "text"), "Keep my words")
        XCTAssertNil(reopened.draft(server: "b", id: "text"))
        XCTAssertEqual(try reopened.records(server: "a", kind: "cache").count, LocalStore.cacheLimit)
        XCTAssertEqual(try reopened.outgoing.work(server: "a", id: "send")?.message?.senderMessageId, "send")
        XCTAssertNil(try reopened.outgoing.work(server: "b", id: "send"))
    }
    func testLaunchVideoSweepPreservesAllFilesUntilDraftAndOutgoingOwnershipIsReadable() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let videos = directory.appendingPathComponent("videos")
        try FileManager.default.createDirectory(at: videos, withIntermediateDirectories: true)
        let store = LocalStore(url: directory.appendingPathComponent("phone.store"))
        func piece(_ name: String) -> ConversationSend.Piece {
            .init(piece: "attachment_upload", localVideo: SavedVideo(id: name, fileName: name,
                filePath: "videos/" + name, byteCount: 1))
        }
        for name in ["draft.mov", "outbox.mov", "orphan.mov"] {
            try Data([1]).write(to: videos.appendingPathComponent(name))
        }
        try store.put(server: "a", kind: "attachments", id: "note", data: JSONEncoder().encode([piece("draft.mov")]))
        let work = PendingWork(id: "video", created: .now, kind: .message, owner: .ticket("ticket"),
            message: ConversationSend(conversationId: nil, content: [piece("outbox.mov")], senderMessageId: "video", sentAtUnixMilliseconds: 1))
        try store.outgoing.enqueue(work, server: "b")
        let allFiles: Set<String> = ["draft.mov", "outbox.mov", "orphan.mov"]
        try store.put(server: "a", kind: "attachments", id: "malformed", data: Data("broken".utf8))
        try await store.sweepUnownedVideoFiles(in: directory)
        XCTAssertEqual(Set(try FileManager.default.contentsOfDirectory(atPath: videos.path)), allFiles,
                       "An unreadable draft may own any file, so cleanup must preserve all files")

        let outgoing = try XCTUnwrap(store.record(server: "b", kind: "outbox", id: "video"))
        let originalPayload = outgoing.payload
        outgoing.payload = Data("broken".utf8)
        // Saving the repaired draft also commits the deliberately corrupted outbox payload.
        try store.put(server: "a", kind: "attachments", id: "malformed", data: Data("[]".utf8))
        try await store.sweepUnownedVideoFiles(in: directory)
        XCTAssertEqual(Set(try FileManager.default.contentsOfDirectory(atPath: videos.path)), allFiles,
                       "An unreadable outgoing row must also defer the entire sweep")

        outgoing.payload = originalPayload
        try store.put(server: "a", kind: "attachments", id: "malformed", data: Data("[]".utf8))
        try await store.sweepUnownedVideoFiles(in: directory)
        XCTAssertEqual(Set(try FileManager.default.contentsOfDirectory(atPath: videos.path)), ["draft.mov", "outbox.mov"])

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
        XCTAssertEqual(store.saveError, "Couldn’t save on this phone: temporary failure")
        XCTAssertEqual(try store.record(server: "a", kind: "cache", id: "same")?.updated, Date(timeIntervalSince1970: 3))
        try await store.cacheResponse(server: "a", id: "same", data: Data([2]), updated: Date(timeIntervalSince1970: 4))
        XCTAssertEqual(try store.record(server: "a", kind: "cache", id: "same")?.payload, Data([2]))
        XCTAssertNil(try store.record(server: "b", kind: "cache", id: "same"))
        XCTAssertEqual(store.workRevision, revision)
        let reopened = LocalStore(url: url)
        XCTAssertEqual(try reopened.record(server: "a", kind: "cache", id: "same")?.payload, Data([2]))
        XCTAssertEqual(reopened.draft(server: "a", id: "draft"), "Keep")
    }
    func testFailedBackgroundCacheKeepsLiveResourceDraftAndDurableError() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = LocalStore(url: directory.appendingPathComponent("phone.store"), cacheWrite: { throw CocoaError(.fileWriteUnknown) })
        let api = APIClient(baseURL: URL(string: "https://cache-failure.invalid")!)
        let server = LocalStore.server(api.baseURL)
        store.saveDraft("Unfinished words", server: server, id: "composer")
        store.saveError = "An earlier durable save failed."
        let transport = ResourceTransport(versions: { _, keys in
            ResourceVersions(epoch: "epoch", resources: keys.map { .init(key: $0, revision: "1") })
        }, read: { _, _, _ in ResourceRead(data: Data("\"Live content\"".utf8), epoch: "epoch", revision: "1") })
        let owner = ResourceOwner(api: api, transport: transport, cacheStore: store)
        let handle = owner.handle(ResourceRegistration<String>(key: "/api/example"))
        handle.acquire(); defer { handle.release(); owner.stop() }
        await handle.refresh()
        XCTAssertEqual(handle.value, "Live content")
        await store.flushCache()
        for _ in 0..<100 where store.cacheErrors[server] == nil { await Task.yield() }
        XCTAssertNotNil(store.cacheErrors[server])
        XCTAssertNil(handle.error, "A cache failure is not a failed authoritative read")
        XCTAssertEqual(handle.value, "Live content")
        XCTAssertEqual(store.draft(server: server, id: "composer"), "Unfinished words")
        XCTAssertEqual(store.saveError, "An earlier durable save failed.")
    }

    func testRestoredResourceEnvelopeIsStaleAndFailedLookupCannotReturnOldContent() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = LocalStore(url: directory.appendingPathComponent("phone.store"))
        let api = APIClient(baseURL: URL(string: "https://restored-resource.invalid")!)
        let key = "/api/example"
        let transport = ResourceTransport(versions: { _, keys in
            ResourceVersions(epoch: "epoch", resources: keys.map { .init(key: $0, revision: "1") })
        }, read: { _, _, _ in ResourceRead(data: Data("\"Live content\"".utf8), epoch: "epoch", revision: "1") })
        let first = ResourceOwner(api: api, transport: transport, cacheStore: store)
        let original = first.handle(ResourceRegistration<String>(key: key))
        await original.refresh(); await store.flushCache(); first.stop()
        let failing = ResourceTransport(versions: { _, _ in throw URLError(.timedOut) },
                                        read: { _, _, _ in throw URLError(.timedOut) })
        let restored = ResourceOwner(api: api, transport: failing, cacheStore: store)
        let handle = restored.handle(ResourceRegistration<String>(key: key))
        XCTAssertNil(handle.value, "Acquiring a cold handle does not fetch or decode saved data on main")
        handle.acquire(); handle.release()
        await handle.entry.restoring?.value
        XCTAssertEqual(handle.value, "Live content", "A quick departure cannot strand async cache restoration")
        XCTAssertNil(handle.entry.restoring)
        XCTAssertTrue(handle.isStale)
        do {
            let _: String = try await restored.read(ResourceRegistration<String>(key: key))
            XCTFail("A failed fresh lookup cannot return saved content as current")
        } catch { XCTAssertNotNil(handle.failure); XCTAssertNil(ServerReadFailure.note(error), "The failed lookup stays a connection failure") }
        XCTAssertEqual(handle.value, "Live content", "Saved display remains useful after the failed read")
        restored.stop()
    }

    func testReplaceableCacheQueueBoundsPendingBytesAndCoalescesExactServerKeys() {
        let pending = ResponseCacheWriter.Pending(entryLimit: 2, byteLimit: 4)
        func write(_ server: String, _ id: String, _ bytes: [UInt8]) -> ResponseCacheWriter.Write {
            .init(server: server, id: id, data: Data(bytes), updated: .now)
        }
        XCTAssertTrue(pending.append(write("a", "same", [1, 1])))
        XCTAssertFalse(pending.append(write("a", "same", [2])))
        XCTAssertEqual(pending.count, 1)
        XCTAssertEqual(pending.byteCount, 1)
        XCTAssertFalse(pending.append(write("b", "same", [3, 3])))
        XCTAssertEqual(pending.count, 2)
        XCTAssertFalse(pending.append(write("a", "new", [4, 4, 4])))
        XCTAssertEqual(pending.count, 1)
        XCTAssertEqual(pending.byteCount, 3)
        XCTAssertEqual(pending.next()?.id, "new")
        XCTAssertNil(pending.next())
        XCTAssertTrue(pending.append(write("a", "after-idle", [5])))
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
        try store.outgoing.enqueue(pending("stable", conversation: "c"), server: "a")
        XCTAssertEqual(changes.withLock { $0 }, 1)
        XCTAssertEqual(outbox.entries("a").map(\.id), ["stable"])
        withObservationTracking { _ = outbox.entries("a") } onChange: { changes.withLock { $0 += 1 } }
        try store.remove(server: "a", kind: "outbox", id: "stable")
        XCTAssertEqual(changes.withLock { $0 }, 2)
        XCTAssertTrue(outbox.entries("a").isEmpty)
        // A key press saves the draft. It must not wake every saved-work observer.
        withObservationTracking { _ = store.workRevision } onChange: { changes.withLock { $0 += 1 } }
        store.saveDraft("Keep the typed words", server: "a", id: "text")
        XCTAssertEqual(changes.withLock { $0 }, 2)
        XCTAssertEqual(store.draft(server: "a", id: "text"), "Keep the typed words")
    }
    func testClearFinishedRemovesOnlyFinishedWorkAfterItsDayAndKeepsRefusedMessages() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalStore(url: directory.appendingPathComponent("test.store"))
        let outbox = Outbox(store: store)
        let now = Date(), day: TimeInterval = 24 * 60 * 60
        func work(_ id: String, _ kind: PendingWork.Kind, _ state: PendingWork.State, completedAge: TimeInterval) -> PendingWork {
            PendingWork(id: id, created: now.addingTimeInterval(-3 * day), kind: kind, state: state, completed: now.addingTimeInterval(-completedAge))
        }
        for row in [work("old-sent", .message, .sent, completedAge: day), work("recent-sent", .message, .sent, completedAge: day - 60),
                    work("old-refused-decision", .approval, .failed, completedAge: day), work("refused-message", .message, .failed, completedAge: 30 * day),
                    work("waiting", .message, .waiting, completedAge: 30 * day), work("recording", .voice, .recording, completedAge: 30 * day)] {
            try outbox.save(row, server: "a")
        }
        try outbox.save(work("other-server", .message, .sent, completedAge: 2 * day), server: "b")
        let revision = store.workRevision
        outbox.clearFinished("a", now: now)
        XCTAssertEqual(store.workRevision, revision + 1, "One save for the whole batch")
        XCTAssertEqual(Set(outbox.entries("a").map(\.id)), ["recent-sent", "refused-message", "waiting", "recording"])
        XCTAssertEqual(outbox.entries("b").map(\.id), ["other-server"])
    }
    func testEnqueueAndComposerClearShareDurableSave() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("test.store")
        var failSave = false
        let store = LocalStore(url: url, beforeSave: { if failSave { throw CocoaError(.fileWriteUnknown) } })
        store.saveDraft("Unsent", server: "a", id: "draft")
        let attachments = Data("[{\"name\":\"photo.jpg\",\"bytes\":\"AQID\"}]".utf8)
        try store.put(server: "a", kind: "attachments", id: "draft", data: attachments)
        let outgoing = try store.outgoing
        let capture = try outgoing.captureDraft(server: "a", id: "draft")
        let work = pending("stable", conversation: "c")
        failSave = true
        XCTAssertThrowsError(try outgoing.enqueue(work, server: "a", capturedDraft: capture))
        XCTAssertNotNil(store.outgoingError)
        for inspected in [store, LocalStore(url: url)] {
            XCTAssertEqual(inspected.draft(server: "a", id: "draft"), "Unsent")
            XCTAssertEqual(try inspected.record(server: "a", kind: "attachments", id: "draft")?.payload, attachments)
            XCTAssertNil(try inspected.outgoing.summary(server: "a", id: work.id))
        }
        failSave = false
        try outgoing.enqueue(work, server: "a", capturedDraft: capture)
        XCTAssertNil(store.outgoingError)
        let reopened = LocalStore(url: url)
        XCTAssertEqual(reopened.draft(server: "a", id: "draft"), "")
        XCTAssertEqual(try reopened.record(server: "a", kind: "attachments", id: "draft")?.payload, Data("[]".utf8))
        XCTAssertEqual(try reopened.outgoing.work(server: "a", id: "stable")?.message?.sentAtUnixMilliseconds, 123)
        // A captured composer version never clears newer words or attachments.
        store.saveDraft("Captured", server: "a", id: "draft")
        let older = try outgoing.captureDraft(server: "a", id: "draft")
        store.saveDraft("Newer words", server: "a", id: "draft")
        try outgoing.enqueue(pending("next", conversation: "c"), server: "a", capturedDraft: older)
        XCTAssertEqual(store.draft(server: "a", id: "draft"), "Newer words")
        // A duplicate identity changes nothing.
        XCTAssertThrowsError(try outgoing.enqueue(work, server: "a", capturedDraft: try outgoing.captureDraft(server: "a", id: "draft")))
        XCTAssertEqual(LocalStore(url: url).draft(server: "a", id: "draft"), "Newer words")
    }
    /// A Review message to Chief has no conversation ID; the Chief screen's has one. They
    /// still go out in the order they were saved.
    func testOwnersMessagesShareOneLaneWhateverTheirDestination() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalStore(url: directory.appendingPathComponent("test.store"))
        try store.outgoing.enqueue(pending("known", conversation: "c"), server: "a")
        try store.outgoing.enqueue(pending("current"), server: "a")
        XCTAssertEqual(try store.outgoing.runnable(server: "a").map(\.id), ["known"])
    }
    private func pending(_ id: String, conversation: String? = nil, created: Date = .now) -> PendingWork {
        PendingWork(id: id, created: created, kind: .message, owner: .chief,
            message: ConversationSend(conversationId: conversation, content: [.init(text: id)], backendKey: "backend", model: "model", reasoningEffort: "high", senderMessageId: id, sentAtUnixMilliseconds: 123))
    }
    func testRequiredUpdateRetainsPendingIdentityAndDraftWithoutReplay() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalStore(url: directory.appendingPathComponent("test.store"))
        let api = APIClient(baseURL: URL(string: "https://update-\(UUID().uuidString).invalid")!)
        let server = LocalStore.server(api.baseURL)
        store.saveDraft("More unsent words", server: server, id: "draft")
        let outbox = Outbox(store: store)
        try outbox.save(pending("stable", conversation: "c"), server: server)
        NativeBuildGate.requireUpdate(api.baseURL)
        await outbox.drain(api: api)
        let saved = try XCTUnwrap(outbox.entries(server).first)
        XCTAssertEqual(saved.state, .waiting)
        XCTAssertEqual(saved.message?.senderMessageId, "stable")
        XCTAssertEqual(store.draft(server: server, id: "draft"), "More unsent words")
    }
    /// The retry mark: only a refused message shows a note and a retry, and a retry by tap
    /// keeps the bubble where it was.
    func testOnlyRefusedMessageShowsRetryMarkAndRetryKeepsItsBubble() async throws {
        let api = APIClient(baseURL: URL(string: "https://retry-\(UUID().uuidString.lowercased()).invalid")!)
        let server = LocalStore.server(api.baseURL)
        defer { try? LocalStore.shared.remove(server: server, kind: "outbox", ids: Outbox.shared.entries(server).map(\.id)) }
        func message(_ id: String, _ state: PendingWork.State, note: String? = nil) -> PendingWork {
            PendingWork(id: id, created: .now, kind: .message, state: state, note: note ?? (state == .failed ? "Not delivered · agent could not start" : ""), owner: .chief,
                message: ConversationSend(conversationId: "c", content: [.init(text: id)], senderMessageId: id, sentAtUnixMilliseconds: 1))
        }
        try Outbox.shared.save(message("waiting", .waiting), server: server)
        try Outbox.shared.save(message("refused", .failed), server: server)
        try Outbox.shared.save(message("unconfirmed", .sent, note: "Delivery unconfirmed"), server: server)
        let model = ConversationModel()
        model.conversationID = "c"
        model.restore(owner: .chief, api: api)
        XCTAssertEqual(model.rows.map(\.id), ["waiting", "refused", "unconfirmed"])
        XCTAssertNil(model.rows[0].note, "A waiting message looks sent")
        XCTAssertNil(model.rows[0].retryID)
        XCTAssertEqual(model.rows[1].note, "Not delivered · agent could not start")
        XCTAssertEqual(model.rows[1].retryID, "refused")
        XCTAssertEqual(model.rows[2].note, "Delivery unconfirmed", "An unconfirmed message does not look delivered")
        XCTAssertNil(model.rows[2].retryID, "An unconfirmed message is never resent")
        await model.retry("refused", to: .chief, using: api)
        XCTAssertEqual(model.rows.map(\.id), ["waiting", "refused", "unconfirmed"], "The retried message keeps its bubble")
        XCTAssertNil(model.rows[1].note)
        XCTAssertNil(model.rows[1].retryID)
        XCTAssertNil(Outbox.shared.work(server, id: "refused"), "A refused original has no receipt to wait for")
    }
}

/// Each kind lands once across a lost connection, a lost response and a relaunch.
@MainActor final class OutboxRecoveryTests: XCTestCase {
    private var directory: URL!
    private var store: LocalStore!
    private var api: APIClient!
    private let server = "https://offline-test.invalid"
    private var storeURL: URL { directory.appendingPathComponent("test.store") }
    override func setUp() async throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = LocalStore(url: storeURL)
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
    /// A relaunch: a new store and outbox over the same file, with no foreground memory.
    private func relaunch() -> Outbox { store = LocalStore(url: storeURL); return Outbox(store: store) }
    private func message(_ id: String, conversation: String? = "conversation", at: TimeInterval = 1) -> PendingWork {
        PendingWork(id: id, created: Date(timeIntervalSinceNow: at - 60), kind: .message, owner: .ticket("ticket"),
            message: ConversationSend(conversationId: conversation, content: [.init(text: id)], backendKey: conversation == nil ? "codex" : nil,
                senderMessageId: id, sentAtUnixMilliseconds: Int64(at * 1000)))
    }
    private var tinyPhoto: ConversationSend.Piece {
        .init(piece: "image", data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", mediaType: "image/png", fileName: "photo.png")
    }

    private func json(_ data: Data) throws -> NSDictionary { try JSONSerialization.jsonObject(with: data) as! NSDictionary }

    // MARK: Messages

    func testMessagesLandOnceInOrderAcrossLostConnectionLostResponseAndRelaunch() async throws {
        var outbox = Outbox(store: store)
        try store.outgoing.enqueue(message("first"), server: server)
        try store.outgoing.enqueue(message("second", at: 2), server: server)
        OfflineProtocol.state.offline = true
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.entries(server).map(\.state), [.waiting, .waiting])

        outbox = relaunch()
        OfflineProtocol.state.offline = false
        OfflineProtocol.state.loseResponse = true
        await outbox.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.writes, ["first"], "The second waits behind the first in its conversation")
        XCTAssertEqual(outbox.entries(server).map(\.state), [.waiting, .waiting], "A lost response is not shown")

        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.writes, ["first", "first", "second"])
        let bodies = OfflineProtocol.state.messageBodies.filter { $0.0 == "first" }.map(\.1)
        XCTAssertEqual(bodies.count, 2); XCTAssertEqual(try json(bodies[0]), try json(bodies[1]), "A resend is the exact body that went out")
        XCTAssertEqual(OfflineProtocol.state.deliveredMessages, ["first", "second"], "The server keeps one of each")
        XCTAssertEqual(outbox.entries(server).map(\.state), [.sent, .sent])
    }
    /// File-backed uploads are not covered by the embedded-photo cases: a lost PUT
    /// needs a new reservation, while a lost send reply must retain its ready token.
    func testVideoRestartsInterruptedUploadAndResendsFrozenReferenceOnceAfterRelaunch() async throws {
        let relativePath = UUID().uuidString + "-clip.mov"
        try FileManager.default.createDirectory(at: LocalStore.directory, withIntermediateDirectories: true)
        let file = LocalStore.directory.appendingPathComponent(relativePath)
        defer { try? FileManager.default.removeItem(at: file) }
        try Data("video fixture bytes".utf8).write(to: file)
        let video = SavedVideo(id: "clip", fileName: "clip.mov", filePath: relativePath, byteCount: 19)
        let original = PendingWork(id: "with-video", created: .now, kind: .message, owner: .ticket("ticket"),
            message: ConversationSend(conversationId: "conversation", content: [.init(piece: "local_video", localVideo: video)],
                senderMessageId: "with-video", sentAtUnixMilliseconds: 1))
        try store.outgoing.enqueue(original, server: server)
        var outbox = Outbox(store: store)
        OfflineProtocol.state.offline = true
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .waiting)
        XCTAssertTrue(FileManager.default.fileExists(atPath: file.path))
        OfflineProtocol.state.offline = false
        OfflineProtocol.state.interruptUpload = true
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .waiting)
        XCTAssertEqual(OfflineProtocol.state.uploadReservations, 1)
        XCTAssertTrue(OfflineProtocol.state.messageBodies.isEmpty)

        OfflineProtocol.state.loseResponseIDs = [original.id]
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.uploadReservations, 2)
        XCTAssertEqual(OfflineProtocol.state.uploadTransfers, ["upload-1", "upload-2"])
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .waiting)
        let firstBody = try XCTUnwrap(OfflineProtocol.state.messageBodies.first?.1)
        let content = try XCTUnwrap(try json(firstBody)["content"] as? [[String: Any]])
        XCTAssertEqual(content.first?["piece"] as? String, "attachment_upload")
        XCTAssertEqual(content.first?["upload_id"] as? String, "upload-2")
        XCTAssertNil(content.first?["local_video"])
        XCTAssertNil(content.first?["data"])

        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.deliveredMessages, [original.id])
        XCTAssertEqual(OfflineProtocol.state.uploadTransfers, ["upload-1", "upload-2"], "A lost send reply never uploads again")
        XCTAssertEqual(try json(firstBody), try json(XCTUnwrap(OfflineProtocol.state.messageBodies.last?.1)))
    }

    /// A request lost before admission must not strand its local file behind an
    /// expired ready token. Accepted tokens remain pinned and are never replaced.
    func testExpiredUnadmittedVideoReferencesRefreshWithoutChangingMessageOrDecisionIdentity() async throws {
        let relativePath = UUID().uuidString + "-expiry.mov"
        try FileManager.default.createDirectory(at: LocalStore.directory, withIntermediateDirectories: true)
        let file = LocalStore.directory.appendingPathComponent(relativePath)
        defer { try? FileManager.default.removeItem(at: file) }
        try Data("expiry video".utf8).write(to: file)
        let video = SavedVideo(id: "expiry-clip", fileName: "expiry.mov", filePath: relativePath, byteCount: 12)
        let piece = ConversationSend.Piece(piece: "local_video", localVideo: video)
        let message = PendingWork(id: "expiry-message", created: .now, kind: .message, owner: .ticket("ticket"),
            message: ConversationSend(conversationId: "conversation", content: [piece],
                senderMessageId: "expiry-message", sentAtUnixMilliseconds: 1))
        try store.outgoing.enqueue(message, server: server)
        var outbox = Outbox(store: store)
        OfflineProtocol.state.loseMessageBeforeCommit = true
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: message.id)?.state, .waiting)
        OfflineProtocol.state.expireReadyUploads()
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: message.id)?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.writes, [message.id, message.id])
        XCTAssertEqual(OfflineProtocol.state.deliveredMessages, [message.id])
        XCTAssertEqual(OfflineProtocol.state.uploadReservations, 2)

        var original = try decision("expiry-decision", guidance: "Keep this note")
        original.decisionAttachments = [piece]
        original.decisionConversationID = try APIClient.decoder().decode(TicketDetail.self, from: OfflineProtocol.state.ticket).conversationId
        OfflineProtocol.state.loseDecisionBeforeCommit = true
        try outbox.enqueue(original, api: api)
        await outbox.drain(api: api)
        let first = try XCTUnwrap(outbox.work(server, id: original.id)?.protectedDecisionBody)
        OfflineProtocol.state.expireReadyUploads()
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.map(\.0), [original.id, original.id])
        XCTAssertNotEqual(OfflineProtocol.state.decisionBodies.last?.1, first)
        XCTAssertEqual(OfflineProtocol.state.decisionOutcomes, 1)
        XCTAssertEqual(OfflineProtocol.state.uploadReservations, 4)
    }

    func testFirstMessageResendsWithoutDestinationAndLaterMessagesJoinTheConversationItCreated() async throws {
        var outbox = Outbox(store: store)
        try store.outgoing.enqueue(message("start", conversation: nil), server: server)
        try store.outgoing.enqueue(message("follow", conversation: nil, at: 2), server: server)
        OfflineProtocol.state.createdConversation = "created"
        OfflineProtocol.state.loseResponseIDs = ["follow"]
        OfflineProtocol.state.loseResponse = true
        await outbox.drain(api: api)
        outbox = relaunch()
        await outbox.drain(api: api)
        // The follow-up loses its response too: its resend names the conversation again.
        outbox = relaunch()
        await outbox.drain(api: api)
        let sent = OfflineProtocol.state.messageBodies
        XCTAssertEqual(sent.map(\.0), ["start", "start", "follow", "follow"])
        XCTAssertEqual(try json(sent[2].1), try json(sent[3].1), "Every send of the follow-up is the same body")
        XCTAssertEqual(try json(sent[0].1), try json(sent[1].1), "The creating message resends unchanged, with no destination")
        let start = try JSONSerialization.jsonObject(with: sent[1].1) as! [String: Any]
        XCTAssertNil(start["conversation_id"]); XCTAssertEqual(start["backend_key"] as? String, "codex")
        let follow = try JSONSerialization.jsonObject(with: sent[3].1) as! [String: Any]
        XCTAssertEqual(follow["conversation_id"] as? String, "created")
        XCTAssertNil(follow["backend_key"], "Creation settings stay with the creating message")
        XCTAssertEqual(outbox.entries(server).map(\.state), [.sent, .sent])
    }
    func testRefusedMessageShowsItsReasonAndReleasesItsConversation() async throws {
        let outbox = Outbox(store: store)
        try store.outgoing.enqueue(message("refused"), server: server)
        try store.outgoing.enqueue(message("after", at: 2), server: server)
        OfflineProtocol.state.refuseMessageIDs = ["refused"]
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: "refused")?.state, .failed)
        XCTAssertEqual(outbox.work(server, id: "refused")?.note, "Not delivered · agent could not start")
        XCTAssertEqual(outbox.work(server, id: "after")?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.writes, ["refused", "after"], "A refusal is never resent by itself")
    }
    /// Uncertain, and a fate this build does not know, settle without a resend and keep
    /// "Delivery unconfirmed" rather than looking delivered.
    func testUncertainAndUnknownFatesSettleOnceUnconfirmedAndNeverResend() async throws {
        var outbox = Outbox(store: store)
        try store.outgoing.enqueue(message("uncertain"), server: server)
        try store.outgoing.enqueue(message("unknown", at: 2), server: server)
        try store.outgoing.enqueue(message("after", at: 3), server: server)
        OfflineProtocol.state.messageFates = ["uncertain": "uncertain", "unknown": "handed_to_a_future_relay"]
        await outbox.drain(api: api)
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.writes, ["uncertain", "unknown", "after"], "Neither is ever resent, and the lane moves on")
        for id in ["uncertain", "unknown"] {
            XCTAssertEqual(outbox.work(server, id: id)?.state, .sent)
            XCTAssertEqual(outbox.work(server, id: id)?.note, "Delivery unconfirmed")
        }
        XCTAssertEqual(outbox.work(server, id: "after")?.state, .sent)
        XCTAssertEqual(outbox.work(server, id: "after")?.note, "", "An accepted message carries no note")
    }
    /// Home answers at acceptance, so a refusal usually arrives later, in the transcript. The
    /// message fails again with that refusal; Try Again sends it once under a new ID that names
    /// the refused row, which then folds away.
    func testLateRefusalAfterAcceptanceTriesAgainOnceAndFoldsTheRefusedRow() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [OfflineProtocol.self]
        let api = APIClient(baseURL: URL(string: "https://offline-test-\(UUID().uuidString.lowercased()).invalid")!, session: URLSession(configuration: configuration))
        let shared = LocalStore.server(api.baseURL)
        defer { try? LocalStore.shared.remove(server: shared, kind: "outbox", ids: Outbox.shared.entries(shared).map(\.id)) }
        func row(_ sender: String, kind: String, sequence: Int) -> [String: Any] {
            ["kind": "row", "key": "e\(sequence)", "sender_message_id": sender, "source_sequence": sequence,
             "body": ["kind": "inline", "value": ["kind": kind, "reason": "backend_did_not_start", "content": [["piece": "text", "text": "late"]]]]]
        }
        try LocalStore.shared.outgoing.enqueue(message("late"), server: shared)
        await Outbox.shared.drain(api: api)
        XCTAssertEqual(Outbox.shared.work(shared, id: "late")?.state, .sent, "Acceptance settles the message")
        let model = ConversationModel()
        model.conversationID = "conversation"
        model.restore(owner: .ticket("ticket"), api: api)
        OfflineProtocol.state.viewItems = [row("late", kind: "prompt_refused", sequence: 7)]
        await model.refresh(using: api, transcript: true)
        XCTAssertEqual(Outbox.shared.work(shared, id: "late")?.state, .failed, "The later refusal reopens it")
        XCTAssertEqual(model.rows.map(\.id), ["late"])
        XCTAssertEqual(model.rows.first?.note, "Not delivered · agent could not start")
        XCTAssertEqual(model.rows.first?.retryID, "late")

        await model.retry("late", to: .ticket("ticket"), using: api)
        await Outbox.shared.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.writes.count, 2, "Try Again sends it once")
        let resendID = try XCTUnwrap(OfflineProtocol.state.writes.last)
        XCTAssertNotEqual(resendID, "late", "Home keeps the refused ID's answer, so the resend needs its own")
        let resend = try json(XCTUnwrap(OfflineProtocol.state.messageBodies.last?.1))
        XCTAssertEqual(resend["reconciles_sequence"] as? Int, 7, "The resend names the refused row")
        XCTAssertEqual(Outbox.shared.work(shared, id: resendID)?.state, .sent)
        // Home folds the refused row once the resend is recorded.
        OfflineProtocol.state.viewItems = [row(resendID, kind: "prompt", sequence: 9)]
        await model.refresh(using: api, transcript: true)
        XCTAssertEqual(model.rows.map(\.id), ["late"], "One bubble, in the refused message's place")
        XCTAssertNil(model.rows.first?.note)
        XCTAssertNil(model.rows.first?.retryID)

        // Another device sends a refused message again: its row folds away live, and the
        // phone's failed copy leaves rather than offering a second resend.
        try LocalStore.shared.outgoing.enqueue(message("elsewhere", at: 2), server: shared)
        await Outbox.shared.drain(api: api)
        let status: [String: Any] = ["authority": "current", "turn_running": true, "rest": ["plan_progress": [:]]]
        let snapshot: [String: Any] = ["page": ["items": [row(resendID, kind: "prompt", sequence: 9), row("elsewhere", kind: "prompt_refused", sequence: 10)],
            "older": NSNull()], "status": status, "coverage": ["through_sequence": 10]]
        model.beginTail()
        _ = await model.applyTail(.snapshot(try APIClient.decoder().decode(ConversationSnapshot.self, from: JSONSerialization.data(withJSONObject: snapshot))), using: api)
        XCTAssertEqual(Outbox.shared.work(shared, id: "elsewhere")?.state, .failed)
        let fold: [String: Any] = ["lens": "focus", "from_sequence": 10, "coverage": ["through_sequence": 11], "upserts": [], "scope_changes": [],
            "removed_keys": ["e10"], "invalidated_scopes": [], "status": status]
        let applied = await model.applyTail(.changes(try APIClient.decoder().decode(ConversationTailChanges.self, from: JSONSerialization.data(withJSONObject: fold))), using: api)
        XCTAssertTrue(applied)
        XCTAssertNil(Outbox.shared.work(shared, id: "elsewhere"), "The folded refusal is settled")
        XCTAssertFalse(model.rows.contains { $0.id == "elsewhere" })
        XCTAssertEqual(OfflineProtocol.state.writes.count, 3, "Nothing sends it a second time")
    }
    func testPhoto422RestoresDraftAndRetryClearsOnlyThatDraft() async throws {
        store.saveDraft("  Original words  ", server: server, id: "photo-draft")
        try store.put(server: server, kind: "attachments", id: "photo-draft", data: JSONEncoder().encode([tinyPhoto]))
        let original = PendingWork(id: "photo", created: .now, kind: .message, owner: .ticket("ticket"),
            message: ConversationSend(conversationId: "conversation", content: [.init(text: "Original words"), tinyPhoto], senderMessageId: "photo", sentAtUnixMilliseconds: 123))
        try store.outgoing.enqueue(original, server: server, capturedDraft: store.outgoing.captureDraft(server: server, id: "photo-draft"))
        XCTAssertEqual(store.draft(server: server, id: "photo-draft"), "")
        OfflineProtocol.state.messageStatus = 422
        let outbox = Outbox(store: store)
        await outbox.drain(api: api)
        let reopened = LocalStore(url: storeURL)
        XCTAssertEqual(try reopened.outgoing.work(server: server, id: "photo")?.state, .failed)
        XCTAssertEqual(reopened.draft(server: server, id: "photo-draft"), "  Original words  ")
        let restored = try XCTUnwrap(reopened.record(server: server, kind: "attachments", id: "photo-draft")?.payload)
        XCTAssertEqual(try JSONDecoder().decode([ConversationSend.Piece].self, from: restored), [tinyPhoto])
        OfflineProtocol.state.messageStatus = 200
        let retried = await outbox.retry("photo", api: api)
        let nextID = try XCTUnwrap(retried)
        XCTAssertEqual(outbox.work(server, id: nextID)?.priorMessageIDs, ["photo"])
        XCTAssertEqual(store.draft(server: server, id: "photo-draft"), "")
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: nextID)?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.writes, ["photo", nextID])
    }
    func testNotificationReplySurvivesOfflineRelaunchAndLostResponseWithOneIdentity() async throws {
        var outbox = Outbox(store: store)
        let reply = NotificationReply(serverURL: api.baseURL, conversationID: "conversation", messageID: "conversation:7", owner: .ticket("ticket"))
        let id = try outbox.enqueueNotificationReply(reply, text: "Reply from notification", notificationID: "push-7", api: api)
        OfflineProtocol.state.offline = true
        await outbox.drain(api: api)
        outbox = relaunch()
        XCTAssertEqual(try outbox.enqueueNotificationReply(reply, text: "Reply from notification", notificationID: "push-7", api: api), id)
        XCTAssertEqual(outbox.entries(server).count, 1)
        OfflineProtocol.state.offline = false
        OfflineProtocol.state.loseResponse = true
        OfflineProtocol.state.responseError = .cancelled
        await outbox.drain(api: api)
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.writes, [id, id])
        XCTAssertEqual(OfflineProtocol.state.deliveredMessages, [id])
        XCTAssertEqual(outbox.work(server, id: id)?.state, .sent)
    }

    func testNotificationReplySendsWhenTheOfflineFlagWentStale() async throws {
        let outbox = Outbox(store: store)
        outbox.networkAvailable = false // The app went to the background offline; no loop watches the path now.
        let reply = NotificationReply(serverURL: api.baseURL, conversationID: "conversation", messageID: "conversation:7", owner: .ticket("ticket"))
        let id = try outbox.enqueueNotificationReply(reply, text: "Reply from notification", notificationID: "push-7", api: api)
        await outbox.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.deliveredMessages, [id])
        XCTAssertEqual(outbox.work(server, id: id)?.state, .sent)
    }

    func testServerErrorsFailAMessageAfterTheBoundAndItsLaneMovesOn() async throws {
        var clock = Date.now
        let outbox = Outbox(store: store, now: { clock })
        try store.outgoing.enqueue(message("first"), server: server)
        try store.outgoing.enqueue(message("second", at: 2), server: server)
        OfflineProtocol.state.messageStatus = 503
        for _ in 1..<Outbox.unsettledAttemptBound {
            await outbox.drain(api: api); clock += 60
            OfflineProtocol.state.offline = true
            await outbox.drain(api: api); clock += 60 // No network does not count.
            OfflineProtocol.state.offline = false
        }
        XCTAssertEqual(outbox.entries(server).map(\.state), [.waiting, .waiting], "The second waits behind the first")
        await outbox.drain(api: api); clock += 60
        let first = try XCTUnwrap(outbox.work(server, id: "first"))
        XCTAssertEqual(first.state, .failed)
        XCTAssertEqual(first.note, "Not sent · Server returned HTTP 503.")
        OfflineProtocol.state.messageStatus = 200
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: "second")?.state, .sent, "The lane moves on past the failed item")
        let retried = await outbox.retry("first", api: api)
        XCTAssertEqual(retried, "first", "The server may hold it already, so Try Again keeps its identity")
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: "first")?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.writes, Array(repeating: "first", count: Outbox.unsettledAttemptBound) + ["second", "second", "first"])
        XCTAssertEqual(OfflineProtocol.state.deliveredMessages, ["first", "second"])
    }

    // MARK: Decisions

    private func decision(_ id: String, kind: PendingWork.Kind = .approval, guidance: String? = nil) throws -> PendingWork {
        let detail = try APIClient.decoder().decode(TicketDetail.self, from: OfflineProtocol.state.ticket)
        let proposal = try XCTUnwrap(detail.pendingProposal)
        return PendingWork(id: id, created: .now, kind: kind, ticketID: detail.id, proposal: proposal,
            approval: kind == .approval ? ReviewApproval(expectedProposalRevision: proposal.revision, editedBody: nil, nextCeiling: "needs_implementation", nextHolder: .init(kind: "owner", id: "owner")) : nil,
            guidance: guidance ?? (kind == .sendBack ? "Keep these exact words" : nil))
    }
    func testDecisionLostAfterCommitRelaunchesFromItsReceiptWithoutAnotherSubmit() async throws {
        var outbox = Outbox(store: store)
        var original = try decision("committed", guidance: "  Exact approval note 🪶  ")
        original.draftID = "approval-guidance"
        store.saveDraft(original.guidance!, server: server, id: "approval-guidance")
        original.decisionDraftUpdatedAt = try store.outgoing.captureDraft(server: server, id: "approval-guidance").text?.updated
        OfflineProtocol.state.loseDecisionAfterCommit = true
        try outbox.enqueue(original, api: api)
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .waiting, "A lost response is not shown")
        XCTAssertEqual(store.draft(server: server, id: "approval-guidance"), "  Exact approval note 🪶  ", "A lost response cannot clear the draft")
        OfflineProtocol.state.bumpRevision() // The receipt is read before any current proposal.
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .sent)
        XCTAssertEqual(store.draft(server: server, id: "approval-guidance"), "", "The applied receipt clears the captured draft")
        XCTAssertEqual(OfflineProtocol.state.decisionOutcomes, 1)
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.count, 1)
    }
    func testDecisionLostBeforeCommitResubmitsOnlyItsOperationAndFrozenBytes() async throws {
        var outbox = Outbox(store: store)
        let original = try decision("null-retry", kind: .sendBack)
        OfflineProtocol.state.loseDecisionBeforeCommit = true
        try outbox.enqueue(original, api: api)
        await outbox.drain(api: api)
        let body = try XCTUnwrap(outbox.work(server, id: original.id)?.protectedDecisionBody)
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.map(\.0), [original.id, original.id])
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.map(\.1), [body, body])
        XCTAssertEqual(OfflineProtocol.state.decisionOutcomes, 1)
    }
    /// Risk: a decision's pictures and files are lost before the decision is sent, are sent
    /// in bytes that differ between attempts, or make a decision land twice when its reply
    /// is lost. They are saved first inside the frozen body, resent unchanged, and land once.
    func testDecisionWithAttachmentIsFrozenFirstResentUnchangedAndLandsOnceAcrossLostReplies() async throws {
        var outbox = Outbox(store: store)
        var original = try decision("with-photo", guidance: "See the picture")
        original.draftID = "photo-guidance"
        original.decisionAttachments = [tinyPhoto]
        store.saveDraft("See the picture", server: server, id: "photo-guidance")
        try store.put(server: server, kind: "attachments", id: "photo-guidance", data: JSONEncoder().encode([tinyPhoto]))
        original.decisionDraftUpdatedAt = try store.outgoing.captureDraft(server: server, id: "photo-guidance").text?.updated
        OfflineProtocol.state.loseDecisionBeforeCommit = true
        try outbox.enqueue(original, api: api)
        let saved = try XCTUnwrap(outbox.work(server, id: original.id))
        XCTAssertNil(saved.decisionAttachments, "The pictures live only in the frozen body")
        let body = try XCTUnwrap(saved.protectedDecisionBody)
        let posted = try XCTUnwrap(try json(body)["attachments"] as? [[String: String]])
        XCTAssertEqual(posted, [["piece": "image", "data": tinyPhoto.data!, "media_type": "image/png", "file_name": "photo.png"]])
        await outbox.drain(api: api)
        // The next attempt commits, and its reply is lost too.
        OfflineProtocol.state.loseDecisionAfterCommit = true
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .waiting, "A lost response is not shown")
        XCTAssertNotEqual(try store.record(server: server, kind: "attachments", id: "photo-guidance")?.payload, Data("[]".utf8),
                          "A lost response cannot clear the note's pictures")
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.map(\.0), [original.id, original.id])
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.map(\.1), [body, body])
        XCTAssertEqual(OfflineProtocol.state.decisionOutcomes, 1)
        XCTAssertEqual(try store.record(server: server, kind: "attachments", id: "photo-guidance")?.payload, Data("[]".utf8),
                       "The applied receipt clears the note's pictures with its words")
        XCTAssertEqual(store.draft(server: server, id: "photo-guidance"), "")
    }
    /// A local video decision must save before upload and freeze only its final
    /// wire references. Receipt recovery must not clear a newer note attachment.
    func testVideoDecisionFreezesAfterUploadAndKeepsOneOperationAcrossLostReplies() async throws {
        let relativePath = UUID().uuidString + "-decision.mov"
        try FileManager.default.createDirectory(at: LocalStore.directory, withIntermediateDirectories: true)
        let file = LocalStore.directory.appendingPathComponent(relativePath)
        defer { try? FileManager.default.removeItem(at: file) }
        try Data("decision video".utf8).write(to: file)
        let video = SavedVideo(id: "decision-clip", fileName: "decision.mov", filePath: relativePath, byteCount: 14)
        let piece = ConversationSend.Piece(piece: "local_video", localVideo: video)
        var original = try decision("video-decision", guidance: "Exact video note")
        original.draftID = "video-guidance"
        original.decisionConversationID = try APIClient.decoder().decode(TicketDetail.self, from: OfflineProtocol.state.ticket).conversationId
        original.decisionAttachments = [piece]
        store.saveDraft("Exact video note", server: server, id: "video-guidance")
        try store.put(server: server, kind: "attachments", id: "video-guidance", data: JSONEncoder().encode([piece]))
        original.decisionDraftUpdatedAt = try store.outgoing.captureDraft(server: server, id: "video-guidance").text?.updated
        var outbox = Outbox(store: store)
        OfflineProtocol.state.offline = true
        try outbox.enqueue(original, api: api)
        XCTAssertNotNil(outbox.work(server, id: original.id)?.decisionIntent)
        XCTAssertNil(outbox.work(server, id: original.id)?.protectedDecisionBody)
        await outbox.drain(api: api)
        OfflineProtocol.state.offline = false
        OfflineProtocol.state.loseDecisionBeforeCommit = true
        outbox = relaunch()
        await outbox.drain(api: api)
        let frozen = try XCTUnwrap(outbox.work(server, id: original.id)?.protectedDecisionBody)
        let attachment = try XCTUnwrap((try json(frozen)["attachments"] as? [[String: Any]])?.first)
        XCTAssertEqual(attachment["piece"] as? String, "attachment_upload")
        XCTAssertNil(attachment["local_video"])
        OfflineProtocol.state.loseDecisionAfterCommit = true
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .waiting)
        var newer = piece; newer.fileName = "Newer edit.mov"
        let newerBytes = try JSONEncoder().encode([newer])
        try store.put(server: server, kind: "attachments", id: "video-guidance", data: newerBytes)
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: original.id)?.state, .sent)
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.map(\.0), [original.id, original.id])
        XCTAssertEqual(OfflineProtocol.state.decisionBodies.map(\.1), [frozen, frozen])
        XCTAssertEqual(OfflineProtocol.state.decisionOutcomes, 1)
        XCTAssertEqual(OfflineProtocol.state.uploadReservations, 1)
        XCTAssertEqual(try store.record(server: server, kind: "attachments", id: "video-guidance")?.payload, newerBytes)
    }

    func testChangedProposalRefusesBothDecisionKindsOnTheirItemAndKeepsGuidance() async throws {
        let outbox = Outbox(store: store)
        try outbox.enqueue(try decision("approve"), api: api)
        try outbox.enqueue(try decision("return", kind: .sendBack), api: api)
        OfflineProtocol.state.bumpRevision()
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.entries(server).map(\.state), [.failed, .failed])
        XCTAssertEqual(outbox.entries(server).first?.note, "Not sent · Proposal changed")
        let frozen = try XCTUnwrap(outbox.entries(server).last?.protectedDecisionBody)
        XCTAssertEqual(try APIClient.decoder().decode(DecisionCommand.self, from: frozen).message, "Keep these exact words")
    }

    // MARK: Mark read

    func testMarkReadCoalescesToTheHighestPointAndResendsAfterALostResponse() async throws {
        var outbox = Outbox(store: store)
        OfflineProtocol.state.offline = true
        for through in [3, 9, 5] { try outbox.enqueueMarkRead(owner: .chief, conversationID: "conversation", through: through, api: api) }
        try outbox.enqueueMarkRead(owner: .chief, conversationID: "other", through: 2, api: api)
        await outbox.drain(api: api)
        let saved = outbox.entries(server)
        XCTAssertEqual(saved.map(\.readThroughSequence), [9, 2], "One waiting read per conversation, at the highest point")
        outbox = relaunch()
        OfflineProtocol.state.offline = false
        OfflineProtocol.state.loseResponse = true
        await outbox.drain(api: api)
        outbox = relaunch()
        await outbox.drain(api: api)
        let reads = OfflineProtocol.state.reads
        XCTAssertEqual(reads.count, 3, "One lost read is sent again; nothing else repeats")
        XCTAssertEqual(Set(reads.filter { $0.0 == "conversation" }.map(\.1)), [9])
        XCTAssertEqual(Set(reads.filter { $0.0 == "other" }.map(\.1)), [2])
        XCTAssertEqual(outbox.entries(server).map(\.state), [.sent, .sent])
        try outbox.enqueueMarkRead(owner: .chief, conversationID: "conversation", through: 9, api: api)
        XCTAssertEqual(outbox.entries(server).count, 2, "A point already sent is not saved again")
    }

    // MARK: Ask answers

    private let question = AskAnswer(conversationID: "conversation", request: .userInput(requestID: "request-1"),
        questions: [.init(id: "colourChoice", prompt: "Which colour?", answers: ["blue"], labels: ["Blue"])])
    func testRefusedAskAnswerWaitsWhileItsAskWaitsThenBecomesExactlyOneLateMessage() async throws {
        var outbox = Outbox(store: store)
        OfflineProtocol.state.answerFate = "refused"
        OfflineProtocol.state.pendingUserInput = "request-1"
        let operation = try outbox.enqueueAskAnswer(question, owner: .ticket("ticket"), api: api)
        await outbox.drain(api: api)
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty, "The ask still waits: the answer is sent again later, not as a message")
        XCTAssertEqual(outbox.work(server, id: operation)?.state, .waiting)
        // The view names only the newest waiting ask, which can hide this one: still wait.
        OfflineProtocol.state.pendingUserInput = "request-2"
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty, "Another waiting ask may hide this one")
        OfflineProtocol.state.pendingUserInput = nil
        outbox = relaunch()
        await outbox.drain(api: api)
        await outbox.drain(api: api)
        outbox = relaunch()
        await outbox.drain(api: api)
        let late = Outbox.lateAnswerMessageID(operationID: operation)
        XCTAssertEqual(OfflineProtocol.state.answers, [operation, operation, operation], "Each try reuses the operation ID")
        XCTAssertEqual(OfflineProtocol.state.writes, [late], "One late message, sent once")
        let body = try JSONSerialization.jsonObject(with: XCTUnwrap(OfflineProtocol.state.messageBodies.first?.1)) as! [String: Any]
        XCTAssertEqual(body["conversation_id"] as? String, "conversation")
        XCTAssertEqual((body["content"] as? [[String: Any]])?.first?["text"] as? String, "Answer to \"Which colour?\": Blue")
        XCTAssertEqual(outbox.work(server, id: operation)?.state, .sent)
        XCTAssertEqual(outbox.work(server, id: late)?.state, .sent)
        let sentAnswer = try XCTUnwrap(OfflineProtocol.state.answerBodies.first)
        let answers = (sentAnswer["answers"] as? [String: Any])?["colourChoice"] as? [String: Any]
        XCTAssertEqual(answers?["answers"] as? [String], ["blue"], "Question IDs reach the server as named")
    }
    func testAnswerRefusedWithAnHTTPErrorTakesTheSamePathAndTryAgainKeepsItsIdentity() async throws {
        var clock = Date.now
        let outbox = Outbox(store: store, now: { clock })
        OfflineProtocol.state.answerStatus = 409
        OfflineProtocol.state.pendingUserInput = "request-1"
        let operation = try outbox.enqueueAskAnswer(question, owner: .ticket("ticket"), api: api)
        for _ in 0..<Outbox.unsettledAttemptBound { await outbox.drain(api: api); clock += 60 }
        XCTAssertEqual(outbox.work(server, id: operation)?.state, .failed, "An ask that keeps waiting fails after the bound")
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty)
        OfflineProtocol.state.pendingUserInput = nil
        let retried = await outbox.retry(operation, api: api)
        XCTAssertEqual(retried, operation)
        await outbox.drain(api: api)
        await outbox.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.answers, Array(repeating: operation, count: Outbox.unsettledAttemptBound + 1))
        XCTAssertEqual(OfflineProtocol.state.writes, [Outbox.lateAnswerMessageID(operationID: operation)], "The ask is gone: one late message")
    }
    func testRefusedAskAnswerWithAReceiptSendsNothingMore() async throws {
        let outbox = Outbox(store: store)
        OfflineProtocol.state.answerFate = "refused"
        let operation = try outbox.enqueueAskAnswer(question, owner: .ticket("ticket"), api: api)
        OfflineProtocol.state.answerReceipts[operation] = "applied"
        await outbox.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.answers, [operation])
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty, "A receipt means the answer landed")
        XCTAssertEqual(outbox.work(server, id: operation)?.state, .sent)
        XCTAssertEqual(outbox.entries(server).count, 1)
    }
    func testLostAskAnswerChecksItsReceiptBeforeAnythingElse() async throws {
        var outbox = Outbox(store: store)
        let lost = try outbox.enqueueAskAnswer(question, owner: .ticket("ticket"), api: api)
        OfflineProtocol.state.loseResponse = true
        await outbox.drain(api: api)
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.requests.filter { $0.contains("answer") }, [
            "POST /api/conversation/conversations/conversation/user-input-answers",
            "GET /api/conversation/conversations/conversation/answer-receipts/\(lost)",
        ], "The first answer landed before its response was lost: its receipt settles it")
        XCTAssertEqual(outbox.work(server, id: lost)?.state, .sent)

        let unsaved = try outbox.enqueueAskAnswer(question, owner: .ticket("ticket"), api: api)
        OfflineProtocol.state.loseAnswerBeforeSave = true
        await outbox.drain(api: api)
        outbox = relaunch()
        await outbox.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.answers, [lost, unsaved, unsaved], "No receipt after a lost response: the same answer again")
        XCTAssertEqual(outbox.work(server, id: unsaved)?.state, .sent)
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty)
    }

    // MARK: Recordings

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
        await relaunch().drain(api: api)
        XCTAssertEqual(store.draft(server: server, id: "guidance"), "Typed\n\nRecorded words")
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
        XCTAssertEqual(OfflineProtocol.state.writes, ["voice"])
    }

    /// The note says "saved" while a recording waits and "transcribing" only while its request runs.
    func testRecordingIsInFlightOnlyWhileItsTranscriptionRequestRuns() async throws {
        let file = "test-\(UUID()).m4a"
        let url = LocalStore.directory.appendingPathComponent(file)
        try FileManager.default.createDirectory(at: LocalStore.directory, withIntermediateDirectories: true)
        try Data([1, 2, 3]).write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }
        let outbox = Outbox(store: store)
        try outbox.save(PendingWork(id: "audio", created: .now, kind: .voice, audioFile: file, draftID: "guidance"), server: server)
        OfflineProtocol.state.offline = true
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.work(server, id: "audio")?.state, .waiting)
        XCTAssertTrue(outbox.transcriptionsInFlight.isEmpty, "A dropped request leaves the recording saved, not transcribing")

        OfflineProtocol.state.offline = false
        let gate = DispatchSemaphore(value: 0)
        OfflineProtocol.state.voiceGate = gate
        let online = relaunch()
        let drain = Task { await online.drain(api: api) }
        for _ in 0..<500 where online.transcriptionsInFlight.isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertEqual(online.transcriptionsInFlight, ["audio"])
        gate.signal()
        await drain.value
        XCTAssertTrue(online.transcriptionsInFlight.isEmpty)
        XCTAssertEqual(online.work(server, id: "audio")?.state, .sent)
    }
}

private final class OfflineState: @unchecked Sendable {
    private let lock = NSLock()
    private var values = Values()
    private struct Values {
        var offline = false
        var responseError: URLError.Code = .networkConnectionLost
        var loseResponse = false
        var loseAnswerBeforeSave = false
        var ticket = Data()
        var writes: [String] = []
        var messageBodies: [(String, Data)] = []
        var deliveredMessages: [String] = []
        var refuseMessageIDs: Set<String> = []
        var loseResponseIDs: Set<String> = []
        /// An answer other than acceptance for these sender message IDs.
        var messageFates: [String: String] = [:]
        /// The conversation view's rows.
        var viewItems: [[String: Any]] = []
        var createdConversation: String?
        var requests: [String] = []
        var reads: [(String, Int)] = []
        var answers: [String] = []
        var answerBodies: [[String: Any]] = []
        var answerFate = "applied"
        var answerReceipts: [String: String] = [:]
        var answerStatus = 200
        var pendingUserInput: String?
        var decisionReceipts: [String: Data] = [:]
        var recordedDecisions: [(String, Data)] = []
        var decisionOutcomes = 0
        var messageStatus = 200
        var loseDecisionBeforeCommit = false
        var loseDecisionAfterCommit = false
        /// Holds a transcription request open until the test signals it.
        var voiceGate: DispatchSemaphore?
        var uploadStatuses: [String: [String: Any]] = [:]
        var uploadReservations = 0
        var uploadTransfers: [String] = []
        var interruptUpload = false
        var loseMessageBeforeCommit = false
    }
    private func value<T>(_ key: KeyPath<Values, T>) -> T { lock.withLock { values[keyPath: key] } }
    private func store<T>(_ key: WritableKeyPath<Values, T>, _ new: T) { lock.withLock { values[keyPath: key] = new } }
    var offline: Bool { get { value(\.offline) } set { store(\.offline, newValue) } }
    var responseError: URLError.Code { get { value(\.responseError) } set { store(\.responseError, newValue) } }
    var loseResponse: Bool { get { value(\.loseResponse) } set { store(\.loseResponse, newValue) } }
    var loseAnswerBeforeSave: Bool { get { value(\.loseAnswerBeforeSave) } set { store(\.loseAnswerBeforeSave, newValue) } }
    var refuseMessageIDs: Set<String> { get { value(\.refuseMessageIDs) } set { store(\.refuseMessageIDs, newValue) } }
    var loseResponseIDs: Set<String> { get { value(\.loseResponseIDs) } set { store(\.loseResponseIDs, newValue) } }
    var messageFates: [String: String] { get { value(\.messageFates) } set { store(\.messageFates, newValue) } }
    var viewItems: [[String: Any]] { get { value(\.viewItems) } set { store(\.viewItems, newValue) } }
    var createdConversation: String? { get { value(\.createdConversation) } set { store(\.createdConversation, newValue) } }
    var answerFate: String { get { value(\.answerFate) } set { store(\.answerFate, newValue) } }
    var answerReceipts: [String: String] { get { value(\.answerReceipts) } set { store(\.answerReceipts, newValue) } }
    var answerStatus: Int { get { value(\.answerStatus) } set { store(\.answerStatus, newValue) } }
    var pendingUserInput: String? { get { value(\.pendingUserInput) } set { store(\.pendingUserInput, newValue) } }
    var messageStatus: Int { get { value(\.messageStatus) } set { store(\.messageStatus, newValue) } }
    var loseDecisionBeforeCommit: Bool { get { value(\.loseDecisionBeforeCommit) } set { store(\.loseDecisionBeforeCommit, newValue) } }
    var loseDecisionAfterCommit: Bool { get { value(\.loseDecisionAfterCommit) } set { store(\.loseDecisionAfterCommit, newValue) } }
    var voiceGate: DispatchSemaphore? { get { value(\.voiceGate) } set { store(\.voiceGate, newValue) } }
    var interruptUpload: Bool { get { value(\.interruptUpload) } set { store(\.interruptUpload, newValue) } }
    var loseMessageBeforeCommit: Bool { get { value(\.loseMessageBeforeCommit) } set { store(\.loseMessageBeforeCommit, newValue) } }
    var uploadReservations: Int { value(\.uploadReservations) }
    var uploadTransfers: [String] { value(\.uploadTransfers) }
    var ticket: Data { value(\.ticket) }
    var writes: [String] { value(\.writes) }
    var messageBodies: [(String, Data)] { value(\.messageBodies) }
    var deliveredMessages: [String] { value(\.deliveredMessages) }
    var requests: [String] { value(\.requests) }
    var reads: [(String, Int)] { value(\.reads) }
    var answers: [String] { value(\.answers) }
    var answerBodies: [[String: Any]] { value(\.answerBodies) }
    var decisionBodies: [(String, Data)] { value(\.recordedDecisions) }
    var decisionOutcomes: Int { value(\.decisionOutcomes) }
    func reset(ticket: Data) { lock.withLock { values = Values(); values.ticket = ticket } }
    func expireReadyUploads() {
        lock.withLock {
            for id in Array(values.uploadStatuses.keys) where values.uploadStatuses[id]?["state"] as? String == "ready" {
                values.uploadStatuses[id]?["expires_at"] = Date().timeIntervalSince1970 - 1
            }
        }
    }
    private func admitUploads(_ body: [String: Any], key: String) {
        for piece in body[key] as? [[String: Any]] ?? [] {
            if let uploadID = piece["upload_id"] as? String {
                values.uploadStatuses[uploadID]?["state"] = "admitted"
            }
        }
    }
    func bumpRevision() {
        lock.withLock {
            var value = try! JSONSerialization.jsonObject(with: values.ticket) as! [String: Any]
            var proposal = value["pending_proposal"] as! [String: Any]
            proposal["revision"] = (proposal["revision"] as! Int) + 1
            value["pending_proposal"] = proposal
            values.ticket = try! JSONSerialization.data(withJSONObject: value)
        }
    }
    func status(_ request: URLRequest) -> Int {
        lock.withLock {
            let path = request.url!.path
            return path.hasSuffix("/send") ? values.messageStatus : path.hasSuffix("-answers") ? values.answerStatus : 200
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
    /// Commits first, then may lose the response, like a dropped connection after the server acted.
    private func respond(_ data: Data) throws -> Data {
        if values.loseResponse { values.loseResponse = false; throw URLError(values.responseError) }
        return data
    }
    func response(_ request: URLRequest) throws -> Data {
        try lock.withLock {
            let path = request.url!.path
            values.requests.append("\(request.httpMethod ?? "GET") \(path)")
            if values.offline { throw URLError(.notConnectedToInternet) }
            if path.contains("/video-uploads") {
                if request.httpMethod == "POST" {
                    let body = try JSONSerialization.jsonObject(with: requestBody(request)) as! [String: Any]
                    values.uploadReservations += 1
                    let id = "upload-\(values.uploadReservations)"
                    let status: [String: Any] = ["upload_id": id, "destination": body["destination"]!,
                        "filename": body["filename"]!, "byte_count": body["byte_count"]!, "received_bytes": 0,
                        "state": "reserved", "media_type": NSNull(), "expires_at": Date().timeIntervalSince1970 + 86400,
                        "error": NSNull()]
                    values.uploadStatuses[id] = status
                    return try JSONSerialization.data(withJSONObject: status)
                }
                let id = path.hasSuffix("/bytes") ? String(path.split(separator: "/").dropLast().last!) : request.url!.lastPathComponent
                var status = values.uploadStatuses[id]!
                if request.httpMethod == "PUT" {
                    values.uploadTransfers.append(id)
                    status["state"] = "transferring"; values.uploadStatuses[id] = status
                    if values.interruptUpload { values.interruptUpload = false; throw URLError(.networkConnectionLost) }
                    status["state"] = "ready"; status["media_type"] = "video/quicktime"
                    status["received_bytes"] = status["byte_count"]
                } else if request.httpMethod == "DELETE" { status["state"] = "cancelled" }
                values.uploadStatuses[id] = status
                return try JSONSerialization.data(withJSONObject: status)
            }
            if path.contains("/decision-operations/") {
                let operationID = request.url!.lastPathComponent
                if request.httpMethod != "POST" {
                    return values.decisionReceipts[operationID] ?? Data(#"{"receipt_contract":1,"receipt":null}"#.utf8)
                }
                let data = requestBody(request)
                values.recordedDecisions.append((operationID, data))
                if values.loseDecisionBeforeCommit { values.loseDecisionBeforeCommit = false; throw URLError(.networkConnectionLost) }
                if let existing = values.decisionReceipts[operationID] { return existing }
                let body = try JSONSerialization.jsonObject(with: data) as! [String: Any]
                let ticket = try JSONSerialization.jsonObject(with: values.ticket) as! [String: Any]
                let current = ticket["pending_proposal"] as? [String: Any]
                let refused = (body["expected_proposal_revision"] as? Int) != (current?["revision"] as? Int)
                let value: [String: Any] = ["receipt_contract": 1, "receipt": [
                    "operation_id": operationID, "ticket_id": String(path.split(separator: "/")[2]),
                    "decision": body["decision"]!, "field": body["field"]!, "expected_proposal_revision": body["expected_proposal_revision"]!,
                    "outcome": refused ? "refused" : "applied", "created_at": 100,
                    "transition": refused ? NSNull() : ["stage": "needs_implementation", "ceiling": "needs_implementation", "ceiling_holder": ["kind": "owner", "id": "owner"], "ticket_status": "ready", "pending_proposal_revision": 16],
                    "error": refused ? ["code": "changed_proposal", "message": "Proposal changed", "detail": [:]] : NSNull()
                ]]
                let response = try JSONSerialization.data(withJSONObject: value)
                values.decisionReceipts[operationID] = response; values.decisionOutcomes += 1
                if !refused { admitUploads(body, key: "attachments") }
                if values.loseDecisionAfterCommit { values.loseDecisionAfterCommit = false; throw URLError(.networkConnectionLost) }
                return response
            }
            if path.contains("/answer-receipts/") {
                let operationID = request.url!.lastPathComponent
                let receipt: Any = values.answerReceipts[operationID].map { ["operation_id": operationID, "fate": $0] } ?? NSNull()
                return try JSONSerialization.data(withJSONObject: ["receipt": receipt])
            }
            if path.hasSuffix("/view/receipts") { return Data(#"{"receipts":[]}"#.utf8) }
            if path.hasSuffix("/view") {
                let input: Any = values.pendingUserInput.map { ["kind": "user_input", "request_id": $0, "row_key": "input", "source_sequence": 2] } ?? NSNull()
                return try JSONSerialization.data(withJSONObject: ["page": ["items": values.viewItems, "older": NSNull()],
                    "status": ["authority": "current", "turn_running": true, "rest": ["plan_progress": [:]], "pending_user_input": input]])
            }
            if request.httpMethod != "POST" { return values.ticket }
            let data = requestBody(request)
            let body = try JSONSerialization.jsonObject(with: data) as! [String: Any]
            if path.hasSuffix("/owner-read") {
                let through = body["through_sequence"] as! Int
                values.reads.append((String(path.split(separator: "/")[3]), through))
                return try respond(JSONSerialization.data(withJSONObject: ["owner_read_through_sequence": through]))
            }
            if path.hasSuffix("-answers") {
                let operationID = body["operation_id"] as! String
                values.answers.append(operationID); values.answerBodies.append(body)
                if values.answerStatus != 200 { return Data(#"{"error":{"message":"the conversation is not the Ticket's active conversation"}}"#.utf8) }
                if values.loseAnswerBeforeSave { values.loseAnswerBeforeSave = false; throw URLError(.networkConnectionLost) }
                if values.answerFate != "refused" { values.answerReceipts[operationID] = values.answerFate }
                return try respond(JSONSerialization.data(withJSONObject: ["fate": values.answerFate]))
            }
            if path.hasSuffix("voice-transcriptions") {
                values.writes.append("voice")
                return try respond(Data(#"{"transcript":"Recorded words"}"#.utf8))
            }
            let id = body["sender_message_id"] as! String
            values.writes.append(id); values.messageBodies.append((id, data))
            if values.messageStatus == 422 { return Data(#"{"detail":"unsupported or invalid conversation image"}"#.utf8) }
            if values.refuseMessageIDs.contains(id) {
                return try JSONSerialization.data(withJSONObject: ["fate": "refused", "refusal_reason": "backend_did_not_start"])
            }
            if values.loseMessageBeforeCommit { values.loseMessageBeforeCommit = false; throw URLError(.networkConnectionLost) }
            admitUploads(body, key: "content")
            // The server drops a repeat by its sender message ID.
            if !values.deliveredMessages.contains(id) { values.deliveredMessages.append(id) }
            let conversation = body["conversation_id"] as? String ?? values.createdConversation ?? "conversation"
            if values.loseResponseIDs.remove(id) != nil { throw URLError(.networkConnectionLost) }
            // A current home answers at acceptance.
            return try respond(JSONSerialization.data(withJSONObject: ["conversation_id": conversation,
                "fate": values.messageFates[id] ?? "accepted", "message_id": id]))
        }
    }
}
private final class OfflineProtocol: URLProtocol, @unchecked Sendable {
    static let state = OfflineState()
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host?.hasPrefix("offline-test") == true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        if request.url?.path.hasSuffix("voice-transcriptions") == true { _ = Self.state.voiceGate?.wait(timeout: .now() + 10) }
        do {
            let data = try Self.state.response(request)
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: Self.state.status(request), httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data); client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}
