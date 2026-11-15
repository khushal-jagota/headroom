import XCTest
import UIKit

/// One-off review tools. These methods stay outside the permanent release suite.
/// The existing loopback host proves real SwiftUI placement, keyboard and live updates.
final class ReviewFinalPageTests: XCTestCase {
    @MainActor func testFinalPageChiefDraftAndSend() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        let review = app.tabBars.buttons["Review"]
        XCTAssertTrue(review.waitForExistence(timeout: 10))
        XCTAssertTrue(review.isSelected, "A normal launch must open Review")
        try skipFixtureCards(app)
        let caughtUp = app.staticTexts["You’re caught up."]
        XCTAssertTrue(caughtUp.waitForExistence(timeout: 8))
        let revisit = app.buttons["review.revisitSkipped"]
        XCTAssertTrue(revisit.waitForExistence(timeout: 5))
        capture(app, "review-final-page-with-revisit")
        revisit.tap()
        XCTAssertTrue(app.staticTexts["First unread message"].waitForExistence(timeout: 8))
        try skipFixtureCards(app)
        XCTAssertTrue(caughtUp.waitForExistence(timeout: 8))
        XCTAssertTrue(revisit.exists)
        try await proof(base, "review-final")
        XCTAssertTrue(revisit.waitForNonExistence(timeout: 8))

        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        let attachment = app.descendants(matching: .any).matching(identifier: "conversation-attachment").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertGreaterThan(input.frame.minY, caughtUp.frame.minY, "Message Chief must follow the final-page text")
        input.tap(); input.typeText("Shared Chief draft")
        XCTAssertTrue(app.keyboards.firstMatch.exists)
        let send = app.buttons["Send message"]
        XCTAssertTrue(send.waitForExistence(timeout: 5))
        if !send.isHittable { app.swipeUp() }
        XCTAssertTrue(send.isHittable, "The keyboard must not hide Send")
        UIPasteboard.general.image = UIGraphicsImageRenderer(size: CGSize(width: 90, height: 60)).image { context in
            UIColor.systemTeal.setFill(); context.fill(CGRect(x: 0, y: 0, width: 90, height: 60))
        }
        input.press(forDuration: 1.2)
        XCTAssertTrue(app.menuItems["Paste"].waitForExistence(timeout: 3))
        app.menuItems["Paste"].tap()
        XCTAssertTrue(attachment.waitForExistence(timeout: 5))
        capture(app, "review-final-chief-keyboard-and-attachment")

