import XCTest
import UIKit

/// One-off review walk, outside the permanent suite: every Review card kind and the four
/// summary states on the real screen, each captured by the stub through simctl. Run with
/// PANELS_REVIEW_QUEUE=1 and PANELS_OFFLINE_TEST=ReviewQueueScreensTests/testCardsAndSummaryStates.
final class ReviewQueueScreensTests: XCTestCase {
    @MainActor func testCardsAndSummaryStates() async throws {
        guard ProcessInfo.processInfo.environment["PANELS_REVIEW_QUEUE"] == "1" else {
            throw XCTSkip("Run with the Review queue fixture")
        }
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        func proof(_ name: String) async throws {
            let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + name)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200, name)
        }
        func shot(_ name: String) async throws {
            // Let the entrance finish before the capture.
            try await Task.sleep(for: .milliseconds(700))
            try await proof("screenshot/" + name)
        }
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        let headline = app.staticTexts["review.summary.headline"]
        let detail = app.staticTexts["review.summary.detail"]
        let back = app.buttons["review.return"]
        let skip = app.buttons["review.skip"]

        // New only, with the agents line.
        XCTAssertTrue(headline.waitForExistence(timeout: 15))
        XCTAssertEqual(headline.label, "5 new.")
        XCTAssertEqual(detail.label, "Ready to review.")
        XCTAssertEqual(app.staticTexts["review.summary.working"].label, "4 agents working right now")
        try await shot("summary-new-only")
        back.tap()

        // The window's order: the Chief's message waits longest, then the proposal.
        let message = app.staticTexts["First unread message"]
        XCTAssertTrue(message.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["review.markRead"].exists)
        try await shot("card-message")
        skip.tap()
        let approve = app.buttons["review.approve"]
        XCTAssertTrue(approve.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["decision.reviewer"].waitForExistence(timeout: 8))
        // A note with a pasted picture; both are kept with the proposal's draft.
        let note = app.textFields["review.revision"].firstMatch
        XCTAssertTrue(note.waitForExistence(timeout: 8))
        note.tap(); note.typeText("Keep the summary's four states exactly as approved.")
        UIPasteboard.general.image = UIGraphicsImageRenderer(size: CGSize(width: 90, height: 60)).image { context in
            UIColor.systemTeal.setFill(); context.fill(CGRect(x: 0, y: 0, width: 90, height: 60))
        }
        note.press(forDuration: 1.2)
        XCTAssertTrue(app.menuItems["Paste"].waitForExistence(timeout: 5))
        app.menuItems["Paste"].tap()
        let chips = app.descendants(matching: .any)["conversation-attachments"]
        XCTAssertTrue(chips.waitForExistence(timeout: 8))
        app.scrollViews["review-content"].coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.2)).tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
        try await shot("card-proposal")
        // The Ticket page shares the proposal control; Back returns to the same card.
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(app.backButton.waitForExistence(timeout: 8))
        XCTAssertTrue(app.buttons["review.approve"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["review.skip"].exists, "The Ticket page has no Skip")
        try await shot("ticket-proposal")
        app.backButton.tap()
        XCTAssertTrue(app.buttons["review.skip"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts["Rebuild the iPhone Review queue"].exists)
        skip.tap()
        XCTAssertTrue(app.staticTexts["Historical message"].waitForExistence(timeout: 10))
        skip.tap()
        let question = app.staticTexts["Which build should go to TestFlight first?"]
        XCTAssertTrue(question.waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["review.ask.send"].isEnabled, "Nothing chosen yet")
        app.buttons["review.ask.option.Build 15"].tap()
        XCTAssertTrue(app.buttons["review.ask.send"].isEnabled)
        // Words go in the shared composer docked below; they replace the one choice.
        let words = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(words.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["Add attachment or command"].exists, "An answer takes no pictures or files")
        words.tap(); words.typeText("Build 15 today, 16 next week")
        XCTAssertTrue(app.buttons["review.ask.send"].isEnabled)
        app.scrollViews["review-content"].coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.2)).tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
        try await shot("card-ask-question")
        skip.tap()
        let permission = app.staticTexts["Run the tests on the Mac simulator?"]
        XCTAssertTrue(permission.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["review.ask.decline"].exists)
        try await shot("card-ask-permission")
        skip.tap()

        // Skipped only.
        XCTAssertTrue(headline.waitForExistence(timeout: 8))
        XCTAssertEqual(headline.label, "5 skipped.")
        XCTAssertEqual(detail.label, "Nothing new.")
        XCTAssertEqual(back.label, "Review skipped")
        try await shot("summary-skipped-only")
        // New and skipped: a proposal arrives while the summary is up.
        try await proof("review-new-proposal")
        XCTAssertTrue(app.staticTexts["6 to review."].waitForExistence(timeout: 10))
        XCTAssertEqual(detail.label, "1 new, 5 skipped.")
        XCTAssertEqual(back.label, "Back to review")
        try await shot("summary-new-and-skipped")
        back.tap()

        // New work first. Its proposal moves to Chief while on screen: a notice, actions off.
        XCTAssertTrue(app.staticTexts["Add the summary screen"].waitForExistence(timeout: 10))
        try await proof("review-move/offline-new")
        let notice = app.descendants(matching: .any)["review.notice"]
        XCTAssertTrue(notice.waitForExistence(timeout: 10))
        XCTAssertFalse(approve.isEnabled)
        try await shot("card-reviewer-moved")
        skip.tap()
        XCTAssertTrue(message.waitForExistence(timeout: 10))
        skip.tap()

        // A refused decision comes back to the front with its reason.
        XCTAssertTrue(app.staticTexts["Rebuild the iPhone Review queue"].waitForExistence(timeout: 10))
        try await proof("review-refuse-decisions")
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        approve.tap()
        let refusal = app.descendants(matching: .any)["review.refusal"]
        XCTAssertTrue(refusal.waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["Rebuild the iPhone Review queue"].exists)
        // The refused card brings back its note and its picture.
        XCTAssertEqual(note.value as? String, "Keep the summary's four states exactly as approved.")
        XCTAssertTrue(chips.waitForExistence(timeout: 8))
        try await shot("card-refused-decision")
        skip.tap()

        // The permission ask answers through the outbox, and the card leaves at once.
        XCTAssertTrue(app.staticTexts["Historical message"].waitForExistence(timeout: 10))
        skip.tap()
        XCTAssertTrue(question.waitForExistence(timeout: 10))
        skip.tap()
        XCTAssertTrue(permission.waitForExistence(timeout: 10))
        app.buttons["review.ask.approve"].tap()
        XCTAssertTrue(permission.waitForNonExistence(timeout: 8))
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/ask-answers")!)
        let answers = try JSONSerialization.jsonObject(with: data) as! [[String: Any]]
        XCTAssertEqual((answers.first?["body"] as? [String: Any])?["option_id"] as? String, "allow")

        // All clear.
        try await proof("review-final")
        XCTAssertTrue(app.staticTexts["All clear."].waitForExistence(timeout: 15))
        XCTAssertEqual(detail.label, "Nothing to review.")
        XCTAssertFalse(back.exists)
        try await shot("summary-all-clear")
    }

    /// The card kinds on a bad connection. Each card is read once online, then the stub
    /// drops every connection: the message, proposal, question and permission cards show
    /// their saved copy with no cover, banner or status note. A reviewer move and a refused
    /// decision that arrived online stay on their cards when the connection drops. Run with
    /// PANELS_REVIEW_QUEUE=1 and PANELS_OFFLINE_TEST=ReviewQueueScreensTests/testCardKindsOnABadConnection.
    @MainActor func testCardKindsOnABadConnection() async throws {
        guard ProcessInfo.processInfo.environment["PANELS_REVIEW_QUEUE"] == "1" else {
            throw XCTSkip("Run with the Review queue fixture")
        }
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        func proof(_ name: String) async throws {
            let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + name)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200, name)
        }
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
        }
        func noCover(_ screen: String) {
            for text in ["Offline", "offline", "Delivery uncertain", "Slow connection", "Reconnecting", "Not connected"] {
                XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", text)).firstMatch.exists,
                               "\(screen) shows \"\(text)\"")
            }
        }
        let headline = app.staticTexts["review.summary.headline"]
        let skip = app.buttons["review.skip"]
        let approve = app.buttons["review.approve"]
        let message = app.staticTexts["First unread message"]
        let proposal = app.staticTexts["Rebuild the iPhone Review queue"]
        let historical = app.staticTexts["Historical message"]
        let question = app.staticTexts["Which build should go to TestFlight first?"]
        let permission = app.staticTexts["Run the tests on the Mac simulator?"]
        let order = [message, proposal, historical, question, permission]

        // Online: read each card once.
        XCTAssertTrue(headline.waitForExistence(timeout: 15))
        app.openReviewCards()
        for card in order {
            XCTAssertTrue(card.waitForExistence(timeout: 10)); skip.tap()
        }
        XCTAssertTrue(headline.waitForExistence(timeout: 8))
        XCTAssertEqual(headline.label, "5 skipped.")

        // Bad connection: each card again from its saved copy.
        try await proof("offline")
        app.openReviewCards()
        XCTAssertTrue(message.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["review.markRead"].exists)
        noCover("Message card"); capture("offline-card-message"); skip.tap()
        XCTAssertTrue(proposal.waitForExistence(timeout: 10))
        XCTAssertTrue(approve.waitForExistence(timeout: 5))
        noCover("Proposal card"); capture("offline-card-proposal"); skip.tap()
        XCTAssertTrue(historical.waitForExistence(timeout: 10)); skip.tap()
        XCTAssertTrue(question.waitForExistence(timeout: 10))
        app.buttons["review.ask.option.Build 15"].tap()
        XCTAssertTrue(app.buttons["review.ask.send"].isEnabled, "A choice can be made offline")
        noCover("Question card"); capture("offline-card-ask-question"); skip.tap()
        XCTAssertTrue(permission.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["review.ask.decline"].exists)
        noCover("Permission card"); capture("offline-card-ask-permission"); skip.tap()
        XCTAssertTrue(headline.waitForExistence(timeout: 8))
        noCover("Summary")

        // A reviewer move that lands online stays on its card when the connection drops.
        try await proof("online")
        try await proof("review-new-proposal")
        XCTAssertTrue(app.staticTexts["6 to review."].waitForExistence(timeout: 15))
        app.openReviewCards()
        XCTAssertTrue(app.staticTexts["Add the summary screen"].waitForExistence(timeout: 10))
        try await proof("review-move/offline-new")
        let notice = app.descendants(matching: .any)["review.notice"]
        XCTAssertTrue(notice.waitForExistence(timeout: 10))
        try await proof("offline")
        try await Task.sleep(for: .seconds(2))
        XCTAssertTrue(notice.exists, "The move stays on its card offline")
        XCTAssertFalse(approve.isEnabled)
        noCover("Moved card"); capture("offline-card-reviewer-moved"); skip.tap()

        // A refused decision that lands online stays on its card when the connection drops.
        try await proof("online")
        XCTAssertTrue(message.waitForExistence(timeout: 10)); skip.tap()
        XCTAssertTrue(proposal.waitForExistence(timeout: 10))
        try await proof("review-refuse-decisions")
        XCTAssertTrue(approve.waitForExistence(timeout: 8)); approve.tap()
        let refusal = app.descendants(matching: .any)["review.refusal"]
        XCTAssertTrue(refusal.waitForExistence(timeout: 15))
        try await proof("offline")
        try await Task.sleep(for: .seconds(2))
        XCTAssertTrue(refusal.exists, "The refusal stays on its card offline")
        XCTAssertTrue(proposal.exists)
        noCover("Refused card"); capture("offline-card-refused-decision")
        try await proof("online")
    }

    /// Cards the phone never read, acted on offline. The stub fails every read of a card's
    /// conversation, so only the review window reaches the phone, then drops every
    /// connection and adds a message the window never saw. Mark read reads through the
    /// window's bound, the question and the permission are answered from the window, and
    /// each lands once when the connection returns; the later message stays unread. Run
    /// with PANELS_REVIEW_QUEUE=1 and
    /// PANELS_OFFLINE_TEST=ReviewQueueScreensTests/testCardsNeverReadActOffline.
    @MainActor func testCardsNeverReadActOffline() async throws {
        guard ProcessInfo.processInfo.environment["PANELS_REVIEW_QUEUE"] == "1" else {
            throw XCTSkip("Run with the Review queue fixture")
        }
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        func proof(_ name: String) async throws -> Data {
            let (data, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + name)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200, name)
            return data
        }
        _ = try await proof("card-reads-offline")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
        }
        func noCover(_ screen: String) {
            for text in ["Offline", "offline", "Delivery uncertain", "Slow connection", "Reconnecting", "Not connected"] {
                XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", text)).firstMatch.exists,
                               "\(screen) shows \"\(text)\"")
            }
        }
        let headline = app.staticTexts["review.summary.headline"]
        let skip = app.buttons["review.skip"]
        let markRead = app.buttons["review.markRead"]
        let proposal = app.staticTexts["Rebuild the iPhone Review queue"]
        let question = app.staticTexts["Which build should go to TestFlight first?"]
        let permission = app.staticTexts["Run the tests on the Mac simulator?"]

        XCTAssertTrue(headline.waitForExistence(timeout: 15))
        app.openReviewCards()
        // The message card: its run was never read, so it shows its title and recap only.
        XCTAssertTrue(markRead.waitForExistence(timeout: 10))
        let enabled = NSPredicate(format: "isEnabled == true")
        await fulfillment(of: [expectation(for: enabled, evaluatedWith: markRead)], timeout: 10)
        XCTAssertFalse(app.staticTexts["First unread message"].exists, "The run was never read")
        _ = try await proof("offline")
        _ = try await proof("new-message")
        noCover("Never-read message card"); capture("never-read-message")
        markRead.tap()
        XCTAssertTrue(proposal.waitForExistence(timeout: 10)); skip.tap()
        // The historical message card is read only.
        XCTAssertTrue(skip.waitForExistence(timeout: 10)); skip.tap()
        XCTAssertTrue(question.waitForExistence(timeout: 10))
        app.buttons["review.ask.option.Build 15"].tap()
        noCover("Never-read question card"); capture("never-read-question")
        app.buttons["review.ask.send"].tap()
        XCTAssertTrue(permission.waitForExistence(timeout: 10))
        noCover("Never-read permission card"); capture("never-read-permission")
        app.buttons["review.ask.approve"].tap()
        XCTAssertTrue(headline.waitForExistence(timeout: 10))
        noCover("Summary")

        // Back online, each action lands once.
        _ = try await proof("online")
        var answers: [[String: Any]] = []
        var reads: [Int] = []
        for _ in 0..<60 {
            answers = try JSONSerialization.jsonObject(with: try await proof("ask-answers")) as? [[String: Any]] ?? []
            let writes = try JSONSerialization.jsonObject(with: try await proof("writes")) as? [Any] ?? []
            reads = writes.compactMap { ($0 as? [String: Any])?["through_sequence"] as? Int }
            if answers.count >= 2 && !reads.isEmpty { break }
            try await Task.sleep(for: .milliseconds(500))
        }
        XCTAssertEqual(reads, [3], "Mark read reaches the window's newest message, not the later one")
        let bodies = answers.compactMap { $0["body"] as? [String: Any] }
        XCTAssertEqual(answers.count, 2)
        XCTAssertEqual(bodies.first { $0["request_id"] != nil }?["request_id"] as? String, "request-1")
        let approval = bodies.first { $0["ask_id"] != nil }
        XCTAssertEqual(approval?["ask_id"] as? String, "ask-1")
        XCTAssertEqual(approval?["option_id"] as? String, "allow")
        capture("never-read-after-reconnect")
    }
}
