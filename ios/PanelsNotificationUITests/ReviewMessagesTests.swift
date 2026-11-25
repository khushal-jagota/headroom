import XCTest

/// Real shell routes, Review controls and exact-history navigation need the app.
final class ReviewMessagesTests: XCTestCase {
    /// Act on a Review item and return to skipped work. One Ticket holds a proposal and an
    /// unread message: each is its own card, Skip is per card, an artifact opened from a
    /// card docks the card's reply and returns to it, and the summary brings skipped cards back.
    @MainActor func testSplitCardsSkipAndReturn() async throws {
        guard ProcessInfo.processInfo.environment["PANELS_REVIEW_SPLIT"] == "1" else {
            throw XCTSkip("Run alone with the split fixture")
        }
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        _ = try await URLSession.shared.data(from: URL(string: base + "/proof/review-owner/ticket-split")!)
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        // The app opens on the summary, not straight into a card.
        let summary = app.readableText("review.summary.headline")
        XCTAssertTrue(summary.waitForExistence(timeout: 12))
        XCTAssertEqual(summary.readableContent, "3 new.")
        let back = app.buttons["review.return"]
        XCTAssertEqual(back.label, "Back to review")
        back.tap()
        // The proposal is its own card, with Skip, Return and Approve in one row.
        let approve = app.buttons["review.approve"]
        XCTAssertTrue(approve.waitForExistence(timeout: 10))

        // The real Sidebar action must leave an open card, even while Review is already
        // selected. Model tests cannot prove selection through the shell's NavigationStack.
        let selectionProofStart = Date()
        app.openDestination("sidebar-review")
        XCTAssertTrue(summary.waitForExistence(timeout: 8))
        XCTAssertEqual(summary.label, "3 new.")
        XCTAssertEqual(app.readableText("review.summary.detail").label, "Ready to review.")
        XCTAssertFalse(approve.exists)
        capture(app, "review-selection-from-card")
        // A repeated tap stays on the summary. A trip through Chief does the same.
        app.openDestination("sidebar-review")
        XCTAssertTrue(summary.waitForExistence(timeout: 8))
        XCTAssertEqual(summary.label, "3 new.")
        XCTAssertFalse(approve.exists)
        capture(app, "review-selection-already-summary")
        back.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 10))
        app.openDestination("sidebar-chief")
        XCTAssertTrue(app.descendants(matching: .any)["chief-screen"].waitForExistence(timeout: 8))
        app.openDestination("sidebar-review")
        XCTAssertTrue(summary.waitForExistence(timeout: 8))
        XCTAssertEqual(summary.label, "3 new.")
        XCTAssertFalse(approve.exists)
        capture(app, "review-selection-from-chief")
        print("Review selection proof: \(Date().timeIntervalSince(selectionProofStart)) seconds")
        back.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 10))
        let message = app.readableText("First unread message")

        XCTAssertFalse(message.exists, "The message is a card of its own")
        let note = app.textFields["review.revision"].firstMatch
        XCTAssertTrue(note.waitForExistence(timeout: 8))
        note.tap(); note.typeText("Keep this decision note")
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        tapReviewGutter(app)
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5), "An inert Review tap must end editing")
        let skip = app.buttons["review.skip"]
        skip.tap()
        // The Ticket's message card, with its reply composer and Mark read.
        XCTAssertTrue(message.waitForExistence(timeout: 12))
        XCTAssertFalse(approve.exists)
        let reply = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(reply.waitForExistence(timeout: 5))
        reply.tap(); reply.typeText("Keep this Review reply")
        tapReviewGutter(app)
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
        capture(app, "review-message-card")
        // An artifact pushed from the card docks the card's own reply and Mark read, with
        // the card's draft, and Back returns to the same card.
        let artifact = app.links.matching(NSPredicate(format: "label CONTAINS %@", "proof.html")).firstMatch
        XCTAssertTrue(artifact.waitForExistence(timeout: 5))
        artifact.tap()
        XCTAssertTrue(app.buttons["Artifact actions"].waitForExistence(timeout: 8))
        let viewerReply = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(viewerReply.waitForExistence(timeout: 5))
        XCTAssertEqual(viewerReply.value as? String, "Keep this Review reply", "The viewer's reply is the card's draft")
        XCTAssertTrue(app.buttons["review.markRead"].exists)
        app.backButton.tap()
        XCTAssertTrue(message.waitForExistence(timeout: 8))
        XCTAssertEqual(reply.value as? String, "Keep this Review reply")
        skip.tap()
        let historical = app.readableText("Historical message")
        XCTAssertTrue(historical.waitForExistence(timeout: 8))
        skip.tap()
        // Every card skipped: the summary offers them again.
        XCTAssertTrue(summary.waitForExistence(timeout: 8))
        XCTAssertEqual(summary.readableContent, "3 skipped.")
        XCTAssertEqual(app.readableText("review.summary.detail").readableContent, "Nothing new.")
        XCTAssertEqual(back.label, "Review skipped")
        back.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 10))
        XCTAssertEqual(note.value as? String, "Keep this decision note")
        approve.tap()
        // The fixture advances to Done, so Approve resolves in one tap; the next card follows.
        XCTAssertTrue(message.waitForExistence(timeout: 8))
        XCTAssertFalse(approve.exists)
        let markRead = app.buttons["review.markRead"]
        XCTAssertTrue(markRead.waitForExistence(timeout: 8))
        markRead.tap()
        XCTAssertTrue(historical.waitForExistence(timeout: 10))
        XCTAssertTrue(message.waitForNonExistence(timeout: 8))
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        let writes = try JSONSerialization.jsonObject(with: data) as! [Any]
        XCTAssertEqual(writes.first as? String, "approve")
        XCTAssertEqual(writes.compactMap { ($0 as? [String: Any])?["through_sequence"] as? Int }, [3])
    }

    @MainActor func testUnreadReplyRefusalQueueAndHistoricalNavigation() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        app.openDestination("sidebar-chief", timeout: 8)
        XCTAssertTrue(app.descendants(matching: .any)["chief-screen"].waitForExistence(timeout: 8))
        let back = app.backButton
        let parentInput = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(parentInput.waitForExistence(timeout: 8))
        parentInput.tap(); parentInput.typeText("Retain Chief draft without focus")
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        capture(app, "chief-parent-focused-before-chat")
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 5)); line.tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 8))
        // Prior parent focus alone caused restoration. Do not focus the child here.
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5), "The parent must release focus before the push")
        back.tap()
        XCTAssertTrue(line.waitForExistence(timeout: 8))
        capture(app, "chief-parent-return-before-focus-check")
        XCTAssertFalse(app.buttons["conversation-actions"].exists)
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5), "Back must not restore the retained parent's keyboard")
        XCTAssertTrue(line.isHittable)
        XCTAssertEqual(parentInput.value as? String, "Retain Chief draft without focus")
        capture(app, "chief-parent-return-without-keyboard")
        app.openDestination("sidebar-review", timeout: 8)
        // A return from another destination shows the summary first.
        let reviewReturn = app.buttons["review.return"]
        XCTAssertTrue(reviewReturn.waitForExistence(timeout: 12))
        reviewReturn.tap()
        XCTAssertTrue(app.readableText("First unread message").waitForExistence(timeout: 12))
        let input = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap()
        input.typeText("Reply kept after refusal")
        app.buttons["Send message"].tap()
        let retry = app.buttons["Retry reply"]
        XCTAssertTrue(retry.waitForExistence(timeout: 8))
        // The refused card comes back with its words; type after them.
        XCTAssertEqual(input.value as? String, "Reply kept after refusal")
        input.coordinate(withNormalizedOffset: CGVector(dx: 0.97, dy: 0.5)).tap()
        input.typeText(" corrected")
        retry.tap()
        XCTAssertTrue(app.buttons["review.approve"].waitForExistence(timeout: 12))
        let skip = app.buttons["review.skip"]
        XCTAssertTrue(skip.waitForExistence(timeout: 8))
        skip.tap()
        XCTAssertTrue(app.readableText("Historical message").waitForExistence(timeout: 10))
        let openConversation = app.buttons["review.openConversation"]
        XCTAssertTrue(openConversation.waitForExistence(timeout: 8))
        openConversation.tap()
        XCTAssertTrue(app.readableText("Historical transcript").waitForExistence(timeout: 8))
        XCTAssertTrue(app.readableText("Historical conversation · read only").waitForExistence(timeout: 8))
        // The conversation opens on top of Review, so Back returns to the card.
        back.tap()
        XCTAssertTrue(app.readableText("Historical message").waitForExistence(timeout: 8))
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        let writes = try JSONSerialization.jsonObject(with: data) as! [[String: Any]]
        let sends = writes.filter { $0["sender_message_id"] != nil }
        XCTAssertEqual(sends.count, 2)
        XCTAssertEqual((sends.last?["content"] as? [[String: Any]])?.first?["text"] as? String, "Reply kept after refusal corrected")
    }

    @MainActor private func tapReviewGutter(_ app: XCUIApplication) {
        let scroll = app.scrollViews["review-content"]
        XCTAssertTrue(scroll.exists)
        scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.01, dy: 0.25)).tap()
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
