import XCTest
@testable import Panels

@MainActor final class ConversationTests: XCTestCase {
    func testLatestRequiresDistanceAndDoesNotFlickerAtThreshold() {
        var policy = ConversationScrollPolicy()
        policy.beginGesture()
        policy.measure(distance: 0)
        XCTAssertFalse(policy.showLatest, "Touch-down is not distance")
        policy.measure(distance: 24)
        XCTAssertFalse(policy.showLatest)
        policy.measure(distance: 121)
        XCTAssertTrue(policy.showLatest)
        policy.measure(distance: 119)
        XCTAssertTrue(policy.showLatest, "A small reversal must not flicker")
        policy.measure(distance: 32)
        XCTAssertFalse(policy.showLatest)
        policy.endGesture(distance: 24)
        XCTAssertTrue(policy.shouldFollowContent)
    }

    func testLatestPreservesReturnThroughGrowthAndResumesFollowing() {
        var policy = ConversationScrollPolicy()
        policy.beginGesture()
        policy.endGesture(distance: 600)
        policy.receiveOutput()
        XCTAssertTrue(policy.hasNewOutput)
        XCTAssertFalse(policy.shouldFollowContent)
        policy.returnToLatest()
        XCTAssertTrue(policy.showLatest, "A tap has not landed yet")
        policy.measure(distance: 900)
        policy.receiveOutput()
        XCTAssertTrue(policy.returningToLatest)
        XCTAssertFalse(policy.shouldFollowContent, "Do not restart an in-flight animation")
        policy.finishReturn()
        XCTAssertTrue(policy.shouldFollowContent)
        XCTAssertFalse(policy.showLatest)
        XCTAssertFalse(policy.hasNewOutput)
        policy.measure(distance: 500)
        XCTAssertFalse(policy.showLatest, "A pre-alignment layout measurement must not detach following")
    }

    func testLatestGestureInterruptsReturnAndKeepsReadingPosition() {
        var policy = ConversationScrollPolicy()
        policy.beginGesture()
        policy.endGesture(distance: 500)
        policy.returnToLatest()
        policy.beginGesture()
        policy.finishReturn()
        policy.endGesture(distance: 300)
        XCTAssertFalse(policy.returningToLatest)
        XCTAssertFalse(policy.shouldFollowContent)
        XCTAssertTrue(policy.showLatest)
        policy.receiveOutput()
        XCTAssertTrue(policy.hasNewOutput)
        policy.beginGesture()
        policy.endGesture(distance: -20)
        XCTAssertTrue(policy.shouldFollowContent)
        XCTAssertFalse(policy.hasNewOutput)
    }

    func testOpeningUnreadDoesNotFollowUntilReaderReturns() {
        var policy = ConversationScrollPolicy()
        policy.open(atUnread: true)
        policy.measure(distance: 500)
        XCTAssertTrue(policy.showLatest)
        XCTAssertFalse(policy.shouldFollowContent)
        policy.receiveOutput()
        policy.open(atUnread: false)
        XCTAssertTrue(policy.shouldFollowContent)
        XCTAssertFalse(policy.showLatest)
        XCTAssertFalse(policy.hasNewOutput)
    }

    func testSpeechOnlyReleasesFinishedCaptureAndRejectsLateCallbacks() {
        var capture = ConversationSpeechDraft()
        let cancelled = capture.begin()
        capture.receive("Discard this", generation: cancelled)
        capture.cancel()
        let current = capture.begin()
        capture.receive(" Keep this\n", generation: current)
        capture.receive("Late old words", generation: cancelled)
        XCTAssertNil(capture.finish(generation: cancelled))
        XCTAssertEqual(capture.finish(generation: current), "Keep this")
        XCTAssertNil(capture.finish(generation: current))
    }

    func testSpeechTranscriptDoesNotLeaveAnEmptyLastLine() {
        let model = ConversationModel()
        model.updateSpeechTranscript("First line\nSecond line\n", prefix: "")
        XCTAssertEqual(model.draft, "First line\nSecond line")
        model.updateSpeechTranscript(" More words\r\n", prefix: "Existing draft\n")
        XCTAssertEqual(model.draft, "Existing draft More words")
        model.updateSpeechTranscript("\n", prefix: "Existing draft")
        XCTAssertEqual(model.draft, "Existing draft")
    }

