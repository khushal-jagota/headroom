import Foundation
import XCTest
@testable import Panels

@MainActor final class ReviewChiefMessageTests: XCTestCase {
    /// Existing outbox tests do not prove the final page's shared draft, pinned pointer,
    /// or Check delivery action. This uses the same in-memory HTTP transport boundary.
    func testPinnedAttemptAndHeldDeliveryKeepNewerSharedDraftWithoutAnotherPost() async throws {
        let api = makeAPI()
        ReviewChiefProtocol.state.reset(pointer: "original")
        defer { clean(api) }
        let page = ReviewChiefMessageModel()
        page.restore(api: api)
        page.composer.draft = "Original words"
        page.composer.attachments = [.init(piece: "file", data: "YWJj", mediaType: "text/plain", fileName: "note.txt")]
        let chief = ConversationModel()
        chief.restore(owner: .chief, api: api)
        XCTAssertEqual(chief.draft, "Original words")
        XCTAssertEqual(chief.attachments.first?.fileName, "note.txt")
        chief.draft = "Words from Chief"
        page.restore(api: api)
        XCTAssertEqual(page.composer.draft, "Words from Chief")
        _ = await page.send()
        XCTAssertEqual(page.attempt?.state, .uncertain)
        let identity = try XCTUnwrap(page.attempt?.id)
        XCTAssertEqual(page.attempt?.message?.conversationId, "original")
        XCTAssertEqual(page.composer.attachments.first?.fileName, "note.txt")
        ReviewChiefProtocol.state.pointer = "replacement"
        let restored = ReviewChiefMessageModel()
        restored.restore(api: api)
        XCTAssertEqual(restored.attempt?.id, identity)
        XCTAssertEqual(restored.composer.draft, "Words from Chief")
        await restored.checkDelivery()
        await restored.checkDelivery()
        XCTAssertEqual(restored.attempt?.state, .uncertain)
        XCTAssertEqual(ReviewChiefProtocol.state.posts.count, 1)
        XCTAssertTrue(ReviewChiefProtocol.state.receiptPaths.allSatisfy { $0.contains("/original/") })
        // A later identical edit and a new attachment must survive an old acceptance.
        chief.draft = "Words from Chief"
        chief.attachments = [.init(piece: "file", data: "ZGVm", mediaType: "text/plain", fileName: "new.txt")]
        ReviewChiefProtocol.state.held = true
        await restored.checkDelivery()
        XCTAssertEqual(restored.attempt?.state, .serverQueued)
        XCTAssertEqual(restored.composer.draft, "Words from Chief")
        XCTAssertEqual(restored.composer.attachments.first?.fileName, "new.txt")
        XCTAssertEqual(ReviewChiefProtocol.state.posts.count, 1)
        chief.reloadDraft()
        XCTAssertEqual(chief.draft, restored.composer.draft)
    }
    func testFirstConversationReceiptClearsOnlyOriginalDraftAndKeepsCapturedIdentity() async throws {
        let api = makeAPI()
        ReviewChiefProtocol.state.reset(pointer: nil)
        defer { clean(api) }
        let page = ReviewChiefMessageModel()
        page.restore(api: api)
        page.composer.draft = "First message"
        page.composer.attachments = [.init(piece: "image", data: "YWJj", mediaType: "image/png", fileName: "image.png")]
        _ = await page.send()
        let identity = try XCTUnwrap(page.attempt?.id)
        XCTAssertNil(page.attempt?.message?.conversationId)
        let restored = ReviewChiefMessageModel()
        restored.restore(api: api)
        await restored.checkDelivery()
        XCTAssertEqual(restored.attempt?.state, .uncertain)
        XCTAssertEqual(ReviewChiefProtocol.state.posts.count, 1)
        ReviewChiefProtocol.state.pointer = "created"
        ReviewChiefProtocol.state.receipt = "prompt"
        await restored.checkDelivery()
        XCTAssertEqual(restored.attempt?.id, identity)
        XCTAssertEqual(restored.attempt?.message?.conversationId, "created")
        XCTAssertEqual(restored.attempt?.state, .sent)
        XCTAssertTrue(restored.composer.draft.isEmpty)
        XCTAssertTrue(restored.composer.attachments.isEmpty)
        restored.composer.draft = "First message"
        let again = ReviewChiefMessageModel()
        again.restore(api: api)
        XCTAssertEqual(again.composer.draft, "First message", "A prior accepted attempt cannot clear a newer identical draft")
        await again.checkDelivery()
        XCTAssertEqual(ReviewChiefProtocol.state.posts.count, 1)
    }
    func testConfirmedRefusalAllowsExplicitNewAttemptWithCurrentDestination() async throws {
        let api = makeAPI()
        ReviewChiefProtocol.state.reset(pointer: "old")
        ReviewChiefProtocol.state.fate = "refused"
        defer { clean(api) }
        let page = ReviewChiefMessageModel()
        page.restore(api: api)
        page.composer.draft = "Keep refused words"
        _ = await page.send()
        let first = try XCTUnwrap(page.attempt?.id)
        XCTAssertEqual(page.attempt?.state, .failed)
        XCTAssertEqual(page.composer.draft, "Keep refused words")
        ReviewChiefProtocol.state.pointer = "current"
        ReviewChiefProtocol.state.fate = "queued"
        _ = await page.send()
        XCTAssertNotEqual(page.attempt?.id, first)
        XCTAssertEqual(page.attempt?.message?.conversationId, "current")
        XCTAssertEqual(page.attempt?.state, .serverQueued)
        XCTAssertTrue(page.composer.draft.isEmpty)
        XCTAssertEqual(ReviewChiefProtocol.state.posts.count, 2)
    }
    func testUnreadableAttemptBlocksAnotherSend() async throws {
        let api = makeAPI()
        ReviewChiefProtocol.state.reset(pointer: "original")
        defer { clean(api) }
        let page = ReviewChiefMessageModel()
        page.restore(api: api)
        page.composer.draft = "Do not duplicate"
        _ = await page.send()
        let identity = try XCTUnwrap(page.attempt?.id)
        let record = try XCTUnwrap(LocalStore.shared.record(server: LocalStore.server(api.baseURL), kind: "outbox", id: identity))
        let original = record.payload
        defer { record.payload = original }
        record.payload = Data("unreadable".utf8)
        let restored = ReviewChiefMessageModel()
        restored.restore(api: api)
        XCTAssertTrue(restored.blocked)
        XCTAssertNotNil(restored.storageError)
        _ = await restored.send()
        XCTAssertEqual(ReviewChiefProtocol.state.posts.count, 1)
    }
    func testServerChangeDuringPointerReadDoesNotDispatchIntoAnotherPartition() async throws {
        let api = makeAPI()
        let other = makeAPI()
        ReviewChiefProtocol.state.reset(pointer: "original")
        ReviewChiefProtocol.state.delayPointer = true
        defer { clean(api); clean(other) }
        let page = ReviewChiefMessageModel()
        page.restore(api: api)
        page.composer.draft = "Original server only"
        let sending = Task { await page.send() }
        while !ReviewChiefProtocol.state.pointerSuspended { await Task.yield() }
        page.restore(api: other)
        ReviewChiefProtocol.state.releasePointer()
        let sent = await sending.value
        XCTAssertFalse(sent)
        XCTAssertTrue(ReviewChiefProtocol.state.posts.isEmpty)
        XCTAssertNil(page.attempt)
        XCTAssertTrue(try LocalStore.shared.outgoing.summaries(server: LocalStore.server(other.baseURL), draftID: ReviewChiefMessageModel.attemptDraftID).isEmpty)
    }
    private func makeAPI() -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ReviewChiefProtocol.self]
        return APIClient(baseURL: URL(string: "https://review-chief-\(UUID().uuidString.lowercased()).invalid")!, session: URLSession(configuration: configuration))
    }
    private func clean(_ api: APIClient) {
        let server = LocalStore.server(api.baseURL)
        for kind in ["outbox", "draft", "attachments", "cache"] {
            for record in (try? LocalStore.shared.records(server: server, kind: kind)) ?? [] {
                if let id = record.key.components(separatedBy: "\u{1f}").last {
                    try? LocalStore.shared.remove(server: server, kind: kind, id: id)
                }
            }
        }
    }
}

