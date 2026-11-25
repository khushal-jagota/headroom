import XCTest
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
@testable import Panels

@MainActor final class ConversationTests: XCTestCase {
    func testOversizedFilesPickShowsItemErrorWithoutReadingBytes() async throws {
        let source = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".bin")
        defer { try? FileManager.default.removeItem(at: source) }
        FileManager.default.createFile(atPath: source.path, contents: nil)
        let handle = try FileHandle(forWritingTo: source)
        try handle.truncate(atOffset: 20_000_001)
        try handle.close()
        let model = ConversationModel()
        let attached = await model.attachFile(source, type: .data, read: { _ in
            XCTFail("An oversized Files pick must be refused before reading its bytes")
            return Data()
        })
        XCTAssertFalse(attached)
        XCTAssertEqual(model.attachments.count, 1)
        XCTAssertEqual(model.attachments.first?.fileName, source.lastPathComponent)
        XCTAssertNotNil(model.attachments.first?.preparationError)
        XCTAssertNil(model.attachments.first?.data)
        do { _ = try await model.preparedContent(); XCTFail("Failed item must block sending") }
        catch { XCTAssertNotNil(model.failedAttachment) }
    }

    func testFilesImageOverNonImageLimitUsesBoundedImageIOPreparation() async throws {
        let source = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".jpg")
        defer { try? FileManager.default.removeItem(at: source) }
        try Self.jpegPhoto(size: 32).write(to: source)
        let handle = try FileHandle(forWritingTo: source)
        try handle.truncate(atOffset: 20_000_001)
        try handle.close()
        let model = ConversationModel()
        let attached = await model.attachFile(source, type: .jpeg, read: { _ in
            XCTFail("Files images must use ImageIO's URL path, not the whole-file reader")
            return Data()
        })
        XCTAssertTrue(attached, "The 20 MB non-image limit does not refuse a Files photo")
        let piece = try XCTUnwrap(model.attachments.first)
        XCTAssertEqual(piece.piece, "image")
        XCTAssertLessThanOrEqual(try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(piece.data))).count,
                                 ConversationImagePreparation.maximumImageBytes)
        XCTAssertNotNil(try Self.readJPEG(piece))
    }

    func testFilesPickWithUnknownMetadataSizeIsNotRefusedForMissingSize() async throws {
        let source = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".txt")
        defer { try? FileManager.default.removeItem(at: source) }
        try Data("note".utf8).write(to: source)
        let model = ConversationModel()
        let attached = await model.attachFile(source, type: .plainText, fileSize: { _ in nil })
        XCTAssertTrue(attached)
        XCTAssertEqual(model.attachments.first?.data, Data("note".utf8).base64EncodedString())
        XCTAssertNil(model.failedAttachment)
    }

    func testVideoIntakeFinishesInStartingDraftAfterOwnerAndDraftSwitch() async throws {
        let source = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".mov")
        defer { try? FileManager.default.removeItem(at: source) }
        try Data([1, 2, 3]).write(to: source)
        let api = APIClient(baseURL: URL(string: "https://video-draft-\(UUID().uuidString).invalid")!)
        let model = ConversationModel()
        model.restore(owner: .ticket("starting-ticket"), api: api, draftID: "starting-note")
        model.draft = "Keep my note"
        let attached = await model.attachVideo(fileName: "clip.mov", load: {
            model.restore(owner: .manager("other-manager"), api: api, draftID: "other-note")
            return source
        })
        XCTAssertTrue(attached)
        XCTAssertTrue(model.attachments.isEmpty, "Completion must not join the new draft")
        model.restore(owner: .ticket("starting-ticket"), api: api, draftID: "starting-note")
        XCTAssertEqual(model.draft, "Keep my note")
        let video = try XCTUnwrap(model.attachments.first?.localVideo)
        XCTAssertEqual(try Data(contentsOf: video.fileURL), Data([1, 2, 3]))
        try? FileManager.default.removeItem(at: video.fileURL)
    }

    func testRemovingVideoFromSharedDraftDoesNotDeleteOtherLiveModelsFile() async throws {
        let source = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".mov")
        defer { try? FileManager.default.removeItem(at: source) }
        try Data([4, 5, 6]).write(to: source)
        let api = APIClient(baseURL: URL(string: "https://shared-video-\(UUID().uuidString).invalid")!)
        let review = ConversationModel()
        review.restore(owner: .ticket("ticket"), api: api, draftID: "shared-note")
        let attached = await review.attachVideo(fileName: "clip.mov", load: { source })
        XCTAssertTrue(attached)
        let ticket = ConversationModel()
        ticket.restore(owner: .ticket("ticket"), api: api, draftID: "shared-note")
        let video = try XCTUnwrap(ticket.attachments.first?.localVideo)
        defer { try? FileManager.default.removeItem(at: video.fileURL) }
        review.attachments = []
        XCTAssertNotNil(ticket.attachments.first?.localVideo)
        XCTAssertEqual(try Data(contentsOf: video.fileURL), Data([4, 5, 6]),
                       "Saving removal in Review must not invalidate the Ticket page's live reference")
        ticket.attachments.append(.init(piece: "file", data: "YQ==", fileName: "another.txt"))
        let restored = ConversationModel()
        restored.restore(owner: .ticket("ticket"), api: api, draftID: "shared-note")
        XCTAssertEqual(restored.attachments.first?.localVideo, video)
    }

    func testPreviewStatusDecodesPendingRequestsElapsedStartAndBackgroundWork() throws {
        let json = #"{"authority":"current","turn_running":false,"rest":{"plan_progress":{},"latest_turn":{"started_at_unix_milliseconds":1234}},"pending_permission":{"kind":"permission","request_id":"p","row_key":"row","source_sequence":1},"pending_user_input":{"kind":"user_input","request_id":"u","row_key":"input","source_sequence":2},"background_tasks":[{"task_id":"task","task_description":"Build"}]}"#
        let status = try APIClient.decoder().decode(ConversationStatus.self, from: Data(json.utf8))
        XCTAssertEqual(status.pendingPermission?.requestId, "p")
        XCTAssertEqual(status.pendingUserInput?.requestId, "u")
        XCTAssertEqual(status.rest.latestTurn?.startedAtUnixMilliseconds, 1234)
        XCTAssertTrue(status.hasBackgroundTaskAfterTheTurn)
        let runningJSON = json.replacingOccurrences(of: #""turn_running":false"#, with: #""turn_running":true"#)
        let running = try APIClient.decoder().decode(ConversationStatus.self, from: Data(runningJSON.utf8))
        XCTAssertFalse(running.hasBackgroundTaskAfterTheTurn, "A task during its live turn does not count as work after the turn")
    }

    func testRemoteAnswerEvidenceDoesNotClaimApplicationWhilePending() throws {
        let expectedEvidence = [
            ("saved", "Delivery to the worker machine is pending."),
            ("applied", "The worker machine applied your answer."),
            ("unconfirmed", "the app cannot confirm delivery."),
        ]
        for (fate, text) in expectedEvidence {
            let json = "{\"kind\":\"permission_ask\",\"title\":\"Approve command\",\"state\":\"answered\",\"answer_fate\":\"\(fate)\"}"
            let body = try APIClient.decoder().decode(ConversationBody.self, from: Data(json.utf8))
            XCTAssertTrue(body.answerSentence?.contains(text) == true, "Wrong evidence for \(fate)")
            switch fate {
            case "saved": XCTAssertEqual(body.answerSentence(machineName: "Mac 1"), "The home saved your answer. Delivery to Mac 1 is pending.")
            case "applied": XCTAssertEqual(body.answerSentence(machineName: "Mac 1"), "Mac 1 applied your answer.")
            default: XCTAssertEqual(body.answerSentence(machineName: "Mac 1"), body.answerSentence)
            }
        }
    }

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
            XCTAssertNil(model.rows.first?.note, "A delivered message shows no status")
            XCTAssertEqual(model.latestLine, "\(sender == "owner" ? "You" : sender): Continue the authorized step.")
        }
    }
    func testUnchangedPollRetainsProjectionButBodyAndOutgoingChangesRefreshIt() async throws {
        let model = ConversationModel(); model.conversationID = "conv_1"
        let service = ConversationStub(fate: "started")
        await service.setSnapshot(Self.snapshot([Self.item("reply", kind: "agent_message", text: "Before")]))
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.latestLine, "Before")
        let count = model.projectionBuildCount
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.rows.first?.text, "Before")
        XCTAssertEqual(model.latestLine, "Before")
        XCTAssertEqual(model.projectionBuildCount, count)
        await service.setSnapshot(Self.snapshot([Self.item("reply", kind: "agent_message", text: "After")]))
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.latestLine, "After")
        XCTAssertEqual(model.projectionBuildCount, count + 1)
        model.outgoing = [.init(id: "pending", text: "New")]
        XCTAssertEqual(model.latestLine, "You: New")
        XCTAssertEqual(model.projectionBuildCount, count + 2)
        model.expandedBodies["reply"] = try APIClient.decoder().decode(ConversationBody.self, from: Data(#"{"kind":"agent_message","content":[{"piece":"text","text":"Expanded"}]}"#.utf8))
        XCTAssertEqual(model.rows.first?.text, "Expanded")
        await service.setSnapshot(Self.snapshot([Self.item("reply", kind: "agent_message", text: "Fresh after reconnect")]))
        await model.refresh(using: service, transcript: true)
        XCTAssertNil(model.expandedBodies["reply"], "A new snapshot body invalidates the previous expansion")
        XCTAssertEqual(model.rows.first?.text, "Fresh after reconnect")
    }
    func testLiveReferenceReusesOnlyIdenticalBytesAndCoalescesRefreshDuringInFlightRead() async throws {
        let model = ConversationModel(); model.conversationID = "conv_1"; model.lens = .full
        let service = ConversationStub(fate: "started")
        let reference: [String: Any] = ["body_id": "live", "version": "v1", "byte_count": 10, "media_type": "text/markdown"]
        await service.setSnapshot(Self.snapshot([], live: ["turn_key": "turn", "reference": reference]))
        await model.refresh(using: service, transcript: true)
        await model.refresh(using: service, transcript: true)
        let firstReads = await service.liveReads
        XCTAssertEqual(firstReads, 1)
        var grown = reference; grown["byte_count"] = 20
        await service.setSnapshot(Self.snapshot([], live: ["turn_key": "turn", "reference": grown]))
        await model.refresh(using: service, transcript: true)
        let grownReads = await service.liveReads
        XCTAssertEqual(grownReads, 2)
        let beforeOverlap = await service.requestedLenses.count
        await service.holdNextSnapshot()
        let first = Task { await model.refresh(using: service, transcript: true) }
        while !(await service.snapshotHeld) { await Task.yield() }
        model.lens = .focus
        for _ in 0..<3 { await model.refresh(using: service, transcript: true) }
        await service.releaseSnapshot()
        await first.value
        let lenses = await service.requestedLenses
        XCTAssertEqual(lenses.suffix(2), [.full, .focus])
        XCTAssertEqual(lenses.count, beforeOverlap + 2)
    }
    // The bar follows its open tail: frames change the window without another transcript read.
    func testTailFramesUpdateWindowWithoutReadingTranscript() async throws {
        let model = ConversationModel(); model.conversationID = "conv_1"
        let service = ConversationStub(fate: "started")
        func ordered(_ id: String, _ sequence: Int, _ text: String) -> [String: Any] {
            var item = Self.item(id, kind: "agent_message", text: text); item["order"] = ["sequence": sequence, "slot": 0]; return item
        }
        var parser = ConversationTailParser()
        // Bytes as the server writes them: CRLF line ends and U+2028 left unescaped in text.
        func frame(_ event: String, _ value: [String: Any]) async throws -> ConversationTailFrame {
            let json = String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self).replacingOccurrences(of: "\\u2028", with: "\u{2028}")
            let bytes = AsyncStream<UInt8> { continuation in
                for byte in Data(": heartbeat\r\n\r\nevent: \(event)\r\ndata: \(json)\r\n\r\n".utf8) { continuation.yield(byte) }
                continuation.finish()
            }
            var frames: [ConversationTailFrame] = []
            for try await line in ServerSentLines(bytes) { if let frame = try parser.consume(line) { frames.append(frame) } }
            XCTAssertEqual(frames.count, 1)
            return try XCTUnwrap(frames.first)
        }
        var snapshot = try JSONSerialization.jsonObject(with: Data(Self.snapshot([ordered("a", 1, "First"), ordered("b", 2, "Second")]).utf8)) as! [String: Any]
        snapshot["coverage"] = ["through_sequence": 2]
        model.beginTail()
        let applied = await model.applyTail(try await frame("snapshot", snapshot), using: service)
        XCTAssertTrue(applied)
        XCTAssertEqual(model.latestLine, "Second")
        let status = snapshot["status"]!
        let changes: [String: Any] = ["kind": "changes", "lens": "focus", "from_sequence": 2, "coverage": ["through_sequence": 4],
            "upserts": [ordered("b", 2, "Second,\u{2028}edited"), ordered("c", 4, "Third")],
            "scope_changes": [["scope": ["kind": "timeline", "key": NSNull()], "item_keys": ["b", "c"], "removed_keys": []]],
            // The server marks the window invalidated with every removed row.
            "removed_keys": ["a"], "invalidated_scopes": [["kind": "timeline", "key": NSNull()]], "status": status]
        let changed = await model.applyTail(try await frame("changes", changes), using: service)
        XCTAssertTrue(changed, "A removed row applies without reopening the stream")
        XCTAssertEqual(model.items.map(\.key), ["b", "c"])
        XCTAssertEqual(model.rows.map(\.text), ["Second,\u{2028}edited", "Third"])
        let reads = await service.requestedLenses
        XCTAssertTrue(reads.isEmpty, "A tail frame never reads the transcript again")
    }
    func testTailGapResetOrChangedEpochNeedsFreshSnapshot() async throws {
        let model = ConversationModel(); model.conversationID = "conv_1"
        let service = ConversationStub(fate: "started")
        let snapshotJSON = try JSONSerialization.jsonObject(with: Data(Self.snapshot([]).utf8)) as! [String: Any]
        var withCoverage = snapshotJSON; withCoverage["coverage"] = ["through_sequence": 5]
        func decode(_ value: [String: Any]) throws -> ConversationTailChanges {
            try APIClient.decoder().decode(ConversationTailChanges.self, from: JSONSerialization.data(withJSONObject: value))
        }
        func changes(from: Int, status: Any? = nil, invalidated: [[String: Any]] = []) throws -> ConversationTailFrame {
            .changes(try decode(["lens": "focus", "from_sequence": from, "coverage": ["through_sequence": from + 1], "upserts": [], "scope_changes": [],
                                  "removed_keys": [], "invalidated_scopes": invalidated, "status": status ?? snapshotJSON["status"]!]))
        }
        model.beginTail()
        let beforeSnapshot = await model.applyTail(try changes(from: 0), using: service)
        XCTAssertFalse(beforeSnapshot, "Changes need the stream's own snapshot first")
        let snapshot = try APIClient.decoder().decode(ConversationSnapshot.self, from: JSONSerialization.data(withJSONObject: withCoverage))
        let admitted = await model.applyTail(.snapshot(snapshot), using: service)
        XCTAssertTrue(admitted)
        let gap = await model.applyTail(try changes(from: 4), using: service)
        XCTAssertFalse(gap)
        XCTAssertNil(model.tailAfterSequence)
        _ = await model.applyTail(.snapshot(snapshot), using: service)
        let invalidated = await model.applyTail(try changes(from: 5, invalidated: [["kind": "timeline", "key": NSNull()]]), using: service)
        XCTAssertFalse(invalidated)
        _ = await model.applyTail(.snapshot(snapshot), using: service)
        var restarted = snapshotJSON["status"] as! [String: Any]; restarted["runtime_epoch"] = "later"
        let epoch = await model.applyTail(try changes(from: 5, status: restarted), using: service)
        XCTAssertFalse(epoch)
        let reset = await model.applyTail(.reset, using: service)
        XCTAssertFalse(reset)
        XCTAssertNil(model.tailAfterSequence)
        _ = await model.applyTail(.snapshot(snapshot), using: service)
        let continued = await model.applyTail(try changes(from: 5), using: service)
        XCTAssertTrue(continued)
    }
    func testUncertainSendShowsNoStatusAndDoesNotRetry() async {
        let model = ConversationModel()
        let service = ConversationStub(fate: "uncertain")
        model.draft = " \n "
        let blank = await model.send(to: .chief, using: service)
        XCTAssertFalse(blank)
        model.draft = "Keep my words"
        let result = await model.send(to: .chief, using: service)
        XCTAssertFalse(result)
        XCTAssertEqual(model.outgoing.first?.text, "Keep my words")
        XCTAssertNil(model.outgoing.first?.note)
        XCTAssertFalse(model.outgoing.first?.retryable ?? true)
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
        let content = try await ConversationImagePreparation.prepare([
            .init(text: "Hello"),
            .init(piece: "image", data: try Self.heicPhoto().base64EncodedString(), mediaType: "image/heic", fileName: "Picture.heic")
        ])
        let message = ConversationSend(conversationId: "conv_1", content: content, senderMessageId: "message-1", sentAtUnixMilliseconds: 123)
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
        let sent = try XCTUnwrap(json["content"] as? [[String: String]])
        XCTAssertEqual(sent.first, ["piece": "text", "text": "Hello"])
        XCTAssertEqual(sent.last?["media_type"], "image/jpeg")
        XCTAssertEqual(sent.last?["file_name"], "Picture.jpg")
        XCTAssertEqual(sent.last?["data"], content.last?.data)
        XCTAssertNil(sent.last?["mediaType"])
    }

    func testHEICPhotoBecomesReadableJPEGBeforeItJoinsTheMessage() async throws {
        let source = try Self.heicPhoto()
        let model = ConversationModel()
        let attached1 = await model.attach(source, type: .heic, fileName: "Picture.heic")
        XCTAssertTrue(attached1)
        let picture = try XCTUnwrap(model.attachments.first)
        XCTAssertEqual(picture.mediaType, "image/jpeg")
        XCTAssertEqual(picture.fileName, "Picture.jpg")
        XCTAssertNotEqual(picture.data, source.base64EncodedString())
        _ = try Self.readJPEG(picture)
        model.draft = "Keep these words with the prepared photo"
        let prepared = model.attachments
        let attached2 = await model.attach(Data("not a photo".utf8), type: .heic, fileName: "Corrupt.heic")
        XCTAssertFalse(attached2)
        XCTAssertEqual(model.draft, "Keep these words with the prepared photo")
        XCTAssertEqual(model.attachments, prepared)
        XCTAssertTrue(model.error?.lowercased().contains("photo") == true)
    }

    func testOversizedJPEGAndMultiplePhotosFitTheSharedImageBudget() async throws {
        let original = try Self.jpegPhoto(size: 2048)
        let limit = 3 * 1024 * 1024
        XCTAssertGreaterThan(original.count, limit, "The source must exercise actual JPEG compression, not padding")
        let model = ConversationModel()
        let attached3 = await model.attach(original, type: .jpeg, fileName: "Large.jpg")
        XCTAssertTrue(attached3)
        let first = try XCTUnwrap(model.attachments.first)
        XCTAssertLessThanOrEqual(try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(first.data))).count, limit)
        _ = try Self.readJPEG(first)

        let attached4 = await model.attach(original, type: .jpeg, fileName: "Another.jpg")
        XCTAssertTrue(attached4)
        XCTAssertEqual(model.attachments.count, 2)
        let bytes = try model.attachments.map { piece in
            _ = try Self.readJPEG(piece)
            return try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(piece.data))).count
        }
        XCTAssertLessThanOrEqual(bytes.reduce(0, +), limit, "The server limits the whole message, not each photo")
    }

    func testNormalJPEGPhotoKeepsItsBytesAndFilename() async throws {
        let original = try Self.jpegPhoto(size: 16)
        let model = ConversationModel()
        let attached5 = await model.attach(original, type: .jpeg, fileName: "Normal.jpg")
        XCTAssertTrue(attached5)
        let picture = try XCTUnwrap(model.attachments.first)
        XCTAssertEqual(picture.data, original.base64EncodedString())
        XCTAssertEqual(picture.mediaType, "image/jpeg")
        XCTAssertEqual(picture.fileName, "Normal.jpg")
        _ = try Self.readJPEG(picture)
    }

    func testPhotoPreparationRunsOffMainActorAndAcceptedPhotosAreNotPreparedAgain() async throws {
        let probe = PhotoPreparationProbe()
        let model = ConversationModel(imageWorkStarted: { probe.observe() })
        let photo = try Self.heicPhoto()
        let attachment = Task {
            await model.attachPhoto(fileName: "Picture.heic") {
                XCTAssertFalse(Thread.isMainThread, "Camera encoding must use the same background intake boundary")
                return photo
            }
        }
        await fulfillment(of: [probe.started], timeout: 3)
        defer { probe.release() }
        XCTAssertTrue(model.preparingPhoto)
        XCTAssertEqual(Array(model.preparingPhotos.values), ["Picture.heic"])
        model.draft = "The main actor remains available"
        let service = ConversationStub(fate: "queued")
        let sentDuringPreparation = await model.send(to: .chief, using: service)
        XCTAssertFalse(sentDuringPreparation)
        let callsDuringPreparation = await service.calls
        XCTAssertEqual(callsDuringPreparation, 0)
        probe.release()
        let attached = await attachment.value
        XCTAssertTrue(attached)
        XCTAssertFalse(model.preparingPhoto)
        XCTAssertFalse(probe.usedMainThread)
        XCTAssertEqual(probe.count, 1)
        let second = await model.attach(try Self.jpegPhoto(size: 16), type: .jpeg, fileName: "Normal.jpg")
        XCTAssertTrue(second)
        XCTAssertEqual(probe.count, 2, "Adding a photo must not decode the accepted first photo again")
        _ = try await model.preparedContent()
        _ = try await model.preparedContent()
        XCTAssertEqual(probe.count, 2, "Send capture must reuse accepted photo preparation")
        XCTAssertEqual(model.draft, "The main actor remains available")
    }

    func testRestoredPhotoPreparationCannotSendAfterDraftOrDestinationChanges() async throws {
        for change in ["draft", "destination"] {
            let probe = PhotoPreparationProbe()
            let model = ConversationModel(imageWorkStarted: { probe.observe() })
            model.conversationID = "original"
            model.attachments = [.init(piece: "image", data: try Self.heicPhoto().base64EncodedString(),
                mediaType: "image/heic", fileName: "Restored.heic")]
            model.draft = "Original"
            let original = model.attachments
            let service = ConversationStub(fate: "queued")
            let send = Task { await model.send(to: .chief, using: service) }
            await fulfillment(of: [probe.started], timeout: 3)
            defer { probe.release() }
            XCTAssertTrue(model.preparingPhoto)
            if change == "draft" { model.draft = "New words" }
            else { model.selectConversation("new-destination") }
            probe.release()
            let accepted = await send.value
            XCTAssertFalse(accepted)
            XCTAssertEqual(model.draft, change == "draft" ? "New words" : "Original")
            if change == "draft" { XCTAssertEqual(model.attachments, original) }
            XCTAssertFalse(model.preparingPhoto)
            let calls = await service.calls
            XCTAssertEqual(calls, 0)
        }
    }

    private static func heicPhoto() throws -> Data {
        #if SWIFT_PACKAGE
        let url = Bundle.module.url(forResource: "photo", withExtension: "heic", subdirectory: "Fixtures")
        #else
        let url = Bundle(for: Self.self).url(forResource: "photo", withExtension: "heic")
        #endif
        return try Data(contentsOf: XCTUnwrap(url))
    }

    /// Real deterministic pixel noise prevents a flat-color JPEG from missing the size boundary.
    private static func jpegPhoto(size: Int) throws -> Data {
        var pixels = [UInt32](repeating: 0, count: size * size)
        var noise: UInt32 = 0x6a09e667
        for index in pixels.indices {
            noise ^= noise << 13; noise ^= noise >> 17; noise ^= noise << 5
            pixels[index] = noise
        }
        let data = pixels.withUnsafeBytes { Data($0) }
        let provider = try XCTUnwrap(CGDataProvider(data: data as CFData))
        let image = try XCTUnwrap(CGImage(width: size, height: size, bitsPerComponent: 8, bitsPerPixel: 32,
            bytesPerRow: size * 4, space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent))
        let encoded = NSMutableData()
        let destination = try XCTUnwrap(CGImageDestinationCreateWithData(encoded as CFMutableData, UTType.jpeg.identifier as CFString, 1, nil))
        CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: 1] as CFDictionary)
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        return encoded as Data
    }

    private static func readJPEG(_ piece: ConversationSend.Piece) throws -> CGImage {
        let data = try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(piece.data)))
        let source = try XCTUnwrap(CGImageSourceCreateWithData(data as CFData, nil))
        XCTAssertEqual(try XCTUnwrap(CGImageSourceGetType(source)) as String, UTType.jpeg.identifier)
        let image = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil))
        XCTAssertGreaterThan(image.width, 0)
        XCTAssertGreaterThan(image.height, 0)
        return image
    }
    func testQueueKeepsOneVisualIdentityThroughDeliveryAndLatestLine() async throws {
        let model = ConversationModel()
        let service = ConversationStub(fate: "queued")
        model.draft = "Hello"
        model.deliveryMode = "queue"
        _ = await model.send(to: .chief, using: service)
        let rowID = try XCTUnwrap(model.rows.first?.id)
        XCTAssertNil(model.rows.first?.note)
        XCTAssertEqual(model.latestLine, "You: Hello")
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.rows.count, 1)
        XCTAssertEqual(model.rows.first?.id, rowID)
        XCTAssertNil(model.rows.first?.note)
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

    func testConnectionErrorShowsOnlyWhileTheConversationHasNothingLoaded() throws {
        let model = ConversationModel()
        model.reportReadFailure(URLError(.secureConnectionFailed))
        XCTAssertNotNil(model.error, "A TLS failure shows when nothing is loaded")
        model.error = nil
        model.items = [try APIClient.decoder().decode(ConversationItem.self, from: Data(#"{"kind":"row","key":"row-1","body":{"kind":"inline","value":{"kind":"prompt","content":[{"piece":"text","text":"Hello"}]}}}"#.utf8))]
        model.reportReadFailure(URLError(.secureConnectionFailed))
        XCTAssertNil(model.error, "With content on screen a failed refresh stays silent")
    }

    // Risk: a historical conversation inherits the Ticket's current destination.
    // The existing decoder/model host proves conversation identity takes precedence.
    func testConversationMachineIdentityPrecedesCurrentTicket() throws {
        let model = ConversationModel()
        model.machineId = "current-ticket-machine"
        XCTAssertEqual(model.presentedMachineId, "current-ticket-machine")
        let data = Data(#"{"backend_key":"codex","machine_id":"historical-machine"}"#.utf8)
        model.applyConfiguration(try APIClient.decoder().decode(ConversationConfiguration.self, from: data))
        XCTAssertEqual(model.presentedMachineId, "historical-machine")
        let older = Data(#"{"backend_key":"codex"}"#.utf8)
        model.applyConfiguration(try APIClient.decoder().decode(ConversationConfiguration.self, from: older))
        XCTAssertEqual(model.presentedMachineId, "current-ticket-machine", "Older records retain the Ticket fallback")
    }

    func testHeldPromptRecoveredWithoutDuplicatingLocalQueue() async throws {
        let model = ConversationModel()
        let json = #"{"backend_key":"codex","model":"sol","reasoning_effort":"medium","held_prompts":[{"held_prompt_id":"held-1","sender_message_id":"message-1","content":[{"piece":"text","text":"Waiting"}]}]}"#
        let configuration = try APIClient.decoder().decode(ConversationConfiguration.self, from: Data(json.utf8))
        model.applyConfiguration(configuration)
        model.applyConfiguration(configuration)
        XCTAssertEqual(model.rows.count, 1)
        XCTAssertEqual(model.rows.first?.id, "message-1")
        XCTAssertEqual(model.latestLine, "You: Waiting")
        model.selectedModel = "user-selection"
        model.applyConfiguration(configuration)
        XCTAssertEqual(model.selectedModel, "user-selection")
        let held = json.replacingOccurrences(of: #""held_prompt_id""#, with: #""queue_reason":"attachment","held_prompt_id""#)
        model.applyConfiguration(try APIClient.decoder().decode(ConversationConfiguration.self, from: Data(held.utf8)))
        XCTAssertEqual(model.heldPrompts.first?.note, "Queued · attachments wait for the turn to end")
        XCTAssertNil(model.rows.first?.note, "A held message is not refused: its row carries no mark")
        // Risk: a backend that cannot join a running turn still offers Steer, or an older
        // server's view, which has no supports_steer, loses it.
        XCTAssertTrue(configuration.supportsSteer, "A view without supports_steer keeps Steer")
        XCTAssertTrue(model.supportsSteer)
        XCTAssertEqual(model.deliveryMode, "steer")
        let withoutSteer = json.replacingOccurrences(of: #""model":"sol""#, with: #""supports_steer":false,"model":"sol""#)
        let refused = try APIClient.decoder().decode(ConversationConfiguration.self, from: Data(withoutSteer.utf8))
        XCTAssertFalse(refused.supportsSteer)
        model.applyConfiguration(refused)
        XCTAssertFalse(model.supportsSteer)
        XCTAssertEqual(model.deliveryMode, "queue", "Steer is not offered, so it is not the kept choice")
    }

    // The server holds any message that names a model until a new turn, so an unchanged
    // selection must not ride along or a steer becomes a wait for the turn to end.
    func testSendCarriesOnlyARealModelChange() async throws {
        func configuration(_ model: String) throws -> ConversationConfiguration {
            try APIClient.decoder().decode(ConversationConfiguration.self, from: Data(#"{"backend_key":"claude","model":"MODEL","reasoning_effort":"medium"}"#.replacingOccurrences(of: "MODEL", with: model).utf8))
        }
        let model = ConversationModel()
        let service = ConversationStub(fate: "injected")
        model.conversationID = "conv_1"
        model.applyConfiguration(try configuration("opus"))
        model.applyConfiguration(try configuration("opus-next"))
        XCTAssertEqual(model.selectedModel, "opus-next")
        model.draft = "Steer this"
        _ = await model.send(to: .chief, using: service)
        let first = await service.lastMessage
        var message = try XCTUnwrap(first)
        XCTAssertEqual(message.mode, "steer")
        XCTAssertNil(message.model)
        XCTAssertNil(message.reasoningEffort)
        model.selectedModel = "sonnet"
        model.draft = "Switch"
        _ = await model.send(to: .chief, using: service)
        let second = await service.lastMessage
        message = try XCTUnwrap(second)
        XCTAssertEqual(message.model, "sonnet")
        XCTAssertNil(message.reasoningEffort)
        XCTAssertNil(message.backendKey)
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        let unchanged = try JSONSerialization.jsonObject(with: encoder.encode(message)) as! [String: Any]
        XCTAssertNil(unchanged["reasoning_effort"])
        XCTAssertNil(unchanged["backend_key"])
        model.reasoningEffort = nil
        model.draft = "Reset effort"
        _ = await model.send(to: .chief, using: service)
        let resetMessage = await service.lastMessage
        let reset = try XCTUnwrap(resetMessage)
        let resetBody = try JSONSerialization.jsonObject(with: encoder.encode(reset)) as! [String: Any]
        XCTAssertTrue(resetBody["reasoning_effort"] is NSNull)
        let restored = try JSONDecoder().decode(ConversationSend.self, from: JSONEncoder().encode(reset))
        XCTAssertTrue(restored.reasoningEffortIsSet)
        XCTAssertTrue((try JSONSerialization.jsonObject(with: encoder.encode(restored)) as! [String: Any])["reasoning_effort"] is NSNull)
        let creation = ConversationModel()
        creation.applyConfiguration(try configuration("opus"))
        creation.draft = "Start"
        _ = await creation.send(to: .chief, using: service)
        let creationMessage = await service.lastMessage
        let start = try XCTUnwrap(creationMessage)
        XCTAssertEqual(start.backendKey, "claude")
        XCTAssertEqual(start.model, "opus")
        XCTAssertEqual(start.reasoningEffort, "medium")
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

    func testReadCreditRequiresVisibleDecodedRowsInTheActiveConversation() async throws {
        let service = ConversationStub(fate: "started")
        var shown = Self.item("shown", kind: "agent_message", text: "On screen")
        shown["source_sequence"] = 8
        var hidden = Self.item("hidden", kind: "agent_message", text: "Not on screen")
        hidden["source_sequence"] = 12
        await service.setSnapshot(Self.snapshot([shown, hidden], ownerSequence: 12))
        let model = ConversationModel(); model.conversationID = "conv_1"
        await model.refresh(using: service, transcript: true)
        let config = #"{"backend_key":"codex","owner_read_through_sequence":0}"#
        model.applyConfiguration(try APIClient.decoder().decode(ConversationConfiguration.self, from: Data(config.utf8)))
        let afterPoll = await service.readRequests
        XCTAssertTrue(afterPoll.isEmpty)
        await model.acknowledgeVisibleRows(["shown"], active: false, using: service)
        await model.acknowledgeVisibleRows([], active: true, using: service)
        let whileHidden = await service.readRequests
        XCTAssertTrue(whileHidden.isEmpty, "Hidden/background and dock views have no visible rows")
        await model.acknowledgeVisibleRows(["shown"], active: true, using: service)
        let visible = await service.readRequests
        XCTAssertEqual(visible.map(\.throughSequence), [8], "The hidden latest row/status must not earn credit")
        XCTAssertTrue(model.hasUnread)
        // An older in-flight configuration response cannot undo a confirmed read.
        model.applyConfiguration(try APIClient.decoder().decode(ConversationConfiguration.self, from: Data(config.utf8)))
        await model.acknowledgeVisibleRows(["shown"], active: true, using: service)
        let repeated = await service.readRequests
        XCTAssertEqual(repeated.count, 1)
        await model.acknowledgeVisibleRows(["hidden"], active: true, using: service)
        XCTAssertFalse(model.hasUnread)
        model.conversationID = "another"
        XCTAssertNil(model.visibleReadBoundary(rowIDs: ["hidden"], active: true), "Rows belong to the conversation they were loaded from")
    }

    func testDeferredAndFailedReadAcknowledgmentKeepUnreadState() async throws {
        let service = ConversationStub(fate: "started", failure: .networkConnectionLost)
        let model = ConversationModel(); model.conversationID = "conv_1"
        let deferred: [String: Any] = ["kind": "row", "key": "long", "source_sequence": 8,
            "body": ["kind": "deferred", "reference": ["body_id": "long", "version": "v", "byte_count": 10, "media_type": "application/json"]]]
        await service.setSnapshot(Self.snapshot([deferred], ownerSequence: 8))
        await model.refresh(using: service, transcript: true)
        model.applyConfiguration(try APIClient.decoder().decode(ConversationConfiguration.self, from: Data(#"{"backend_key":"codex","owner_read_through_sequence":0}"#.utf8)))
        await model.acknowledgeVisibleRows(["long"], active: true, using: service)
        let beforeBody = await service.readRequests
        XCTAssertTrue(beforeBody.isEmpty)
        model.expandedBodies["long"] = try APIClient.decoder().decode(ConversationBody.self, from: Data(#"{"kind":"agent_message","content":[{"piece":"text","text":"Decoded visible body"}]}"#.utf8))
        await model.acknowledgeVisibleRows(["long"], active: true, using: service)
        let failed = await service.readRequests
        XCTAssertEqual(failed.map(\.throughSequence), [8])
        XCTAssertTrue(model.hasUnread, "An unconfirmed acknowledgment cannot clear unread state")
    }

    func testReadAcknowledgmentAdapterUsesExactConversationAndBoundary() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ConversationRequestStub.self]
        let api = APIClient(baseURL: URL(string: "https://conversation.invalid")!, session: URLSession(configuration: configuration))
        let confirmed = try await api.markConversationRead("historical", through: 8)
        XCTAssertEqual(confirmed, 8)
        let request = try XCTUnwrap(ConversationRequestStub.request)
        XCTAssertEqual(request.url?.path, "/api/conversation/conversations/historical/owner-read")
        XCTAssertEqual(request.httpMethod, "POST")
        let stream = try XCTUnwrap(request.httpBodyStream)
        stream.open(); defer { stream.close() }
        var bytes = [UInt8](repeating: 0, count: 4096)
        let count = stream.read(&bytes, maxLength: bytes.count)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(bytes.prefix(count))) as? [String: Int])
        XCTAssertEqual(body, ["through_sequence": 8])
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

    func testSavedDisplayIsStaleAndCannotAcknowledgeBeforeFreshAdmission() async throws {
        let id = "display-" + UUID().uuidString
        let api = APIClient(baseURL: URL(string: "https://display.invalid")!)
        var row = Self.item("saved", kind: "agent_message", text: "Saved words")
        row["source_sequence"] = 8
        let snapshot = try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(Self.snapshot([row], ownerSequence: 8).utf8))
        let stale = ConversationStatus(authority: "stale", turnRunning: nil, liveOutput: nil, rest: snapshot.status.rest)
        let saved = ConversationDisplaySnapshot(schema: 1, conversationID: id, lens: "focus", page: snapshot.page, status: stale)
        try await LocalStore.shared.cacheResponse(server: LocalStore.server(api.baseURL), id: "conversation-display:v1:" + id + ":focus", data: JSONEncoder().encode(saved))
        let model = ConversationModel(); model.conversationID = id
        model.restore(owner: .chief, api: api)
        XCTAssertEqual(model.rows.first?.text, "Saved words")
        XCTAssertEqual(model.status?.authority, "stale")
        XCTAssertNil(model.visibleReadBoundary(rowIDs: ["saved"], active: true))
        let service = ConversationStub(fate: "started")
        await service.setSnapshot(Self.snapshot([row], ownerSequence: 8))
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.visibleReadBoundary(rowIDs: ["saved"], active: true)?.throughSequence, 8)
        let generation = model.beginTail()
        let gap = ConversationTailChanges(lens: "focus", fromSequence: 0, coverage: .init(throughSequence: 1), upserts: [], scopeChanges: [], removedKeys: [], invalidatedScopes: [], status: snapshot.status)
        let admitted = await model.applyTail(.changes(gap), generation: generation, using: service)
        XCTAssertFalse(admitted, "Saved display is never a stream patch base")
        XCTAssertNil(model.visibleReadBoundary(rowIDs: ["saved"], active: true))
    }

    func testCollapsedDemandDoesNotReadOrAdmitTranscriptAndObsoleteTailCannotWin() async throws {
        let model = ConversationModel(); model.conversationID = "conv_1"
        let retained = try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(Self.snapshot([Self.item("cached", kind: "agent_message", text: "Cached reply")]).utf8)).page.items
        model.items = retained
        let service = ConversationStub(fate: "started")
        let live: [String: Any] = ["turn_key": "turn", "reference": ["body_id": "live", "version": "v1", "byte_count": 10, "media_type": "text/plain"]]
        let rest: [String: Any] = ["latest_happened": ["kind": "prompt", "sender_label": "Panels deployment recovery", "excerpt": "Inspect interrupted work"]]
        let snapshot = try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(Self.snapshot([Self.item("hidden", kind: "agent_message", text: "Hidden")], live: live, rest: rest).utf8))
        let oldGeneration = model.beginTail()
        let generation = model.beginTail()
        let obsolete = await model.applyTail(.snapshot(snapshot), transcript: true, generation: oldGeneration, using: service)
        XCTAssertFalse(obsolete)
        let admitted = await model.applyTail(.snapshot(snapshot), transcript: false, generation: generation, using: service)
        XCTAssertTrue(admitted)
        XCTAssertEqual(model.items, retained, "Status-only facts do not replace cached transcript rows")
        XCTAssertEqual(model.compactPreview, "Panels deployment recovery: Inspect interrupted work")
        model.status = .init(authority: "stale", turnRunning: nil, liveOutput: nil, rest: snapshot.status.rest)
        XCTAssertEqual(model.compactPreview, "Cached reply")
        let reply = try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(Self.snapshot([], rest: ["latest_owner_message_preview": "Current reply"]).utf8))
        let replyAdmitted = await model.applyTail(.snapshot(reply), transcript: false, generation: generation, using: service)
        XCTAssertTrue(replyAdmitted)
        XCTAssertEqual(model.compactPreview, "Current reply")
        XCTAssertEqual(model.items, retained)
        let refusal = Self.item("refused", kind: "prompt_refused", text: "Earlier refused prompt")
        model.items = try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(Self.snapshot([refusal]).utf8)).page.items
        XCTAssertTrue(try XCTUnwrap(model.latestMessage).own)
        XCTAssertEqual(model.compactPreview, "Current reply", "A retained server refusal is not a new local unsent message")
        model.items = retained
        model.status = .init(authority: "stale", turnRunning: nil, liveOutput: nil, rest: reply.status.rest)
        XCTAssertEqual(model.compactPreview, "Cached reply")
        model.outgoing = [.init(id: "local", text: "Unsent local message", note: "Queued")]
        XCTAssertEqual(model.compactPreview, "You: Unsent local message")
        model.status = reply.status
        XCTAssertEqual(model.compactPreview, "You: Unsent local message")
        let reads = await service.liveReads
        XCTAssertEqual(reads, 0, "Status-only demand never downloads transcript live output")
        // Dock status can advance past unchanged transcript rows. Opening the page
        // must fetch them in a fresh snapshot rather than resume that dock cursor.
        var dockSnapshot = reply
        dockSnapshot.coverage = .init(throughSequence: 30)
        let dockAdmitted = await model.applyTail(.snapshot(dockSnapshot), transcript: false, generation: generation, using: service)
        XCTAssertTrue(dockAdmitted)
        let dockDelta = ConversationTailChanges(lens: "focus", fromSequence: 30, coverage: .init(throughSequence: 31), upserts: [], scopeChanges: [], removedKeys: [], invalidatedScopes: [], status: reply.status)
        let dockChanged = await model.applyTail(.changes(dockDelta), transcript: false, generation: generation, using: service)
        XCTAssertTrue(dockChanged)
        XCTAssertEqual(model.tailAfterSequence(transcript: false), 31)
        XCTAssertNil(model.tailAfterSequence(transcript: true))
        XCTAssertFalse(model.scopeReaderReady)
        let pageGeneration = model.beginTail()
        var pageSnapshot = try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(Self.snapshot([Self.item("fresh", kind: "agent_message", text: "Completed reply")]).utf8))
        pageSnapshot.coverage = .init(throughSequence: 31)
        let pageAdmitted = await model.applyTail(.snapshot(pageSnapshot), transcript: true, generation: pageGeneration, using: service)
        XCTAssertTrue(pageAdmitted)
        XCTAssertEqual(model.items.map(\.key), ["fresh"])
        XCTAssertEqual(model.tailAfterSequence(transcript: true), 31)
        XCTAssertTrue(model.scopeReaderReady)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ConversationRequestStub.self]
        let api = APIClient(baseURL: URL(string: "https://conversation.invalid")!, session: URLSession(configuration: configuration))
        for try await _ in api.conversationTail("conv_1", lens: .focus, transcript: false) {}
        let query = URLComponents(url: try XCTUnwrap(ConversationRequestStub.request?.url), resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query?.first { $0.name == "transcript" }?.value, "false")
    }

    func testOwnerReplacementClearsOldDisplayAndKeepsUnsentWorkAndRecordedDestination() async throws {
        for owner in [ConversationOwner.chief, .manager("manager"), .ticket("worker")] {
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: directory) }
            let store = LocalStore(url: directory.appendingPathComponent("replacement.store"))
            let api = APIClient(baseURL: URL(string: "https://replacement-\(UUID().uuidString).invalid")!)
            let server = LocalStore.server(api.baseURL)
            let message = ConversationSend(conversationId: "old", content: [.init(text: "Already recorded")],
                senderMessageId: "original", sentAtUnixMilliseconds: 123)
            try store.outgoing.enqueue(PendingWork(id: "original", created: .now, kind: .message, owner: owner, message: message), server: server)
            let model = ConversationModel(outgoingStore: store)
            model.selectConversation("old")
            model.restore(owner: owner, api: api, recoverConversationID: false)
            model.draft = "Keep these unsent words"
            model.attachments = [.init(piece: "file", data: "dGVzdA==", mediaType: "text/plain", fileName: "draft.txt")]
            let attachments = model.attachments
            let service = ConversationStub(fate: "started")
            await service.setSnapshot(Self.snapshot([Self.item("old-reply", kind: "agent_message", text: "Old conversation")]))
            await model.refresh(using: service, transcript: true)
            XCTAssertEqual(model.latestLine, "Old conversation")
            XCTAssertTrue(model.outgoing.contains { $0.id == "original" })

            // A cleared server link is a known destination change, not permission to
            // infer the current conversation from an earlier outgoing message.
            model.selectConversation(nil)
            model.restore(owner: owner, api: api, recoverConversationID: false)
            XCTAssertNil(model.conversationID)
            XCTAssertTrue(model.rows.isEmpty)
            XCTAssertNil(model.status)
            XCTAssertTrue(model.heldPrompts.isEmpty)
            XCTAssertFalse(model.hasUnread)
            model.syncOutbox()
            XCTAssertNil(model.conversationID)

            model.selectConversation("new")
            XCTAssertTrue(model.rows.isEmpty)
            XCTAssertEqual(model.draft, "Keep these unsent words")
            XCTAssertEqual(model.attachments, attachments)
            XCTAssertEqual(try store.outgoing.work(server: server, id: "original")?.message?.conversationId, "old")
            XCTAssertEqual(try store.outgoing.work(server: server, id: "original")?.message?.content.first?.text, "Already recorded")
            XCTAssertFalse(model.outgoing.contains { $0.id == "original" })
        }
    }

    func testReplacementRejectsLateResponseEvenAfterReturnToOriginalID() async throws {
        let model = ConversationModel(); model.selectConversation("old")
        let service = ConversationStub(fate: "started")
        await service.holdNextSnapshot()
        let oldRead = Task { await model.refresh(using: service, transcript: true) }
        while !(await service.snapshotHeld) { await Task.yield() }
        model.selectConversation(nil)
        model.selectConversation("new")
        model.selectConversation("old")
        await service.releaseSnapshot()
        await oldRead.value
        XCTAssertTrue(model.rows.isEmpty, "A response from the replaced reader cannot enter the new display")
        XCTAssertNil(model.status)
    }

    func testReaderGenerationRejectsAnOldResponseAfterReturningToSameLens() async throws {
        let model = ConversationModel(); model.conversationID = "conv_1"
        let service = ConversationStub(fate: "started")
        await service.holdNextSnapshot()
        let pending = Task { await model.refresh(using: service, transcript: true) }
        while !(await service.snapshotHeld) { await Task.yield() }
        model.lens = .full; model.lens = .focus
        await service.releaseSnapshot()
        await pending.value
        XCTAssertTrue(model.items.isEmpty)
    }

    func testHistoryBoundsKeepVisibleAnchorAndExposeOlderAndLatestRecovery() async throws {
        let model = ConversationModel(); model.conversationID = "conv_1"
        let service = ConversationStub(fate: "started")
        let records: [[String: Any]] = (1...320).map { index in
            var row = Self.item("row-\(index)", kind: "agent_message", text: "Words \(index)")
            row["order"] = ["sequence": index, "slot": 0]
            return row
        }
        await service.setSnapshot(Self.snapshot(records))
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.items.count, 300)
        XCTAssertEqual(model.older?.order.sequence, 21)
        model.setVisibleRows(["row-21"])
        let page = ConversationPage(items: try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(Self.snapshot(Array(records.prefix(20))).utf8)).page.items, older: nil)
        await service.setPage(page)
        await model.loadOlder(using: service)
        XCTAssertEqual(model.items.count, 300)
        XCTAssertTrue(model.items.contains { $0.id == "row-21" })
        XCTAssertEqual(model.items.first?.id, "row-1")
        XCTAssertTrue(model.hasNewerHistory)
        await model.returnToLatest(using: service)
        XCTAssertEqual(model.items.last?.id, "row-320")
        XCTAssertFalse(model.hasNewerHistory)
        let large = try APIClient.decoder().decode(ConversationBody.self, from: JSONSerialization.data(withJSONObject: ["kind": "agent_message", "content": [["piece": "text", "text": String(repeating: "x", count: 120 * 1024)]]]))
        let expanded = Dictionary((21...26).map { ("row-\($0)", large) }, uniquingKeysWith: { _, last in last })
        let deferred = model.items.map { item in
            guard expanded[item.key] != nil else { return item }
            return ConversationItem(kind: item.kind, key: item.key, senderMessageId: item.senderMessageId, turnKey: item.turnKey,
                sourceSequence: item.sourceSequence, body: .init(kind: "deferred", value: nil,
                    reference: .init(bodyId: item.key, version: "v", byteCount: 120 * 1024, mediaType: "application/json")), order: item.order)
        }
        let retained = ConversationRetention.retain(deferred, bodies: expanded, visible: ["row-26"], preferOlder: false)
        XCTAssertNotNil(retained.bodies["row-26"], "The visible expansion wins the shared body budget")
        XCTAssertLessThan(retained.bodies.count, expanded.count, "Decoded expansions share the inline-body budget")
        XCTAssertNotNil(retained.items.first { $0.id == "row-21" }?.body?.reference, "Evicted expanded content retains its server reference")
        let pressure = (1...5).map { index in
            ConversationItem(kind: "row", key: "inline-\(index)", senderMessageId: nil, turnKey: nil,
                sourceSequence: nil, body: .init(kind: "inline", value: large, reference: nil), order: nil)
        } + [deferred.first { $0.key == "row-26" }!]
        let visibleExpansion = ConversationRetention.retain(pressure, bodies: ["row-26": large], visible: ["row-26"], preferOlder: false)
        XCTAssertNotNil(visibleExpansion.bodies["row-26"], "Offscreen inline rows yield space to a visible expansion")
        XCTAssertTrue(visibleExpansion.removedOldest)
        XCTAssertEqual(visibleExpansion.items.last?.key, "row-26")
    }

    func testOneVisibleOversizedDeferredExpansionKeepsHistoryAndReleasesOffscreen() async throws {
        let service = ConversationStub(fate: "started")
        func deferred(_ key: String) -> [String: Any] {
            ["kind": "row", "key": key, "body": ["kind": "deferred", "reference":
                ["body_id": key, "version": "v1", "byte_count": 700_000, "media_type": "application/json"]]]
        }
        await service.setSnapshot(Self.snapshot([Self.item("earlier", kind: "agent_message", text: "Earlier words"), deferred("large"), deferred("second")]))
        let model = ConversationModel(); model.conversationID = "conv_1"
        await model.refresh(using: service, transcript: true)
        model.setVisibleRows(["large", "second"])
        let body = try APIClient.decoder().decode(ConversationBody.self, from: JSONSerialization.data(withJSONObject:
            ["kind": "agent_message", "content": [["piece": "text", "text": String(repeating: "x", count: 700_000)]]]))
        await service.setBody(body)
        await model.loadBody(try XCTUnwrap(model.items.first { $0.key == "large" }), using: service)
        XCTAssertEqual(model.expandedBodies["large"]?.text.count, 700_000)
        XCTAssertEqual(model.rows.first { $0.id == "large" }?.text.count, 700_000)
        XCTAssertEqual(model.items.map(\.key), ["earlier", "large", "second"])
        XCTAssertNil(model.error)
        await model.loadBody(try XCTUnwrap(model.items.last), using: service)
        XCTAssertEqual(Set(model.expandedBodies.keys), ["second"], "Only one oversized expansion remains decoded")
        XCTAssertEqual(model.expandedBodies["second"]?.text.count, 700_000)
        model.setVisibleRows(["earlier"])
        XCTAssertTrue(model.expandedBodies.isEmpty)
        XCTAssertEqual(model.rows.last?.text, "Read message")
        XCTAssertEqual(model.items.map(\.key), ["earlier", "large", "second"])
        XCTAssertNotNil(model.items.last?.body?.reference)
        let retained = ConversationRetention.retain(model.items, bodies: ["large": body, "second": body], visible: ["large", "second"], preferOlder: false, oversizedKey: "second")
        XCTAssertEqual(retained.items.map(\.key), ["earlier", "large", "second"])
        XCTAssertEqual(Set(retained.bodies.keys), ["second"])
        let overCap = ConversationRetention.retain(model.items, bodies: ["second": body], visible: ["second"], preferOlder: false, oversizedKey: "second") { key, _ in
            key.hasPrefix("expanded:") ? ConversationRetention.oversizedBodyLimit + 1 : 100
        }
        XCTAssertTrue(overCap.bodies.isEmpty, "The visible exception still enforces the 2 MB cap")
        XCTAssertEqual(overCap.items.map(\.key), ["earlier", "large", "second"])
    }

    func testLateOlderPageCannotBridgeAReplacedSnapshotAfterReconnect() async throws {
        let service = ConversationStub(fate: "started")
        let model = ConversationModel(); model.conversationID = "conv_1"
        await service.setSnapshot("{\"page\":" + Self.page + ",\"status\":{\"authority\":\"current\",\"rest\":{\"plan_progress\":{}}}}")
        await model.refresh(using: service, transcript: true)
        let oldRow = try APIClient.decoder().decode(ConversationItem.self, from: JSONSerialization.data(withJSONObject: Self.item("obsolete-older", kind: "agent_message", text: "Old range")))
        await service.setPage(ConversationPage(items: [oldRow], older: nil))
        await service.holdPage()
        let read = Task { await model.loadOlder(using: service) }
        while !(await service.pageHeld) { await Task.yield() }
        let previous = model.beginTail(); model.endTail(generation: previous)
        let generation = model.beginTail()
        let currentRow = try APIClient.decoder().decode(ConversationItem.self, from: JSONSerialization.data(withJSONObject: Self.item("new-window", kind: "agent_message", text: "Current range")))
        let cursor = ConversationCursor(projectionVersion: 4, lens: "focus", scope: .init(kind: "timeline", key: nil), order: .init(sequence: 100, slot: 0))
        let status = try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(Self.snapshot([]).utf8)).status
        let applied = await model.applyTail(.snapshot(.init(page: .init(items: [currentRow], older: cursor), status: status)), generation: generation, using: service)
        XCTAssertTrue(applied)
        await service.releasePage(); await read.value
        XCTAssertEqual(model.items.map(\.key), ["new-window"])
        XCTAssertEqual(model.older?.order.sequence, 100, "A discarded page cannot overwrite the new window cursor")
        XCTAssertFalse(model.loadingOlder)
    }

    func testTailReconnectPreservesPendingHistoryAndBodyReads() async throws {
        let service = ConversationStub(fate: "started")
        let model = ConversationModel(); model.conversationID = "conv_1"
        let snapshot = try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(("{\"page\":" + Self.page + ",\"status\":{\"authority\":\"current\",\"rest\":{\"plan_progress\":{}}}}").utf8))
        // The fixture decoder uses snake case, as the server does.
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        await service.setSnapshot(String(decoding: try encoder.encode(snapshot), as: UTF8.self))
        await model.refresh(using: service, transcript: true)
        await service.setPage(ConversationPage(items: [try APIClient.decoder().decode(ConversationItem.self, from: JSONSerialization.data(withJSONObject: Self.item("older", kind: "agent_message", text: "Older words")))], older: nil))
        await service.holdPage()
        let pageRead = Task { await model.loadOlder(using: service) }
        while !(await service.pageHeld) { await Task.yield() }
        let old = model.beginTail(); model.endTail(generation: old)
        _ = model.beginTail()
        await service.releasePage()
        await pageRead.value
        XCTAssertEqual(model.items.first?.key, "older")
        let target = try XCTUnwrap(model.items.first { $0.key == "row-2" })
        let body = try APIClient.decoder().decode(ConversationBody.self, from: Data(#"{"kind":"agent_message","content":[{"piece":"text","text":"Expanded words"}]}"#.utf8))
        await service.setBody(body); await service.holdBody()
        let bodyRead = Task { await model.loadBody(target, using: service) }
        while !(await service.bodyHeld) { await Task.yield() }
        let previous = model.beginTail(); model.endTail(generation: previous)
        _ = model.beginTail()
        await service.releaseBody()
        await bodyRead.value
        XCTAssertEqual(model.expandedBodies[target.key]?.text, "Expanded words")
        XCTAssertNil(model.error)
    }

    func testHistoryScopesEvictInactiveLeastRecentScopeAfterTwenty() {
        var history = ConversationHistoryScopes()
        let timeline = ConversationCursor.Scope(kind: "timeline", key: nil)
        XCTAssertTrue(history.admit(timeline, keys: ["latest"], visibleKeys: ["visible"], active: timeline).isEmpty)
        var discarded: Set<String> = []
        for index in 1...20 {
            discarded.formUnion(history.admit(.init(kind: "turn", key: String(index)), keys: [index == 1 ? "visible" : "row-\(index)"], visibleKeys: ["visible"], active: timeline))
        }
        XCTAssertEqual(history.count, 20)
        XCTAssertEqual(discarded, ["row-2"])
        XCTAssertTrue(history.scopes.contains(timeline))
        XCTAssertTrue(history.scopes.contains(.init(kind: "turn", key: "1")))
        _ = history.admit(.init(kind: "turn", key: "3"), keys: ["row-3"], visibleKeys: ["visible"], active: timeline)
        let next = history.admit(.init(kind: "work_group", key: "new"), keys: ["new"], visibleKeys: ["visible"], active: timeline)
        XCTAssertEqual(next, ["row-4"])
        XCTAssertEqual(history.count, 20)
    }

    func testRetentionHostCostAtTheReaderBudget() throws {
        func measure(_ label: String, count: Int, characters: Int) throws {
            let body = try APIClient.decoder().decode(ConversationBody.self, from: JSONSerialization.data(withJSONObject:
                ["kind": "agent_message", "content": [["piece": "text", "text": String(repeating: "x", count: characters)]]]))
            let rows = (0..<count).map { index in ConversationItem(kind: "row", key: "cost-\(index)", senderMessageId: nil,
                turnKey: nil, sourceSequence: index, body: .init(kind: "inline", value: body, reference: nil)) }
            let encoder = JSONEncoder()
            var cold: [Double] = [], warm: [Double] = []
            for _ in 0..<30 {
                var sizes: [String: Int] = [:]
                let start = ContinuousClock.now
                _ = ConversationRetention.retain(rows, bodies: [:], visible: ["cost-0"], preferOlder: false) { key, value in
                    let count = (try? encoder.encode(value).count) ?? 0; sizes[key] = count; return count
                }
                cold.append(Self.milliseconds(start.duration(to: .now)))
                let next = ContinuousClock.now
                _ = ConversationRetention.retain(rows, bodies: [:], visible: ["cost-0"], preferOlder: false) { key, _ in sizes[key] ?? 0 }
                warm.append(Self.milliseconds(next.duration(to: .now)))
            }
            func report(_ phase: String, _ values: [Double]) {
                let sorted = values.sorted()
                print("RETENTION_HOST_COST \(label) \(phase) samples=\(sorted.count) p50_ms=\(sorted[15]) p95_ms=\(sorted[28]) max_ms=\(sorted[29])")
            }
            report("cold-admission", cold); report("unchanged-tail", warm)
        }
        try measure("300-items-480KB", count: 300, characters: 1_600)
        try measure("4-large-bodies-520KB", count: 4, characters: 130_000)
    }
    // The old projection discarded every bodyless head and had no scoped reads.
    // These cases cover the wire-to-display boundary in the existing Foundation host.
    func testFocusFoldExpansionKeepsToolAndNarrationRowsOut() async throws {
        let fixture = Self.structuredProjection()
        let service = ConversationStub(fate: "started")
        await service.setSnapshot(Self.snapshot(fixture.timeline))
        try await service.setScope(.init(kind: "turn", key: "turn"), items: fixture.turn)
        let model = ConversationModel(); model.conversationID = "conv_1"
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.rows.map(\.id), ["turn", "latest"])
        let head = try XCTUnwrap(model.items.first { $0.key == "turn" })
        await model.toggleScope(head, using: service)
        XCTAssertEqual(model.rows.map(\.id), ["turn", "earlier", "latest"])
        XCTAssertTrue(model.rows.last?.breakBefore == true)
        XCTAssertFalse(model.rows.contains { $0.presentation == .tool || $0.presentation == .workGroup || $0.presentation == .narration })
        await model.toggleScope(head, using: service)
        XCTAssertEqual(model.rows.map(\.id), ["turn", "latest"])
    }

    func testFullScopeSurvivesReconnectAndReceivesScopedTailChanges() async throws {
        let fixture = Self.structuredProjection()
        let service = ConversationStub(fate: "started")
        try await service.setScope(.init(kind: "turn", key: "turn"), items: fixture.turn)
        try await service.setScope(.init(kind: "work_group", key: "group"), items: fixture.calls)
        let model = ConversationModel(); model.conversationID = "conv_1"
        await service.setSnapshot(Self.snapshot(fixture.timeline))
        await model.refresh(using: service, transcript: true)
        let previousLensHead = try XCTUnwrap(model.items.first { $0.key == "turn" })
        model.lens = .full
        // SwiftUI can reuse the Focus head and demand its scope before Full's first
        // snapshot. Admitting it now would cancel that snapshot and then erase it.
        XCTAssertFalse(model.scopeReaderReady)
        await model.loadScope(previousLensHead, using: service)
        XCTAssertFalse(model.retainedRanges.contains { $0.scope.kind == "turn" })
        XCTAssertFalse(model.items.contains { $0.key == "group" })
        var snapshot = try APIClient.decoder().decode(ConversationSnapshot.self, from: Data(Self.snapshot(fixture.timeline).utf8))
        snapshot.coverage = .init(throughSequence: 8)
        let admitted = await model.applyTail(.snapshot(snapshot), using: service)
        XCTAssertTrue(admitted)
        XCTAssertTrue(model.scopeReaderReady, "The visible head retries when the matching live snapshot is admitted")
        let head = try XCTUnwrap(model.items.first { $0.key == "turn" })
        XCTAssertEqual(head.durationSeconds, 168, "The renderer needs the settled head's real duration")
        await model.loadScope(head, using: service)
        let group = try XCTUnwrap(model.items.first { $0.key == "group" })
        XCTAssertEqual(group.entryCount, 2, "The renderer needs the run's real call count")
        XCTAssertFalse(model.rows.contains { $0.presentation == .tool }, "A multi-call run starts closed")
        await model.toggleScope(group, using: service)
        XCTAssertEqual(model.rows.filter { $0.presentation == .tool }.count, 2)
        XCTAssertTrue(model.rows.contains { $0.presentation == .narration })
        XCTAssertEqual(Set(model.retainedRanges.map(\.scope.kind)), ["timeline", "turn", "work_group"])
        // A resumed stream must send its admitted cursor along with retained scopes.
        // Otherwise the real server replaces them with a timeline-only subscription.
        let previousGeneration = model.beginTail(resuming: true)
        model.endTail(generation: previousGeneration)
        let after = try XCTUnwrap(model.tailAfterSequence)
        XCTAssertEqual(after, 8)
        model.beginTail(resuming: true)
        let api = APIClient(baseURL: URL(string: "https://conversation.invalid")!)
        let request = try api.conversationTailRequest("conv_1", lens: .full, transcript: true, ranges: model.retainedRanges, after: after)
        let query = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false)?.queryItems)
        XCTAssertEqual(query.first { $0.name == "after" }?.value, "8")
        let encodedRanges = try XCTUnwrap(query.first { $0.name == "ranges" }?.value)
        let ranges = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(encodedRanges.utf8)) as? [[String: Any]])
        XCTAssertEqual(Set(ranges.compactMap { ($0["scope"] as? [String: Any])?["kind"] as? String }), ["timeline", "turn", "work_group"])
        let timelineScope = try XCTUnwrap(ranges.compactMap { $0["scope"] as? [String: Any] }.first { $0["kind"] as? String == "timeline" })
        XCTAssertTrue(timelineScope["key"] is NSNull, "The server requires an explicit null key for timeline scope")
        XCTAssertEqual(model.rows.filter { $0.presentation == .tool }.count, 2)
        var changed = fixture.calls[0]
        changed["body"] = ["kind": "inline", "value": ["kind": "tool_call", "title": "Read files", "status": "completed", "detail": "Final output"]]
        let updated = try APIClient.decoder().decode(ConversationItem.self, from: JSONSerialization.data(withJSONObject: changed))
        let delta = ConversationTailChanges(lens: "full", fromSequence: 8, coverage: .init(throughSequence: 9), upserts: [updated],
            scopeChanges: [.init(scope: .init(kind: "work_group", key: "group"), itemKeys: [updated.key], removedKeys: ["call-2"])],
            removedKeys: [], invalidatedScopes: [], status: snapshot.status)
        let applied = await model.applyTail(.changes(delta), using: service)
        XCTAssertTrue(applied)
        XCTAssertEqual(model.rows.first { $0.id == "call-1" }?.body?.detail, "Final output")
        XCTAssertFalse(model.rows.contains { $0.id == "call-2" }, "A scoped removal cannot remain frozen on screen")
    }

    func testTypedMediaKeepsImageOnlyRowsAndOutgoingThumbnailThroughReceipt() async throws {
        let content: [[String: Any]] = [["piece": "image", "stored_file_id": "image-1", "media_type": "image/jpeg", "file_name": "photo.jpeg", "width": 800, "height": 600],
            ["piece": "text", "text": "The updated screen."],
            ["piece": "file", "stored_file_id": "movie-1", "media_type": "video/quicktime", "file_name": "walkthrough.mov", "byte_count": 123]]
        let owner: [String: Any] = ["kind": "row", "key": "owner", "sender_message_id": "local", "body": ["kind": "inline", "value": ["kind": "prompt", "content": [content[0]]]]]
        let agent: [String: Any] = ["kind": "row", "key": "agent", "body": ["kind": "inline", "value": ["kind": "agent_message", "content": content]]]
        let model = ConversationModel(); model.conversationID = "conv_1"
        let local = ConversationSend.Piece(piece: "image", data: "local-image-bytes", mediaType: "image/jpeg", fileName: "photo.jpeg")
        model.outgoing = [.init(id: "local", text: "", attachments: [local])]
        let service = ConversationStub(fate: "started")
        await service.setSnapshot(Self.snapshot([owner, agent]))
        await model.refresh(using: service, transcript: true)
        XCTAssertEqual(model.rows.count, 2, "An image-only message is real content")
        let received = try XCTUnwrap(model.rows.first { $0.id == "local" })
        XCTAssertEqual(received.pieces.first?.storedFileId, "image-1")
        XCTAssertEqual(received.attachments, [local], "Keep the local preview until stored media loads")
        XCTAssertTrue(model.outgoing.isEmpty, "The fallback must not duplicate the received message")
        let reply = try XCTUnwrap(model.rows.first { $0.id == "agent" })
        XCTAssertEqual(reply.pieces.map(\.piece), ["image", "text", "file"])
        XCTAssertTrue(reply.pieces[2].isVideo)
        XCTAssertEqual(reply.pieces[0].width, 800)
        XCTAssertEqual(reply.text, "The updated screen.", "Filenames must not become message prose")
    }

    func testConversationMediaURLKeepsEncodedConversationAndFileSegments() throws {
        let api = APIClient(baseURL: URL(string: "https://home.example")!)
        let url = try api.conversationFileURL("conversation/one", storedFileID: "file?#two")
        XCTAssertEqual(url.absoluteString, "https://home.example/api/conversation/conversations/conversation%2Fone/files/file%3F%23two")
        XCTAssertNil(url.query)
        XCTAssertNil(url.fragment)
    }

    private static func structuredProjection() -> (timeline: [[String: Any]], turn: [[String: Any]], calls: [[String: Any]]) {
        func row(_ key: String, sequence: Int, kind: String, body: [String: Any], group: String? = nil) -> [String: Any] {
            var result: [String: Any] = ["kind": "row", "key": key, "row_kind": kind, "turn_key": "turn", "order": ["sequence": sequence, "slot": 1], "body": ["kind": "inline", "value": body]]
            if kind == "agent_message" { result["fold_rule"] = "earlier_message" }
            if let group { result["work_group_key"] = group }
            return result
        }
        let head: [String: Any] = ["kind": "turn", "key": "turn", "order": ["sequence": 1, "slot": 0], "settled": true, "stopped": false, "ending": "completed", "duration_seconds": 168, "folded_message_count": 1, "tool_call_count": 2, "latest_message_key": "latest"]
        let earlier = row("earlier", sequence: 2, kind: "agent_message", body: ["kind": "agent_message", "to_owner": true, "content": [["piece": "text", "text": "First result"]]])
        let narration = row("narration", sequence: 3, kind: "agent_message", body: ["kind": "agent_message", "to_owner": false, "content": [["piece": "text", "text": "Check the files"]]])
        let group: [String: Any] = ["kind": "work_group", "key": "group", "turn_key": "turn", "order": ["sequence": 4, "slot": 0], "entry_count": 2, "settled": true]
        let first = row("call-1", sequence: 4, kind: "tool_call", body: ["kind": "tool_call", "title": "Read files", "status": "running"], group: "group")
        let second = row("call-2", sequence: 5, kind: "tool_call", body: ["kind": "tool_call", "title": "Check files", "status": "completed"], group: "group")
        let latest = row("latest", sequence: 8, kind: "agent_message", body: ["kind": "agent_message", "to_owner": true, "content": [["piece": "text", "text": "Final result"]]])
        return ([head, earlier, latest], [narration, group], [first, second])
    }

    private static func milliseconds(_ duration: Duration) -> Double {
        Double(duration.components.seconds) * 1_000 + Double(duration.components.attoseconds) / 1_000_000_000_000_000
    }

    private static func item(_ id: String, kind: String, text: String, sender: String? = nil) -> [String: Any] {
        var item: [String: Any] = ["kind": "row", "key": id, "body": ["kind": "inline", "value": ["kind": kind, "content": [["piece": "text", "text": text]]]]]
        if let sender { item["sender_message_id"] = sender }
        return item
    }
    private static func snapshot(_ items: [[String: Any]], live: [String: Any]? = nil, ownerSequence: Int = 0, rest: [String: Any] = [:]) -> String {
        var facts: [String: Any] = ["plan_progress": [:], "latest_owner_message_sequence": ownerSequence]
        facts.merge(rest) { _, current in current }
        var status: [String: Any] = ["authority": "current", "turn_running": live != nil, "rest": facts]
        if let live { status["live_output"] = live }
        let object: [String: Any] = ["page": ["items": items, "older": NSNull(), "projection_version": 4], "status": status]
        return String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
    }

    static let page = #"{"items":[{"kind":"row","key":"row-1","sender_message_id":"message-1","body":{"kind":"inline","value":{"kind":"prompt","content":[{"piece":"text","text":"Hello"}]}}},{"kind":"row","key":"row-2","body":{"kind":"deferred","reference":{"body_id":"long","version":"v1","byte_count":50000,"media_type":"application/json"}}}],"older":{"projection_version":1,"lens":"focus","scope":{"kind":"timeline","key":null},"order":{"sequence":7,"slot":0}}}"#
}

