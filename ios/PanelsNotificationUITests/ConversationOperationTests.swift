import XCTest
import UIKit

/// Native editing, interactive Back, Paste and Send need the app. Model tests own delivery policy.
final class ConversationOperationTests: XCTestCase {
    @MainActor func testOperationsAgainstLocalStub() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_CONVERSATION_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/reset")!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-smoke", "--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 8))
        line.tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts["The plan is ready. What should I work on next?"].waitForExistence(timeout: 5))
        let input = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap()
        input.typeText("Please check this image.")
        UIPasteboard.general.image = UIGraphicsImageRenderer(size: CGSize(width: 90, height: 60)).image { context in
            UIColor.systemTeal.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 90, height: 60))
        }
        input.press(forDuration: 1.2)
        let paste = app.menuItems["Paste"]
        XCTAssertTrue(paste.waitForExistence(timeout: 3))
        paste.tap()
        let composer = app.otherElements["conversation-composer-region"]
        let attachment = composer.descendants(matching: .any).matching(identifier: "conversation-attachment").firstMatch
        XCTAssertTrue(attachment.waitForExistence(timeout: 5))
        capture(app, "chat-draft-keyboard-open")
        let thread = app.scrollViews["conversation-thread"]
        XCTAssertTrue(thread.waitForExistence(timeout: 5))
        // The gutter is inert. Message prose stays selectable and is not a dismissal target.
        thread.coordinate(withNormalizedOffset: CGVector(dx: 0.01, dy: 0.5)).tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5), "An inert chat tap must end editing")
        XCTAssertEqual(input.value as? String, "Please check this image.")
        XCTAssertTrue(attachment.exists)
        capture(app, "chat-draft-keyboard-dismissed")
        input.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        let edge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.005, dy: 0.45))
        edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.16, dy: 0.45)), withVelocity: .slow, thenHoldForDuration: 0.5)
        XCTAssertTrue(app.buttons["conversation-actions"].exists, "The short Back swipe must cancel")
        XCTAssertTrue(app.keyboards.firstMatch.exists, "A canceled Back swipe must retain editing")
        XCTAssertEqual(input.value as? String, "Please check this image.")
        XCTAssertTrue(attachment.exists)
        capture(app, "chat-canceled-back-keeps-editing")
        app.buttons["Send message"].tap()
        let sending = app.staticTexts["Sending…"].firstMatch
        XCTAssertTrue(sending.waitForExistence(timeout: 3))
        XCTAssertTrue(sending.waitForNonExistence(timeout: 8))
        XCTAssertTrue(attachment.waitForNonExistence(timeout: 5))
        let sent = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Please check this image.")).firstMatch
        XCTAssertTrue(sent.waitForExistence(timeout: 8))
        let reply = thread.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "I’ll keep the conversation steady as messages arrive.")).firstMatch
        XCTAssertTrue(reply.waitForExistence(timeout: 8), "The server did not accept the send and return its reply")
        app.buttons["detail-back"].tap()
        XCTAssertTrue(line.waitForExistence(timeout: 5))
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