    func testProjectionUnwrapsInlineBodyAndKeepsDeferredReference() throws {
        let page = try APIClient.decoder().decode(ConversationPage.self, from: Data(Self.page.utf8))
        XCTAssertEqual(page.items.first?.body?.value?.text, "Hello")
        XCTAssertEqual(page.items.first?.id, "message-1")
        XCTAssertEqual(page.items.last?.body?.reference?.bodyId, "long")
        XCTAssertEqual(page.older?.order.sequence, 7)
    }
    // Sender attribution is public projection data, independent of delivery IDs.
    func testPromptAttributionSurvivesProjectionIntoTranscriptAndRestLine() async throws {
        for sender in ["owner", "Panels deployment recovery", "Chief", "Manager mgr_1"] {
            let body: [String: Any] = ["kind": "prompt", "sender_label": sender,
                                     "content": [["piece": "text", "text": "Continue the authorized step."]]]
            let item: [String: Any] = ["kind": "row", "key": "prompt", "body": ["kind": "inline", "value": body]]
            let service = ConversationStub(fate: "started")
            await service.setSnapshot(Self.snapshot([item]))
            let model = ConversationModel(); model.conversationID = "conv_1"
            await model.refresh(using: service, transcript: true)
            XCTAssertEqual(model.rows.first?.own, sender == "owner")
            XCTAssertEqual(model.rows.first?.senderLabel, sender == "owner" ? nil : sender)
            XCTAssertEqual(model.rows.first?.note, sender == "owner" ? "Sent" : nil)
            XCTAssertEqual(model.latestLine, "\(sender == "owner" ? "You" : sender): Continue the authorized step.")
        }
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
    func testSuppressedDeliveryIsExplicitAndDoesNotRetry() async {
        let model = ConversationModel()
        let service = ConversationStub(fate: "suppressed")
        model.draft = "Stale notice"
        let sent = await model.send(to: .chief, using: service)
        XCTAssertFalse(sent)
        XCTAssertEqual(model.outgoing.first?.note, "Not delivered · no longer current")
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
    func testQueueKeepsOneVisualIdentityThroughDeliveryAndLatestLine() async throws {
        let model = ConversationModel()
        let service = ConversationStub(fate: "queued")
        model.draft = "Hello"
        model.deliveryMode = "queue"
        _ = await model.send(to: .chief, using: service)
        let rowID = try XCTUnwrap(model.rows.first?.id)
        XCTAssertEqual(model.rows.first?.note, "Queued")
        XCTAssertEqual(model.latestLine, "You: Hello · Queued")
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.rows.count, 1)
        XCTAssertEqual(model.rows.first?.id, rowID)
        XCTAssertEqual(model.rows.first?.note, "Sent")
        XCTAssertEqual(model.latestLine, "You: Hello")
    }

    func testExplicitRefusalRetriesInPlaceWithNewDeliveryIdentity() async throws {
        let model = ConversationModel()
        let service = ConversationStub(fate: "refused")
        model.draft = "Hello"
        _ = await model.send(to: .chief, using: service)
        let original = try XCTUnwrap(model.outgoing.first?.id)
        XCTAssertEqual(model.rows.first?.retryID, original)
        await service.setFate("started")
        await model.retry(original, to: .chief, using: service)
        XCTAssertNotEqual(model.outgoing.first?.id, original)
        XCTAssertEqual(model.rows.first?.id, original)
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.rows.count, 1)
        XCTAssertEqual(model.rows.first?.id, original)
        let calls = await service.calls
        XCTAssertEqual(calls, 2)
    }

    func testOfflineIsRetryableButLostResponseIsNot() async throws {
        for (code, retryable) in [(URLError.notConnectedToInternet, true), (.timedOut, false)] {
            let model = ConversationModel()
            let service = ConversationStub(fate: "started", failure: code)
            model.draft = "Keep this"
            _ = await model.send(to: .chief, using: service)
            let id = try XCTUnwrap(model.outgoing.first?.id)
            XCTAssertEqual(model.outgoing.first?.retryable, retryable)
            if !retryable {
                await model.retry(id, to: .chief, using: service)
                let calls = await service.calls
                XCTAssertEqual(calls, 1)
            }
        }
    }

    func testHeldPromptRecoveredWithoutDuplicatingLocalQueue() async throws {
        let model = ConversationModel()
        let json = #"{"backend_key":"codex","model":"sol","reasoning_effort":"medium","held_prompts":[{"held_prompt_id":"held-1","sender_message_id":"message-1","content":[{"piece":"text","text":"Waiting"}]}]}"#
        let configuration = try APIClient.decoder().decode(ConversationConfiguration.self, from: Data(json.utf8))
        model.applyConfiguration(configuration)
        model.applyConfiguration(configuration)
        XCTAssertEqual(model.rows.count, 1)
        XCTAssertEqual(model.rows.first?.id, "message-1")
        XCTAssertEqual(model.latestLine, "You: Waiting · Queued")
        model.selectedModel = "user-selection"
        model.applyConfiguration(configuration)
        XCTAssertEqual(model.selectedModel, "user-selection")
    }

