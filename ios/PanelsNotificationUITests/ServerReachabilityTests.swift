import XCTest

/// Real URLSession refusal must cover cached controls and recover without a relaunch.
final class ServerReachabilityTests: XCTestCase {
    @MainActor func testUnreachableServerReplacesContentAndRecovers() async throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        // XCTest owns both blocks even when continueAfterFailure aborts this method.
        // LIFO order captures failures before termination. A separate cleanup block
        // still runs if evidence capture itself fails.
        addTeardownBlock { MainActor.assumeIsolated { app.terminate() } }
        addTeardownBlock { [self] in
            MainActor.assumeIsolated {
                guard (testRun?.totalFailureCount ?? 0) > 0 else { return }
                let hierarchy = XCTAttachment(string: app.debugDescription)
                hierarchy.name = "unreachable-app-hierarchy"
                hierarchy.lifetime = .keepAlways
                add(hierarchy)
                let retry = XCTAttachment(string: app.buttons.matching(identifier: "server-unreachable.retry").debugDescription)
                retry.name = "unreachable-retry-query"
                retry.lifetime = .keepAlways
                add(retry)
                let screenshot = XCTAttachment(screenshot: app.screenshot())
                screenshot.name = "unreachable-failure"
                screenshot.lifetime = .keepAlways
                add(screenshot)
            }
        }
        let env = ProcessInfo.processInfo.environment
        let base = try XCTUnwrap(env["PANELS_OFFLINE_STUB_URL"])
        let control = try XCTUnwrap(env["PANELS_REACHABILITY_CONTROL_URL"])
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
        let tabRoot = XCTAttachment(screenshot: app.screenshot())
        tabRoot.name = "owned-host-tab-root"
        tabRoot.lifetime = .keepAlways
        add(tabRoot)
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
        XCTAssertTrue(title.isHittable)
        let retry = app.buttons["server-unreachable.retry"]
        XCTAssertTrue(retry.waitForExistence(timeout: remainingCoverTime()))
        XCTAssertEqual(retry.label, "Try again")
        XCTAssertTrue(retry.isHittable)
        // The title and accessibility tree can settle in separate updates.
        // All absence checks share the same transition deadline.
        let refreshRetries = app.buttons.matching(identifier: "screen-refresh-error.retry")
        for coveredElement in [
            proposal,
            refreshRetries.firstMatch,
            app.buttons["review.approve"],
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "specified hostname")).firstMatch,
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "Couldn’t refresh")).firstMatch,
        ] {
            XCTAssertTrue(coveredElement.waitForNonExistence(timeout: remainingCoverTime()))
            XCTAssertFalse(coveredElement.exists)
        }
        XCTAssertEqual(refreshRetries.count, 0)
        let cover = XCTAttachment(screenshot: app.screenshot())
        cover.name = "owned-host-server-cover"
        cover.lifetime = .keepAlways
        add(cover)
        try await state("online")
        XCTAssertTrue(proposal.waitForExistence(timeout: 70))
        XCTAssertTrue(title.waitForNonExistence(timeout: 5))
        XCTAssertTrue(proposal.isHittable)
        proposal.tap()
        XCTAssertTrue(app.buttons["detail-back"].waitForExistence(timeout: 8))
    }
}
