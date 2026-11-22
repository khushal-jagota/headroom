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
        defer { app.terminate() }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
        }
        for owner in ["ticket", "manager"] {
            _ = try await URLSession.shared.data(from: URL(string: base + "/proof/review-owner/" + owner)!)
            // Each owner scenario needs its own saved-view namespace: Review holds
            // a restored unread card until handled, even as the fixture changes owner.
            // Both spellings reach the same loopback fixture host and process.
            var appBase = try XCTUnwrap(URLComponents(string: base))
            if owner == "manager" { appBase.host = "localhost" }
            app.launchArguments = [
                "--conversation-stub", try XCTUnwrap(appBase.string),
                "--conversation-fixture-speech", "--conversation-fixture-silence",
            ]
            app.launch()
            // Review is the root at launch.
            XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 10))
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
            XCTAssertFalse(app.sidebarButton.exists, "A pushed page shows Back, not the Sidebar button")
            capture(owner + "-conversation")
            app.buttons["Record voice input"].firstMatch.tap()
            XCTAssertTrue(app.buttons["Stop recording"].waitForExistence(timeout: 5))
            app.buttons["Stop recording"].tap()
            XCTAssertTrue(feedback.waitForExistence(timeout: 6))
            XCTAssertTrue(app.otherElements["conversation-composer-region"].frame.contains(feedback.frame))
            capture(owner + "-conversation-feedback")
            // A cancelled pop remains in the conversation; a completed pop returns to the
            // Review card, since the conversation opened on top of Review.
            let edge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.005, dy: 0.45))
            edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.16, dy: 0.45)), withVelocity: .slow, thenHoldForDuration: 0.5)
            XCTAssertTrue(app.buttons["conversation-actions"].exists)
            edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.45)))
            XCTAssertTrue(app.buttons["conversation-actions"].waitForNonExistence(timeout: 8))
            XCTAssertTrue(app.buttons["review.openConversation"].waitForExistence(timeout: 8))
            XCTAssertTrue(app.sidebarButton.exists)
            capture(owner + "-back-to-review")
            if owner == "ticket" {
                // The Ticket's own conversation draft stays on the Ticket, apart from the Review reply.
                // The fixture's Manager is not in the Sidebar and its message card has no Open ticket,
                // so only the Ticket owner reaches its own page from here.
                app.buttons["review.openTicket"].tap()
                XCTAssertTrue(app.buttons["ticket-actions"].waitForExistence(timeout: 8))
                app.buttons["conversation-line"].tap()
                XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 8))
                input.tap(); input.typeText("Keep this normal conversation draft")
                app.backButton.tap()
                XCTAssertTrue(app.buttons["ticket-actions"].waitForExistence(timeout: 8))
                XCTAssertEqual(input.value as? String, "Keep this normal conversation draft")
                // Open ticket pushes the Ticket on Review, so Back returns to the card.
                app.backButton.tap()
                XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 8))
            }
            XCTAssertTrue(["", "Reply…"].contains(input.value as? String ?? "missing"))
            app.terminate()
        }
        _ = try await URLSession.shared.data(from: URL(string: base + "/proof/review-owner/chief")!)
    }

    // This one-off walkthrough needs explicit live-read authorization before use.
    @MainActor func testLiveReadOnlyWorkspaceAndReview() throws {
        guard ProcessInfo.processInfo.environment["PANELS_LIVE_READ_ONLY"] == "1" else { throw XCTSkip("Explicit read-only live pass only") }
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()
        // Review is the root at launch; the Sidebar lists Chief and the Managers.
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 20))
        app.openSidebar(timeout: 20)
        XCTAssertTrue(app.buttons["sidebar-chief"].waitForExistence(timeout: 20))
        let sidebar = XCTAttachment(screenshot: app.screenshot()); sidebar.name = "live-sidebar"; sidebar.lifetime = .keepAlways; add(sidebar)
        app.buttons["sidebar-review"].tap()
        XCTAssertTrue(app.buttons["review.skip"].waitForExistence(timeout: 15) || app.staticTexts["You’re caught up."].exists)
        let review = XCTAttachment(screenshot: app.screenshot()); review.name = "live-review-read-only"; review.lifetime = .keepAlways; add(review)
        app.openDestination("sidebar-chief", timeout: 15)
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 15))
        line.tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 10))
        let conversation = XCTAttachment(screenshot: app.screenshot()); conversation.name = "live-conversation-read-only"; conversation.lifetime = .keepAlways; add(conversation)
        app.terminate()
    }
}