    func testRetryKeepsAttachmentBytesWithoutDuplicatingFilenameInText() async throws {
        let model = ConversationModel()
        let service = ConversationStub(fate: "refused")
        model.draft = "Read this"
        model.attachments = [.init(piece: "file", data: "YQ==", mediaType: "text/plain", fileName: "note.txt")]
        _ = await model.send(to: .chief, using: service)
        let id = try XCTUnwrap(model.outgoing.first?.id)
        await model.retry(id, to: .chief, using: service)
        let message = await service.lastMessage
        XCTAssertEqual(message?.content.count, 2)
        XCTAssertEqual(message?.content.first?.text, "Read this")
        XCTAssertEqual(message?.content.last?.data, "YQ==")
        XCTAssertTrue(model.attachments.isEmpty)
    }

    func testUnresolvedMessageDoesNotStayAboveNewerReplyAndRetryDoesNotDuplicateOldReceipt() async throws {
        let model = ConversationModel()
        let service = ConversationStub(fate: "refused")
        model.draft = "Hello"
        _ = await model.send(to: .chief, using: service)
        let original = try XCTUnwrap(model.outgoing.first?.id)
        await service.setFate("started")
        await model.retry(original, to: .chief, using: service)
        let retry = try XCTUnwrap(model.outgoing.first?.id)
        let data = Self.snapshot([
            Self.item(original, kind: "prompt_refused", text: "Hello", sender: original),
            Self.item("reply-before-delivery", kind: "agent_message", text: "A new reply"),
            Self.item("delivered", kind: "prompt", text: "Hello", sender: retry)
        ])
        await service.setSnapshot(data)
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.rows.map(\.id), [original, "reply-before-delivery"])
        XCTAssertEqual(model.latestLine, "A new reply")
    }

    func testReceiptSettlesQueueEvenOutsideLoadedWindow() async throws {
        let model = ConversationModel()
        let service = ConversationStub(fate: "queued")
        model.draft = "Hello"
        _ = await model.send(to: .chief, using: service)
        await service.setSnapshot(Self.snapshot([]))
        await service.setReceipt("prompt_discarded")
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.rows.first?.note, "Not delivered · no longer applicable")
        XCTAssertNil(model.rows.first?.retryID)
    }

    func testLiveBodyBecomesRecordedReplyWithoutChangingIdentity() async throws {
        let model = ConversationModel()
        model.conversationID = "conv_1"; model.lens = .full
        let service = ConversationStub(fate: "started")
        let output: [String: Any] = ["turn_key": "turn-1", "reference": ["body_id": "live", "version": "epoch:turn-1:0", "byte_count": 10, "media_type": "text/markdown"]]
        await service.setSnapshot(Self.snapshot([], live: output))
        await model.refresh(using: service, transcript: true)
        let liveID = try XCTUnwrap(model.rows.first?.id)
        XCTAssertEqual(model.rows.first?.text, "Live words")
        var recorded = Self.item("reply-1", kind: "agent_message", text: "Live words completed")
        recorded["turn_key"] = "turn-1"
        await service.setSnapshot(Self.snapshot([recorded]))
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.rows.count, 1)
        XCTAssertEqual(model.rows.first?.id, liveID)
        XCTAssertEqual(model.latestLine, "Live words completed")
    }

    func testFirstUnreadUsesOwnerMessageBoundaryNotToolActivity() async throws {
        let model = ConversationModel(); model.conversationID = "conv_1"
        let service = ConversationStub(fate: "started")
        var read = Self.item("read", kind: "agent_message", text: "Read")
        read["source_sequence"] = 3
        var unread = Self.item("unread", kind: "agent_message", text: "Unread")
        unread["source_sequence"] = 8
        await service.setSnapshot(Self.snapshot([read, unread], ownerSequence: 8))
        await model.refresh(using: service, transcript: true)
        let config = #"{"backend_key":"codex","model":"sol","owner_read_through_sequence":4,"latest_sequence":20}"#
        model.applyConfiguration(try APIClient.decoder().decode(ConversationConfiguration.self, from: Data(config.utf8)))
        XCTAssertEqual(model.firstUnreadID, "unread")
        let caughtUp = config.replacingOccurrences(of: "sequence\":4", with: "sequence\":8")
        model.applyConfiguration(try APIClient.decoder().decode(ConversationConfiguration.self, from: Data(caughtUp.utf8)))
        XCTAssertFalse(model.hasUnread)
    }

    func testRefusalReasonUsesReadableCopy() {
        XCTAssertEqual(ConversationDelivery.refusalText("backend_did_not_start"), "agent could not start")
        XCTAssertEqual(ConversationDelivery.refusalText("backend_cannot_steer"), "try Queue instead")
    }

    func testQueuePromotionSuppressionIsShownWithoutUnsafeRetry() async throws {
        let model = ConversationModel()
        let service = ConversationStub(fate: "queued")
        model.draft = "Hello"
        _ = await model.send(to: .chief, using: service)
        let id = try XCTUnwrap(model.outgoing.first?.id)
        model.applyHeldChange(.init(fate: "suppressed", discarded: nil, refusalReason: nil, reason: "no longer current"), senderID: id)
        XCTAssertEqual(model.rows.first?.note, "Not delivered · no longer current")
        XCTAssertNil(model.rows.first?.retryID)
    }

    func testQueueActionsUseExistingPromotionAndDiscardDoors() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ConversationRequestStub.self]
        let api = APIClient(baseURL: URL(string: "https://conversation.invalid")!, session: URLSession(configuration: configuration))
        _ = try await api.changeHeldPrompt("conv_1", heldID: "held-1", discard: false)
        XCTAssertEqual(ConversationRequestStub.request?.url?.path, "/api/conversation/conversations/conv_1/held-prompts/held-1/promote")
        XCTAssertEqual(ConversationRequestStub.request?.httpMethod, "POST")
        _ = try await api.changeHeldPrompt("conv_1", heldID: "held-1", discard: true)
        XCTAssertEqual(ConversationRequestStub.request?.url?.path, "/api/conversation/conversations/conv_1/held-prompts/held-1")
        XCTAssertEqual(ConversationRequestStub.request?.httpMethod, "DELETE")
    }

    private static func item(_ id: String, kind: String, text: String, sender: String? = nil) -> [String: Any] {
        var item: [String: Any] = ["kind": "row", "key": id, "body": ["kind": "inline", "value": ["kind": kind, "content": [["piece": "text", "text": text]]]]]
        if let sender { item["sender_message_id"] = sender }
        return item
    }
    private static func snapshot(_ items: [[String: Any]], live: [String: Any]? = nil, ownerSequence: Int = 0) -> String {
        var status: [String: Any] = ["authority": "current", "turn_running": live != nil, "rest": ["plan_progress": [:], "latest_owner_message_sequence": ownerSequence]]
        if let live { status["live_output"] = live }
        let object: [String: Any] = ["page": ["items": items, "older": NSNull()], "status": status]
        return String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
    }

    static let page = #"{"items":[{"kind":"row","key":"row-1","sender_message_id":"message-1","body":{"kind":"inline","value":{"kind":"prompt","content":[{"piece":"text","text":"Hello"}]}}},{"kind":"row","key":"row-2","body":{"kind":"deferred","reference":{"body_id":"long","version":"v1","byte_count":50000,"media_type":"application/json"}}}],"older":{"projection_version":1,"lens":"focus","scope":{"kind":"timeline","key":null},"order":{"sequence":7,"slot":0}}}"#
}

