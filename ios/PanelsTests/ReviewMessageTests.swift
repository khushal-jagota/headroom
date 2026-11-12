import Foundation
import XCTest
@testable import Panels

@MainActor final class ReviewMessageTests: XCTestCase {
    func testRetainedMessagePresentationReadsOnlyTheSelectedSnapshot() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        defer { clean(api) }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = Outbox(store: LocalStore(url: directory.appendingPathComponent("review.store")))
        let server = LocalStore.server(api.baseURL)
        let message = ConversationSend(conversationId: "c", content: [.init(text: "Saved reply")], senderMessageId: "saved-reply", sentAtUnixMilliseconds: 1, ownerReadThroughSequence: 66)
        var work = PendingWork(id: "saved-reply", created: .now, kind: .message, state: .uncertain, owner: .chief, message: message)
        try outbox.save(work, server: server)
        var reads = 0
        var selected: [PendingWork] = []
        var revision: Int?
        let refresh: (String) -> Void = { server in
            let current = outbox.store.outgoingRevision(server)
            guard revision != current else { return }
            reads += 1; selected = outbox.entries(server, conversationID: "c"); revision = current
        }
        refresh(server)
        let decision = ReviewMessageDecision(savedWork: { _ in selected }, refreshSavedWork: refresh)
        await decision.load(item: item(), api: api)
        XCTAssertNil(decision.error)
        XCTAssertEqual(decision.attempt?.id, work.id)
        XCTAssertEqual(decision.composer.outgoing.first?.id, work.id)
        XCTAssertEqual(reads, 1)
        // These getters also run when SwiftUI retains the hidden Review subtree.
        for _ in 0..<100 {
            XCTAssertEqual(decision.attempt?.state, .uncertain)
            XCTAssertTrue(decision.pending)
            XCTAssertFalse(decision.accepted)
        }
        XCTAssertEqual(reads, 1)
        work.state = .sent
        try outbox.save(work, server: server)
        refresh(server); decision.refreshAttempt()
        XCTAssertEqual(reads, 2)
        XCTAssertTrue(decision.accepted)
        XCTAssertFalse(decision.pending)
    }
    func testAutomaticRecoveryRetainsSenderInsteadOfBecomingOwnerWords() throws {
        let body = try APIClient.decoder().decode(ConversationBody.self, from: Data(#"{"kind":"prompt","sender_label":"Panels deployment recovery","content":[{"piece":"text","text":"Inspect interrupted work"}]}"#.utf8))
        let model = ConversationModel()
        model.items = [ConversationItem(kind: "row", key: "recovery", senderMessageId: nil, turnKey: nil, sourceSequence: 5, body: .init(kind: "inline", value: body, reference: nil))]
        XCTAssertFalse(model.rows.first!.own)
        XCTAssertEqual(model.rows.first?.senderLabel, "Panels deployment recovery")
        XCTAssertEqual(model.latestLine, "Panels deployment recovery: Inspect interrupted work")
    }
    func testCompleteUnreadRunAndMarkReadFreezeTheDisplayedBoundary() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        defer { clean(api) }
        let decision = ReviewMessageDecision()
        await decision.load(item: item(), api: api)
        XCTAssertNil(decision.error)
        XCTAssertEqual(decision.events.count, 65)
        XCTAssertEqual(decision.events.first?.sequence, 2)
        XCTAssertEqual(decision.through, 66)
        ReviewMessageProtocol.state.newMessage = true
        let marked = await decision.markRead()
        XCTAssertTrue(marked)
        XCTAssertEqual(ReviewMessageProtocol.state.bodies.last?["through_sequence"] as? Int, 66)
        // The view carries no sequence that moves when a message joins the run, so the
        // read-ahead looks current. The shown card reads again, so a reply or Mark read
        // never stops at a run that has since grown.
        let reads = ReviewMessageProtocol.state.eventReads
        await decision.load(item: item(), api: api)
        XCTAssertEqual(decision.through, 66, "A read-ahead with unchanged view facts reads nothing")
        XCTAssertEqual(ReviewMessageProtocol.state.eventReads, reads)
        await decision.load(item: item(), api: api, shownAt: .now)
        XCTAssertEqual(decision.through, 67, "The shown card reads the run again")
        XCTAssertEqual(ReviewMessageProtocol.state.eventReads, reads + 1)
        let marked2 = await decision.markRead()
        XCTAssertTrue(marked2)
        XCTAssertEqual(ReviewMessageProtocol.state.bodies.last?["through_sequence"] as? Int, 67)
    }
    func testRefusedReplyKeepsDraftAttachmentAndRetryBoundaryAcrossRecreation() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        ReviewMessageProtocol.state.refuse = true
        defer { clean(api) }
        let decision = ReviewMessageDecision()
        await decision.load(item: item(), api: api)
        decision.composer.draft = "Keep this reply"
        decision.composer.attachments = [.init(piece: "file", data: "YWJj", mediaType: "text/plain", fileName: "note.txt")]
        _ = await decision.send()
        await Outbox.shared.drain(api: api)
        XCTAssertEqual(decision.attempt?.state, .failed)
        let originalID = try XCTUnwrap(decision.attemptID)
        ReviewMessageProtocol.state.newMessage = true
        let restored = ReviewMessageDecision()
        await restored.load(item: item(), api: api)
        XCTAssertEqual(restored.composer.draft, "Keep this reply")
        XCTAssertEqual(restored.composer.attachments.first?.data, "YWJj")
        restored.composer.draft = "Corrected reply"
        restored.composer.attachments = [.init(piece: "file", data: "ZGVm", mediaType: "text/plain", fileName: "corrected.txt")]
        let edited = ReviewMessageDecision()
        await edited.load(item: item(), api: api)
        XCTAssertEqual(edited.composer.draft, "Corrected reply")
        XCTAssertEqual(edited.composer.attachments.first?.fileName, "corrected.txt")
        ReviewMessageProtocol.state.refuse = false
        ReviewMessageProtocol.state.newMessage = true
        _ = await restored.send()
        await Outbox.shared.drain(api: api)
        let retry = try XCTUnwrap(restored.attempt)
        XCTAssertNotEqual(retry.id, originalID)
        XCTAssertEqual(retry.message?.ownerReadThroughSequence, 66)
        XCTAssertEqual(retry.message?.content.first?.text, "Corrected reply")
        XCTAssertEqual(retry.message?.content.last?.fileName, "corrected.txt")
        XCTAssertTrue(restored.accepted)
        XCTAssertEqual(ReviewMessageProtocol.state.bodies.last?["owner_read_through_sequence"] as? Int, 66)
        await Outbox.shared.drain(api: api)
        XCTAssertEqual(ReviewMessageProtocol.state.bodies.filter { $0["sender_message_id"] != nil }.count, 2)
    }
    func testLostQueuedReplyWithoutReceiptStaysUncertainWithoutAnotherSend() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        ReviewMessageProtocol.state.loseResponse = true
        defer { clean(api) }
        let decision = ReviewMessageDecision()
        await decision.load(item: item(), api: api)
        decision.composer.draft = "Queued once"
        _ = await decision.send()
        XCTAssertEqual(decision.attempt?.state, .uncertain)
        let original = try XCTUnwrap(decision.attemptID)
        ReviewMessageProtocol.state.newMessage = true
        let restored = ReviewMessageDecision()
        await restored.load(item: item(), api: api)
        XCTAssertEqual(restored.attemptID, original)
        await Outbox.shared.drain(api: api)
        restored.refreshAttempt()
        XCTAssertEqual(restored.attempt?.state, .uncertain)
        XCTAssertEqual(restored.attempt?.message?.ownerReadThroughSequence, 66)
        XCTAssertEqual(ReviewMessageProtocol.state.bodies.count, 1)
    }
    func testHistoricalConversationCannotSendAndKeepsExactID() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        defer { clean(api) }
        let decision = ReviewMessageDecision()
        // The view reports that the owner's current address has moved to another conversation.
        await decision.load(item: item(current: false), api: api)
        XCTAssertTrue(decision.historical)
        XCTAssertEqual(decision.composer.conversationID, "c")
        decision.composer.draft = "Keep historical draft"
        let sent = await decision.send()
        XCTAssertFalse(sent)
        XCTAssertTrue(ReviewMessageProtocol.state.bodies.isEmpty)
    }
    private func item(current: Bool = true) -> ReviewItem { .init(reviewItemType: "message", title: "Chief", waitingSince: 1, conversationId: "c", ownerKind: "chief", ownerId: "chief", ownerReadThroughSequence: 1, addressCurrent: current) }
    private func makeAPI() -> APIClient {
        let config = URLSessionConfiguration.ephemeral; config.protocolClasses = [ReviewMessageProtocol.self]
        return APIClient(baseURL: URL(string: "https://review-\(UUID().uuidString.lowercased()).invalid")!, session: URLSession(configuration: config))
    }
    private func clean(_ api: APIClient) {
        // Unique server partitions isolate durable drafts and the shared outbox in these tests.
        let server = LocalStore.server(api.baseURL)
        for kind in ["outbox", "draft", "attachments", "cache"] {
            for record in (try? LocalStore.shared.records(server: server, kind: kind)) ?? [] {
                if let id = record.key.components(separatedBy: "\u{1f}").last { try? LocalStore.shared.remove(server: server, kind: kind, id: id) }
            }
        }
    }
}

