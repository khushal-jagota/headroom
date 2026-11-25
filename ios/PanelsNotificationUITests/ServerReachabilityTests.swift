import XCTest

/// A stopped listener refuses real URLSession reads. No cover replaces the app, and it
/// recovers without a relaunch.
final class ServerReachabilityTests: XCTestCase {
    @MainActor func testUnreachableServerShowsNoCoverAndRecovers() async throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        // XCTest owns both blocks even when continueAfterFailure aborts this method.
        // LIFO order captures failures before termination.
        addTeardownBlock { MainActor.assumeIsolated { app.terminate() } }
        addTeardownBlock { [self] in
            MainActor.assumeIsolated {
                guard (testRun?.totalFailureCount ?? 0) > 0 else { return }
                let hierarchy = XCTAttachment(string: app.debugDescription)
                hierarchy.name = "unreachable-app-hierarchy"
                hierarchy.lifetime = .keepAlways
                add(hierarchy)
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
        // Review is the root at launch.
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 15))
        // Review opens on its summary; its return button leads to the cards.
        let reviewReturn = app.buttons["review.return"]
        XCTAssertTrue(reviewReturn.waitForExistence(timeout: 15))
        reviewReturn.tap()
        let proposal = app.buttons["review.openTicket"]
        XCTAssertTrue(proposal.waitForExistence(timeout: 15))
        capture(app, "review-root")
        // Stop the actual listener, then let the active scene observe its failed read.
        try await state("offline")
        XCUIDevice.shared.press(.home)
        app.activate()
        // The removed cover appeared within 35 seconds. It must never appear now.
        let retry = app.buttons["server-unreachable.retry"]
        let title = app.readableText("Panels can’t reach the server.")
        let readNote = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@ OR label BEGINSWITH %@ OR value BEGINSWITH %@ OR value BEGINSWITH %@", "Couldn’t refresh", "Couldn’t load", "Couldn’t refresh", "Couldn’t load")).firstMatch
        for second in 0..<35 {
            XCTAssertFalse(retry.exists, "The server cover appeared after \(second)s")
            XCTAssertFalse(title.exists, "The server cover appeared after \(second)s")
            XCTAssertFalse(readNote.exists, "A read note appeared after \(second)s")
            try await Task.sleep(for: .seconds(1))
        }
        XCTAssertTrue(app.sidebarButton.exists)
        capture(app, "unreachable-no-cover")
        try await state("online")
        XCTAssertTrue(proposal.waitForExistence(timeout: 70))
        XCTAssertTrue(proposal.isHittable)
        XCTAssertFalse(retry.exists)
        proposal.tap()
        XCTAssertTrue(app.backButton.waitForExistence(timeout: 8))
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
