import XCTest

/// Opt-in test: run through scripts/test-notifications-simulator.py so push
/// delivery comes from simctl on the host while XCUITest operates SpringBoard.
final class NotificationDeliveryTests: XCTestCase {
    @MainActor func testDeliveryAndNativeTapDestinations() async throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()
        app.tabBars.buttons["More"].tap()
        app.buttons["Settings"].tap()
        app.segmentedControls.buttons["Notifications"].tap()
        let allow = app.buttons["allow-notifications"]
        if allow.waitForExistence(timeout: 5) {
            allow.tap()
            let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
            let permission = springboard.buttons["Allow"]
            if permission.waitForExistence(timeout: 5) { permission.tap() }
        }
        // Test presentation while the app remains foreground, then actual taps
        // from background and from a terminated app. No live mutation controls.
        let cases = [("ticket", "iOS app · Needs an answer", "Ticket actions"),
                     ("review", "iOS app · Needs approval", "Ticket actions"),
                     ("orchestrator", "iOS launch · Error", "orchestrator-screen"),
                     ("chief", "Chief of Staff · Message", "chief-screen")]
        for (index, entry) in cases.enumerated() {
            if index == 1 { XCUIDevice.shared.press(.home) }
            if index == 2 { app.terminate() }
            let url = URL(string: "http://127.0.0.1:18763/push/\(entry.0)")!
            let (_, response) = try await URLSession.shared.data(from: url)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
            let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
            let banner = springboard.descendants(matching: .any).matching(identifier: "NotificationShortLookView").firstMatch
            XCTAssertTrue(banner.waitForExistence(timeout: 10), "Missing notification: \(entry.0)")
            XCTAssertTrue(banner.label.contains(entry.1))
            let capture = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
            capture.name = "\(entry.0)-delivered"; capture.lifetime = .keepAlways; add(capture)
            banner.tap()
            XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10))
            let destination = app.descendants(matching: .any).matching(identifier: entry.2).firstMatch
            XCTAssertTrue(destination.waitForExistence(timeout: 10), "Wrong destination for \(entry.0)")
            if entry.0 == "review" { XCTAssertTrue(app.navigationBars.buttons["Workspace"].exists) }
            let landing = XCTAttachment(screenshot: app.screenshot())
            landing.name = "\(entry.0)-opened"; landing.lifetime = .keepAlways; add(landing)
        }
    }
}
