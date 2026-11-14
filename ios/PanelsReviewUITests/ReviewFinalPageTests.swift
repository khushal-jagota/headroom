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
        XCTAssertEqual(pieces.first?["text"] as? String, changedDraft)
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