private final class ReviewChiefState: @unchecked Sendable {
    private let lock = NSLock()
    private var current: String?
    private var outcome = "uncertain"
    private var received: String?
    private var queued = false
    private var sent: [[String: Any]] = []
    private var reads: [String] = []
    private var delay = false
    private var pointers = 0
    private var pointerDelivery: (@Sendable () -> Void)?
    var pointerSuspended: Bool { lock.withLock { pointerDelivery != nil } }
    func holdPointer(_ delivery: @escaping @Sendable () -> Void) { lock.withLock { pointerDelivery = delivery } }
    func releasePointer() {
        let delivery = lock.withLock { let delivery = pointerDelivery; pointerDelivery = nil; return delivery }
        delivery?()
    }
    var delayPointer: Bool { get { lock.withLock { delay } } set { lock.withLock { delay = newValue } } }
    var pointerReads: Int { lock.withLock { pointers } }
    var pointer: String? { get { lock.withLock { current } } set { lock.withLock { current = newValue } } }
    var fate: String { get { lock.withLock { outcome } } set { lock.withLock { outcome = newValue } } }
    var receipt: String? { get { lock.withLock { received } } set { lock.withLock { received = newValue } } }
    var held: Bool { get { lock.withLock { queued } } set { lock.withLock { queued = newValue } } }
    var posts: [[String: Any]] { lock.withLock { sent } }
    var receiptPaths: [String] { lock.withLock { reads } }
    func reset(pointer: String?) { lock.withLock { current = pointer; outcome = "uncertain"; received = nil; queued = false; sent = []; reads = []; delay = false; pointers = 0; pointerDelivery = nil } }
    func response(_ request: URLRequest) throws -> Data {
        try lock.withLock {
            let path = request.url!.path
            let value: [String: Any]
            if request.httpMethod == "POST" {
                var data = request.httpBody ?? Data()
                if let stream = request.httpBodyStream {
                    stream.open(); defer { stream.close() }
                    var buffer = [UInt8](repeating: 0, count: 4096)
                    while stream.hasBytesAvailable {
                        let size = stream.read(&buffer, maxLength: buffer.count)
                        if size <= 0 { break }
                        data.append(buffer, count: size)
                    }
                }
                let body = try JSONSerialization.jsonObject(with: data) as! [String: Any]
                sent.append(body)
                // An uncertain first send deliberately supplies no destination.
                value = ["fate": outcome, "conversation_id": body["conversation_id"] ?? NSNull()]
            } else if path == "/api/chief/conversation" {
                pointers += 1
                value = ["conversation_id": current as Any? ?? NSNull()]
            } else if path.hasSuffix("/view/receipts") {
                reads.append(path)
                value = ["receipts": received.map { [["sender_message_id": sent.last?["sender_message_id"] ?? "", "fate": $0]] } ?? []]
            } else {
                let prompts: [[String: Any]] = queued ? [["held_prompt_id": "held", "sender_message_id": sent.last?["sender_message_id"] ?? "", "content": []]] : []
                value = ["backend_key": "codex", "held_prompts": prompts]
            }
            return try JSONSerialization.data(withJSONObject: value)
        }
    }
}
private final class ReviewChiefProtocol: URLProtocol, @unchecked Sendable {
    static let state = ReviewChiefState()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let data = try Self.state.response(request)
            if request.url?.path == "/api/chief/conversation", Self.state.delayPointer {
                Self.state.holdPointer { self.deliver(data) }
            } else { deliver(data) }
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    private func deliver(_ data: Data) {
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
