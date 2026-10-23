import XCTest

/// One connected UI proof for keyboard geometry, scrolling, and page transitions.
/// Host tests own delivery decisions; this test needs a real scroll view and server.
final class ConversationOperationTests: XCTestCase {
    @MainActor func testOperationsAgainstLocalStub() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_CONVERSATION_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        try await proof(base, "reset")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-smoke", "--conversation-stub", base, "--conversation-fixture-speech"]
        app.launch()
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 8))
        try await proof(base, "start/open-close")
        let page = app.scrollViews["conversation-page"]
        page.swipeUp()
        let worker = app.staticTexts["Worker 8"]
        XCTAssertTrue(worker.isHittable)
        let originalPosition = worker.frame.minY
        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        let restingInputFrame = input.frame
        line.tap()
        let close = app.buttons["Close conversation"]
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        assertFrame(input.frame, equals: restingInputFrame, context: "Expand with keyboard closed")
        XCTAssertFalse(line.exists, "Expanded chat must not show the resting status line")
        XCTAssertLessThan(close.frame.minY, 100, "Chat header must occupy the full screen")
        let header = app.staticTexts["conversation-header"]
        let start = header.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        start.press(forDuration: 0.1, thenDragTo: start.withOffset(CGVector(dx: 0, dy: 45)), withVelocity: .slow, thenHoldForDuration: 0.5)
        XCTAssertTrue(close.exists, "A short pull should return the chat to its open position")
        close.tap()
        XCTAssertTrue(line.waitForExistence(timeout: 5))
        XCTAssertEqual(worker.frame.minY, originalPosition, accuracy: 1, "Opening chat changed the page's scroll position")
        line.swipeUp()
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        let swipeStart = header.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        swipeStart.press(forDuration: 0.1, thenDragTo: swipeStart.withOffset(CGVector(dx: 0, dy: 350)), withVelocity: .slow, thenHoldForDuration: 0.2)
        XCTAssertTrue(line.waitForExistence(timeout: 5))
        XCTAssertEqual(worker.frame.minY, originalPosition, accuracy: 1, "Swiping down changed the page's scroll position")
        input.tap(); input.typeText("Keep this draft")
        XCTAssertTrue(app.keyboards.firstMatch.exists, "Growing the composer dropped keyboard focus")
        let keyboardVisible = NSPredicate { _, _ in
            MainActor.assumeIsolated {
                app.keyboards.firstMatch.frame.minY < app.frame.height - 200
            }
        }
        await fulfillment(of: [expectation(for: keyboardVisible, evaluatedWith: nil)], timeout: 5)
        let focusedCapture = XCTAttachment(screenshot: app.screenshot())
        focusedCapture.name = "collapsed-focused-input"; focusedCapture.lifetime = .keepAlways; add(focusedCapture)
        let focusedInputFrame = input.frame
        XCTAssertLessThan(input.frame.maxY, app.keyboards.firstMatch.frame.minY, "Collapsed input must stay above the keyboard")
        line.tap()
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        XCTAssertTrue(app.keyboards.firstMatch.exists, "Expanding replaced or unfocused the input")
        assertFrame(input.frame, equals: focusedInputFrame, context: "Expand with keyboard open")
        XCTAssertEqual(app.descendants(matching: .any).matching(identifier: "conversation-input").count, 1)
        // Synthetic typeText can retract the simulator's software keyboard.
        // Use its real key so this measures closing with the keyboard still up.
        app.keyboards.firstMatch.keys["x"].tap()
        XCTAssertLessThan(app.keyboards.firstMatch.frame.minY, app.frame.height - 200)
        assertFrame(input.frame, equals: focusedInputFrame, context: "Typing while expanded")
        app.keyboards.firstMatch.keys["space"].tap() // Commit autocorrection before comparing the retained draft.
        let expandedDraft = input.value as? String
        close.tap()
        XCTAssertEqual(input.value as? String, expandedDraft, "Collapsing replaced the draft")
        XCTAssertLessThan(app.keyboards.firstMatch.frame.minY, app.frame.height - 200, "Collapsing dismissed the keyboard")
        assertFrame(input.frame, equals: focusedInputFrame, context: "Collapse with keyboard open")
        app.keyboards.firstMatch.keys["y"].tap()
        XCTAssertTrue((input.value as? String)?.hasSuffix("y") == true, "Typing did not continue in the same input")
        try await proof(base, "stop")

        try await proof(base, "start/send-stream")
        line.tap()
        app.buttons["Choose model"].tap()
        XCTAssertTrue(app.navigationBars["Model"].waitForExistence(timeout: 5))
        app.buttons["Cancel"].tap()
        app.buttons["Conversation lens"].tap()
        app.buttons["Send message"].tap()
        XCTAssertTrue(app.staticTexts["Sending…"].firstMatch.waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["Sending…"].firstMatch.waitForNonExistence(timeout: 8))
        XCTAssertFalse(app.staticTexts["Sent"].firstMatch.exists)
        let thread = app.scrollViews["conversation-thread"]
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
        app.buttons["Steer"].tap()
        XCTAssertTrue(app.buttons["Send now"].waitForExistence(timeout: 3))
        app.buttons["Queue"].tap()
        XCTAssertTrue(app.buttons["Send now"].waitForNonExistence(timeout: 3))
        app.buttons["Send message"].tap()
        XCTAssertTrue(app.staticTexts["Queued"].firstMatch.waitForExistence(timeout: 5))
        try await Task.sleep(for: .seconds(7))
        XCTAssertFalse(app.staticTexts["Queued"].firstMatch.exists)
        input.tap(); input.typeText("Remove this queued message")
        app.buttons["Send message"].tap()
        XCTAssertTrue(app.buttons["Remove queued message"].waitForExistence(timeout: 6))
        capture(app, "queue-controls-before-relaunch")
        app.terminate(); app.launch()
        XCTAssertTrue(app.buttons["Remove queued message"].waitForExistence(timeout: 8))
        capture(app, "queue-controls-after-relaunch")
        line.tap()
        app.buttons["Remove queued message"].tap()
        XCTAssertTrue(app.staticTexts["Not delivered · no longer applicable"].firstMatch.waitForExistence(timeout: 5))
        input.tap(); input.typeText("Steer this queued message")
        app.buttons["Steer"].tap()
        XCTAssertTrue(app.buttons["Send now"].waitForExistence(timeout: 3))
        app.buttons["Queue"].tap()
        XCTAssertTrue(app.buttons["Send now"].waitForNonExistence(timeout: 3))
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
        XCTAssertTrue(app.staticTexts["Not sent · no longer applicable"].firstMatch.waitForExistence(timeout: 5))
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
        capture(app, "settled-composer")
        try await proof(base, "stop")

        // Parent and thread refresh failures share one status above the fixed input.
        input.tap(); input.typeText("Offline draft")
        await fulfillment(of: [expectation(for: keyboardVisible, evaluatedWith: nil)], timeout: 5)
        let offlineFrame = input.frame
        try await proof(base, "offline")
        XCTAssertTrue(app.buttons["Try again"].waitForExistence(timeout: 8))
        XCTAssertEqual(app.buttons.matching(identifier: "Try again").count, 1)
        assertFrame(input.frame, equals: offlineFrame, context: "Offline status appears")
        capture(app, "offline-single-status")
        try await proof(base, "online")
        XCTAssertTrue(app.buttons["Try again"].waitForNonExistence(timeout: 8))
        assertFrame(input.frame, equals: offlineFrame, context: "Connection recovers")
        capture(app, "online-stable-input")
        input.tap(); input.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: "Offline draft".count))

        // The root overlay must leave with its page and restore that page's draft.
        input.tap(); input.typeText("Return to this draft")
        app.buttons["Choose model"].tap(); app.buttons["Cancel"].tap()
        page.swipeUp()
        app.buttons["Worker details"].tap()
        XCTAssertTrue(input.waitForNonExistence(timeout: 5))
        app.navigationBars["Worker details"].buttons.firstMatch.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "Return to this draft")

    }

    /// Scaled layout needs a native render; reuse the same host and loopback fixture.
    /// Keeping this separate lets geometry fixes run without replaying delivery waits.
    @MainActor func testLargeTextAndAttributionAgainstLocalStub() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_CONVERSATION_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-smoke", "--conversation-stub", base, "--conversation-fixture-speech"]
        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        let line = app.buttons["conversation-line"]
        let close = app.buttons["Close conversation"]
        let keyboardVisible = NSPredicate { _, _ in
            MainActor.assumeIsolated { app.keyboards.firstMatch.frame.minY < app.frame.height - 200 }
        }
        // Same host and fixture, maximum Dynamic Type, no simulator preference changes.
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        capture(app, "largest-text-resting")
        input.tap(); input.typeText("Large text draft")
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap()
        let largeKeyboard = app.keyboards.firstMatch
        XCTAssertTrue(largeKeyboard.waitForExistence(timeout: 5))
        await fulfillment(of: [expectation(for: keyboardVisible, evaluatedWith: nil)], timeout: 5)
        for name in ["Choose model", "Steer", "Add attachment or command", "Record voice input", "Send message"] {
            let button = app.buttons[name]
            XCTAssertTrue(button.isHittable, "Large text hid \(name)")
            XCTAssertGreaterThanOrEqual(button.frame.minX, 0)
            XCTAssertLessThanOrEqual(button.frame.maxX, app.frame.width)
            XCTAssertLessThanOrEqual(button.frame.maxY, largeKeyboard.frame.minY)
        }
        capture(app, "largest-text-composer")
        line.tap()
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        capture(app, "largest-text-expanded")
        close.tap()
        app.terminate()
        app.launchArguments.removeLast(2)
        app.launch()
        XCTAssertTrue(line.waitForExistence(timeout: 8))
        try await proof(base, "recovery")
        let recoveryLine = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Panels deployment recovery:'")).firstMatch
        XCTAssertTrue(recoveryLine.waitForExistence(timeout: 5))
        capture(app, "recovery-system-rest-line")
        line.tap()
        XCTAssertTrue(app.staticTexts["Panels deployment recovery"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["You: Inspect the interrupted work and continue the current authorized step."].exists)
        XCTAssertTrue(app.staticTexts["Panels deployment recovery"].isHittable)
        capture(app, "recovery-system-transcript")
        close.tap()
        // Both cases share the persisted draft in this loopback host.
        input.coordinate(withNormalizedOffset: CGVector(dx: 0.98, dy: 0.5)).tap()
        input.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: "Large text draft".count))
        XCTAssertEqual(input.value as? String, "")
        app.terminate()
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }

    private func assertFrame(_ actual: CGRect, equals expected: CGRect, context: String,
                             file: StaticString = #filePath, line: UInt = #line) {
        print("Input frame — \(context): before=\(expected), after=\(actual)")
        XCTAssertEqual(actual.minX, expected.minX, accuracy: 1, context, file: file, line: line)
        XCTAssertEqual(actual.minY, expected.minY, accuracy: 1, context, file: file, line: line)
        XCTAssertEqual(actual.width, expected.width, accuracy: 1, context, file: file, line: line)
        XCTAssertEqual(actual.height, expected.height, accuracy: 1, context, file: file, line: line)
    }

    @MainActor private func proof(_ base: String, _ operation: String) async throws {
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + operation)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }
}
