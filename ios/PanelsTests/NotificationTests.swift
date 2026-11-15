import XCTest
@testable import Panels

final class NotificationTests: XCTestCase {
    func testExistingWebRoutesAndReviewItemBecomeNativeDestinations() {
        XCTAssertEqual(NotificationRouting.destination("/#/workspace/t_42"), .ticket("t_42"))
        XCTAssertEqual(NotificationRouting.destination("/#/workspace/item/m_9"), .orchestrator("m_9"))
        XCTAssertEqual(NotificationRouting.destination("/#/agents/chief-of-staff"), .chief)
        XCTAssertEqual(NotificationRouting.destination("/#/review?ticket=t_42"), .review(ticketID: "t_42"))
        XCTAssertEqual(NotificationRouting.destination("/#/review"), .review(ticketID: nil))
        for route in ["https://evil.example/#/workspace/t_42", "//#/workspace/t_42", "/#/workspace/a%2Fb", "/#/workspace/..", "/#/workspace/", "/#/review?ticket=", "/#/unknown", "/#/workspace/t_42?redirect=evil"] {
            XCTAssertNil(NotificationRouting.destination(route), route)
        }
    }

    @MainActor func testColdLaunchRetainsTapAndRejectsAnotherServer() throws {
        let controller = NotificationController()
        let payload: [AnyHashable: Any] = ["server_url": "https://panels.example:443/", "route": "/#/workspace/t_42"]
        controller.receivedTap(NotificationRouting.parse(payload))
        XCTAssertNotNil(controller.pendingOpen)
        XCTAssertEqual(controller.consumeOpen(for: URL(string: "https://panels.example")!), .ticket("t_42"))
        XCTAssertNil(controller.consumeOpen(for: URL(string: "https://panels.example")!))
        controller.receivedTap(NotificationRouting.parse(payload))
        XCTAssertNil(controller.consumeOpen(for: URL(string: "https://other.example")!))
        XCTAssertNotNil(controller.routingError)
        XCTAssertNil(NotificationRouting.parse(["route": "/#/workspace/t_42"]))
        XCTAssertNil(NotificationRouting.parse(["server_url": "https://user:pass@panels.example", "route": "/#/review"]))
    }

    func testReplyRequiresOriginalConversationAndSupportedRecipient() throws {
        var payload: [AnyHashable: Any] = ["server_url": "https://panels.example", "reply": [
            "conversation_id": "c_original", "message_id": "c_original:7", "recipient_kind": "manager", "recipient_id": "m_original"
        ]]
        let reply = try XCTUnwrap(NotificationRouting.reply(payload))
        XCTAssertEqual(reply.conversationID, "c_original")
        XCTAssertEqual(reply.owner, .manager("m_original"))
        let work = try NotificationReplies.work(reply: reply, text: "Keep this conversation", notificationID: "push-1", selectedServer: reply.serverURL)
        XCTAssertEqual(work.message?.conversationId, "c_original")
        XCTAssertThrowsError(try NotificationReplies.work(reply: reply, text: "Reply", notificationID: "push-1", selectedServer: URL(string: "https://other.example")!))
        XCTAssertThrowsError(try NotificationReplies.work(reply: reply, text: " \n", notificationID: "push-1", selectedServer: reply.serverURL))
        payload["reply"] = ["recipient_kind": "manager", "recipient_id": "m_new"]
        XCTAssertNil(NotificationRouting.reply(payload), "A legacy alert cannot borrow the recipient's current conversation")
        payload["reply"] = ["conversation_id": "../other", "message_id": "source", "recipient_kind": "manager", "recipient_id": "m_original"]
        XCTAssertNil(NotificationRouting.reply(payload))
        payload["reply"] = ["conversation_id": "c_original", "message_id": "source", "recipient_kind": "goal", "recipient_id": "g_retired"]
        XCTAssertNil(NotificationRouting.reply(payload))
    }

    @MainActor func testNotificationReplySaveFailureCannotLeaveDispatchableWork() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        var fail = false
        let store = LocalStore(url: directory.appendingPathComponent("reply.store"), beforeSave: { if fail { throw CocoaError(.fileWriteUnknown) } })
        let outbox = Outbox(store: store)
        let api = APIClient(baseURL: URL(string: "https://reply-test.invalid")!)
        let server = LocalStore.server(api.baseURL)
        try store.put(server: server, kind: "draft", id: "composer", data: Data("Unfinished draft".utf8))
        let reply = NotificationReply(serverURL: api.baseURL, conversationID: "c_original", messageID: "source:7", owner: .chief)
        fail = true
        XCTAssertThrowsError(try outbox.enqueueNotificationReply(reply, text: "Reply", notificationID: "push", api: api))
        XCTAssertTrue(try store.outgoing.schedulingSummaries(server: server).isEmpty)
        XCTAssertEqual(store.draft(server: server, id: "composer"), "Unfinished draft")
        XCTAssertNotNil(store.outgoingError)
    }

    func testRegistrationEncodesNativeContractAndSurfacesRejection() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [NativePushURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let device = NativePushRegistration(deviceToken: "0011aaff", bundleId: "app.panels.ios.pios-notify", environment: "development")
        try await NativePushAPI(client: APIClient(baseURL: URL(string: "https://push-stub.invalid")!, session: session)).register(device)
        try await NativePushAPI(client: APIClient(baseURL: URL(string: "https://push-stub.invalid")!, session: session)).unregister(device)
        do {
            try await NativePushAPI(client: APIClient(baseURL: URL(string: "https://rejected.invalid")!, session: session)).register(device)
            XCTFail("A refused registration must not be reported as connected")
        } catch let error as APIError { XCTAssertEqual(error.status, 503) }
    }
}

private final class NativePushURLProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var body = request.httpBody ?? Data()
        if let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                body.append(buffer, count: count)
            }
        }
        guard ["PUT", "DELETE"].contains(request.httpMethod ?? ""), request.url?.path == "/api/notifications/apns/devices",
              request.value(forHTTPHeaderField: "Content-Type") == "application/json",
              let payload = try? JSONSerialization.jsonObject(with: body) as? [String: String],
              payload == ["device_token": "0011aaff", "bundle_id": "app.panels.ios.pios-notify", "environment": "development"] else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL)); return
        }
        let status = request.url?.host == "rejected.invalid" ? 503 : 204
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
