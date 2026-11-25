import XCTest

/// One-off review on the existing offline fixture, outside the permanent screen suite.
/// The system selection menu, clipboard handoff, and competing card gestures require
/// the real app. Host assertions about modifiers cannot prove this boundary.
final class TextSelectionTests: XCTestCase {
    /// Run with PANELS_ARTIFACT_DOCK_SMOKE=1 and the existing PanelsReview host.
    /// Copies stay inside this app: only the system Copy and Paste menus touch the clipboard.
    @MainActor func testCopyTextWithoutLosingNavigation() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/review-owner/ticket-split")!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        app.openReviewCards()

        // Copy one word, then expand the title range with the native selection handle.
        // Different pasted values prove a real range rather than a no-op long press.
        let reviewTitle = readable("Approve offline", in: app)
        XCTAssertTrue(reviewTitle.waitForExistence(timeout: 8))
        let reviewNote = app.descendants(matching: .any).matching(identifier: "review.revision").firstMatch
        XCTAssertTrue(reviewNote.waitForExistence(timeout: 5))
        reviewTitle.coordinate(withNormalizedOffset: CGVector(dx: 0.10, dy: 0.5)).press(forDuration: 1.1)
        let wordCopy = systemAction("Copy", in: app)
        XCTAssertTrue(wordCopy.waitForExistence(timeout: 3), "A native word selection must offer Copy")
        capture(app, "review-title-word-selection")
        wordCopy.tap()
        paste(into: reviewNote, expected: "Approve", in: app)
        clear(reviewNote, expected: "Approve", in: app)
        dismissKeyboard(in: app)

        // Drag across selected prose without the card consuming the gesture.
        let start = reviewTitle.coordinate(withNormalizedOffset: CGVector(dx: 0.75, dy: 0.5))
        let end = reviewTitle.coordinate(withNormalizedOffset: CGVector(dx: 0.12, dy: 0.5))
        start.press(forDuration: 1.1, thenDragTo: end)
        XCTAssertTrue(app.buttons["review.openTicket"].exists, "Text selection must not skip the Review card")
        XCTAssertTrue(reviewTitle.isHittable)
        // The first drag ends inside the first glyph. The native leading grabber
        // sits above that insertion point; move it past the text's leading edge.
        // The failed run's screenshot showed that Copy otherwise omits the initial A.
        reviewTitle.coordinate(withNormalizedOffset: CGVector(dx: 0.12, dy: -0.2))
            .press(forDuration: 0.1, thenDragTo: reviewTitle.coordinate(withNormalizedOffset: CGVector(dx: -0.05, dy: -0.2)))
        let rangeCopy = systemAction("Copy", in: app)
        XCTAssertTrue(rangeCopy.waitForExistence(timeout: 3), "The selection drag must leave a real native Copy action")
        capture(app, "review-title-selection-without-card-swipe")
        rangeCopy.tap()
        paste(into: reviewNote, expected: "Approve offline", in: app)
        capture(app, "review-title-expanded-range-pasted")
        clear(reviewNote, expected: "Approve offline", in: app)
        dismissKeyboard(in: app)
        app.buttons["review.openTicket"].tap()

        // A selectable title and recap must copy real text, then paste it into the
        // Ticket's existing note. No message or decision is submitted by this walk.
        let title = app.descendants(matching: .any).matching(identifier: "ticket-title").firstMatch
        XCTAssertTrue(title.waitForExistence(timeout: 8))
        let note = app.descendants(matching: .any).matching(identifier: "review.revision").firstMatch
        XCTAssertTrue(note.waitForExistence(timeout: 8))
        copy(title, in: app, capture: "ticket-title-system-menu")
        paste(into: note, expected: "Approve offline", in: app)
        capture(app, "ticket-title-copied-and-pasted")
        clear(note, expected: "Approve offline", in: app)
        dismissKeyboard(in: app)

        let recapContainer = app.descendants(matching: .any).matching(identifier: "ticket-recap").firstMatch
        XCTAssertTrue(recapContainer.waitForExistence(timeout: 5))
        // The recap's containing stack and its native text can share the identifier.
        // The readable text is the precise interaction target, not the disclosure.
        let recap = readable("Offline recovery proof", in: app)
        XCTAssertTrue(recap.waitForExistence(timeout: 5))
        copy(recap, in: app, capture: "ticket-recap-system-menu")
        paste(into: note, expected: "Offline recovery proof", in: app)
        capture(app, "ticket-recap-copied-and-pasted")
        clear(note, expected: "Offline recovery proof", in: app)
        dismissKeyboard(in: app)

