import Foundation
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
    func testEnqueueAndComposerClearShareDurableSave() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("test.store")
        let store = LocalStore(url: url)
        store.saveDraft("Unsent", server: "a", id: "draft")
        let message = ConversationSend(conversationId: "c", content: [.init(text: "Unsent")], senderMessageId: "stable", sentAtUnixMilliseconds: 123)
        let work = PendingWork(id: "stable", created: .now, kind: .message, owner: .chief, message: message)
        try store.put(server: "a", kind: "outbox", id: work.id, data: JSONEncoder().encode(work), draft: ("draft", ""))
        let reopened = LocalStore(url: url)
        XCTAssertEqual(reopened.draft(server: "a", id: "draft"), "")
        let saved = try XCTUnwrap(Outbox(store: reopened).entries("a").first)
        XCTAssertEqual(saved.message?.senderMessageId, "stable")
        XCTAssertEqual(saved.message?.sentAtUnixMilliseconds, 123)
        XCTAssertEqual(saved.displayText, "Unsent")
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
    private func message(_ id: String, at: TimeInterval = 1) -> PendingWork {
        PendingWork(id: id, created: Date(timeIntervalSince1970: at), kind: .message, owner: .ticket("ticket"),
            message: ConversationSend(conversationId: "conversation", content: [.init(text: id)], senderMessageId: id, sentAtUnixMilliseconds: Int64(at * 1000)))
    }
    func testOfflineRelaunchDrainsInOrderOnceAndPreservesIdentity() async throws {
        let outbox = Outbox(store: store)
        try outbox.save(message("first"), server: server)
        try outbox.save(message("second", at: 2), server: server)
        OfflineProtocol.state.offline = true
        await outbox.drain(api: api)
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty)
        let reopened = Outbox(store: LocalStore(url: directory.appendingPathComponent("test.store")))
        OfflineProtocol.state.offline = false
        await reopened.drain(api: api)
        await reopened.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.writes, ["first", "second"])
        XCTAssertEqual(reopened.entries(server).map(\.state), [.sent, .sent])
    }
    func testLostResponseReconcilesReceiptWithoutResending() async throws {
        let outbox = Outbox(store: store)
        try outbox.save(message("lost"), server: server)
        OfflineProtocol.state.loseResponse = true
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.entries(server).first?.state, .uncertain)
        let reopened = Outbox(store: LocalStore(url: directory.appendingPathComponent("test.store")))
        await reopened.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.writes, ["lost"])
        XCTAssertEqual(reopened.entries(server).first?.state, .sent)
    }
    func testChangedProposalHoldsBothDecisionKindsAndKeepsGuidance() async throws {
        let detail = try APIClient.decoder().decode(TicketDetail.self, from: OfflineProtocol.state.ticket)
        let proposal = try XCTUnwrap(detail.pendingProposal)
        let outbox = Outbox(store: store)
        let approval = ReviewApproval(expectedProposalRevision: proposal.revision, editedBody: "Edit", nextCeiling: "done", nextHolder: .init(kind: "owner", id: "owner"))
        try outbox.save(PendingWork(id: "approve", created: .now, kind: .approval, ticketID: detail.id, proposal: proposal, approval: approval), server: server)
        try outbox.save(PendingWork(id: "return", created: .now, kind: .sendBack, ticketID: detail.id, proposal: proposal, guidance: "Keep these words"), server: server)
        OfflineProtocol.state.bumpRevision()
        await outbox.drain(api: api)
        XCTAssertTrue(OfflineProtocol.state.writes.isEmpty)
        XCTAssertEqual(outbox.entries(server).map(\.state), [.changed, .changed])
        XCTAssertEqual(outbox.entries(server).last?.guidance, "Keep these words")
    }
    func testUnknownDecisionResponseNeverAutomaticallyReplays() async throws {
        let detail = try APIClient.decoder().decode(TicketDetail.self, from: OfflineProtocol.state.ticket)
        let outbox = Outbox(store: store)
        try outbox.save(PendingWork(id: "return", created: .now, kind: .sendBack, ticketID: detail.id, proposal: detail.pendingProposal, guidance: "Change it"), server: server)
        OfflineProtocol.state.loseResponse = true
        await outbox.drain(api: api)
        let reopened = Outbox(store: LocalStore(url: directory.appendingPathComponent("test.store")))
        await reopened.drain(api: api)
        XCTAssertEqual(OfflineProtocol.state.writes.count, 1)
        XCTAssertEqual(reopened.entries(server).first?.state, .uncertain)
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
    }
}

private final class OfflineState: @unchecked Sendable {
    private let lock = NSLock()
    private var _offline = false
    private var _loseResponse = false
    private var _ticket = Data()
    private var _writes: [String] = []
    private var receipts: Set<String> = []
    var offline: Bool { get { lock.withLock { _offline } } set { lock.withLock { _offline = newValue } } }
    var loseResponse: Bool { get { lock.withLock { _loseResponse } } set { lock.withLock { _loseResponse = newValue } } }
    var ticket: Data { lock.withLock { _ticket } }
    var writes: [String] { lock.withLock { _writes } }
    func reset(ticket: Data) { lock.withLock { _ticket = ticket; _offline = false; _loseResponse = false; _writes = []; receipts = [] } }
    func bumpRevision() {
        lock.withLock {
            var value = try! JSONSerialization.jsonObject(with: _ticket) as! [String: Any]
            var proposal = value["pending_proposal"] as! [String: Any]
            proposal["revision"] = (proposal["revision"] as! Int) + 1
            value["pending_proposal"] = proposal
            _ticket = try! JSONSerialization.data(withJSONObject: value)
        }
    }
    func response(_ request: URLRequest) throws -> Data {
        try lock.withLock {
            if _offline { throw URLError(.notConnectedToInternet) }
            let path = request.url!.path
            if path.hasSuffix("/receipts") {
                return try JSONSerialization.data(withJSONObject: ["receipts": receipts.map { ["sender_message_id": $0, "fate": "prompt"] }])
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
            _writes.append(id); receipts.insert(id)
            if _loseResponse { _loseResponse = false; throw URLError(.networkConnectionLost) }
            return Data((id == "voice" ? #"{"transcript":"Recorded words"}"# : #"{"conversation_id":"conversation","fate":"started"}"#).utf8)
        }
    }
}
private final class OfflineProtocol: URLProtocol, @unchecked Sendable {
    static let state = OfflineState()
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "offline-test.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let data = try Self.state.response(request)
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
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
        }
        let old = PendingWork(id: "old", created: .now, kind: .message, state: .failed, owner: .chief,
            message: ConversationSend(conversationId: "c", content: [.init(text: "Words")], senderMessageId: "old", sentAtUnixMilliseconds: 1))
        var retry = PendingWork(id: "new", created: .now, kind: .message, owner: .chief,
            message: ConversationSend(conversationId: "c", content: [.init(text: "Words")], senderMessageId: "new", sentAtUnixMilliseconds: 2))
        retry.priorMessageIDs = ["old"]
        try Outbox.shared.save(old, server: server)
        try Outbox.shared.save(retry, server: server)
        let model = ConversationModel()
        model.restore(owner: .chief, api: api)
        XCTAssertEqual(model.rows.count, 1)
        XCTAssertEqual(model.rows.first?.id, "old")
        XCTAssertEqual(model.rows.first?.text, "Words")
    }
}
