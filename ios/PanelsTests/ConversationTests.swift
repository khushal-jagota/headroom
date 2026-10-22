import XCTest
@testable import Panels

@MainActor final class ConversationTests: XCTestCase {
    func testProjectionUnwrapsInlineBodyAndKeepsDeferredReference() throws {
        let page = try APIClient.decoder().decode(ConversationPage.self, from: Data(Self.page.utf8))
        XCTAssertEqual(page.items.first?.body?.value?.text, "Hello")
        XCTAssertEqual(page.items.first?.id, "message-1")
        XCTAssertEqual(page.items.last?.body?.reference?.bodyId, "long")
        XCTAssertEqual(page.older?.order.sequence, 7)
    }
    func testSendKeepsUncertainMessageAndDoesNotRetry() async {
        let model = ConversationModel()
        let service = ConversationStub(fate: "uncertain")
        model.draft = " \n "
        let blank = await model.send(to: .chief, using: service)
        XCTAssertFalse(blank)
        model.draft = "Keep my words"
        let result = await model.send(to: .chief, using: service)
        XCTAssertFalse(result)
        XCTAssertEqual(model.outgoing.first?.text, "Keep my words")
        XCTAssertEqual(model.outgoing.first?.note, "Delivery uncertain · do not resend")
        XCTAssertEqual(model.draft, "")
        let calls = await service.calls
        XCTAssertEqual(calls, 1)
    }
    func testAcceptedMessageReconcilesBySenderIDWithoutDuplicate() async throws {
        let model = ConversationModel()
        let service = ConversationStub(fate: "started")
        model.draft = "Hello"
        let sent = await model.send(to: .ticket("t_1"), using: service)
        XCTAssertTrue(sent)
        XCTAssertEqual(model.conversationID, "conv_1")
        let localID = try XCTUnwrap(model.outgoing.first?.id)
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.items.first?.id, localID)
        XCTAssertTrue(model.outgoing.isEmpty)
        XCTAssertEqual(model.status?.line, "Reading the code")
    }
    func testSendAdapterEncodesOwnerDoorAndStableIdentityUsingStubOnly() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ConversationRequestStub.self]
        let api = APIClient(baseURL: URL(string: "https://conversation.invalid")!, session: URLSession(configuration: configuration))
        let message = ConversationSend(conversationId: "conv_1", content: [.init(text: "Hello")], senderMessageId: "message-1", sentAtUnixMilliseconds: 123)
        let delivery = try await api.sendConversation(.manager("si_1"), message: message)
        XCTAssertEqual(delivery.fate, "queued")
        let request = try XCTUnwrap(ConversationRequestStub.request)
        XCTAssertEqual(request.url?.path, "/api/managers/si_1/conversation/send")
        XCTAssertEqual(request.httpMethod, "POST")
        let stream = try XCTUnwrap(request.httpBodyStream)
        stream.open(); defer { stream.close() }
        var bytes = [UInt8](repeating: 0, count: 4096)
        let count = stream.read(&bytes, maxLength: bytes.count)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(bytes.prefix(count))) as? [String: Any])
        XCTAssertEqual(json["conversation_id"] as? String, "conv_1")
        XCTAssertEqual(json["sender_message_id"] as? String, "message-1")
        XCTAssertEqual(json["mode"] as? String, "steer")
        XCTAssertEqual(json["sent_at_unix_milliseconds"] as? Int, 123)
        XCTAssertEqual((json["content"] as? [[String: String]])?.first, ["piece": "text", "text": "Hello"])
    }
    static let page = #"{"items":[{"kind":"row","key":"row-1","sender_message_id":"message-1","body":{"kind":"inline","value":{"kind":"prompt","content":[{"piece":"text","text":"Hello"}]}}},{"kind":"row","key":"row-2","body":{"kind":"deferred","reference":{"body_id":"long","version":"v1","byte_count":50000,"media_type":"application/json"}}}],"older":{"projection_version":1,"lens":"focus","scope":{"kind":"timeline","key":null},"order":{"sequence":7,"slot":0}}}"#
}

private actor ConversationStub: ConversationServing {
    let fate: String
    var calls = 0
    var senderID = ""
    init(fate: String) { self.fate = fate }
    func sendConversation(_ owner: ConversationOwner, message: ConversationSend) async throws -> ConversationDelivery {
        calls += 1; senderID = message.senderMessageId
        return ConversationDelivery(conversationId: "conv_1", fate: fate, refusalReason: nil)
    }
    func conversationSnapshot(_ id: String, lens: ConversationLens, transcript: Bool) async throws -> ConversationSnapshot {
        let json = #"{"page":{"items":[{"kind":"row","key":"row-1","sender_message_id":"SENDER","body":{"kind":"inline","value":{"kind":"prompt","content":[{"piece":"text","text":"Hello"}]}}}],"older":null},"status":{"authority":"current","turn_running":true,"rest":{"latest_happened":null,"latest_tool":{"kind":"tool_call","title":"Reading the code"},"permission_title_excerpt":null,"plan_progress":{"current_text_excerpt":null}}}}"#.replacingOccurrences(of: "SENDER", with: senderID)
        return try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(json.utf8))
    }
    func conversationPage(_ id: String, cursor: ConversationCursor) async throws -> ConversationPage { throw URLError(.unsupportedURL) }
    func conversationBody(_ id: String, reference: ConversationBodyReference) async throws -> ConversationBody { throw URLError(.unsupportedURL) }
}
private final class ConversationRequestStub: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var request: URLRequest?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.request = request
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(#"{"conversation_id":"conv_1","fate":"queued","queue_position":1}"#.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
