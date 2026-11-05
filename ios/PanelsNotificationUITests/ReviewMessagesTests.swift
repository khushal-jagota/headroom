import XCTest

/// Reuses the offline harness: real SwiftUI, persisted outbox and loopback HTTP only.
final class ReviewMessagesTests: XCTestCase {
    /// Native layout, keyboard dismissal and interactive pop require the real SwiftUI host.
    /// Reuses the offline HTTP fixture; no additional browser or server host.
    @MainActor func testReviewConversationLinkAndComposer() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base, "--conversation-fixture-speech", "--conversation-fixture-silence"]
        defer { app.terminate() }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
        }
        for owner in ["ticket", "manager"] {
            _ = try await URLSession.shared.data(from: URL(string: base + "/proof/review-owner/" + owner)!)
            app.launch()
            XCTAssertTrue(app.tabBars.buttons["Review"].waitForExistence(timeout: 10))
            app.tabBars.buttons["Review"].tap()
            let input = app.textFields["conversation-input"].firstMatch
            let box = app.otherElements["conversation-input-box"].firstMatch
            XCTAssertTrue(input.waitForExistence(timeout: 10))
            app.swipeUp()
            let collapsedHeight = box.frame.height
            XCTAssertEqual(collapsedHeight, 48, accuracy: 2)
            capture(owner + "-review-collapsed")
            input.tap(); input.typeText("A reply that expands the shared input box.")
            XCTAssertTrue(app.keyboards.firstMatch.exists)
            XCTAssertGreaterThanOrEqual(box.frame.height, 95)
            capture(owner + "-review-expanded")
            input.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 60))
            let collapseDeadline = Date().addingTimeInterval(3)
            while box.frame.height > collapsedHeight + 2, Date() < collapseDeadline {
                try await Task.sleep(for: .milliseconds(100))
            }
            XCTAssertEqual(box.frame.height, collapsedHeight, accuracy: 2)
            XCTAssertTrue(app.keyboards.firstMatch.exists)
            app.buttons["Record voice input"].firstMatch.tap()
            XCTAssertTrue(app.buttons["Stop recording"].waitForExistence(timeout: 5))
            XCTAssertFalse(app.keyboards.firstMatch.exists)
            app.buttons["Stop recording"].tap()
            let feedback = app.staticTexts["No speech heard. Try again."]
            XCTAssertTrue(feedback.waitForExistence(timeout: 6))
            XCTAssertTrue(box.frame.contains(feedback.frame))
            XCTAssertTrue(box.frame.contains(app.buttons["Try again"].frame))
            capture(owner + "-review-feedback")
            app.buttons["review.openConversation"].tap()
            XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 10))
            XCTAssertTrue(input.waitForExistence(timeout: 8))
            XCTAssertEqual(app.otherElements["conversation-composer-region"].frame.height, collapsedHeight, accuracy: 2)
            XCTAssertEqual(app.navigationBars.count, 0)
            XCTAssertFalse(app.tabBars.firstMatch.isHittable)
            capture(owner + "-conversation")
            app.buttons["Record voice input"].firstMatch.tap()
            XCTAssertTrue(app.buttons["Stop recording"].waitForExistence(timeout: 5))
            app.buttons["Stop recording"].tap()
            XCTAssertTrue(feedback.waitForExistence(timeout: 6))
            XCTAssertTrue(app.otherElements["conversation-composer-region"].frame.contains(feedback.frame))
            capture(owner + "-conversation-feedback")
            // A cancelled pop remains in the conversation; a completed pop reveals its owner.
            let edge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.005, dy: 0.45))
            edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.16, dy: 0.45)), withVelocity: .slow, thenHoldForDuration: 0.5)
            XCTAssertTrue(app.buttons["conversation-actions"].exists)
            edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.45)))
            let title = owner == "ticket" ? "Approve offline" : "Review orchestrator"
            XCTAssertTrue(app.staticTexts[title].waitForExistence(timeout: 8))
            XCTAssertFalse(app.buttons["conversation-actions"].exists)
            capture(owner + "-back-to-owner")
            app.buttons["conversation-line"].tap()
            XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 8))
            input.tap(); input.typeText("Keep this normal conversation draft")
            app.buttons["detail-back"].tap()
            XCTAssertTrue(app.staticTexts[title].waitForExistence(timeout: 8))
            XCTAssertEqual(input.value as? String, "Keep this normal conversation draft")
            app.buttons["detail-back"].tap()
            XCTAssertTrue(app.tabBars.buttons["Review"].waitForExistence(timeout: 8))
            app.tabBars.buttons["Review"].tap()
            XCTAssertTrue(["", "Reply…"].contains(input.value as? String ?? "missing"))
            app.terminate()
        }
        _ = try await URLSession.shared.data(from: URL(string: base + "/proof/review-owner/chief")!)
    }

    /// One connected case proves the finished view's count and queue-restoring button,
    /// which the queue model alone cannot prove. Reuses the existing loopback host.
    @MainActor func testCompletionAndRevisit() async throws {
        guard ProcessInfo.processInfo.environment["PANELS_REVIEW_COMPLETION"] == "1" else {
            throw XCTSkip("Run with the completion fixture")
        }
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.tabBars.buttons["Review"].waitForExistence(timeout: 10))
        app.tabBars.buttons["Review"].tap()
        for title in ["Approve offline", "Send back offline", "Record offline"] {
            XCTAssertTrue(app.buttons["review.openTicket"].waitForExistence(timeout: 10))
            XCTAssertTrue(app.buttons["review.openTicket"].label.contains(title))
            app.buttons["review.skip"].tap()
        }
        let revisit = app.buttons["review.revisitSkipped"]
        XCTAssertTrue(revisit.waitForExistence(timeout: 8))
        XCTAssertEqual(revisit.label, "Revisit 3 skipped items")
        XCTAssertTrue(app.staticTexts["You’re caught up."].exists)
        XCTAssertFalse(app.staticTexts["7 agents in progress"].exists)
        XCTAssertTrue(revisit.isHittable)
        XCTAssertLessThan(revisit.frame.maxY, app.tabBars.firstMatch.frame.minY)
        let skipped = XCTAttachment(screenshot: app.screenshot())
        skipped.name = "review-finished-secondary-action"; skipped.lifetime = .keepAlways; add(skipped)
        let (before, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        XCTAssertEqual(try JSONDecoder().decode([String].self, from: before), [])
        revisit.tap()
        // Revisit must actually return the original asks, then completion must clear
        // the button once the approved asks leave the server queue.
        for title in ["Approve offline", "Send back offline", "Record offline"] {
            XCTAssertTrue(app.buttons["review.approve"].waitForExistence(timeout: 10))
            XCTAssertTrue(app.buttons["review.openTicket"].label.contains(title))
            app.buttons["review.approve"].tap()
            let confirm = app.buttons["review.confirmApproval"]
            XCTAssertTrue(confirm.waitForExistence(timeout: 5))
            confirm.tap()
        }
        XCTAssertTrue(app.staticTexts["You’re caught up."].waitForExistence(timeout: 10))
        XCTAssertFalse(revisit.exists)
        let empty = XCTAttachment(screenshot: app.screenshot())
        empty.name = "review-finished-no-skips"; empty.lifetime = .keepAlways; add(empty)
        let (after, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        XCTAssertEqual(try JSONDecoder().decode([String].self, from: after), ["approve", "approve", "approve"])
    }

    @MainActor func testLiveReadOnlyWorkspaceAndReview() throws {
        guard ProcessInfo.processInfo.environment["PANELS_LIVE_READ_ONLY"] == "1" else { throw XCTSkip("Explicit read-only live pass only") }
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()
        let chief = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Chief of Staff'")).firstMatch
        XCTAssertTrue(chief.waitForExistence(timeout: 20))
        let workspace = XCTAttachment(screenshot: app.screenshot()); workspace.name = "live-workspace"; workspace.lifetime = .keepAlways; add(workspace)
        app.tabBars.buttons["Review"].tap()
        XCTAssertTrue(app.buttons["review.skip"].waitForExistence(timeout: 15) || app.staticTexts["You’re caught up."].exists)
        let review = XCTAttachment(screenshot: app.screenshot()); review.name = "live-review-read-only"; review.lifetime = .keepAlways; add(review)
        app.tabBars.buttons["Workspace"].tap()
        chief.tap()
        // The Chief page's container identifier is inherited by this button.
        let line = app.buttons.containing(.image, identifier: "chevron.up").firstMatch
        XCTAssertTrue(line.waitForExistence(timeout: 15))
        line.tap()
        XCTAssertTrue(app.buttons["Close conversation"].waitForExistence(timeout: 10))
        let conversation = XCTAttachment(screenshot: app.screenshot()); conversation.name = "live-conversation-read-only"; conversation.lifetime = .keepAlways; add(conversation)
        app.terminate()
    }
    @MainActor func testUnreadReplyRefusalQueueAndHistoricalNavigation() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        XCTAssertTrue(app.tabBars.buttons["Review"].waitForExistence(timeout: 10))
        let chief = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Chief of Staff'")).firstMatch
        XCTAssertTrue(chief.waitForExistence(timeout: 8))
        XCTAssertGreaterThanOrEqual(chief.frame.height, 44)
        print("FEEL Chief target: \(chief.frame)")
        // Hit the added padding above the visible heading, then exercise the real tab shell.
        chief.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.05)).tap()
        let detailBack = app.buttons["detail-back"]
        XCTAssertTrue(detailBack.waitForExistence(timeout: 8))
        XCTAssertEqual(app.navigationBars.count, 0)
        XCTAssertFalse(app.tabBars.firstMatch.isHittable)
        let edge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.005, dy: 0.45))
        edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.16, dy: 0.45)), withVelocity: .slow, thenHoldForDuration: 0.5)
        XCTAssertTrue(detailBack.exists)
        XCTAssertFalse(app.tabBars.firstMatch.isHittable)
        edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.45)))
        XCTAssertTrue(app.tabBars.buttons["Review"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.tabBars.buttons["Review"].isHittable)
        app.tabBars.buttons["Review"].tap()
        XCTAssertTrue(app.staticTexts["First unread message"].waitForExistence(timeout: 12))
        XCTAssertTrue(app.staticTexts["Last unread message"].exists)
        XCTAssertTrue(app.buttons["proof.html"].exists || app.staticTexts["proof.html"].exists)
        let input = app.textFields["conversation-input"].firstMatch
        if !input.isHittable { app.swipeUp() }
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        let resting = input.frame
        input.tap(); input.typeText("Reply kept after refusal")
        XCTAssertGreaterThan(input.frame.width, resting.width)
        XCTAssertLessThan(input.frame.minX, resting.minX)
        print("FEEL resting input: \(resting); typed: \(input.frame)")
        app.buttons["Send message"].tap()
        XCTAssertTrue(app.buttons["Retry reply"].waitForExistence(timeout: 8))
        XCTAssertTrue(input.isEnabled)
        input.tap(); input.typeText(" corrected")
        let first = XCTAttachment(screenshot: app.screenshot()); first.name = "review-refused-draft"; first.lifetime = .keepAlways; add(first)
        app.terminate(); app.launch()
        app.tabBars.buttons["Review"].tap()
        XCTAssertTrue(app.buttons["Retry reply"].waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, "Reply kept after refusal corrected")
        XCTAssertTrue(input.isEnabled)
        _ = try await URLSession.shared.data(from: URL(string: base + "/proof/new-message")!)
        // Settle the scrolled card before recording, excluding XCTest's tap-to-reveal scroll.
        app.swipeUp()
        _ = try await URLSession.shared.data(from: URL(string: base + "/proof/start/retry-exit")!)
        app.buttons["Retry reply"].tap()
        XCTAssertTrue(app.buttons["review.approve"].waitForExistence(timeout: 12))
        _ = try await URLSession.shared.data(from: URL(string: base + "/proof/stop")!)
        app.buttons["review.skip"].tap()
        XCTAssertTrue(app.staticTexts["Arrived after display"].waitForExistence(timeout: 8))
        XCTAssertFalse(app.staticTexts["First unread message"].exists)
        app.buttons["review.markRead"].tap()
        XCTAssertTrue(app.staticTexts["Historical message"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.textFields["conversation-input"].exists)
        app.buttons["review.openConversation"].tap()
        XCTAssertTrue(app.staticTexts["Historical transcript"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts["Historical conversation · read only"].exists)
        let historical = XCTAttachment(screenshot: app.screenshot()); historical.name = "review-exact-history"; historical.lifetime = .keepAlways; add(historical)
        XCTAssertTrue(app.buttons["conversation-actions"].exists)
        app.buttons["detail-back"].tap()
        XCTAssertTrue(app.staticTexts["Chief of Staff"].waitForExistence(timeout: 8))
        app.buttons["detail-back"].tap()
        app.tabBars.buttons["Review"].tap()
        app.buttons["review.markRead"].tap()
        XCTAssertTrue(app.buttons["review.revisitSkipped"].waitForExistence(timeout: 8))
        app.buttons["review.revisitSkipped"].tap()
        XCTAssertTrue(app.buttons["review.approve"].waitForExistence(timeout: 8))
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        let writes = try JSONSerialization.jsonObject(with: data) as! [[String: Any]]
        let sends = writes.filter { $0["sender_message_id"] != nil }
        XCTAssertEqual(sends.count, 2)
        XCTAssertNotEqual(sends[0]["sender_message_id"] as? String, sends[1]["sender_message_id"] as? String)
        XCTAssertEqual(sends[0]["owner_read_through_sequence"] as? Int, 3)
        XCTAssertEqual(sends[1]["owner_read_through_sequence"] as? Int, 3)
        XCTAssertEqual((sends[1]["content"] as? [[String: Any]])?.first?["text"] as? String, "Reply kept after refusal corrected")
        XCTAssertEqual(writes.compactMap { $0["through_sequence"] as? Int }, [4, 8])
    }
}
