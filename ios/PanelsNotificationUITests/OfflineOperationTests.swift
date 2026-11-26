import XCTest

/// Real SwiftUI controls against the localhost fixture, which drops connections and,
/// separately, drops replies after it has done the write. Each action looks done at
/// once and lands exactly once. A refused message offers Try Again.
final class OfflineOperationTests: XCTestCase {
    /// What the old outbox showed. None of it may appear now.
    private static let removedNotes = [
        "Waiting for connection", "Sent", "Queued on server", "Delivery uncertain",
        "Delivery uncertain · check before another attempt", "Sending…", "Approved", "Sent back",
        "Send again", "Keep waiting", "Check delivery", "Panels can’t reach the server.",
    ]
    /// Read notes carry the error after their prefix, so they are matched by their start.
    private static let removedReadNotes = ["Couldn’t refresh", "Couldn’t load"]

    @MainActor func testEveryActionLandsOnceAcrossDroppedConnectionsAndLostResponses() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        try await proof(base, "fast")
        app.launch()
        defer { app.terminate() }
        app.openSidebar(timeout: 12)
        // The Sidebar's Chief card; it exists while the Sidebar is open.
        let chief = app.buttons["sidebar-chief"]
        XCTAssertTrue(chief.waitForExistence(timeout: 12))
        app.buttons["sidebar-review"].tap()
        enterCards(app)
        XCTAssertTrue(app.buttons["review.approve"].waitForExistence(timeout: 10))
        // Preload the three proposal cards and their manifests before losing connectivity.
        try await Task.sleep(for: .seconds(3))
        app.openDestination("sidebar-chief")
        let input = app.textFields["Message"]
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        try await Task.sleep(for: .seconds(2))

