import XCTest

/// One connected UI proof for keyboard geometry, scrolling, and page transitions.
/// Host tests own delivery decisions; this test needs a real scroll view and server.
final class ConversationOperationTests: XCTestCase {
    @MainActor func testOperationsAgainstLocalStub() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_CONVERSATION_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-smoke", "--conversation-stub", base, "--conversation-fixture-speech"]
        app.launch()
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 8))
        try await proof(base, "start/open-close")
        line.tap()
        let close = app.buttons["Close conversation"]
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        close.tap()
        XCTAssertTrue(line.waitForExistence(timeout: 5))
        line.swipeUp()
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        close.tap()
        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        input.tap(); input.typeText("Keep this draft")
        XCTAssertTrue(app.keyboards.firstMatch.exists, "Growing the composer dropped keyboard focus")
        line.tap()
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        close.tap()
        XCTAssertEqual(input.value as? String, "Keep this draft")
        try await proof(base, "stop")

        try await proof(base, "start/send-stream")
        line.tap()
        app.buttons["Conversation lens"].tap()
        app.buttons["Send message"].tap()
        XCTAssertTrue(app.staticTexts["Sending…"].firstMatch.waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["Sending…"].firstMatch.waitForNonExistence(timeout: 8))
        XCTAssertFalse(app.staticTexts["Sent"].firstMatch.exists)
        let thread = app.scrollViews.firstMatch
        thread.swipeDown()
        XCTAssertTrue(app.buttons["Latest"].waitForExistence(timeout: 5))
        let anchor = try XCTUnwrap(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Review '")).allElementsBoundByIndex.first(where: { $0.isHittable }))
        let before = anchor.frame.minY
        try await Task.sleep(for: .seconds(3))
        XCTAssertEqual(anchor.frame.minY, before, accuracy: 3, "Streaming moved the reader's position")
        app.buttons["Latest"].tap()
        try await Task.sleep(for: .seconds(3))
        XCTAssertFalse(app.buttons["Latest"].exists)
        try await proof(base, "stop")

        try await proof(base, "start/queued-retry")
        input.tap(); input.typeText("Queue this next")
        app.buttons["Steer"].tap(); app.buttons["Queue"].tap()
        app.buttons["Send message"].tap()
        XCTAssertTrue(app.staticTexts["Queued"].firstMatch.waitForExistence(timeout: 5))
        try await Task.sleep(for: .seconds(7))
        XCTAssertFalse(app.staticTexts["Queued"].firstMatch.exists)
        input.tap(); input.typeText("Remove this queued message")
        app.buttons["Send message"].tap()
        XCTAssertTrue(app.buttons["Remove queued message"].waitForExistence(timeout: 6))
        app.buttons["Remove queued message"].tap()
        XCTAssertTrue(app.staticTexts["Not delivered · no longer applicable"].firstMatch.waitForExistence(timeout: 5))
        input.tap(); input.typeText("Steer this queued message")
        app.buttons["Send message"].tap()
        XCTAssertTrue(app.buttons["Steer in"].waitForExistence(timeout: 6))
        app.buttons["Steer in"].tap()
        XCTAssertTrue(app.staticTexts["Queued"].firstMatch.waitForNonExistence(timeout: 5))
        input.tap(); input.typeText("Fail once")
        app.buttons["Send message"].tap()
        XCTAssertTrue(app.buttons["Retry"].waitForExistence(timeout: 5))
        app.buttons["Retry"].tap()
        XCTAssertTrue(app.staticTexts["Sending…"].firstMatch.waitForExistence(timeout: 3))
        try await Task.sleep(for: .seconds(3))
        XCTAssertFalse(app.buttons["Retry"].exists)
        input.tap(); input.typeText("Suppress this stale message")
        app.buttons["Send message"].tap()
        XCTAssertTrue(app.staticTexts["Not delivered · no longer current"].firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["Retry"].exists)
        try await proof(base, "stop")

        // This explicitly injects recognition output, not microphone/audio evidence.
        try await proof(base, "start/voice-fixture")
        close.tap()
        let draftFrame = input.frame
        app.buttons["Record voice input"].tap()
        XCTAssertTrue(app.buttons["Stop recording"].waitForExistence(timeout: 3))
        XCTAssertTrue(line.exists)
        app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: draftFrame.midX, dy: draftFrame.midY)).tap()
        XCTAssertFalse(app.keyboards.firstMatch.exists, "Tapping the recording area focused the hidden draft")
        XCTAssertTrue(app.buttons["Stop recording"].exists)
        XCTAssertFalse(input.isHittable, "The draft must not be editable during recording")
        try await Task.sleep(for: .seconds(2))
        app.buttons["Stop recording"].tap()
        XCTAssertTrue(app.buttons["Send message"].waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "Keep the newest message in view.")
        input.tap(); input.typeText(" Please.")
        XCTAssertTrue(line.exists)
        app.buttons["Choose model"].tap()
        XCTAssertTrue(app.navigationBars["Model"].waitForExistence(timeout: 5))
        app.buttons["Cancel"].tap()
        app.buttons["Send message"].tap()
        try await Task.sleep(for: .seconds(2))
        let capture = XCTAttachment(screenshot: app.screenshot())
        capture.name = "settled-composer"; capture.lifetime = .keepAlways; add(capture)
        try await proof(base, "stop")
    }

    @MainActor private func proof(_ base: String, _ operation: String) async throws {
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + operation)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }
}
