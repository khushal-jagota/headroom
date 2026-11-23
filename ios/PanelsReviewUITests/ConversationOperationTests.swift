import XCTest

/// One-off native detail and Dynamic Type reviews. Excluded from permanent release checks.
final class ConversationOperationTests: XCTestCase {
    /// One-off gesture review: inert dismissal must not replace native text selection
    /// or swallow composer controls. The existing conversation host supplies the content.
    @MainActor func testSelectableConversationTextAndControlsKeepTheirActions() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_CONVERSATION_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        try await proof(base, "reset")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-smoke", "--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 8)); line.tap()
        let prose = app.staticTexts["The plan is ready. What should I work on next?"]
        XCTAssertTrue(prose.waitForExistence(timeout: 8))
        let input = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap(); input.typeText("Keep this selection draft")
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        XCTAssertTrue(prose.isHittable)
        prose.tap()
        XCTAssertTrue(app.keyboards.firstMatch.exists, "Selectable message prose is not an inert dismissal surface")
        XCTAssertEqual(input.value as? String, "Keep this selection draft")
        prose.press(forDuration: 1.2)
        let copy = app.menuItems["Copy"].firstMatch
        XCTAssertTrue(copy.waitForExistence(timeout: 5), "Message text must retain its native selection menu")
        capture(app, "focus-review-selectable-message")
        copy.tap()
        XCTAssertEqual(input.value as? String, "Keep this selection draft")
        input.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        let actions = app.buttons["conversation-actions"]
        actions.tap()
        let full = app.buttons["Full"]
        XCTAssertTrue(full.waitForExistence(timeout: 5)); full.tap()
        XCTAssertEqual(actions.value as? String, "Full")
        XCTAssertEqual(input.value as? String, "Keep this selection draft")
        app.buttons["Add attachment or command"].tap()
        let command = app.buttons["Command"]
        XCTAssertTrue(command.waitForExistence(timeout: 5))
        capture(app, "attach-sheet")
        command.tap()
        XCTAssertEqual(input.value as? String, "/")
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5), "Command must still request input focus")
        capture(app, "focus-review-command-and-input")
    }

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
        app.openDestination("sidebar-chief", timeout: 8)
        let chiefScreen = app.descendants(matching: .any)["chief-screen"]
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 8))
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
        // The app saves its view in a task as it leaves the front. Wait for that save, so
        // ending the app does not race it.
        var saved = false
        for _ in 0..<50 where !saved {
            let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/replacement/saved")!)
            saved = (response as? HTTPURLResponse)?.statusCode == 200
            if !saved { try await Task.sleep(for: .milliseconds(200)) }
        }
        XCTAssertTrue(saved, "The app saved its old Chief reference")
        app.terminate()
        try await proof(base, "replacement/relaunch-new")
        app.launch()
        app.openDestination("sidebar-chief", timeout: 8)
        XCTAssertTrue(line.waitForExistence(timeout: 8)); line.tap()
        XCTAssertTrue(replacement.waitForExistence(timeout: 8))
        XCTAssertFalse(old.exists)
        XCTAssertEqual(input.value as? String, "Draft survives replacement")
        capture(app, "replacement-cold-fresh-new")

        // An explicit Review link must keep its historical destination.
        let back = app.backButton
        back.tap(); XCTAssertTrue(line.waitForExistence(timeout: 5))
        app.openDestination("sidebar-review", timeout: 5)
        try await proof(base, "replacement/unread")
        app.openReviewCards()
        XCTAssertTrue(app.staticTexts["First unread message"].waitForExistence(timeout: 8))
        let open = app.buttons["review.openConversation"]
        XCTAssertTrue(open.waitForExistence(timeout: 8)); open.tap()
        XCTAssertTrue(old.waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts["Historical conversation · read only"].waitForExistence(timeout: 8))
        XCTAssertFalse(app.buttons["Send message"].exists)
        capture(app, "replacement-explicit-history")
        // The conversation opens on top of Review, so Back returns to the card.
        back.tap(); XCTAssertTrue(open.waitForExistence(timeout: 5))

        // Record the separate Review-card risk. This is an observation, not a
        // claim that this Ticket fixes Review or all failures on the phone.
        try await proof(base, "replacement/old")
        app.openDestination("sidebar-chief", timeout: 5)
        XCTAssertTrue(line.waitForExistence(timeout: 5)); line.tap()
        XCTAssertTrue(old.waitForExistence(timeout: 8))
        back.tap(); XCTAssertTrue(line.waitForExistence(timeout: 5))
        try await proof(base, "replacement/unread")
        app.openDestination("sidebar-review", timeout: 5)
        app.openReviewCards()
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
            let back = app.backButton
            let line = app.buttons["conversation-line"]
            XCTAssertTrue(line.waitForExistence(timeout: 8))
            XCTAssertTrue(app.buttons[name == "Ticket" ? "ticket-actions" : "agent-page-menu"].exists)
            let page = app.scrollViews.firstMatch
            let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
            capture(app, "after-\(name)-\(appearance)-detail")
            if name == "Orchestrator" && appearance == "light" {
                // The Manager page actions open the same kind of sheet as the Ticket actions.
                app.buttons["agent-page-menu"].tap()
                XCTAssertTrue(app.buttons["agent-action-recap"].waitForExistence(timeout: 5))
                XCTAssertTrue(app.buttons["agent-action-settle"].exists)
                capture(app, "manager-actions-sheet")
                app.buttons["Cancel"].tap()
                XCTAssertTrue(app.buttons["agent-action-recap"].waitForNonExistence(timeout: 5))
            }
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
            XCTAssertTrue(back.exists)
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
            XCTAssertEqual(input.value as? String, "Retained draft")
            XCTAssertEqual(anchor.frame.minY, originalY, accuracy: 1)
            capture(app, "\(name)-returned")
            // Exercise the bar's Back button too, retaining the fixture draft.
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
        let close = app.backButton
        let keyboardVisible = NSPredicate { _, _ in
            MainActor.assumeIsolated { app.keyboards.firstMatch.frame.minY < app.frame.height - 200 }
        }
        // Same host and fixture, maximum Dynamic Type, no simulator preference changes.
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        capture(app, "largest-text-resting")
        // One tap: a second tap on the focused field opens the system edit menu, which
        // covers the conversation line.
        input.tap(); input.typeText("Large text draft")
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        let largeKeyboard = app.keyboards.firstMatch
        XCTAssertTrue(largeKeyboard.waitForExistence(timeout: 5))
        await fulfillment(of: [expectation(for: keyboardVisible, evaluatedWith: nil)], timeout: 5)
        for name in ["Choose model", "Add attachment or command", "Record voice input", "Send message"] {
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

    @MainActor private func proof(_ base: String, _ operation: String) async throws {
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + operation)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }
}