        app.backButton.tap()
        XCTAssertTrue(app.buttons["review.openTicket"].waitForExistence(timeout: 5))

        // A deliberate swipe outside the text still skips the proposal card.
        let card = app.descendants(matching: .any).matching(identifier: "review-content").firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 5))
        let reviewRecap = readable("Offline recovery proof", in: app)
        XCTAssertTrue(reviewRecap.waitForExistence(timeout: 5))
        // The gesture belongs to the content stack, not the empty ScrollView below it.
        // Use the midpoint of its existing 18-point gap after the recap.
        let swipeY = reviewRecap.frame.maxY + 9 - card.frame.minY
        let swipeStart = card.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: card.frame.width * 0.92, dy: swipeY))
        let swipeEnd = card.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: card.frame.width * 0.12, dy: swipeY))
        swipeStart.press(forDuration: 0.05, thenDragTo: swipeEnd)
        let unread = readable("First unread message", in: app)
        XCTAssertTrue(unread.waitForExistence(timeout: 8), "The ordinary Review swipe must remain available")
        copy(unread, in: app, capture: "review-message-system-menu")
        let reply = app.textFields["conversation-input"].firstMatch
        paste(into: reply, expected: "First unread message", in: app)
        capture(app, "review-message-copied-and-pasted")
        clear(reply, expected: "First unread message", in: app)
        dismissKeyboard(in: app)

        // Open this same Ticket from the message card before the conversation visit.
        // That visit earns read credit, so it follows the Review message copy proof.
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        // The existing conversation row must still open on one ordinary tap.
        let conversationRow = app.buttons["conversation-line"]
        XCTAssertTrue(conversationRow.waitForExistence(timeout: 5))
        let preview = conversationRow.label
        XCTAssertFalse(preview.isEmpty)
        conversationRow.press(forDuration: 1.1)
        let controlCopy = systemAction("Copy", in: app)
        XCTAssertTrue(controlCopy.waitForExistence(timeout: 3), "The control must expose its own native Copy menu")
        capture(app, "conversation-row-native-copy-menu")
        controlCopy.tap()
        XCTAssertTrue(title.exists, "Copy from the row must not navigate")
        paste(into: note, expected: preview, in: app)
        capture(app, "conversation-row-name-copied-without-navigation")
        clear(note, expected: preview, in: app)
        dismissKeyboard(in: app)
        conversationRow.tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 8))
        capture(app, "conversation-row-opened-with-one-tap")
        let message = readable("Plain update 1", in: app)
        XCTAssertTrue(message.waitForExistence(timeout: 8))
        copy(message, in: app, capture: "conversation-message-system-menu")
        let composer = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        paste(into: composer, expected: "Plain update 1", in: app)
        capture(app, "conversation-message-copied-and-pasted")
        clear(composer, expected: "Plain update 1", in: app)
        dismissKeyboard(in: app)
        app.backButton.tap()
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        app.backButton.tap()

        // Static text in a native Settings List must also offer the system menu.
        app.openDestination("sidebar-settings")
        let server = readable("Panels server", in: app)
        XCTAssertTrue(server.waitForExistence(timeout: 8))
        copy(server, in: app, capture: "settings-list-text-system-menu")
        app.openDestination("sidebar-chief")
        let chiefInput = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(chiefInput.waitForExistence(timeout: 8))
        paste(into: chiefInput, expected: "Panels server", in: app)
        capture(app, "settings-list-text-copied-and-pasted")
    }

    /// Run separately with PANELS_GOALS_SMOKE=1 on the same existing loopback host.
    /// Canvas digits and a native navigation label need real selection overlays.
    @MainActor func testCopyGoalReadingAndNavigationTitle() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        app.openDestination("sidebar-chief")
        let goalRow = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "Write 1,000 words")).firstMatch
        XCTAssertTrue(goalRow.waitForExistence(timeout: 10))
        for _ in 0..<4 where goalRow.frame.maxY > app.frame.height * 0.6 {
            app.swipeUp(velocity: .slow)
        }
        goalRow.tap()
        let goalScreen = app.descendants(matching: .any).matching(identifier: "goal-screen").firstMatch
        XCTAssertTrue(goalScreen.waitForExistence(timeout: 8), "A tap on the useful Goal row must still navigate")
        // The unchanged fixture initializes the daily reading to 400. Select the
        // native overlay itself, rather than its Canvas or accessibility-only parent.
        let reading = app.textViews.matching(NSPredicate(format: "value == %@ OR label == %@", "400", "400")).firstMatch
        XCTAssertTrue(reading.waitForExistence(timeout: 8), "The drawn value needs a real native selection surface")
        copy(reading, in: app, capture: "goal-canvas-reading-system-menu")
        let goalComposer = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(goalComposer.waitForExistence(timeout: 5))
        paste(into: goalComposer, expected: "400", in: app)
        capture(app, "goal-canvas-reading-copied-and-pasted")
        clear(goalComposer, expected: "400", in: app)
        dismissKeyboard(in: app)
        app.backButton.tap()
        XCTAssertTrue(goalScreen.waitForNonExistence(timeout: 5))

        app.openDestination("sidebar-settings")
        let navigationTitle = app.navigationBars.firstMatch.descendants(matching: .textView)
            .matching(NSPredicate(format: "value == %@ OR label == %@", "Settings", "Settings")).firstMatch
        XCTAssertTrue(navigationTitle.waitForExistence(timeout: 8), "The native bar title needs a selectable text overlay")
        copy(navigationTitle, in: app, capture: "settings-navigation-title-system-menu")
        app.openDestination("sidebar-chief")
        let chiefComposer = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(chiefComposer.waitForExistence(timeout: 5))
        paste(into: chiefComposer, expected: "Settings", in: app)
        capture(app, "settings-navigation-title-copied-and-pasted")
        clear(chiefComposer, expected: "Settings", in: app)

        // Copy/paste uses unsent local drafts. It never changes a Goal or submits a message.
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        let writes = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [Any])
        let paths = writes.compactMap { ($0 as? [String: Any])?["path"] as? String }
        XCTAssertFalse(paths.contains { $0.contains("manual-writes") || $0.hasSuffix("/messages") || $0.hasSuffix("/send") })
    }

    @MainActor private func readable(_ text: String, in app: XCUIApplication) -> XCUIElement {
        // Native selectable prose is a UITextView. Existing SwiftUI Text remains
        // a staticText where the OS already supplies its selection menu.
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "value == %@ OR label == %@", text, text)).firstMatch
    }

    @MainActor private func systemAction(_ title: String, in app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", title)).firstMatch
    }

    @MainActor private func copy(_ text: XCUIElement, in app: XCUIApplication, capture name: String) {
        XCTAssertTrue(text.isHittable)
        if text.elementType == .textView {
            // These fixture values each occupy one line. Start on the final word
            // and extend its range past the first glyph. The native menu can hide
            // Select All in overflow, so its first page does not prove a full range.
            text.coordinate(withNormalizedOffset: CGVector(dx: 0.98, dy: 0.5))
                .press(forDuration: 1.1, thenDragTo: text.coordinate(withNormalizedOffset: CGVector(dx: -0.05, dy: 0.5)))
        } else {
            // SwiftUI's static text uses the system's whole-text Copy menu.
            text.press(forDuration: 1.1)
        }
        let copy = systemAction("Copy", in: app)
        XCTAssertTrue(copy.waitForExistence(timeout: 3), "The real system Copy menu must be available")
        capture(app, name)
        copy.tap()
    }

    @MainActor private func paste(into field: XCUIElement, expected text: String, in app: XCUIApplication) {
        field.tap()
        field.press(forDuration: 1.1)
        let paste = systemAction("Paste", in: app)
        XCTAssertTrue(paste.waitForExistence(timeout: 3), "Paste must consume the app's own system Copy result")
        paste.tap()
        let exact = NSPredicate(format: "value == %@", text)
        expectation(for: exact, evaluatedWith: field)
        waitForExpectations(timeout: 3)
    }

    @MainActor private func clear(_ field: XCUIElement, expected text: String, in app: XCUIApplication) {
        // Paste leaves the insertion point at the end of the text just proved above.
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: text.count))
    }

    @MainActor private func dismissKeyboard(in app: XCUIApplication) {
        // A normal drag dismisses the keyboard without an app-specific action.
        if app.keyboards.firstMatch.exists { app.swipeDown(velocity: .slow) }
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
