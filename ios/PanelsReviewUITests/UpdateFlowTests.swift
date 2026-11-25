import XCTest

/// One-off review of native touch delivery and Dynamic Type. The existing loopback
/// fixture owns all writes. Foundation tests cover the request and presentation rules.
final class UpdateFlowTests: XCTestCase {
    @MainActor func testUpdateCardAndTargetedRetry() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        defer { app.terminate() }

        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name
            attachment.lifetime = .keepAlways
            add(attachment)
        }
        func reveal(_ element: XCUIElement, upward: Bool = true) {
            for _ in 0..<8 {
                if element.exists && element.isHittable { return }
                if upward { app.swipeUp() } else { app.swipeDown() }
            }
            XCTAssertTrue(element.exists && element.isHittable)
        }
        // The update card sits behind Settings' Updates row. The Sidebar's gear stays quiet.
        func openSettings(capturing name: String? = nil) {
            app.openDestination("sidebar-settings", timeout: 15)
            let updates = app.buttons["settings.updates"]
            XCTAssertTrue(updates.waitForExistence(timeout: 10))
            if let name { capture("settings-updates-row-" + name) }
            updates.tap()
            XCTAssertTrue(app.descendants(matching: .any)["updates.card"].waitForExistence(timeout: 10))
        }
        func proof(_ path: String) async throws -> Any {
            let (data, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + path)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
            return try JSONSerialization.jsonObject(with: data)
        }
        func updateWrites() async throws -> [[String: Any]] {
            let value = try await proof("writes")
            let all = try XCTUnwrap(value as? [[String: Any]])
            return all.filter { ($0["path"] as? String)?.hasPrefix("/api/updates/") == true }
        }

        app.launch()
        openSettings(capturing: "offer")
        capture("updates-screen-offer")
        let hierarchy = XCTAttachment(string: app.debugDescription)
        hierarchy.name = "updates-initial-accessibility-hierarchy"
        hierarchy.lifetime = .keepAlways
        add(hierarchy)
        XCTAssertTrue(app.readableText("This update stops your agents").waitForExistence(timeout: 10))
        capture("updates-offer-and-machine-rows")

        let retry = app.buttons["updates.retry-machine.machine-failed"]
        reveal(retry)
        retry.tap()
        let error = app.descendants(matching: .any)["updates.error"]
        XCTAssertTrue(error.waitForExistence(timeout: 10))
        capture("updates-refused-machine-retry")
        var writes = try await updateWrites()
        XCTAssertEqual(writes.count, 1)
        XCTAssertEqual(writes[0]["path"] as? String,
                       "/api/updates/attempts/previous-attempt/machines/machine-failed/retry")
        XCTAssertEqual(writes[0]["app_sha"] as? String, "running-release")
        XCTAssertNotNil(writes[0]["native_build"] as? String)
        XCTAssertNotNil(writes[0]["client_identity"] as? String)

        // A screen revisit can refresh facts, but it must not replay the refused write.
        // The Sidebar button is on root pages only, so go back from Updates to Settings first.
        let back = app.backButton
        XCTAssertTrue(back.waitForExistence(timeout: 5))
        back.tap()
        XCTAssertTrue(app.buttons["settings.updates"].waitForExistence(timeout: 10))
        app.openDestination("sidebar-review")
        openSettings()
        writes = try await updateWrites()
        XCTAssertEqual(writes.count, 1)
        reveal(retry)
        retry.tap()
        XCTAssertTrue(retry.waitForNonExistence(timeout: 10))
        let studio = app.descendants(matching: .any)["updates.machine.machine-failed"]
        XCTAssertTrue(studio.waitForExistence(timeout: 10))
        XCTAssertTrue(app.readableText("Updating…").firstMatch.waitForExistence(timeout: 10))
        writes = try await updateWrites()
        XCTAssertEqual(writes.count, 2)
        XCTAssertEqual(writes[1]["path"] as? String, writes[0]["path"] as? String)
        capture("updates-explicit-targeted-retry")

        let install = app.buttons["updates.install"]
        reveal(install)
        install.tap()
        XCTAssertTrue(install.waitForNonExistence(timeout: 10))
        XCTAssertTrue(app.readableText("Updating…").firstMatch.waitForExistence(timeout: 10))
        reveal(app.readableText("This update stops your agents"), upward: false)
        capture("updates-progress-retains-press-snapshot")
        writes = try await updateWrites()
        XCTAssertEqual(writes.count, 3)
        XCTAssertEqual(writes[2]["path"] as? String, "/api/updates/install")
        XCTAssertEqual((writes[2]["body"] as? [String: Any])?["app_sha"] as? String, "offered-release")
        XCTAssertEqual(writes[2]["app_sha"] as? String, "running-release")

        _ = try await proof("updates/failed")
        app.terminate()
        app.launch()
        openSettings()
        let serverRetry = app.buttons["updates.retry-server"]
        reveal(serverRetry)
        capture("updates-server-failure")
        serverRetry.tap()
        XCTAssertTrue(serverRetry.waitForNonExistence(timeout: 10))
        XCTAssertTrue(app.readableText("Updating…").firstMatch.waitForExistence(timeout: 10))
        writes = try await updateWrites()
        XCTAssertEqual(writes.count, 4)
        XCTAssertEqual(writes[3]["path"] as? String, "/api/updates/attempts/server-attempt/retry")

        _ = try await proof("updates/offer")
        app.terminate()
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        openSettings()
        XCTAssertTrue(app.readableText("This update stops your agents").waitForExistence(timeout: 10))
        capture("updates-large-text-card-top")
        reveal(app.buttons["updates.install"])
        capture("updates-large-text-card-actions")
        // Large text is a layout review only. It must not send another update.
        writes = try await updateWrites()
        XCTAssertEqual(writes.count, 4)

        // The same host drops its listener during an expected restart. The card must retain
        // progress until the server deadline, then say that Panels can't be reached. The
        // rebuilt shell has no full-screen cover, so the card's own Try now recovers.
        let control = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_REACHABILITY_CONTROL_URL"])
        func connection(_ action: String) async throws {
            let (_, response) = try await URLSession.shared.data(from: URL(string: control + "/" + action)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        }
        _ = try await proof("updates/restart")
        app.terminate()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        openSettings()
        XCTAssertTrue(app.readableText("Updating…").firstMatch.waitForExistence(timeout: 10))
        try await connection("offline")
        XCTAssertFalse(app.buttons["server-unreachable.retry"].exists)
        capture("updates-progress-during-expected-restart")
        XCTAssertTrue(app.readableText("Can't reach Panels").waitForExistence(timeout: 30))
        XCTAssertFalse(app.buttons["server-unreachable.retry"].exists)
        let tryNow = app.buttons["Try now"]
        XCTAssertTrue(tryNow.exists)
        capture("updates-deadline-card-cannot-reach")
        try await connection("online")
        tryNow.tap()
        XCTAssertTrue(app.readableText("Can't reach Panels").waitForNonExistence(timeout: 15))
        XCTAssertTrue(app.descendants(matching: .any)["updates.card"].waitForExistence(timeout: 10))
        capture("updates-reconnected-without-write-replay")
        writes = try await updateWrites()
        XCTAssertEqual(writes.count, 4)

        // With nothing offered, the row stays quiet and the screen says so.
        _ = try await proof("updates/complete")
        app.terminate()
        app.launch()
        openSettings(capturing: "none")
        XCTAssertTrue(app.readableText("Up to date").firstMatch.waitForExistence(timeout: 10))
        // The last attempt keeps its press snapshot, but with nothing offered or running
        // the card names no impact and no agents.
        XCTAssertFalse(app.readableText("This update stops your agents").exists)
        XCTAssertFalse(app.readableText("Release reviewer").exists)
        capture("updates-screen-none")
    }
}
