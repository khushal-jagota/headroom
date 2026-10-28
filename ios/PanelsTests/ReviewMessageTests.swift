import Foundation
import XCTest
@testable import Panels

@MainActor final class ReviewMessageTests: XCTestCase {
    func testGroupedReviewPreservesExactComponentsAndCompletion() throws {
        let proposal: [String: Any] = ["review_item_type": "proposal", "ticket_id": "t", "field": "plan", "title": "Ticket", "waiting_since": 200]
        let message: [String: Any] = ["review_item_type": "message", "conversation_id": "old", "address_id": "history", "owner_kind": "ticket", "owner_id": "t", "title": "Ticket", "waiting_since": 100, "recap": "Context"]
        func card(proposal: [String: Any]?, messages: [[String: Any]]) -> [String: Any] {
            ["review_item_type": "ticket", "ticket_id": "t", "title": "Ticket", "waiting_since": messages.isEmpty ? 200 : 100,
             "proposal": proposal as Any? ?? NSNull(), "messages": messages]
        }
        let wire: [String: Any] = ["items": [card(proposal: proposal, messages: []), card(proposal: nil, messages: [message]), card(proposal: proposal, messages: [message])], "running_worker_count": 3]
        let queue = try APIClient.decoder().decode(ReviewResponse.self, from: JSONSerialization.data(withJSONObject: wire))
        XCTAssertEqual(queue.items.map { $0.components.count }, [1, 1, 2])
        let combined = queue.items[2]
        XCTAssertEqual(combined.waitingSince, 100)
        XCTAssertEqual(combined.components[0].field, "plan")
        XCTAssertEqual(combined.components[0].waitingSince, 200)
        XCTAssertEqual(combined.components[1].owner, .ticket("t"))
        XCTAssertEqual(combined.components[1].conversationId, "old")
        XCTAssertEqual(combined.components[1].addressId, "history")
        let afterProposal = try XCTUnwrap(combined.keepingComponents { $0.isMessage })
        XCTAssertEqual(afterProposal.components.map(\.conversationId), ["old"])
        let afterMessage = try XCTUnwrap(combined.keepingComponents { !$0.isMessage })
        XCTAssertEqual(afterMessage.components.map(\.field), ["plan"])
        XCTAssertNil(afterMessage.keepingComponents { _ in false })
        var visit = ReviewQueue()
        visit.skip(combined)
        XCTAssertNil(visit.current(in: [combined]))
        visit.revisit()
        XCTAssertEqual(visit.current(in: [combined])?.id, combined.id)
        let manager = ReviewItem(reviewItemType: "message", title: "Manager", waitingSince: 1,
            conversationId: "manager-history", ownerKind: "manager", ownerId: "m")
        XCTAssertEqual(manager.components.first?.conversationId, "manager-history")
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
    func testLostQueuedReplyReconcilesHeldIdentityWithoutAnotherSend() async throws {
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
        XCTAssertEqual(restored.attempt?.state, .serverQueued)
        XCTAssertEqual(restored.attempt?.message?.ownerReadThroughSequence, 66)
        XCTAssertEqual(ReviewMessageProtocol.state.bodies.count, 1)
    }
    func testHistoricalConversationCannotSendAndKeepsExactID() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        ReviewMessageProtocol.state.currentID = "replacement"
        defer { clean(api) }
        let decision = ReviewMessageDecision()
        await decision.load(item: item(), api: api)
        XCTAssertTrue(decision.historical)
        XCTAssertEqual(decision.composer.conversationID, "c")
        decision.composer.draft = "Keep historical draft"
        let sent = await decision.send()
        XCTAssertFalse(sent)
        XCTAssertTrue(ReviewMessageProtocol.state.bodies.isEmpty)
    }
    private func item() -> ReviewItem { .init(reviewItemType: "message", title: "Chief", waitingSince: 1, conversationId: "c", ownerKind: "chief", ownerId: "chief") }
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
    private var _currentID = "c"
    private var _loseResponse = false
    private var held: Set<String> = []
    var loseResponse: Bool { get { lock.withLock { _loseResponse } } set { lock.withLock { _loseResponse = newValue } } }
    var bodies: [[String: Any]] { lock.withLock { _bodies } }
    var refuse: Bool { get { lock.withLock { _refuse } } set { lock.withLock { _refuse = newValue } } }
    var newMessage: Bool { get { lock.withLock { _newMessage } } set { lock.withLock { _newMessage = newValue } } }
    var currentID: String { get { lock.withLock { _currentID } } set { lock.withLock { _currentID = newValue } } }
    func reset() { lock.withLock { _bodies = []; _refuse = false; _newMessage = false; _currentID = "c"; _loseResponse = false; held = [] } }
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
                var events: [[String: Any]] = (1...(_newMessage ? 67 : 66)).map { ["sequence": $0, "kind": "message_to_owner", "payload": ["text": "Message \($0)"]] }
                events.append(["sequence": 90, "kind": "agent_message", "payload": ["text": "Internal output"]])
                return try JSONSerialization.data(withJSONObject: ["events": events])
            }
            if path == "/api/chief/conversation" { return try JSONSerialization.data(withJSONObject: ["conversation_id": _currentID]) }
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
