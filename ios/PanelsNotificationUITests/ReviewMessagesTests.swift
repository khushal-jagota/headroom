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
        let review = app.tabBars.buttons["Review"]
        XCTAssertTrue(review.waitForExistence(timeout: 10))
        review.tap()
        let message = app.staticTexts["First unread message"]
        let approve = app.buttons["review.approve"]
        XCTAssertTrue(message.waitForExistence(timeout: 12))
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        let skip = app.buttons["review.skip"]
        XCTAssertTrue(skip.waitForExistence(timeout: 8))
        skip.tap()
        let historical = app.staticTexts["Historical message"]
        XCTAssertTrue(historical.waitForExistence(timeout: 8))
        skip.tap()
        let revisit = app.buttons["review.revisitSkipped"]
        XCTAssertTrue(revisit.waitForExistence(timeout: 8))
        revisit.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 10))
        approve.tap()
        let confirm = app.buttons["review.confirmApproval"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
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
        let workspace = app.tabBars.buttons["Workspace"]
        XCTAssertTrue(workspace.waitForExistence(timeout: 8)); workspace.tap()
        let chief = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Chief of Staff'")).firstMatch
        XCTAssertTrue(chief.waitForExistence(timeout: 8))
        chief.tap()
        let back = app.buttons["detail-back"]
        XCTAssertTrue(back.waitForExistence(timeout: 8))
        back.tap()
        let review = app.tabBars.buttons["Review"]
        XCTAssertTrue(review.waitForExistence(timeout: 8))
        review.tap()
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
        back.tap()
        XCTAssertTrue(app.staticTexts["Chief of Staff"].waitForExistence(timeout: 8))
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        let writes = try JSONSerialization.jsonObject(with: data) as! [[String: Any]]
        let sends = writes.filter { $0["sender_message_id"] != nil }
        XCTAssertEqual(sends.count, 2)
        XCTAssertEqual((sends.last?["content"] as? [[String: Any]])?.first?["text"] as? String, "Reply kept after refusal corrected")
    }
}
