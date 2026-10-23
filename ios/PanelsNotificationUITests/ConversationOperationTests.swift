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
        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        let keyboardVisible = NSPredicate { _, _ in
            MainActor.assumeIsolated { app.keyboards.firstMatch.frame.minY < app.frame.height - 200 }
        }
        input.tap(); input.typeText("Keep this draft")
        app.buttons["Choose model"].tap(); app.buttons["Cancel"].tap()
        let originalPosition = worker.frame.minY
        line.tap()
        let close = app.navigationBars.buttons.firstMatch
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        XCTAssertFalse(line.exists, "Conversation page must not repeat the resting line")
        XCTAssertEqual(input.value as? String, "Keep this draft")
        close.tap()
        XCTAssertTrue(line.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "Keep this draft")
        XCTAssertEqual(worker.frame.minY, originalPosition, accuracy: 1, "Push changed the page scroll position")
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

        // The page composer must leave with its page and restore that page's draft.
        input.tap(); input.typeText("Return to this draft")
        app.buttons["Choose model"].tap(); app.buttons["Cancel"].tap()
        page.swipeUp()
        app.buttons["Worker details"].tap()
        XCTAssertTrue(input.waitForNonExistence(timeout: 5))
        app.navigationBars["Worker details"].buttons.firstMatch.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "Return to this draft")

    }

    /// Reuses the existing host to prove native push/pop, draft ownership and page
    /// scroll restoration. Host-only tests cannot exercise UIKit's edge gesture.
    @MainActor func testDetailPagePushAgainstLocalStub() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_CONVERSATION_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-smoke", "--conversation-stub", base, "--conversation-detail-pages"]
        app.launch()
        for (name, title) in [
            ("Ticket", "Make the conversation experience feel settled"),
            ("Orchestrator", "The left sidebar and shell design"),
            ("Chief", "Chief of Staff")
        ] {
            let link = app.buttons["\(name) fixture"]
            XCTAssertTrue(link.waitForExistence(timeout: 8)); link.tap()
            let back = app.buttons["detail-back"]
            let line = app.buttons["conversation-line"]
            XCTAssertTrue(line.waitForExistence(timeout: 8))
            let page = app.scrollViews.firstMatch
            let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
            input.tap(); input.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: (input.value as? String ?? "").count))
            input.typeText("Retained draft")
            app.buttons["Choose model"].tap(); app.buttons["Cancel"].tap()
            page.swipeUp(velocity: .slow)
            let anchor = try XCTUnwrap(page.staticTexts.allElementsBoundByIndex.first(where: { $0.isHittable }))
            let originalY = anchor.frame.minY
            try await proof(base, "start/\(name.lowercased())-push-back")
            line.tap()
            XCTAssertTrue(app.buttons["Conversation lens"].waitForExistence(timeout: 5))
            XCTAssertTrue(app.staticTexts[title].exists)
            XCTAssertEqual(app.navigationBars.count, 1)
            XCTAssertFalse(line.exists)
            XCTAssertEqual(input.value as? String, "Retained draft")
            capture(app, "\(name)-pushed")
            let edge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.002, dy: 0.5))
            edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)), withVelocity: .slow, thenHoldForDuration: 0.1)
            XCTAssertTrue(line.waitForExistence(timeout: 5))
            XCTAssertEqual(app.navigationBars.count, 0)
            XCTAssertEqual(input.value as? String, "Retained draft")
            XCTAssertEqual(anchor.frame.minY, originalY, accuracy: 1)
            capture(app, "\(name)-returned")
            try await proof(base, "stop")
            // Exercise the standard back button too, and clear only this fixture draft.
            line.tap()
            XCTAssertTrue(app.buttons["Conversation lens"].waitForExistence(timeout: 5))
            input.coordinate(withNormalizedOffset: CGVector(dx: 0.98, dy: 0.5)).tap()
            input.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: "Retained draft".count))
            XCTAssertEqual(input.value as? String, "")
            app.navigationBars.buttons.firstMatch.tap()
            XCTAssertTrue(line.waitForExistence(timeout: 5))
            page.swipeDown(velocity: .fast)
            if !back.isHittable { page.swipeDown(velocity: .fast) }
            back.tap()
        }
        app.terminate()
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
        let close = app.navigationBars.buttons.firstMatch
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
        capture(app, "recovery-system-transcript")
        XCTAssertTrue(app.staticTexts["Panels deployment recovery"].isHittable)
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
