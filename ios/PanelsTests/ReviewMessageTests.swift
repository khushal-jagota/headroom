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
        var work = PendingWork(id: "saved-reply", created: .now, kind: .message, state: .failed, owner: .chief, message: message)
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
            XCTAssertEqual(decision.attempt?.state, .failed)
            XCTAssertFalse(decision.accepted)
        }
        XCTAssertEqual(reads, 1)
        work.state = .sent
        try outbox.save(work, server: server)
        refresh(server); decision.refreshAttempt()
        XCTAssertEqual(reads, 2)
        XCTAssertTrue(decision.accepted)
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
        XCTAssertTrue(marked, "Mark read is saved and counts at once")
        await Outbox.shared.drain(api: api)
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
        await Outbox.shared.drain(api: api)
        XCTAssertEqual(ReviewMessageProtocol.state.bodies.last?["through_sequence"] as? Int, 67)
    }
    func testRefusedReplyKeepsDraftAttachmentAndRetryBoundaryAcrossRecreation() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        ReviewMessageProtocol.state.refuse = true
        ReviewMessageProtocol.state.postStatus = 422
        defer { clean(api) }
        let decision = ReviewMessageDecision()
        await decision.load(item: item(), api: api)
        decision.composer.draft = "Keep this reply"
        #if SWIFT_PACKAGE
        let fixture = Bundle.module.url(forResource: "photo", withExtension: "heic", subdirectory: "Fixtures")
        #else
        let fixture = Bundle(for: Self.self).url(forResource: "photo", withExtension: "heic")
        #endif
        let heic = try Data(contentsOf: XCTUnwrap(fixture)).base64EncodedString()
        decision.composer.attachments = [.init(piece: "image", data: heic, mediaType: "image/heic", fileName: "note.heic")]
        let saved = await decision.send()
        XCTAssertTrue(saved, "The reply counts at once")
        await Outbox.shared.drain(api: api)
        decision.refreshAttempt()
        XCTAssertEqual(decision.attempt?.state, .failed)
        XCTAssertTrue(decision.attempt?.note.lowercased().contains("photo") == true)
        XCTAssertEqual(decision.attempt?.message?.content.last?.mediaType, "image/jpeg")
        let preparedPhoto = decision.composer.attachments.first
        let originalID = try XCTUnwrap(decision.attemptID)
        ReviewMessageProtocol.state.newMessage = true
        let restored = ReviewMessageDecision()
        await restored.load(item: item(), api: api)
        XCTAssertEqual(restored.composer.draft, "Keep this reply")
        XCTAssertEqual(restored.composer.attachments.first, preparedPhoto)
        restored.composer.draft = "Corrected reply"
        restored.composer.attachments = [.init(piece: "file", data: "ZGVm", mediaType: "text/plain", fileName: "corrected.txt")]
        let edited = ReviewMessageDecision()
        await edited.load(item: item(), api: api)
        XCTAssertEqual(edited.composer.draft, "Corrected reply")
        XCTAssertEqual(edited.composer.attachments.first?.fileName, "corrected.txt")
        ReviewMessageProtocol.state.refuse = false
        ReviewMessageProtocol.state.postStatus = 200
        ReviewMessageProtocol.state.newMessage = true
        _ = await restored.send()
        await Outbox.shared.drain(api: api)
        restored.refreshAttempt()
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
    func testLostReplyResendsWithItsOwnIdentityAndBoundary() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        ReviewMessageProtocol.state.loseResponse = true
        defer { clean(api) }
        let decision = ReviewMessageDecision()
        await decision.load(item: item(), api: api)
        decision.composer.draft = "Queued once"
        _ = await decision.send()
        await Outbox.shared.drain(api: api)
        decision.refreshAttempt()
        XCTAssertEqual(decision.attempt?.state, .waiting, "A lost response is not shown")
        XCTAssertTrue(decision.accepted)
        let original = try XCTUnwrap(decision.attemptID)
        let restored = ReviewMessageDecision()
        await restored.load(item: item(), api: api)
        XCTAssertEqual(restored.attemptID, original)
        // A relaunch has no foreground retry delay.
        await Outbox(store: .shared).drain(api: api)
        restored.refreshAttempt()
        XCTAssertEqual(restored.attempt?.state, .sent)
        let posts = ReviewMessageProtocol.state.bodies.filter { $0["sender_message_id"] != nil }
        XCTAssertEqual(posts.map { $0["sender_message_id"] as? String }, [original, original])
        XCTAssertEqual(posts.last?["owner_read_through_sequence"] as? Int, 66)
    }
    /// Risk: Try Again on a reply that gave up sends the card's text under a new ID, so a
    /// reply the server already saved lands twice. It must go again as it was, under its own ID.
    func testGivenUpReplyTryAgainKeepsItsIdentity() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        defer { clean(api) }
        let decision = ReviewMessageDecision()
        await decision.load(item: item(), api: api)
        decision.composer.draft = "Maybe it landed"
        ReviewMessageProtocol.state.garble = true
        _ = await decision.send()
        await Outbox.shared.drain(api: api)
        decision.refreshAttempt()
        let original = try XCTUnwrap(decision.attempt)
        XCTAssertEqual(original.state, .failed)
        XCTAssertEqual(try LocalStore.shared.outgoing.summary(server: LocalStore.server(api.baseURL), id: original.id)?.gaveUp, true)
        ReviewMessageProtocol.state.garble = false
        decision.composer.draft = "Edited after it gave up"
        let retried = await decision.send()
        XCTAssertTrue(retried)
        await Outbox.shared.drain(api: api)
        decision.refreshAttempt()
        XCTAssertEqual(decision.attempt?.id, original.id)
        XCTAssertEqual(decision.attempt?.state, .sent)
        let posts = ReviewMessageProtocol.state.bodies.filter { $0["sender_message_id"] != nil }
        XCTAssertEqual(posts.map { $0["sender_message_id"] as? String }, [original.id, original.id])
        XCTAssertEqual((posts.last?["content"] as? [[String: Any]])?.first?["text"] as? String, "Maybe it landed")
        XCTAssertEqual(Outbox.shared.entries(LocalStore.server(api.baseURL), conversationID: "c").count, 1)
    }
    /// Risk: offline, a card whose detail this run never read shows a spinner forever, so
    /// Mark read and an ask answer cannot be saved. The phone's saved copy of the last read
    /// stands in; the actions are saved and land when the server answers.
    func testOfflineCardsActOnTheirSavedCopy() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        ReviewMessageProtocol.state.ask = true
        defer { clean(api) }
        let ask = ReviewItem(reviewItemType: "ask", title: "Chief", waitingSince: 5, conversationId: "c", ownerKind: "chief", ownerId: "chief")
        // An earlier read on this phone, such as the read-ahead before the connection dropped.
        await ReviewMessageDecision().load(item: item(), api: api)
        await ReviewAskDecision().load(item: ask, api: api, viewFreshAt: Date())
        await LocalStore.shared.flushCache()
        ReviewMessageProtocol.state.offline = true
        let message = ReviewMessageDecision()
        await message.load(item: item(), api: api, shownAt: .now)
        XCTAssertNil(message.error)
        XCTAssertEqual(message.through, 66)
        let marked = await message.markRead()
        XCTAssertTrue(marked)
        let answer = ReviewAskDecision()
        await answer.load(item: ask, api: api, viewFreshAt: Date())
        XCTAssertTrue(answer.available)
        let question = try XCTUnwrap(answer.userInput?.questions.first)
        answer.toggle("Blue", in: question)
        XCTAssertTrue(answer.sendAnswer(item: ask, api: api))
        ReviewMessageProtocol.state.offline = false
        // Saving each one woke the outbox; wait for both to land.
        let deadline = ContinuousClock.now + .seconds(5)
        while ReviewMessageProtocol.state.bodies.count < 2, ContinuousClock.now < deadline {
            await Outbox.shared.drain(api: api)
            try await Task.sleep(for: .milliseconds(20))
        }
        let bodies = ReviewMessageProtocol.state.bodies
        XCTAssertEqual(bodies.first { $0["through_sequence"] != nil }?["through_sequence"] as? Int, 66)
        let sent = try XCTUnwrap(bodies.first { $0["request_id"] != nil })
        XCTAssertEqual(sent["request_id"] as? String, "request-1")
        XCTAssertEqual(sent["answers"] as? [String: [String: [String]]], ["colour": ["answers": ["Blue"]]])
    }
    /// Risk: offline, a card this phone never read has no saved copy, so it waits and
    /// cannot act. The saved view's bound and ask stand in: Mark read reaches only the
    /// newest message the view knew, so a later one stays unread, and the ask is answered
    /// with the view's own IDs. A run the card did read always wins over the bound.
    func testOfflineCardsNeverReadActOnTheSavedView() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        defer { clean(api) }
        ReviewMessageProtocol.state.offline = true
        // The server has message 67 since the view was saved with 66 as its newest.
        ReviewMessageProtocol.state.newMessage = true
        var unread = item(); unread.latestOwnerMessageSequence = 66
        let message = ReviewMessageDecision()
        await message.load(item: unread, api: api, shownAt: .now)
        XCTAssertNil(message.error)
        XCTAssertFalse(message.loaded)
        XCTAssertEqual(message.markReadThrough, 66)
        let marked = await message.markRead()
        XCTAssertTrue(marked)
        var ask = ReviewItem(reviewItemType: "ask", title: "Chief", waitingSince: 5, conversationId: "c", ownerKind: "chief", ownerId: "chief")
        ask.pendingAsk = ReviewAskState(isRunning: true, pendingPermissionAsk: .init(askId: "ask-1", title: "Run ls", detail: "ls", options: [
            .init(optionId: "allow", label: "Allow", optionKind: "allow_once"), .init(optionId: "reject", label: "Reject", optionKind: "reject_once")]), pendingUserInput: nil)
        let answer = ReviewAskDecision()
        await answer.load(item: ask, api: api, viewFreshAt: Date())
        XCTAssertTrue(answer.available)
        XCTAssertTrue(answer.answerPermission(try XCTUnwrap(answer.approveOption), item: ask, api: api))
        ReviewMessageProtocol.state.offline = false
        let deadline = ContinuousClock.now + .seconds(5)
        while ReviewMessageProtocol.state.bodies.count < 2, ContinuousClock.now < deadline {
            await Outbox.shared.drain(api: api)
            try await Task.sleep(for: .milliseconds(20))
        }
        let bodies = ReviewMessageProtocol.state.bodies
        XCTAssertEqual(bodies.filter { $0["through_sequence"] != nil }.map { $0["through_sequence"] as? Int }, [66])
        let sent = try XCTUnwrap(bodies.first { $0["ask_id"] != nil })
        XCTAssertEqual(sent["ask_id"] as? String, "ask-1")
        XCTAssertEqual(sent["option_id"] as? String, "allow")
        // Once read, the displayed run bounds Mark read, even below a higher window bound.
        unread.latestOwnerMessageSequence = 70
        ReviewMessageProtocol.state.newMessage = false
        let read = ReviewMessageDecision()
        await read.load(item: unread, api: api, shownAt: .now)
        XCTAssertEqual(read.markReadThrough, 66)
    }
    /// Risk: the view's ask is older than a read, for example one answered on the web while
    /// another now waits, and replaces it, so the card answers a dead ask. The view's ask
    /// stands in only for a read made before it was raised, or for no read at all.
    func testWindowAskStandsInOnlyForAnOlderRead() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        ReviewMessageProtocol.state.ask = true
        defer { clean(api) }
        func ask(raisedAt: Double) -> ReviewItem {
            var item = ReviewItem(reviewItemType: "ask", title: "Chief", waitingSince: raisedAt, conversationId: "c", ownerKind: "chief", ownerId: "chief")
            item.pendingAsk = ReviewAskState(isRunning: true, pendingPermissionAsk: .init(askId: "ask-window", title: "Run ls", detail: nil, options: [
                .init(optionId: "allow", label: "Allow", optionKind: "allow_once")]), pendingUserInput: nil)
            return item
        }
        let stale = ask(raisedAt: Date().timeIntervalSince1970 - 3600)
        // A live read after the view's ask was raised wins, online and offline after it.
        let live = ReviewAskDecision()
        await live.load(item: stale, api: api, viewFreshAt: Date())
        XCTAssertEqual(live.userInput?.requestId, "request-1")
        await LocalStore.shared.flushCache()
        ReviewMessageProtocol.state.offline = true
        await live.load(item: stale, api: api, viewFreshAt: Date())
        XCTAssertEqual(live.userInput?.requestId, "request-1")
        // A saved read newer than the view's ask wins too.
        let saved = ReviewAskDecision()
        await saved.load(item: stale, api: api, viewFreshAt: Date())
        XCTAssertEqual(saved.userInput?.requestId, "request-1")
        // An ask raised after the saved read replaces it.
        let newer = ReviewAskDecision()
        await newer.load(item: ask(raisedAt: Date().timeIntervalSince1970 + 60), api: api, viewFreshAt: Date())
        XCTAssertEqual(newer.permission?.askId, "ask-window")
    }
    /// Risk: a view kept for hours offline, in memory or saved, still offers its ask after
    /// the turn moved on. The view's ask stands in only while its last fresh snapshot is
    /// under an hour old, both when the card takes it and when it is answered later.
    func testWindowAskWaitsOnceTheViewIsAnHourOld() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        ReviewMessageProtocol.state.offline = true
        defer { clean(api) }
        var item = ReviewItem(reviewItemType: "ask", title: "Chief", waitingSince: 5, conversationId: "c", ownerKind: "chief", ownerId: "chief")
        item.pendingAsk = ReviewAskState(isRunning: true, pendingPermissionAsk: .init(askId: "ask-window", title: "Run ls", detail: nil, options: [
            .init(optionId: "allow", label: "Allow", optionKind: "allow_once")]), pendingUserInput: nil)
        let now = Date()
        let old = ReviewAskDecision()
        await old.load(item: item, api: api, viewFreshAt: now.addingTimeInterval(-2 * 60 * 60), now: now)
        XCTAssertNil(old.permission)
        XCTAssertFalse(old.available)
        let recent = ReviewAskDecision()
        await recent.load(item: item, api: api, viewFreshAt: now.addingTimeInterval(-10 * 60), now: now)
        XCTAssertEqual(recent.permission?.askId, "ask-window")
        XCTAssertTrue(recent.available)
        // The same card shown again two hours later, with no fresh view since.
        await recent.load(item: item, api: api, viewFreshAt: now.addingTimeInterval(-10 * 60), now: now.addingTimeInterval(2 * 60 * 60))
        XCTAssertNil(recent.permission)
        XCTAssertFalse(recent.loaded)
        // A tap past the hour on a card still showing the view's ask puts it back to waiting.
        let shown = ReviewAskDecision()
        await shown.load(item: item, api: api, viewFreshAt: now, now: now)
        let allow = try XCTUnwrap(shown.approveOption)
        XCTAssertFalse(shown.answerPermission(allow, item: item, api: api, now: now.addingTimeInterval(2 * 60 * 60)))
        XCTAssertNil(shown.permission)
        XCTAssertFalse(shown.loaded)
        XCTAssertTrue(Outbox.shared.entries(LocalStore.server(api.baseURL), conversationID: "c").isEmpty, "Nothing was saved to send")
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

        // The address can become historical while a restored photo prepares off actor.
        let probe = PhotoPreparationProbe()
        let active = ReviewMessageDecision(composer: ConversationModel(imageWorkStarted: { probe.observe() }))
        await active.load(item: item(), api: api)
        active.composer.draft = "Keep the reply when this address changes"
        #if SWIFT_PACKAGE
        let fixture = Bundle.module.url(forResource: "photo", withExtension: "heic", subdirectory: "Fixtures")
        #else
        let fixture = Bundle(for: Self.self).url(forResource: "photo", withExtension: "heic")
        #endif
        active.composer.attachments = [.init(piece: "image", data: try Data(contentsOf: XCTUnwrap(fixture)).base64EncodedString(),
            mediaType: "image/heic", fileName: "Restored.heic")]
        let pending = Task { await active.send() }
        await fulfillment(of: [probe.started], timeout: 3)
        defer { probe.release() }
        XCTAssertTrue(active.busy)
        active.updateAddressCurrent(false)
        probe.release()
        let acceptedAfterAddressChange = await pending.value
        XCTAssertFalse(acceptedAfterAddressChange)
        XCTAssertTrue(ReviewMessageProtocol.state.bodies.isEmpty)
        XCTAssertNil(active.attemptID)
        XCTAssertEqual(active.composer.draft, "Keep the reply when this address changes")
        XCTAssertEqual(active.composer.attachments.count, 1)
    }
    func testReplacementUpdatesAuthorityWithoutChangingDisplayedRunOrDraft() async throws {
        let api = makeAPI()
        ReviewMessageProtocol.state.reset()
        defer { clean(api) }
        let decision = ReviewMessageDecision()
        await decision.load(item: item(), api: api)
        decision.composer.draft = "Keep this old-conversation draft"
        decision.composer.attachments = [.init(piece: "file", data: "YWJj", mediaType: "text/plain", fileName: "note.txt")]
        let reads = ReviewMessageProtocol.state.eventReads
        ReviewMessageProtocol.state.newMessage = true
        decision.updateAddressCurrent(false)
        XCTAssertTrue(decision.historical)
        XCTAssertTrue(decision.isCurrent(for: item(current: false)), "Replacement does not invalidate the displayed unread run")
        XCTAssertEqual(decision.events.count, 65)
        XCTAssertEqual(decision.through, 66)
        XCTAssertEqual(ReviewMessageProtocol.state.eventReads, reads)
        XCTAssertEqual(decision.composer.conversationID, "c")
        XCTAssertEqual(decision.composer.draft, "Keep this old-conversation draft")
        XCTAssertEqual(decision.composer.attachments.first?.fileName, "note.txt")
        let sent = await decision.send()
        XCTAssertFalse(sent)
        XCTAssertTrue(ReviewMessageProtocol.state.bodies.isEmpty)
        let marked = await decision.markRead()
        XCTAssertTrue(marked, "Mark read is saved and counts at once")
        await Outbox.shared.drain(api: api)
        XCTAssertEqual(ReviewMessageProtocol.state.bodies.last?["through_sequence"] as? Int, 66)
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
    private var _postStatus = 200
    private var _garble = false
    private var _offline = false
    private var _ask = false
    /// The next message gets an unreadable reply, which gives up at once.
    var garble: Bool { get { lock.withLock { _garble } } set { lock.withLock { _garble = newValue } } }
    /// Reads fail as they do without a network; writes still land when it returns.
    var offline: Bool { get { lock.withLock { _offline } } set { lock.withLock { _offline = newValue } } }
    var ask: Bool { get { lock.withLock { _ask } } set { lock.withLock { _ask = newValue } } }
    var postStatus: Int { get { lock.withLock { _postStatus } } set { lock.withLock { _postStatus = newValue } } }
    func status(_ request: URLRequest) -> Int { lock.withLock { request.url?.path.hasSuffix("/send") == true ? _postStatus : 200 } }
    private var held: Set<String> = []
    var loseResponse: Bool { get { lock.withLock { _loseResponse } } set { lock.withLock { _loseResponse = newValue } } }
    var bodies: [[String: Any]] { lock.withLock { _bodies } }
    var eventReads: Int { lock.withLock { _eventReads } }
    var refuse: Bool { get { lock.withLock { _refuse } } set { lock.withLock { _refuse = newValue } } }
    var newMessage: Bool { get { lock.withLock { _newMessage } } set { lock.withLock { _newMessage = newValue } } }
    func reset() { lock.withLock { _bodies = []; _refuse = false; _newMessage = false; _loseResponse = false; _eventReads = 0; _postStatus = 200; held = []; _garble = false; _offline = false; _ask = false } }
    func response(_ request: URLRequest) throws -> Data {
        try lock.withLock {
            let path = request.url!.path
            if _offline, request.httpMethod != "POST" { throw URLError(.notConnectedToInternet) }
            if request.httpMethod == "POST" {
                var data = request.httpBody ?? Data()
                if let stream = request.httpBodyStream {
                    stream.open(); defer { stream.close() }
                    var buffer = [UInt8](repeating: 0, count: 4096)
                    while stream.hasBytesAvailable { let n = stream.read(&buffer, maxLength: buffer.count); if n <= 0 { break }; data.append(buffer, count: n) }
                }
                let body = try JSONSerialization.jsonObject(with: data) as! [String: Any]
                _bodies.append(body)
                if _postStatus == 422 { return Data(#"{"detail":"unsupported or invalid conversation image"}"#.utf8) }
                if !_refuse, let id = body["sender_message_id"] as? String { held.insert(id) }
                if _loseResponse { _loseResponse = false; throw URLError(.networkConnectionLost) }
                if _garble, body["sender_message_id"] != nil { _garble = false; return Data("not json".utf8) }
                return Data((_refuse ? #"{"conversation_id":"c","fate":"refused"}"# : #"{"conversation_id":"c","fate":"queued","owner_read_through_sequence":66}"#).utf8)
            }
            if path.hasSuffix("/receipts") { return Data(#"{"receipts":[]}"#.utf8) }
            if path.hasSuffix("/events") {
                _eventReads += 1
                var events: [[String: Any]] = (1...(_newMessage ? 67 : 66)).map { ["sequence": $0, "kind": "message_to_owner", "payload": ["text": "Message \($0)"]] }
                events.append(["sequence": 90, "kind": "agent_message", "payload": ["text": "Internal output"]])
                return try JSONSerialization.data(withJSONObject: ["events": events])
            }
            if _ask, path.hasSuffix("/conversations/c") {
                return try JSONSerialization.data(withJSONObject: ["is_running": true, "pending_permission_ask": NSNull(), "pending_user_input": [
                    "request_id": "request-1", "questions": [["question_id": "colour", "header": "", "question": "Which colour?",
                    "options": [["label": "Blue", "description": "Calm"], ["label": "Red", "description": "Loud"]], "multi_select": false, "allow_other": false]]]])
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
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: Self.state.status(request), httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data); client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}
