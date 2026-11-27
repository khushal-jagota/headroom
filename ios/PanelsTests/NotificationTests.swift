import XCTest
import UserNotifications
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

    /// The server's push `content-state` reaches the widget only if these keys decode. A mismatch fails silently on the phone.
    func testReviewLiveActivityContentStateDecodesTheSharedServerFixture() throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "review-live-activity", withExtension: "json", subdirectory: "Fixtures"))
        let fixture = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
        func state(_ key: String) throws -> ReviewActivityContentState {
            try JSONDecoder().decode(ReviewActivityContentState.self, from: JSONSerialization.data(withJSONObject: fixture[key]!))
        }
        XCTAssertEqual(try state("update"), ReviewActivityContentState(count: 4, arrivedTitle: "Work move feels live and finishes clearly"))
        XCTAssertEqual(try state("clear"), ReviewActivityContentState(count: 0, arrivedTitle: nil))
    }

    /// A token that has not arrived yet must never clear a live one on the server. Only an ended activity sends null.
    func testLiveActivityReportClearsTheUpdateTokenOnlyWhenTheActivityEnded() throws {
        func json(_ tokens: LiveActivityTokens) throws -> [String: Any?] {
            try JSONSerialization.jsonObject(with: JSONEncoder().encode(tokens)) as! [String: Any?]
        }
        XCTAssertTrue(try json(LiveActivityTokens(pushToStart: nil, update: .unknown)).isEmpty)
        XCTAssertEqual(try json(LiveActivityTokens(pushToStart: "aa", update: .token("bb"))) as NSDictionary, ["push_to_start_token": "aa", "update_token": "bb"])
        let ended = try json(LiveActivityTokens(pushToStart: "aa", update: .ended))
        XCTAssertEqual(ended.keys.sorted(), ["push_to_start_token", "update_token"])
        XCTAssertTrue(ended["update_token"]! is NSNull)
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

    @MainActor func testReplyBeforeHomeSelectionDoesNotEnqueueWorkForPayloadOrigin() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = Outbox(store: LocalStore(url: directory.appendingPathComponent("reply.store")))
        let reply = NotificationReply(serverURL: URL(string: "https://other-server.invalid")!, conversationID: "c_original", messageID: "source:7", owner: .chief)
        var notices: [UNNotificationRequest] = []
        let controller = NotificationController(replyOutbox: outbox, selectedServer: { nil }, postLocalNotification: { notices.append($0) })

        await controller.receivedReply(reply, text: "Private reply", notificationID: "unconfigured-push")

        XCTAssertTrue(try outbox.store.outgoing.allSummaries(server: LocalStore.server(reply.serverURL)).isEmpty)
        XCTAssertEqual(notices.count, 1)
        XCTAssertNil(notices.first?.content.userInfo["server_url"])
        XCTAssertNotNil(controller.routingError)
    }

    @MainActor func testReplyWithoutValidDestinationPostsLocalFailureNotification() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = Outbox(store: LocalStore(url: directory.appendingPathComponent("reply.store")))
        let selected = URL(string: "https://reply-test.invalid")!
        var notices: [UNNotificationRequest] = []
        let controller = NotificationController(replyOutbox: outbox, selectedServer: { selected }, postLocalNotification: { notices.append($0) })
        let invalid = NotificationRouting.reply(["server_url": selected.absoluteString, "reply": ["conversation_id": "../wrong"]])
        await controller.receivedReply(invalid, text: "Private reply", notificationID: "invalid-push")
        assertUnsentNotice(notices, selectedServer: selected)
        XCTAssertTrue(try outbox.store.outgoing.allSummaries(server: LocalStore.server(selected)).isEmpty)
        XCTAssertNotNil(controller.routingError)
    }

    @MainActor func testReplyForAnotherServerPostsLocalFailureNotification() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = Outbox(store: LocalStore(url: directory.appendingPathComponent("reply.store")))
        let selected = URL(string: "https://reply-test.invalid")!
        let reply = NotificationReply(serverURL: URL(string: "https://other-server.invalid")!, conversationID: "c_original", messageID: "source:7", owner: .chief)
        var notices: [UNNotificationRequest] = []
        let controller = NotificationController(replyOutbox: outbox, selectedServer: { selected }, postLocalNotification: { notices.append($0) })
        await controller.receivedReply(reply, text: "Private reply", notificationID: "other-server-push")
        assertUnsentNotice(notices, selectedServer: selected)
        XCTAssertTrue(try outbox.store.outgoing.allSummaries(server: LocalStore.server(selected)).isEmpty)
        XCTAssertTrue(try outbox.store.outgoing.allSummaries(server: LocalStore.server(reply.serverURL)).isEmpty)
        XCTAssertNotNil(controller.routingError)
    }

    @MainActor func testNotificationReplySaveFailurePostsLocalFailureWithoutDispatchableWork() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        var fail = false
        let store = LocalStore(url: directory.appendingPathComponent("reply.store"), beforeSave: { if fail { throw CocoaError(.fileWriteUnknown) } })
        let outbox = Outbox(store: store)
        let selected = URL(string: "https://reply-test.invalid")!
        let server = LocalStore.server(selected)
        try store.put(server: server, kind: "draft", id: "composer", data: Data("Unfinished draft".utf8))
        let reply = NotificationReply(serverURL: selected, conversationID: "c_original", messageID: "source:7", owner: .chief)
        var notices: [UNNotificationRequest] = []
        let controller = NotificationController(replyOutbox: outbox, selectedServer: { selected }, postLocalNotification: { notices.append($0) })
        fail = true
        await controller.receivedReply(reply, text: "Private reply", notificationID: "save-failure-push")
        assertUnsentNotice(notices, selectedServer: selected)
        XCTAssertTrue(try store.outgoing.allSummaries(server: server).isEmpty)
        XCTAssertEqual(store.draft(server: server, id: "composer"), "Unfinished draft")
        XCTAssertNotNil(store.outgoingError)
        XCTAssertNotNil(controller.routingError)
    }

    @MainActor private func assertUnsentNotice(_ requests: [UNNotificationRequest], selectedServer: URL,
                                              file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(requests.count, 1, file: file, line: line)
        guard let notice = requests.first else { return }
        XCTAssertEqual(notice.content.title, "Reply not sent", file: file, line: line)
        XCTAssertEqual(notice.content.body, "Your reply was not sent. Open Panels to reply.", file: file, line: line)
        XCTAssertNil(notice.trigger, "The failure notice must appear immediately", file: file, line: line)
        XCTAssertEqual(NotificationRouting.parse(notice.content.userInfo)?.serverURL, selectedServer, file: file, line: line)
        XCTAssertEqual(NotificationRouting.parse(notice.content.userInfo)?.destination, .review(ticketID: nil), file: file, line: line)
        XCTAssertFalse(notice.content.body.contains("Private reply"), file: file, line: line)
        XCTAssertFalse(notice.content.body.contains("fileWriteUnknown"), file: file, line: line)
        XCTAssertNil(notice.content.userInfo["reply"], file: file, line: line)
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
