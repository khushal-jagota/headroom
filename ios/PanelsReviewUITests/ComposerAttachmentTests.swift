import XCTest
import UIKit

/// The edit menu decides whether an image-only clipboard can paste, so only the real
/// SwiftUI host proves it. Reuses the conversation loopback stub, whose send waits three
/// seconds, to see the pending message's previews.
final class ComposerAttachmentTests: XCTestCase {
    @MainActor func testPasteRemoveAndSendPreview() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_CONVERSATION_STUB_URL"])
        _ = try await URLSession.shared.data(from: URL(string: base + "/proof/reset")!)
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-smoke", "--conversation-stub", base, "--conversation-fixture-speech"]
        app.launch()
        func capture(_ name: String) {
            let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = name; shot.lifetime = .keepAlways; add(shot)
        }
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 8))
        line.tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 8))
        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        let composer = app.otherElements["conversation-composer-region"]
        let tiles = composer.descendants(matching: .any).matching(identifier: "conversation-attachment")
        func paste() {
            input.press(forDuration: 1.2)
            let item = app.menuItems["Paste"]
            XCTAssertTrue(item.waitForExistence(timeout: 3), "Paste is missing from the edit menu")
            item.tap()
        }
        input.tap()

        UIPasteboard.general.string = "Words still paste"
        paste()
        XCTAssertEqual(input.value as? String, "Words still paste")
        XCTAssertEqual(tiles.count, 0)

        let picture = UIGraphicsImageRenderer(size: CGSize(width: 160, height: 120)).image { context in
            UIColor.systemOrange.setFill(); context.fill(CGRect(x: 0, y: 0, width: 160, height: 120))
            UIColor.systemBlue.setFill(); context.fill(CGRect(x: 40, y: 30, width: 80, height: 60))
        }
        UIPasteboard.general.image = picture
        paste()
        XCTAssertTrue(tiles.firstMatch.waitForExistence(timeout: 5))
        paste()
        XCTAssertEqual(tiles.count, 2)
        capture("composer-two-pasted-images")
        composer.buttons["Remove Pasted image.png"].firstMatch.tap()
        XCTAssertEqual(tiles.count, 1)

        app.buttons["Send message"].tap()
        let thread = app.scrollViews["conversation-thread"]
        XCTAssertTrue(app.staticTexts["Sending…"].firstMatch.waitForExistence(timeout: 3))
        XCTAssertTrue(thread.descendants(matching: .any).matching(identifier: "conversation-attachment").firstMatch.exists)
        XCTAssertEqual(tiles.count, 0)
        capture("sending-with-preview")
    }
}
