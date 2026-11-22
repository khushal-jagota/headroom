import XCTest

/// One-off diagnosis against the actual branch server and a synthetic database.
/// This walkthrough is excluded from permanent release coverage.
final class ReviewExternalDecisionProbeTests: XCTestCase {
    @MainActor func testExternalFinalDecisionClearsHeldMessage() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_BRANCH_REVIEW_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        // Review is the root at launch.
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 12))
        capture(app, "branch-review-initial")
        let initialHierarchy = XCTAttachment(string: app.debugDescription)
        initialHierarchy.name = "branch-review-initial-hierarchy"
        initialHierarchy.lifetime = .keepAlways
        add(initialHierarchy)
        let proposal = app.staticTexts["External final approval proof"]
        let message = app.staticTexts["Retain this eligible unread message"]
        XCTAssertTrue(proposal.waitForExistence(timeout: 12))
        if !message.exists { app.swipeUp() }
        XCTAssertTrue(message.waitForExistence(timeout: 8))
        capture(app, "branch-review-before-external-read")

        // A read elsewhere clears unread discovery, but it must retain the painted run.
        try await post(base, "/api/conversation/conversations/native-review-diagnostic-conversation/owner-read", ["through_sequence": 1])
        XCUIDevice.shared.press(.home)
        app.activate()
        // Leave Review for another destination and return to it.
        app.openDestination("sidebar-chief", timeout: 8)
        app.openDestination("sidebar-review", timeout: 8)
        XCTAssertTrue(message.waitForExistence(timeout: 5))
        let stateAfterRead = try await state(base)
        XCTAssertEqual(stateAfterRead["read_through"] as? Int, 1)
        XCTAssertEqual(stateAfterRead["stage"] as? String, "needs_consequences")
        capture(app, "branch-review-retained-after-external-read")

        // The decision comes from the test process while the app is in the background.
        XCUIDevice.shared.press(.home)
        try await post(base, "/api/tickets/native-review-diagnostic/accept/consequences", [
            "expected_proposal_revision": 1, "next_ceiling": "done",
            "next_holder": ["kind": "owner", "id": "owner"],
        ])
        let accepted = try await state(base)
        XCTAssertEqual(accepted["stage"] as? String, "done")
        XCTAssertEqual(accepted["proposal_pending"] as? Bool, false)
        app.activate()
        app.openDestination("sidebar-chief", timeout: 8)
        app.openDestination("sidebar-review", timeout: 8)
        let caughtUp = app.staticTexts["You’re caught up."]
        let cleared = caughtUp.waitForExistence(timeout: 6)
        capture(app, "branch-review-after-external-final-approval")
        let hierarchy = XCTAttachment(string: app.debugDescription)
        hierarchy.name = "branch-review-after-final-approval-hierarchy"
        hierarchy.lifetime = .keepAlways
        add(hierarchy)
        XCTAssertTrue(cleared, "An external final approval must remove the held message when its Ticket becomes Done")
        XCTAssertFalse(message.exists, "The Done Ticket must leave Review without an app restart")
    }

    @MainActor private func post(_ base: String, _ path: String, _ body: [String: Any]) async throws {
        var request = URLRequest(url: try XCTUnwrap(URL(string: base + path)))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200, String(decoding: data, as: UTF8.self))
        if path.hasSuffix("/owner-read") {
            let result = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            XCTAssertEqual(result["owner_read_through_sequence"] as? Int, 1)
        }
    }

    @MainActor private func state(_ base: String) async throws -> [String: Any] {
        let (data, response) = try await URLSession.shared.data(from: try XCTUnwrap(URL(string: base + "/proof/state")))
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