private actor ConversationStub: ConversationServing {
    var readRequests: [ConversationReadBoundary] = []
    func markConversationRead(_ id: String, through: Int) async throws -> Int {
        readRequests.append(.init(conversationID: id, throughSequence: through))
        if let failure { throw URLError(failure) }
        return through
    }
    var fate: String
    let failure: URLError.Code?
    var lastMessage: ConversationSend?
    var calls = 0
    var senderID = ""
    var snapshot: String?
    var page: ConversationPage?
    var body: ConversationBody?
    var pausePage = false, pauseBody = false
    var pageContinuation: CheckedContinuation<Void, Never>?
    var bodyContinuation: CheckedContinuation<Void, Never>?
    var pageHeld: Bool { pageContinuation != nil }
    var bodyHeld: Bool { bodyContinuation != nil }
    func holdPage() { pausePage = true }
    func releasePage() { pageContinuation?.resume(); pageContinuation = nil }
    func holdBody() { pauseBody = true }
    func releaseBody() { bodyContinuation?.resume(); bodyContinuation = nil }
    func setBody(_ value: ConversationBody) { body = value }
    func setPage(_ value: ConversationPage) { page = value }
    var scopes: [ConversationCursor.Scope: ConversationPage] = [:]
    func setScope(_ scope: ConversationCursor.Scope, items: [[String: Any]]) throws {
        let data = try JSONSerialization.data(withJSONObject: ["items": items, "older": NSNull()])
        scopes[scope] = try APIClient.decoder().decode(ConversationPage.self, from: data)
    }
    func conversationScope(_ id: String, lens: ConversationLens, scope: ConversationCursor.Scope) async throws -> ConversationPage {
        guard let page = scopes[scope] else { throw URLError(.unsupportedURL) }
        return page
    }
    var receipt: String?
    var liveReads = 0
    var requestedLenses: [ConversationLens] = []
    var holdNext = false
    var held: CheckedContinuation<Void, Never>?
    var snapshotHeld: Bool { held != nil }
    func holdNextSnapshot() { holdNext = true }
    func releaseSnapshot() { held?.resume(); held = nil }
    func conversationLiveText(_ id: String, reference: ConversationBodyReference) async throws -> String { liveReads += 1; return "Live words" }
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
        requestedLenses.append(lens)
        if holdNext { holdNext = false; await withCheckedContinuation { held = $0 } }
        let json = #"{"page":{"items":[{"kind":"row","key":"row-1","sender_message_id":"SENDER","body":{"kind":"inline","value":{"kind":"prompt","content":[{"piece":"text","text":"Hello"}]}}}],"older":null},"status":{"authority":"current","turn_running":true,"rest":{"latest_happened":null,"latest_tool":{"kind":"tool_call","title":"Reading the code"},"permission_title_excerpt":null,"plan_progress":{"current_text_excerpt":null}}}}"#.replacingOccurrences(of: "SENDER", with: senderID)
        return try APIClient.decoder().decode(ConversationSnapshot.self, from: Data((snapshot ?? json).utf8))
    }
    func conversationPage(_ id: String, cursor: ConversationCursor) async throws -> ConversationPage {
        if pausePage { pausePage = false; await withCheckedContinuation { pageContinuation = $0 } }
        if let page { return page }; throw URLError(.unsupportedURL)
    }
    func conversationBody(_ id: String, reference: ConversationBodyReference) async throws -> ConversationBody {
        if pauseBody { pauseBody = false; await withCheckedContinuation { bodyContinuation = $0 } }
        if let body { return body }; throw URLError(.unsupportedURL)
    }
}
private final class ConversationRequestStub: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var request: URLRequest?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.request = request
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        let body = request.url?.path.hasSuffix("/owner-read") == true ? #"{"owner_read_through_sequence":8}"# : #"{"conversation_id":"conv_1","fate":"queued","queue_position":1}"#
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

/// Blocks only the first real image operation; the main actor can keep editing and sending guards can run.
final class PhotoPreparationProbe: @unchecked Sendable {
    let started = XCTestExpectation(description: "Detached image operation started")
    private let lock = NSLock()
    private let gate = DispatchSemaphore(value: 0)
    private var observations = 0
    private var mainThread = false
    var count: Int { lock.withLock { observations } }
    var usedMainThread: Bool { lock.withLock { mainThread } }
    func observe() {
        let first = lock.withLock {
            observations += 1
            mainThread = mainThread || Thread.isMainThread
            return observations == 1
        }
        if first {
            started.fulfill()
            if !Thread.isMainThread { gate.wait() }
        }
    }
    func release() { gate.signal() }
}