        // A fresh process reads the same durable Chief words and attachments.
        app.terminate(); app.launch()
        XCTAssertTrue(review.waitForExistence(timeout: 10))
        XCTAssertTrue(review.isSelected)
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, "Shared Chief draft")
        XCTAssertTrue(attachment.waitForExistence(timeout: 5))
        app.tabBars.buttons["Workspace"].tap()
        let chief = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Chief of Staff'")).firstMatch
        XCTAssertTrue(chief.waitForExistence(timeout: 8)); chief.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, "Shared Chief draft")
        XCTAssertTrue(attachment.waitForExistence(timeout: 5))
        input.tap(); input.typeText(" from Chief")
        let changedDraft = try XCTUnwrap(input.value as? String)
        XCTAssertTrue(changedDraft.contains(" from Chief"))
        app.buttons["detail-back"].tap()
        XCTAssertTrue(review.waitForExistence(timeout: 5)); review.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, changedDraft)
        capture(app, "review-final-chief-shared-draft")

        if !send.isHittable { app.swipeUp() }
        XCTAssertTrue(send.isHittable)
        send.tap()
        let sent = app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'Sent to Chief'")).firstMatch
        XCTAssertTrue(sent.waitForExistence(timeout: 8))
        capture(app, "review-final-chief-sent")
        let writes = try await recordedWrites(base)
        XCTAssertEqual(writes.count, 1, "Revisit and draft edits must not send messages")
        let message = try XCTUnwrap(writes.first as? [String: Any])
        XCTAssertEqual(message["conversation_id"] as? String, "stub")
        XCTAssertNil(message["owner_read_through_sequence"], "A final-page send must not acknowledge unread Review content")
        let pieces = try XCTUnwrap(message["content"] as? [[String: Any]])
        XCTAssertEqual(pieces.first?["text"] as? String, changedDraft.trimmingCharacters(in: .whitespacesAndNewlines))
        XCTAssertTrue(pieces.contains { $0["piece"] as? String == "image" })

        // Appearance is a one-off review, rather than a permanent geometry gate.
        app.terminate()
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        input.tap(); input.typeText("Large text draft")
        if !send.isHittable { app.swipeUp() }
        XCTAssertTrue(send.isHittable)
        capture(app, "review-final-chief-accessibility-text-keyboard")
    }

    @MainActor func testVisibleReviewCardBecomesHistorical() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        try await proof(base, "replacement/old")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        let review = app.tabBars.buttons["Review"]
        XCTAssertTrue(review.waitForExistence(timeout: 10))
        XCTAssertTrue(review.isSelected)
        let originalMessage = app.staticTexts["First unread message"]
        XCTAssertTrue(originalMessage.waitForExistence(timeout: 8))
        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap(); input.typeText("Keep this Review draft")
        capture(app, "review-card-before-replacement")
        try await proof(base, "new-message")
        try await proof(base, "replacement/new")
        let historical = app.staticTexts["Historical conversation · read only"]
        XCTAssertTrue(historical.waitForExistence(timeout: 8))
        XCTAssertTrue(input.waitForNonExistence(timeout: 3))
        XCTAssertTrue(originalMessage.exists, "Replacement must retain the displayed unread run")
        XCTAssertFalse(app.staticTexts["Arrived after display"].exists, "Replacement must not expand the displayed read boundary")
        XCTAssertTrue(review.isSelected, "Historical status must update without navigation")
        capture(app, "review-card-after-replacement")
        let writes = try await recordedWrites(base)
        XCTAssertTrue(writes.isEmpty)
        try await proof(base, "replacement/old")
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, "Keep this Review draft")
        XCTAssertFalse(app.staticTexts["Arrived after display"].exists)
        capture(app, "review-card-draft-retained")
    }

    /// One-off keyboard and decision-flow review on the existing fixture host.
    @MainActor func testPinnedDesignDecisions() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        // Recreate the application and queries for each process. Retained query snapshots
        // in the failed review trace referred to the terminated accessibility-size process.
        @MainActor func launch(_ arguments: [String]) -> XCUIApplication {
            let application = XCUIApplication()
            application.launchArguments = arguments
            application.launch()
            return application
        }
        var app = launch(["--conversation-stub", base])
        defer { app.terminate() }
        var approve: XCUIElement { app.buttons["review.approve"] }
        XCTAssertTrue(approve.waitForExistence(timeout: 12))
        capture(app, "design-review-normal")
        var note: XCUIElement { app.descendants(matching: .any).matching(identifier: "review.revision").firstMatch }
        var sendBack: XCUIElement { app.buttons["review.sendBack"] }
        sendBack.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        let keyboardTip = app.buttons["Continue"]
        if keyboardTip.exists { keyboardTip.tap() }
        note.typeText("Keep this proposal note")
        XCTAssertTrue(approve.isHittable, "The keyboard must not cover pinned decisions")
        capture(app, "design-review-keyboard")
        approve.tap()
        var stage: XCUIElement { app.buttons["decision.stage.needs_implementation"] }
        XCTAssertTrue(waitForDecisionStage(stage))
        capture(app, "design-how-far")
        stage.tap()
        var reviewer: XCUIElement { app.buttons["decision.reviewer.owner"] }
        XCTAssertTrue(reviewer.waitForExistence(timeout: 5))
        capture(app, "design-who-reviews")
        // Dismiss without a decision, then prove the exact note survives relaunch.
        app.swipeDown()
        app.terminate(); app = launch(["--conversation-stub", base])
        XCTAssertTrue(note.waitForExistence(timeout: 12))
        XCTAssertEqual(note.value as? String, "Keep this proposal note")
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(app.buttons["ticket-actions"].waitForExistence(timeout: 8))
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        capture(app, "design-ticket-pinned")
        XCTAssertEqual(note.value as? String, "Keep this proposal note")
        app.buttons["detail-back"].tap()
        // Open ticket uses Workspace's existing navigation stack.
        app.tabBars.buttons["Review"].tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        // Capture the real app at the largest accessibility category, including its safe-area edge.
        app.terminate()
        app = launch(["--conversation-stub", base,
                      "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"])
        XCTAssertTrue(approve.waitForExistence(timeout: 12))
        XCTAssertTrue(approve.isHittable, "Large text must leave pinned decisions reachable")
        capture(app, "design-review-largest-text-normal")
        app.scrollViews.firstMatch.swipeUp()
        XCTAssertTrue(approve.isHittable, "The pinned decision must remain reachable while the proposal scrolls")
        capture(app, "design-review-largest-text-proposal")
        // Restore normal type before the final approval, without changing the saved note.
        app.terminate()
        app = launch(["--conversation-stub", base])
        XCTAssertTrue(approve.waitForExistence(timeout: 12))
        XCTAssertEqual(note.value as? String, "Keep this proposal note")
        capture(app, "design-review-restored-normal")
        let hierarchy = XCTAttachment(string: app.debugDescription)
        hierarchy.name = "design-review-restored-normal-hierarchy"
        hierarchy.lifetime = .keepAlways; add(hierarchy)
        // An earlier-stage choice changes only settings and keeps the proposal pending.
        approve.tap()
        let earlierStage = app.buttons["decision.stage.needs_brief"]
        XCTAssertTrue(waitForDecisionStage(earlierStage)); earlierStage.tap()
        XCTAssertTrue(reviewer.waitForExistence(timeout: 5)); reviewer.tap()
        let scopeConfirmation = app.staticTexts["decision.scopeConfirmation"]
        XCTAssertTrue(scopeConfirmation.waitForExistence(timeout: 5))
        XCTAssertTrue(scopeConfirmation.label.contains("No proposal approval was sent."))
        capture(app, "design-earlier-stage-settings-confirmed")
        app.buttons["decision.scopeDone"].tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Make the work that needs your attention easy to read."].exists)
        XCTAssertEqual(note.value as? String, "Keep this proposal note")
        let (beforeDecision, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        XCTAssertEqual((try JSONSerialization.jsonObject(with: beforeDecision) as? [String]), [])
        let (scopeData, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/scopes")!)
        let scopeWrites = try XCTUnwrap(try JSONSerialization.jsonObject(with: scopeData) as? [[String: Any]])
        XCTAssertEqual(scopeWrites.count, 1)
        let scopeBody = try XCTUnwrap(scopeWrites.first?["body"] as? [String: Any])
        XCTAssertEqual(scopeBody["ceiling"] as? String, "needs_brief")
        approve.tap()
        XCTAssertTrue(waitForDecisionStage(stage)); stage.tap()
        XCTAssertTrue(reviewer.waitForExistence(timeout: 5)); reviewer.tap()
        let next = app.staticTexts["Match the iPhone app to the web"]
        XCTAssertTrue(next.waitForExistence(timeout: 8))
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        XCTAssertEqual((try JSONSerialization.jsonObject(with: data) as? [String]), ["approve"])
    }

    @MainActor private func waitForDecisionStage(_ stage: XCUIElement) -> Bool {
        // Existence alone admitted an infinite/zero frame in the earlier review run.
        let ready = NSPredicate { _, _ in
            MainActor.assumeIsolated {
                guard stage.exists, stage.isHittable else { return false }
                let frame = stage.frame
                return frame.minX.isFinite && frame.minY.isFinite && frame.width.isFinite && frame.height.isFinite
                    && frame.width > 0 && frame.height > 0
            }
        }
        return XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: ready, object: nil)], timeout: 5) == .completed
    }

    @MainActor private func skipFixtureCards(_ app: XCUIApplication) throws {
        for element in [app.staticTexts["First unread message"], app.buttons["review.approve"], app.staticTexts["Historical message"]] {
            XCTAssertTrue(element.waitForExistence(timeout: 8))
            let skip = app.buttons["review.skip"]
            XCTAssertTrue(skip.waitForExistence(timeout: 5)); skip.tap()
            XCTAssertTrue(element.waitForNonExistence(timeout: 5))
        }
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }

    @MainActor private func proof(_ base: String, _ operation: String) async throws {
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + operation)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }

    @MainActor private func recordedWrites(_ base: String) async throws -> [Any] {
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [Any])
    }
}
