import XCTest

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
}
