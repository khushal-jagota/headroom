import XCTest

/// Real URLSession connection failures, cached SwiftUI content and scene recovery
/// share the existing offline fixture; the control listener stays up when it stops.
final class ServerReachabilityTests: XCTestCase {
    @MainActor func testUnreachableServerReplacesContentAndRecovers() async throws {
        continueAfterFailure = false
        let env = ProcessInfo.processInfo.environment
        let base = try XCTUnwrap(env["PANELS_OFFLINE_STUB_URL"])
        let control = try XCTUnwrap(env["PANELS_REACHABILITY_CONTROL_URL"])
        let app = XCUIApplication()
        defer { app.terminate() }
        let title = app.staticTexts["Panels can’t reach the server."]
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
        }
        func state(_ value: String) async throws {
            let (_, response) = try await URLSession.shared.data(from: URL(string: control + "/" + value)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        }
        func foreground() {
            XCUIDevice.shared.press(.home)
            app.activate()
        }
        func assertUnreachable() {
            XCTAssertTrue(title.waitForExistence(timeout: 35))
            XCTAssertTrue(app.staticTexts["Turn on Tailscale, then try again."].exists)
            XCTAssertTrue(app.buttons["Try again"].exists)
            XCTAssertFalse(app.buttons["review.openTicket"].exists)
            XCTAssertFalse(app.buttons["review.approve"].exists)
            XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "specified hostname")).firstMatch.exists)
            XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "Couldn’t refresh")).firstMatch.exists)
        }

        // Cold DNS failure: the override is a launch-domain default, Debug proof only.
        app.launchArguments = ["-panels.baseURL", "https://panels-no-tailscale.invalid"]
        app.launch()
        assertUnreachable()
        capture("unresolvable-host")
        app.terminate()

        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        XCTAssertTrue(app.tabBars.buttons["Review"].waitForExistence(timeout: 15))
        app.tabBars.buttons["Review"].tap()
        let proposal = app.buttons["review.openTicket"]
        XCTAssertTrue(proposal.waitForExistence(timeout: 15))
        capture("reachable-review")

        // Stop the actual listening socket, rather than synthesizing an HTTP error.
        try await state("offline")
        foreground()
        assertUnreachable()
        capture("unreachable-with-cached-review")
        app.buttons["Try again"].tap()
        XCTAssertTrue(app.buttons["Try again"].waitForExistence(timeout: 10))
        try await state("online")
        if app.buttons["Try again"].exists { app.buttons["Try again"].tap() }
        XCTAssertTrue(proposal.waitForExistence(timeout: 20))
        XCTAssertFalse(title.exists)
        capture("recovered-after-retry")

        try await state("offline")
        foreground()
        assertUnreachable()
        XCUIDevice.shared.press(.home)
        try await state("online")
        app.activate()
        XCTAssertTrue(proposal.waitForExistence(timeout: 20))
        XCTAssertFalse(title.exists)
        capture("recovered-on-foreground")

        try await state("request-failed")
        foreground()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Couldn’t refresh. Showing the last loaded content.")).firstMatch.waitForExistence(timeout: 15))
        XCTAssertTrue(proposal.exists)
        XCTAssertFalse(title.exists)
        capture("http-error-retains-content")
        try await state("online")
    }
}
