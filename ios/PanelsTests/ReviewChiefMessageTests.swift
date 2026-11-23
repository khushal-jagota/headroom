import Foundation
import XCTest
@testable import Panels

@MainActor final class ReviewChiefMessageTests: XCTestCase {
    /// The final page shares Chief's ordinary draft. A saved message clears only the draft
    /// it was taken from, at once, and goes to Chief's current conversation without a pointer read.
    func testSavedMessageClearsOnlyItsOwnDraftAtOnceAndLandsOnce() async throws {
        let api = makeAPI()
        ReviewChiefProtocol.state.reset(pointer: "original")
        ReviewChiefProtocol.state.fate = "started"
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
        let sent = await page.send()
        XCTAssertTrue(sent)
        XCTAssertEqual(page.attempt?.state, .waiting)
        XCTAssertNil(page.note, "A saved message shows no status")
        XCTAssertNil(page.attempt?.message?.conversationId, "The server picks Chief's current conversation")
        XCTAssertTrue(page.composer.draft.isEmpty)
        XCTAssertTrue(page.composer.attachments.isEmpty)
        XCTAssertFalse(page.blocked, "Another message can follow at once")
        await Outbox.shared.drain(api: api)
        page.refreshAttempt()
        XCTAssertEqual(page.attempt?.state, .sent)
        XCTAssertEqual(ReviewChiefProtocol.state.posts.count, 1)
        XCTAssertEqual(ReviewChiefProtocol.state.pointerReads, 0)
        // A later identical edit belongs to the person, not the old message.
        chief.draft = "Words from Chief"
        let restored = ReviewChiefMessageModel()
        restored.restore(api: api)
        XCTAssertEqual(restored.composer.draft, "Words from Chief")
    }
    func testRejectedPhotoKeepsRestoredDraftAndAllowsExplicitNewAttempt() async throws {
        let api = makeAPI()
        ReviewChiefProtocol.state.reset(pointer: "old")
        ReviewChiefProtocol.state.postStatus = 422
        defer { clean(api) }
        let page = ReviewChiefMessageModel()
        page.restore(api: api)
        page.composer.draft = "Keep refused words"
        // Saved drafts can predate preparation and do not enter through attach again.
        #if SWIFT_PACKAGE
        let fixture = Bundle.module.url(forResource: "photo", withExtension: "heic", subdirectory: "Fixtures")
        #else
        let fixture = Bundle(for: Self.self).url(forResource: "photo", withExtension: "heic")
        #endif
        let heic = try Data(contentsOf: XCTUnwrap(fixture))
        page.composer.attachments = [.init(piece: "image", data: heic.base64EncodedString(), mediaType: "image/heic", fileName: "Saved.heic")]
        _ = await page.send()
        await Outbox.shared.drain(api: api)
        page.refreshAttempt()
        let first = try XCTUnwrap(page.attempt?.id)
        XCTAssertEqual(page.attempt?.state, .failed)
        XCTAssertTrue(page.attempt?.note.lowercased().contains("photo") == true)
        XCTAssertEqual(page.note, "The message did not reach Chief. Your draft is ready to retry.")
        page.composer.reloadDraft()
        XCTAssertEqual(page.composer.draft, "Keep refused words")
        XCTAssertEqual(page.composer.attachments.count, 1)
        let submitted = try XCTUnwrap(ReviewChiefProtocol.state.posts.first?["content"] as? [[String: Any]])
        XCTAssertEqual(submitted.last?["media_type"] as? String, "image/jpeg")
        XCTAssertEqual(submitted.last?["file_name"] as? String, "Saved.jpg")
        XCTAssertNotEqual(submitted.last?["data"] as? String, heic.base64EncodedString())
        let restored = ReviewChiefMessageModel()
        restored.restore(api: api)
        XCTAssertEqual(restored.attempt?.id, first)
        XCTAssertEqual(restored.composer.draft, "Keep refused words")
        ReviewChiefProtocol.state.postStatus = 200
        ReviewChiefProtocol.state.fate = "queued"
        _ = await restored.send()
        XCTAssertNotEqual(restored.attempt?.id, first)
        XCTAssertTrue(restored.composer.draft.isEmpty)
        XCTAssertTrue(restored.composer.attachments.isEmpty)
        await Outbox.shared.drain(api: api)
        restored.refreshAttempt()
        XCTAssertEqual(restored.attempt?.state, .sent)
        XCTAssertEqual(ReviewChiefProtocol.state.posts.count, 2)
    }
    func testUnreadableAttemptBlocksAnotherSend() async throws {
        let api = makeAPI()
        ReviewChiefProtocol.state.reset(pointer: "original")
        OfflineSwitch.offline = true
        defer { clean(api); OfflineSwitch.offline = false }
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
        let sent = await restored.send()
        XCTAssertFalse(sent)
        XCTAssertTrue(ReviewChiefProtocol.state.posts.isEmpty)
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

private enum OfflineSwitch {
    nonisolated(unsafe) static var offline = false
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
    private var status = 200
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
    var postStatus: Int { get { lock.withLock { status } } set { lock.withLock { status = newValue } } }
    func reset(pointer: String?) { lock.withLock { current = pointer; outcome = "uncertain"; received = nil; queued = false; sent = []; reads = []; delay = false; pointers = 0; pointerDelivery = nil; status = 200 } }
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
                value = status == 422 ? ["detail": "unsupported or invalid conversation image"]
                    : ["fate": outcome, "conversation_id": body["conversation_id"] ?? NSNull()]
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
        if OfflineSwitch.offline { client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet)); return }
        do {
            let data = try Self.state.response(request)
            if request.url?.path == "/api/chief/conversation", Self.state.delayPointer {
                Self.state.holdPointer { self.deliver(data) }
            } else { deliver(data) }
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    private func deliver(_ data: Data) {
        let status = request.httpMethod == "POST" ? Self.state.postStatus : 200
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
