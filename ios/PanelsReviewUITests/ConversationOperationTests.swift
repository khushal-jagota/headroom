import XCTest

/// One-off native detail and Dynamic Type reviews. Excluded from permanent release checks.
final class ConversationOperationTests: XCTestCase {
    /// One-off replacement proof through the real shell and navigation stream.
    /// Model checks cannot prove that the Chief page follows a changed server reference.
    @MainActor func testOwnerReplacementAgainstLocalStub() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        defer { app.terminate() }
        try await proof(base, "replacement/old")
        app.launch()
        // WorkspaceContextMenu wraps this link and drops its source identifier.
        // Use the unique Chief title, as the admitted Review screen walk does.
        let chief = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Chief of Staff'")).firstMatch
        XCTAssertTrue(chief.waitForExistence(timeout: 8)); chief.tap()
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 8))
        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        if input.value as? String != "Draft survives replacement" {
            input.tap(); input.typeText("Draft survives replacement")
        }
        line.tap()
        let old = app.staticTexts["Old Chief transcript"]
        let replacement = app.staticTexts["New Chief transcript"]
        XCTAssertTrue(old.waitForExistence(timeout: 8))
        capture(app, "replacement-before")
        try await proof(base, "replacement/clear")
        XCTAssertTrue(old.waitForNonExistence(timeout: 8), "An unlinked owner retained its old transcript")
        XCTAssertEqual(input.value as? String, "Draft survives replacement")
        capture(app, "replacement-cleared")
        try await proof(base, "replacement/new")
        XCTAssertTrue(replacement.waitForExistence(timeout: 8))
        XCTAssertFalse(old.exists)
        XCTAssertEqual(input.value as? String, "Draft survives replacement")
        capture(app, "replacement-after")

        // Persist the OLD navigation and display through the normal app before
        // relaunch. The fixture reads the owned app's store in read-only mode
        // and refuses the transition unless BOTH saved views still name OLD.
        // The server then returns NEW through the normal startup stream.
        try await proof(base, "replacement/old")
        XCTAssertTrue(old.waitForExistence(timeout: 8))
        XCUIDevice.shared.press(.home)
        XCTAssertTrue(app.wait(for: .runningBackground, timeout: 5))
        app.terminate()
        try await proof(base, "replacement/relaunch-new")
        app.launch()
        XCTAssertTrue(chief.waitForExistence(timeout: 8)); chief.tap()
        XCTAssertTrue(line.waitForExistence(timeout: 8)); line.tap()
        XCTAssertTrue(replacement.waitForExistence(timeout: 8))
        XCTAssertFalse(old.exists)
        XCTAssertEqual(input.value as? String, "Draft survives replacement")
        capture(app, "replacement-cold-fresh-new")

        // An explicit Review link must keep its historical destination.
        let back = app.buttons["detail-back"]
        back.tap(); XCTAssertTrue(line.waitForExistence(timeout: 5)); back.tap()
        let review = app.tabBars.buttons["Review"]
        XCTAssertTrue(review.waitForExistence(timeout: 5)); review.tap()
        try await proof(base, "replacement/unread")
        XCTAssertTrue(app.staticTexts["First unread message"].waitForExistence(timeout: 8))
        let open = app.buttons["review.openConversation"]
        XCTAssertTrue(open.waitForExistence(timeout: 8)); open.tap()
        XCTAssertTrue(old.waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts["Historical conversation · read only"].waitForExistence(timeout: 8))
        XCTAssertFalse(app.buttons["Send message"].exists)
        capture(app, "replacement-explicit-history")
        back.tap(); XCTAssertTrue(line.waitForExistence(timeout: 5)); back.tap()

        // Record the separate Review-card risk. This is an observation, not a
        // claim that this Ticket fixes Review or all failures on the phone.
        try await proof(base, "replacement/old")
        app.tabBars.buttons["Workspace"].tap()
        XCTAssertTrue(chief.waitForExistence(timeout: 5)); chief.tap()
        XCTAssertTrue(line.waitForExistence(timeout: 5)); line.tap()
        XCTAssertTrue(old.waitForExistence(timeout: 8))
        back.tap(); XCTAssertTrue(line.waitForExistence(timeout: 5)); back.tap()
        try await proof(base, "replacement/unread")
        review.tap()
        XCTAssertTrue(app.staticTexts["First unread message"].waitForExistence(timeout: 8))
        let historical = app.staticTexts["Historical conversation · read only"]
        XCTAssertTrue(historical.waitForNonExistence(timeout: 5))
        try await proof(base, "replacement/new")
        // Observe the historical state through the next streamed snapshots.
        // This observation can remain false without failing the Chief proof.
        _ = historical.waitForExistence(timeout: 3)
        let observation = "replacement/review-observation?historical=" + String(historical.exists)
            + "&reply=" + String(app.buttons["Send message"].exists)
        try await proof(base, observation)
        capture(app, "replacement-review-observation")
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/replacement/state")!)
        let state = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let tails = try XCTUnwrap(state["tails"] as? [String])
        XCTAssertTrue(tails.contains("stub")); XCTAssertTrue(tails.contains("replacement"))
        print("Replacement transcript requests: \(tails)")
    }

    /// Reuses the existing host to prove native push/pop, draft ownership and page
    /// scroll restoration. Host-only tests cannot exercise UIKit's edge gesture.
    @MainActor func testDetailPagePushAgainstLocalStub() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_CONVERSATION_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-smoke", "--conversation-stub", base, "--conversation-detail-pages"]
        try await proof(base, "header-fixture")
        for (name, title, appearance) in [
            ("Ticket", "Make the conversation experience feel settled", "light"),
            ("Orchestrator", "The left sidebar and shell design", "light"),
            ("Ticket", "Make the conversation experience feel settled", "dark"),
            ("Orchestrator", "The left sidebar and shell design", "dark"),
            ("Chief", "Chief of Staff", "dark")
        ] {
            try await proof(base, "appearance/" + appearance)
            app.launch()
            let link = app.buttons["\(name) fixture"]
            XCTAssertTrue(link.waitForExistence(timeout: 8)); link.tap()
            let back = app.buttons["detail-back"]
            let line = app.buttons["conversation-line"]
            XCTAssertTrue(line.waitForExistence(timeout: 8))
            let page = app.scrollViews.firstMatch
            let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
            let referenceBack = back.frame
            let referenceMenu = app.buttons[name == "Ticket" ? "ticket-actions" : "agent-page-menu"].frame
            capture(app, "after-\(name)-\(appearance)-detail")
            if input.value as? String != "Retained draft" {
                input.tap()
                input.typeText("Retained draft")
            }
            app.buttons["Choose model"].tap(); app.buttons["Cancel"].tap()
            page.swipeUp(velocity: .slow)
            let anchor = try XCTUnwrap(page.staticTexts.allElementsBoundByIndex.first(where: { $0.isHittable }))
            let originalY = anchor.frame.minY
            line.tap()
            let actions = app.buttons["conversation-actions"]
            XCTAssertTrue(actions.waitForExistence(timeout: 5))
            XCTAssertFalse(app.staticTexts[title].exists)
            XCTAssertFalse(app.buttons["Conversation lens"].exists)
            XCTAssertEqual(app.navigationBars.count, 0)
            assertFrame(back.frame, equals: referenceBack, context: "Shared Back control")
            assertFrame(actions.frame, equals: referenceMenu, context: "Shared actions control")
            XCTAssertTrue(app.staticTexts["Keep this message visible."].firstMatch.waitForExistence(timeout: 5))
            capture(app, "after-\(name)-\(appearance)-conversation")
            actions.tap()
            XCTAssertTrue(app.buttons["Focus"].waitForExistence(timeout: 3))
            app.buttons["Full"].tap()
            XCTAssertEqual(actions.value as? String, "Full")
            actions.tap(); app.buttons["Focus"].tap()
            XCTAssertEqual(actions.value as? String, "Focus")
            XCTAssertFalse(line.exists)
            XCTAssertEqual(input.value as? String, "Retained draft")
            capture(app, "\(name)-pushed")
            let edge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.002, dy: 0.5))
            edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.16, dy: 0.5)), withVelocity: .slow, thenHoldForDuration: 0.5)
            XCTAssertTrue(app.buttons["conversation-actions"].exists, "Short edge swipe must cancel")
            XCTAssertEqual(input.value as? String, "Retained draft")
            capture(app, "\(name)-cancelled-back")
            edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)), withVelocity: .slow, thenHoldForDuration: 0.1)
            XCTAssertTrue(line.waitForExistence(timeout: 5))
            XCTAssertEqual(app.navigationBars.count, 0)
            XCTAssertEqual(input.value as? String, "Retained draft")
            XCTAssertEqual(anchor.frame.minY, originalY, accuracy: 1)
            capture(app, "\(name)-returned")
            // Exercise the quiet Back button too, retaining the fixture draft.
            line.tap()
            XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 5))
            // Retain the isolated fixture draft; text deletion is not this review's gate.
            XCTAssertEqual(input.value as? String, "Retained draft")
            back.tap()
            XCTAssertTrue(line.waitForExistence(timeout: 5))
            page.swipeDown(velocity: .fast)
            if !back.isHittable { page.swipeDown(velocity: .fast) }
            let quietEdge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.005, dy: 0.45))
            // An ordinary horizontal content drag must not start a pop.
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.35, dy: 0.45)).press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.45)))
            XCTAssertTrue(back.exists)
            quietEdge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.16, dy: 0.45)), withVelocity: .slow, thenHoldForDuration: 0.5)
            XCTAssertTrue(back.exists, "Short quiet-page edge swipe must cancel")
            XCTAssertEqual(app.navigationBars.count, 0)
            capture(app, "\(name)-quiet-cancelled")
            quietEdge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.45)))
            let returned = link.waitForExistence(timeout: 2)
            print("FEEL current quiet \(name) edge-back returned: \(returned)")
            capture(app, "\(name)-quiet-edge-back")
            XCTAssertTrue(returned, "Quiet detail must support interactive back")
            link.tap()
            XCTAssertTrue(back.waitForExistence(timeout: 5))
            back.tap()
            XCTAssertTrue(link.waitForExistence(timeout: 5))
            app.terminate()
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
        let close = app.buttons["detail-back"]
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
