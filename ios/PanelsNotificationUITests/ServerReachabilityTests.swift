import XCTest

/// Real URLSession refusal must cover cached controls and recover without a relaunch.
final class ServerReachabilityTests: XCTestCase {
    @MainActor func testUnreachableServerReplacesContentAndRecovers() async throws {
        continueAfterFailure = false
        let env = ProcessInfo.processInfo.environment
        let base = try XCTUnwrap(env["PANELS_OFFLINE_STUB_URL"])
        let control = try XCTUnwrap(env["PANELS_REACHABILITY_CONTROL_URL"])
        let app = XCUIApplication()
        defer { app.terminate() }
        func state(_ value: String) async throws {
            let (_, response) = try await URLSession.shared.data(from: URL(string: control + "/" + value)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        }
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        let review = app.tabBars.buttons["Review"]
        XCTAssertTrue(review.waitForExistence(timeout: 15))
        review.tap()
        let proposal = app.buttons["review.openTicket"]
        XCTAssertTrue(proposal.waitForExistence(timeout: 15))
        // Stop the actual listener, then let the active scene observe its failed read.
        try await state("offline")
        XCUIDevice.shared.press(.home)
        app.activate()
        let title = app.staticTexts["Panels can’t reach the server."]
        let coverDeadline = Date().addingTimeInterval(35)
        func remainingCoverTime() -> TimeInterval {
            let remaining = coverDeadline.timeIntervalSinceNow
            XCTAssertGreaterThan(remaining, 0, "The cover transition exceeded its existing deadline")
            return max(0, remaining)
        }
        XCTAssertTrue(title.waitForExistence(timeout: remainingCoverTime()))
        // The title and accessibility tree can settle in separate updates.
        // All absence checks share the same transition deadline.
        for coveredElement in [
            proposal,
            app.buttons["review.approve"],
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "specified hostname")).firstMatch,
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "Couldn’t refresh")).firstMatch,
        ] {
            XCTAssertTrue(coveredElement.waitForNonExistence(timeout: remainingCoverTime()))
            XCTAssertFalse(coveredElement.exists)
        }
        try await state("online")
        XCTAssertTrue(proposal.waitForExistence(timeout: 70))
        XCTAssertTrue(title.waitForNonExistence(timeout: 5))
    }
}