private final class ReviewMessageState: @unchecked Sendable {
    private let lock = NSLock()
    private var _bodies: [[String: Any]] = []
    private var _refuse = false
    private var _newMessage = false
    private var _loseResponse = false
    private var _eventReads = 0
    private var held: Set<String> = []
    var loseResponse: Bool { get { lock.withLock { _loseResponse } } set { lock.withLock { _loseResponse = newValue } } }
    var bodies: [[String: Any]] { lock.withLock { _bodies } }
    var eventReads: Int { lock.withLock { _eventReads } }
    var refuse: Bool { get { lock.withLock { _refuse } } set { lock.withLock { _refuse = newValue } } }
    var newMessage: Bool { get { lock.withLock { _newMessage } } set { lock.withLock { _newMessage = newValue } } }
    func reset() { lock.withLock { _bodies = []; _refuse = false; _newMessage = false; _loseResponse = false; _eventReads = 0; held = [] } }
    func response(_ request: URLRequest) throws -> Data {
        try lock.withLock {
            let path = request.url!.path
            if request.httpMethod == "POST" {
                var data = request.httpBody ?? Data()
                if let stream = request.httpBodyStream {
                    stream.open(); defer { stream.close() }
                    var buffer = [UInt8](repeating: 0, count: 4096)
                    while stream.hasBytesAvailable { let n = stream.read(&buffer, maxLength: buffer.count); if n <= 0 { break }; data.append(buffer, count: n) }
                }
                let body = try JSONSerialization.jsonObject(with: data) as! [String: Any]
                _bodies.append(body)
                if !_refuse, let id = body["sender_message_id"] as? String { held.insert(id) }
                if _loseResponse { _loseResponse = false; throw URLError(.networkConnectionLost) }
                return Data((_refuse ? #"{"conversation_id":"c","fate":"refused"}"# : #"{"conversation_id":"c","fate":"queued","owner_read_through_sequence":66}"#).utf8)
            }
            if path.hasSuffix("/receipts") { return Data(#"{"receipts":[]}"#.utf8) }
            if path.hasSuffix("/events") {
                _eventReads += 1
                var events: [[String: Any]] = (1...(_newMessage ? 67 : 66)).map { ["sequence": $0, "kind": "message_to_owner", "payload": ["text": "Message \($0)"]] }
                events.append(["sequence": 90, "kind": "agent_message", "payload": ["text": "Internal output"]])
                return try JSONSerialization.data(withJSONObject: ["events": events])
            }
            return try JSONSerialization.data(withJSONObject: ["backend_key": "codex", "held_prompts": held.map { ["held_prompt_id": $0, "sender_message_id": $0, "content": [["piece": "text", "text": "Queued once"]]] }, "owner_read_through_sequence": 1, "latest_sequence": 90])
        }
    }
}
private final class ReviewMessageProtocol: URLProtocol, @unchecked Sendable {
    static let state = ReviewMessageState()
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host?.hasPrefix("review-") == true }
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
