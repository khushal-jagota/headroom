import XCTest
import UIKit

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
            if owner == "ticket" {
                // The Review reply shares the composer: a pasted image shows as a removable tile.
                UIPasteboard.general.image = UIGraphicsImageRenderer(size: CGSize(width: 90, height: 60)).image { context in
                    UIColor.systemTeal.setFill(); context.fill(CGRect(x: 0, y: 0, width: 90, height: 60))
                }
                input.press(forDuration: 1.2)
                XCTAssertTrue(app.menuItems["Paste"].waitForExistence(timeout: 3))
                app.menuItems["Paste"].tap()
                XCTAssertTrue(box.descendants(matching: .any).matching(identifier: "conversation-attachment").firstMatch.waitForExistence(timeout: 5))
                capture("ticket-review-pasted-image")
                box.buttons["Remove Pasted image.png"].tap()
                XCTAssertTrue(box.descendants(matching: .any).matching(identifier: "conversation-attachment").firstMatch.waitForNonExistence(timeout: 3))
            }
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

    // This one-off walkthrough retains obsolete Close conversation/chevron selectors.
    // It needs a separate repair and explicit live-read authorization before use.
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
}
