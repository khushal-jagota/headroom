import XCTest

/// Real shell routes, Review controls and exact-history navigation need the app.
final class ReviewMessagesTests: XCTestCase {
    @MainActor func testStackedTicketCard() async throws {
        guard ProcessInfo.processInfo.environment["PANELS_REVIEW_STACKED"] == "1" else {
            throw XCTSkip("Run alone with the stacked fixture")
        }
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        _ = try await URLSession.shared.data(from: URL(string: base + "/proof/review-owner/ticket-stacked")!)
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        // Review is the root at launch.
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 10))
        let message = app.staticTexts["First unread message"]
        let approve = app.buttons["review.approve"]
        XCTAssertTrue(message.waitForExistence(timeout: 12))
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        let skip = app.buttons["review.skip"]
        XCTAssertTrue(skip.waitForExistence(timeout: 8))
        // One visible Review card owns both the decision note and its message reply.
        let note = app.textFields["review.revision"].firstMatch
        XCTAssertTrue(note.waitForExistence(timeout: 8))
        note.tap(); note.typeText("Keep this decision note")
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        capture(app, "review-note-keyboard-open")
        tapReviewGutter(app)
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5), "An inert Review tap must end editing")
        XCTAssertEqual(note.value as? String, "Keep this decision note")
        let reply = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(reply.waitForExistence(timeout: 5))
        if !reply.isHittable { app.scrollViews["review-content"].swipeUp() }
        reply.tap(); reply.typeText("Keep this Review reply")
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        tapReviewGutter(app)
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
        XCTAssertEqual(reply.value as? String, "Keep this Review reply")
        XCTAssertEqual(note.value as? String, "Keep this decision note")
        capture(app, "review-note-and-reply-keyboard-dismissed")
        // A native artifact push must release the note before it leaves this screen.
        let artifact = app.links.matching(NSPredicate(format: "label CONTAINS %@", "proof.html")).firstMatch
        XCTAssertTrue(artifact.waitForExistence(timeout: 5))
        note.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        // The decision bar rises with the keyboard and covers the lower card; scroll the link clear of it.
        let content = app.scrollViews["review-content"]
        content.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.35))
            .press(forDuration: 0.05, thenDragTo: content.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.12)))
        XCTAssertTrue(app.keyboards.firstMatch.exists, "Scrolling the card must keep the note focused")
        artifact.tap()
        XCTAssertTrue(app.buttons["Artifact actions"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
        XCTAssertFalse(app.textFields["conversation-input"].exists, "Review artifacts must not gain a conversation composer")
        app.backButton.tap()
        XCTAssertTrue(note.waitForExistence(timeout: 8))
        XCTAssertTrue(note.isHittable)
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5), "Returning from an artifact must not restore the note keyboard")
        XCTAssertEqual(note.value as? String, "Keep this decision note")
        capture(app, "review-artifact-return-keeps-note-unfocused")
        if !skip.isHittable { app.scrollViews["review-content"].swipeDown() }
        skip.tap()
        let historical = app.staticTexts["Historical message"]
        XCTAssertTrue(historical.waitForExistence(timeout: 8))
        skip.tap()
        let revisit = app.buttons["review.revisitSkipped"]
        XCTAssertTrue(revisit.waitForExistence(timeout: 8))
        revisit.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 10))
        approve.tap()
        // The fixture advances to Done, so Approve resolves in one tap.
        XCTAssertTrue(approve.waitForNonExistence(timeout: 8))
        XCTAssertTrue(message.waitForExistence(timeout: 8))
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
        XCTAssertTrue(app.staticTexts["First unread message"].waitForExistence(timeout: 12))
        let input = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap()
        input.typeText("Reply kept after refusal")
        app.buttons["Send message"].tap()
        let retry = app.buttons["Retry reply"]
        XCTAssertTrue(retry.waitForExistence(timeout: 8))
        input.tap()
        input.typeText(" corrected")
        retry.tap()
        XCTAssertTrue(app.buttons["review.approve"].waitForExistence(timeout: 12))
        let skip = app.buttons["review.skip"]
        XCTAssertTrue(skip.waitForExistence(timeout: 8))
        skip.tap()
        XCTAssertTrue(app.staticTexts["Historical message"].waitForExistence(timeout: 10))
        let openConversation = app.buttons["review.openConversation"]
        XCTAssertTrue(openConversation.waitForExistence(timeout: 8))
        openConversation.tap()
        XCTAssertTrue(app.staticTexts["Historical transcript"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts["Historical conversation · read only"].waitForExistence(timeout: 8))
        // The conversation opens on top of Review, so Back returns to the card.
        back.tap()
        XCTAssertTrue(app.staticTexts["Historical message"].waitForExistence(timeout: 8))
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