        // 1. The server drops every connection: a message, an approval and a send back.
        try await proof(base, "offline")
        input.tap(); input.typeText("Saved on the train")
        app.buttons["Send message"].tap()
        XCTAssertTrue(element(app, containing: "Saved on the train").waitForExistence(timeout: 5))
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 5)); line.tap()
        // Capture the full conversation inside AppShell without changing its draft.
        let composer = app.otherElements["conversation-composer-region"]
        let conversationInput = composer.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        XCTAssertTrue(conversationInput.waitForExistence(timeout: 8))
        conversationInput.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 8))
        capture(app, "owned-host-conversation-keyboard")
        assertQuiet(app, "offline message")
        // The conversation page goes back to the Chief page; the Sidebar leads to Review.
        app.backButton.tap()
        XCTAssertTrue(app.readableText("Chief of Staff").waitForExistence(timeout: 8))
        app.openDestination("sidebar-review")
        enterCards(app)
        let approve = app.buttons["review.approve"]
        XCTAssertTrue(approve.waitForExistence(timeout: 10))
        approve.tap()
        // The fixture's next stage is Done: approval is one tap.
        let sendBack = app.buttons["review.sendBack"]
        XCTAssertTrue(sendBack.waitForExistence(timeout: 8))
        sendBack.tap()
        let guidanceAny = app.descendants(matching: .any).matching(identifier: "review.revision").firstMatch
        XCTAssertTrue(guidanceAny.waitForExistence(timeout: 5))
        guidanceAny.tap(); guidanceAny.typeText("Keep this send-back guidance")
        // The note stays pinned; the same action now submits it.
        sendBack.tap()
        assertQuiet(app, "offline decisions")
        app.terminate(); app.launch()
        // After this relaunch the app draws at once, but XCTest's accessibility snapshot of
        // the hosted content can stay empty until the scene is activated again. A trip
        // to the Home Screen refreshes it.
        var found = app.sidebarButton.waitForExistence(timeout: 8)
        for _ in 0..<3 where !found {
            XCUIDevice.shared.press(.home)
            app.activate()
            found = app.sidebarButton.waitForExistence(timeout: 8)
        }
        XCTAssertTrue(found); app.openSidebar()
        XCTAssertTrue(chief.waitForExistence(timeout: 12), "The cached Sidebar did not restore offline")
        app.buttons["sidebar-review"].tap()
        enterCards(app)
        XCTAssertTrue(element(app, containing: "Record offline").waitForExistence(timeout: 5), "Cached Review did not restore the remaining ask offline")
        // The old cover appeared on activation after a failed read. Stay offline a while.
        XCUIDevice.shared.press(.home)
        app.activate()
        for second in 0..<15 {
            assertQuiet(app, "offline after relaunch, \(second)s")
            try await Task.sleep(for: .seconds(1))
        }
        app.openSidebar()
        try await proof(base, "online")
        var delivery = try await waitForDelivery(base) {
            $0.count("message") >= 1 && $0.count("approve") >= 1 && $0.count("sendBack") >= 1
        }
        XCTAssertEqual(delivery.count("message"), 1)
        XCTAssertEqual(delivery.count("approve"), 1)
        XCTAssertEqual(delivery.count("sendBack"), 1)
        XCTAssertEqual(delivery.messages.map(\.text), ["Saved on the train"])
        XCTAssertTrue(delivery.decisionPosts.values.allSatisfy { $0 == 1 }, "A decision was submitted twice: \(delivery.decisionPosts)")
        assertQuiet(app, "after reconnect")

        // 1b. A mark read needs the live conversation; its write connection is dropped.
        try await proof(base, "drop/read")
        try await proof(base, "agent-message")
        chief.tap()
        XCTAssertTrue(line.waitForExistence(timeout: 8)); line.tap()
        XCTAssertTrue(app.readableText("Agent update 1").waitForExistence(timeout: 15))
        delivery = try await waitForDelivery(base) { $0.reads == [2] }
        XCTAssertEqual(delivery.reads, [2])
        XCTAssertEqual(delivery.dropped, [Fault(kind: "read", id: "2")], "The dropped read did not happen")
        XCTAssertGreaterThanOrEqual(delivery.readAttempts["2"] ?? 0, 2)
        assertQuiet(app, "after dropped read")
        app.backButton.tap()
        XCTAssertTrue(app.readableText("Chief of Staff").waitForExistence(timeout: 8))
        app.openSidebar()

        // 2. The server does each write, then drops the reply: message, approval, read.
        try await proof(base, "lose/message,decision,read")
        try await proof(base, "agent-message")
        XCTAssertTrue(chief.waitForExistence(timeout: 8)); chief.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap(); input.typeText("Sent through a lost reply")
        app.buttons["Send message"].tap()
        XCTAssertTrue(element(app, containing: "Sent through a lost reply").waitForExistence(timeout: 5))
        XCTAssertTrue(line.waitForExistence(timeout: 8)); line.tap()
        XCTAssertTrue(app.readableText("Agent update 2").waitForExistence(timeout: 15))
        app.backButton.tap()
        XCTAssertTrue(app.readableText("Chief of Staff").waitForExistence(timeout: 8))
        app.openDestination("sidebar-review")
        enterCards(app)
        XCTAssertTrue(element(app, containing: "Record offline").waitForExistence(timeout: 10))
        XCTAssertTrue(approve.waitForExistence(timeout: 10))
        approve.tap()
        assertQuiet(app, "lost-response approval")
        delivery = try await waitForDelivery(base) {
            $0.lost.count == 3 && $0.count("message") >= 2 && $0.count("approve") >= 2 && $0.reads.count >= 2
                && ($0.messageAttempts[$0.lostID("message") ?? ""] ?? 0) >= 2
                && ($0.decisionReceiptReads[$0.lostID("decision") ?? ""] ?? 0) >= 2
                && ($0.readAttempts["3"] ?? 0) >= 2
        }
        XCTAssertEqual(Set(delivery.lost.map(\.kind)), ["message", "decision", "read"], "Not every lost reply happened")
        XCTAssertTrue(delivery.armed.isEmpty)
        // Let one more retry interval pass. Nothing may land a second time.
        try await Task.sleep(for: .seconds(12))
        delivery = try await self.delivery(base)
        XCTAssertEqual(delivery.count("message"), 2)
        XCTAssertEqual(delivery.count("approve"), 2)
        XCTAssertEqual(delivery.count("sendBack"), 1)
        XCTAssertEqual(delivery.reads, [2, 3])
        XCTAssertEqual(delivery.messages.map(\.text), ["Saved on the train", "Sent through a lost reply"])
        let lostMessage = try XCTUnwrap(delivery.lostID("message"))
        XCTAssertGreaterThanOrEqual(delivery.messageAttempts[lostMessage] ?? 0, 2, "The message was not sent again with its ID")
        let lostDecision = try XCTUnwrap(delivery.lostID("decision"))
        XCTAssertEqual(delivery.decisionPosts[lostDecision], 1, "The approval was submitted again instead of reading its receipt")
        XCTAssertGreaterThanOrEqual(delivery.decisionReceiptReads[lostDecision] ?? 0, 2)
        XCTAssertTrue(delivery.decisionPosts.values.allSatisfy { $0 == 1 })
        XCTAssertGreaterThanOrEqual(delivery.readAttempts["3"] ?? 0, 2, "The read was not sent again")
        assertQuiet(app, "after lost responses")

        // 3. A refused message shows a red mark; Try Again sends it once with a new ID.
        try await proof(base, "refuse/message")
        app.openSidebar()
        XCTAssertTrue(chief.waitForExistence(timeout: 8)); chief.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap(); input.typeText("Please refuse this one")
        app.buttons["Send message"].tap()
        XCTAssertTrue(line.waitForExistence(timeout: 8)); line.tap()
        let refused = app.buttons["conversation.refused"]
        XCTAssertTrue(refused.waitForExistence(timeout: 20))
        XCTAssertEqual(refused.label, "Not delivered")
        capture(app, "conversation-refused-mark")
        assertQuiet(app, "refused message")
        refused.tap()
        // The dialog button's identifier may not reach the accessibility tree; its label does.
        let tryAgain = app.buttons.matching(NSPredicate(format: "identifier == 'conversation.retry' OR label == 'Try Again'")).firstMatch
        XCTAssertTrue(tryAgain.waitForExistence(timeout: 5))
        tryAgain.tap()
        XCTAssertTrue(refused.waitForNonExistence(timeout: 30), "The refused mark stayed after Try Again")
        _ = try await waitForDelivery(base) { $0.count("message") >= 3 }
        try await Task.sleep(for: .seconds(6))
        delivery = try await self.delivery(base)
        XCTAssertEqual(delivery.refusedMessages.count, 1)
        let refusedID = try XCTUnwrap(delivery.refusedMessages.first)
        XCTAssertFalse(delivery.messages.contains { $0.id == refusedID }, "The refused ID landed")
        let retried = delivery.messages.filter { $0.text == "Please refuse this one" }
        XCTAssertEqual(retried.count, 1)
        XCTAssertNotEqual(retried.first?.id, refusedID)
        XCTAssertEqual(delivery.count("message"), 3)
        capture(app, "conversation-refused-retried")
        assertQuiet(app, "after Try Again")
        app.backButton.tap()
        XCTAssertTrue(app.readableText("Chief of Staff").waitForExistence(timeout: 8))
    }

    private struct Fault: Decodable, Equatable { let kind: String; let id: String }
    private struct Delivery: Decodable {
        struct Message: Decodable { let id: String; let text: String }
        let writes: [String]
        let messages: [Message]
        let messageAttempts: [String: Int]
        let refusedMessages: [String]
        let decisionPosts: [String: Int]
        let decisionReceiptReads: [String: Int]
        let reads: [Int]
        let readAttempts: [String: Int]
        let armed: [String]
        let dropped: [Fault]
        let lost: [Fault]
        enum CodingKeys: String, CodingKey {
            case writes, messages, reads, armed, dropped, lost
            case messageAttempts = "message_attempts", refusedMessages = "refused_messages"
            case decisionPosts = "decision_posts", decisionReceiptReads = "decision_receipt_reads"
            case readAttempts = "read_attempts"
        }
        func count(_ write: String) -> Int { writes.filter { $0 == write }.count }
        func lostID(_ kind: String) -> String? { lost.first { $0.kind == kind }?.id }
    }
    @MainActor private func delivery(_ base: String) async throws -> Delivery {
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/delivery")!)
        return try JSONDecoder().decode(Delivery.self, from: data)
    }
    /// Retries wait 5 to 30 seconds after a failure, so allow a little over a minute.
    @MainActor private func waitForDelivery(_ base: String, timeout: TimeInterval = 75,
                                            until done: (Delivery) -> Bool) async throws -> Delivery {
        let deadline = Date().addingTimeInterval(timeout)
        var current = try await delivery(base)
        while !done(current), Date() < deadline {
            try await Task.sleep(for: .seconds(1))
            current = try await delivery(base)
        }
        return current
    }
    @MainActor private func proof(_ base: String, _ action: String) async throws {
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + action)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }
    @MainActor private func element(_ app: XCUIApplication, containing text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@ OR value CONTAINS %@", text, text)).firstMatch
    }
    /// No cover screen and no outbox status note, anywhere on screen.
    @MainActor private func assertQuiet(_ app: XCUIApplication, _ moment: String) {
        XCTAssertFalse(app.buttons["server-unreachable.retry"].exists, "The server cover appeared: \(moment)")
        let notes = app.descendants(matching: .any).matching(NSPredicate(format: "label IN %@ OR value IN %@", Self.removedNotes, Self.removedNotes))
        XCTAssertEqual(notes.count, 0, "A removed status note appeared (\(moment)): \(notes.allElementsBoundByIndex.map(\.readableContent))")
        for prefix in Self.removedReadNotes {
            let reads = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@ OR value BEGINSWITH %@", prefix, prefix))
            XCTAssertEqual(reads.count, 0, "A read note appeared (\(moment)): \(reads.allElementsBoundByIndex.map(\.readableContent))")
        }
    }
    /// Review opens on its summary at launch and after another destination.
    @MainActor private func enterCards(_ app: XCUIApplication) {
        let reviewReturn = app.buttons["review.return"]
        XCTAssertTrue(reviewReturn.waitForExistence(timeout: 12), "Review did not show its summary")
        reviewReturn.tap()
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
