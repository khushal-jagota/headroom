import XCTest

/// Real listener refusal must replace every screen, including an existing sheet. Saved
/// work survives the cover and a relaunch, then reaches the server once after recovery.
final class ServerReachabilityTests: XCTestCase {
    @MainActor func testUnreachableServerShowsCoverAndRecovers() async throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        addTeardownBlock { MainActor.assumeIsolated { app.terminate() } }
        addTeardownBlock { [self] in
            MainActor.assumeIsolated {
                guard (testRun?.totalFailureCount ?? 0) > 0 else { return }
                let hierarchy = XCTAttachment(string: app.debugDescription)
                hierarchy.name = "unreachable-app-hierarchy"
                hierarchy.lifetime = .keepAlways
                add(hierarchy)
                capture(app, "unreachable-failure")
            }
        }
        let env = ProcessInfo.processInfo.environment
        let base = try XCTUnwrap(env["PANELS_OFFLINE_STUB_URL"])
        let control = try XCTUnwrap(env["PANELS_REACHABILITY_CONTROL_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        func state(_ value: String) async throws {
            let (_, response) = try await URLSession.shared.data(from: URL(string: control + "/" + value)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        }
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 15))
        // Read the Chief online so its input and saved page are useful later, without a server.
        app.openDestination("sidebar-chief")
        let input = app.textFields["conversation-input"].firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 12))
        app.openDestination("sidebar-review")
        let reviewReturn = app.buttons["review.return"]
        XCTAssertTrue(reviewReturn.waitForExistence(timeout: 12))
        reviewReturn.tap()
        let proposal = app.buttons["review.openTicket"]
        XCTAssertTrue(proposal.waitForExistence(timeout: 12))
        proposal.tap()
        let actions = app.buttons["ticket-actions"]
        XCTAssertTrue(actions.waitForExistence(timeout: 12))
        actions.tap()
        let copyLink = app.buttons["ticket-action-copy-link"]
        XCTAssertTrue(copyLink.waitForExistence(timeout: 5))
        capture(app, "reachability-pushed-ticket-sheet")

        let retry = app.buttons["server-unreachable.retry"]
        let offline = app.buttons["server-unreachable.offline"]
        // A real short refusal must remain quiet, including after the server returns.
        try await state("offline")
        try await Task.sleep(for: .seconds(2))
        XCTAssertFalse(retry.exists)
        try await state("online")
        XCTAssertTrue(copyLink.isHittable)
        for _ in 0..<9 {
            XCTAssertFalse(retry.exists, "A short drop must not leave a delayed cover")
            try await Task.sleep(for: .seconds(1))
        }

        // Keep the pushed page's sheet open while the real listener stops again.
        try await state("offline")
        XCUIDevice.shared.press(.home)
        app.activate()
        XCTAssertTrue(retry.waitForExistence(timeout: 30))
        XCTAssertTrue(retry.isHittable)
        XCTAssertTrue(app.buttons["server-unreachable.tailscale"].isHittable)
        XCTAssertTrue(offline.isHittable)
        XCTAssertFalse(copyLink.isHittable, "The cover must block an already presented sheet")
        capture(app, "unreachable-cover-above-sheet")
        retry.tap()
        XCTAssertTrue(retry.waitForExistence(timeout: 5))
        XCTAssertTrue(offline.isHittable, "A failed retry must leave the confirmed outage visible")
        offline.tap()
        XCTAssertTrue(retry.waitForNonExistence(timeout: 5))
        XCTAssertTrue(copyLink.waitForExistence(timeout: 5))
        XCTAssertTrue(copyLink.isHittable, "Continue offline must restore the same sheet")
        capture(app, "unreachable-continue-offline-sheet")
        app.buttons["Cancel"].tap()
        XCTAssertTrue(actions.waitForExistence(timeout: 5))
        app.backButton.tap()
        XCTAssertTrue(proposal.waitForExistence(timeout: 8))
        app.openDestination("sidebar-chief")
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap()
        input.typeText("Queued through the server cover")
        app.buttons["Send message"].tap()
        let savedMessage = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", "Queued through the server cover")).firstMatch
        XCTAssertTrue(savedMessage.waitForExistence(timeout: 5))
        capture(app, "unreachable-saved-message-offline")
        // The explicit offline choice survives a background return during this outage.
        XCUIDevice.shared.press(.home)
        app.activate()
        for _ in 0..<10 {
            XCTAssertFalse(retry.exists, "A background return must preserve Continue offline")
            try await Task.sleep(for: .seconds(1))
        }

        // A fresh launch resets the choice but retains the message in the durable outbox.
        // Large text exercises the real cover's layout without a second connected host.
        app.terminate()
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        XCTAssertTrue(retry.waitForExistence(timeout: 30))
        XCTAssertTrue(retry.isHittable)
        // The native cover can scroll when the text size does not fit one screen.
        if !offline.isHittable { app.swipeUp() }
        XCTAssertTrue(offline.isHittable)
        capture(app, "unreachable-cover-large-text")
        try await state("online")
        XCTAssertTrue(retry.waitForNonExistence(timeout: 35), "A real answer must dismiss the cover without a tap")
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 8))
        capture(app, "unreachable-recovered")
        let delivered = try await waitForMessage(base)
        XCTAssertEqual(delivered.messages.map(\.text), ["Queued through the server cover"])
        XCTAssertEqual(delivered.writes.filter { $0 == "message" }.count, 1)
        // Leave another retry interval for a duplicate replay to appear in the same ledger.
        try await Task.sleep(for: .seconds(6))
        let settled = try await delivery(base)
        XCTAssertEqual(settled.messages.map(\.id), delivered.messages.map(\.id))
        XCTAssertEqual(settled.writes.filter { $0 == "message" }.count, 1)
    }

    private struct Delivery: Decodable {
        struct Message: Decodable { let id: String; let text: String }
        let writes: [String]
        let messages: [Message]
    }

    @MainActor private func delivery(_ base: String) async throws -> Delivery {
        let (data, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/delivery")!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        return try JSONDecoder().decode(Delivery.self, from: data)
    }

    @MainActor private func waitForMessage(_ base: String) async throws -> Delivery {
        let deadline = Date().addingTimeInterval(45)
        var value = try await delivery(base)
        while value.messages.isEmpty, Date() < deadline {
            try await Task.sleep(for: .seconds(1))
            value = try await delivery(base)
        }
        return value
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