private actor ConversationStub: ConversationServing {
    var fate: String
    let failure: URLError.Code?
    var lastMessage: ConversationSend?
    var calls = 0
    var senderID = ""
    var snapshot: String?
    var receipt: String?
    func conversationLiveText(_ id: String, reference: ConversationBodyReference) async throws -> String { "Live words" }
    func setSnapshot(_ value: String) { snapshot = value }
    func setReceipt(_ value: String) { receipt = value }
    func conversationReceipts(_ id: String, messageIDs: [String]) async throws -> [ConversationReceipt] { receipt.map { [ConversationReceipt(senderMessageId: senderID, fate: $0)] } ?? [] }
    init(fate: String, failure: URLError.Code? = nil) { self.fate = fate; self.failure = failure }
    func setFate(_ fate: String) { self.fate = fate }
    func sendConversation(_ owner: ConversationOwner, message: ConversationSend) async throws -> ConversationDelivery {
        calls += 1; senderID = message.senderMessageId; lastMessage = message
        if let failure { throw URLError(failure) }
        return ConversationDelivery(conversationId: "conv_1", fate: fate, refusalReason: nil, reason: "no longer current")
    }
    func conversationSnapshot(_ id: String, lens: ConversationLens, transcript: Bool) async throws -> ConversationSnapshot {
        let json = #"{"page":{"items":[{"kind":"row","key":"row-1","sender_message_id":"SENDER","body":{"kind":"inline","value":{"kind":"prompt","content":[{"piece":"text","text":"Hello"}]}}}],"older":null},"status":{"authority":"current","turn_running":true,"rest":{"latest_happened":null,"latest_tool":{"kind":"tool_call","title":"Reading the code"},"permission_title_excerpt":null,"plan_progress":{"current_text_excerpt":null}}}}"#.replacingOccurrences(of: "SENDER", with: senderID)
        return try APIClient.decoder().decode(ConversationSnapshot.self, from: Data((snapshot ?? json).utf8))
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
