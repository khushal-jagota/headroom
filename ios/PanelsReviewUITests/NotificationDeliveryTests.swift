import XCTest

/// One-off review. The existing host supplies synthetic pushes and loopback APIs.
/// A model test cannot prove the system's expanded text reply or background launch.
final class NotificationDeliveryTests: XCTestCase {
    @MainActor func testDeliveryRepliesAndPreferences() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_PUSH_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        defer { app.terminate() }
        app.launch()
        app.tabBars.buttons["More"].tap()
        app.buttons["Settings"].tap()
        app.segmentedControls.buttons["Notifications"].tap()
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let allow = app.buttons["allow-notifications"]
        if allow.waitForExistence(timeout: 5) {
            allow.tap()
            let permission = springboard.buttons["Allow"]
            if permission.waitForExistence(timeout: 5) { permission.tap() }
        }
        let preference = app.switches["notification-preference-tickets-awaiting_reply"]
        XCTAssertTrue(preference.waitForExistence(timeout: 8))
        assertValue(preference, "1")
        capture("preference-before-save")
        tapSwitch(preference)
        _ = try await waitState(base) { $0["enabled"] as? Bool == false }
        assertValue(preference, "0")
        capture("preference-saved")
        try await control(base, "/proof/fail-preference")
        tapSwitch(preference)
        let failure = app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'Local preference save refused'")).firstMatch
        XCTAssertTrue(failure.waitForExistence(timeout: 5))
        assertValue(preference, "0")
        capture("preference-failed-confirmed-value")
        app.buttons["screen-refresh-error.retry"].tap()
        _ = try await waitState(base) { $0["enabled"] as? Bool == true }
        assertValue(preference, "1")

        // Foreground banner retains the stage sentence from the shared push copy.
        try await control(base, "/push/approval")
        let banner = springboard.descendants(matching: .any).matching(identifier: "NotificationShortLookView").firstMatch
        XCTAssertTrue(banner.waitForExistence(timeout: 10))
        XCTAssertTrue(banner.label.contains("Notification improvements"))
        XCTAssertTrue(banner.label.contains("What Changes waits for your approval."))
        capture("approval-stage-delivered")
        banner.swipeUp()

        XCUIDevice.shared.press(.home)
        XCTAssertTrue(app.wait(for: .runningBackground, timeout: 5))
        try await reply(base, name: "reply-background", text: "Background notification reply", springboard: springboard)
        _ = try await waitState(base) { ($0["accepted"] as? [[String: Any]])?.count == 1 }
        XCTAssertNotEqual(app.state, .runningForeground, "The text action must not open the app")
        app.terminate()
        try await reply(base, name: "reply-terminated", text: "Terminated notification reply", springboard: springboard)
        _ = try await waitState(base) { ($0["accepted"] as? [[String: Any]])?.count == 2 }
        XCTAssertNotEqual(app.state, .runningForeground, "A terminated launch must handle the reply in the background")

        // Accept a third reply but drop its response. Hide receipts until relaunch.
        // The existing recovery path must reconcile it without another send.
        app.terminate()
        try await control(base, "/proof/drop-reply-response")
        try await reply(base, name: "reply-recovery", text: "Recover the notification receipt", springboard: springboard)
        let beforeRelaunch = try await waitState(base) { ($0["accepted"] as? [[String: Any]])?.count == 3 }
        let accepted = try XCTUnwrap(beforeRelaunch["accepted"] as? [[String: Any]])
        let finalBody = try XCTUnwrap(accepted.last?["body"] as? [String: Any])
        let recoveryID = try XCTUnwrap(finalBody["sender_message_id"] as? String)
        app.terminate()
        try await control(base, "/proof/recover")
        app.launch()
        let state = try await waitState(base) { state in
            guard let receipts = state["receipts"] as? [[String: Any]] else { return false }
            return receipts.contains { receipt in
                receipt["available"] as? Bool == true && (receipt["ids"] as? [String])?.contains(recoveryID) == true
            }
        }
        let attempts = try XCTUnwrap(state["attempts"] as? [[String: Any]])
        XCTAssertEqual(attempts.count, 3, "A receipt recovery must not resend the accepted reply")
        var identities = Set<String>()
        var texts = Set<String>()
        for entry in attempts {
            XCTAssertEqual(entry["path"] as? String, "/api/chief/conversation/send")
            let body = try XCTUnwrap(entry["body"] as? [String: Any])
            XCTAssertEqual(body["conversation_id"] as? String, "c_original", "A replacement conversation must not take the reply")
            identities.insert(try XCTUnwrap(body["sender_message_id"] as? String))
            let content = try XCTUnwrap(body["content"] as? [[String: Any]])
            texts.insert(content.compactMap { $0["text"] as? String }.joined())
        }
        XCTAssertEqual(identities.count, 3)
        XCTAssertEqual(texts, ["Background notification reply", "Terminated notification reply", "Recover the notification receipt"])
        capture("recovered-replies")
        print("Notification reply attempts: \(attempts.count), unique operations: \(identities.count)")
    }

    @MainActor private func reply(_ base: String, name: String, text: String, springboard: XCUIApplication) async throws {
        try await control(base, "/push/" + name)
        let banner = springboard.descendants(matching: .any).matching(identifier: "NotificationShortLookView").firstMatch
        XCTAssertTrue(banner.waitForExistence(timeout: 10), "Missing message notification")
        XCTAssertTrue(banner.label.contains("Chief of Staff"))
        XCTAssertTrue(banner.label.contains("The notification reply stays in this conversation."))
        banner.press(forDuration: 1)
        // An erased Simulator presents the system keyboard's first-use sheet.
        let keyboardIntroduction = springboard.buttons["Continue"]
        if keyboardIntroduction.exists { keyboardIntroduction.tap() }
        capture(name + "-reply-controls")
        let hierarchy = XCTAttachment(string: springboard.debugDescription)
        hierarchy.name = name + "-reply-accessibility"; hierarchy.lifetime = .keepAlways; add(hierarchy)
        let input = springboard.textViews.firstMatch
        let action = springboard.buttons["Reply"]
        let expanded = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in input.exists || action.exists }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [expanded], timeout: 5), .completed)
        if !input.exists && action.exists { action.tap() }
        XCTAssertTrue(input.waitForExistence(timeout: 5), "The expanded notification needs its text action")
        input.tap(); input.typeText(text)
        capture(name + "-expanded-reply")
        springboard.buttons["Send"].tap()
    }

    @MainActor private func control(_ base: String, _ path: String) async throws {
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + path)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }

    @MainActor private func waitState(_ base: String, condition: ([String: Any]) -> Bool) async throws -> [String: Any] {
        let end = Date().addingTimeInterval(8)
        repeat {
            let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/state")!)
            let state = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
            if condition(state) { return state }
            try await Task.sleep(for: .milliseconds(100))
        } while Date() < end
        XCTFail("The local notification operation did not reach its expected state")
        throw URLError(.timedOut)
    }

    @MainActor private func tapSwitch(_ element: XCUIElement) {
        // SwiftUI exposes the full label row as the switch frame. Its center is
        // outside the native control, so use the trailing control shown on screen.
        element.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()
    }

    @MainActor private func assertValue(_ element: XCUIElement, _ value: String) {
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", value), object: element)
        XCTAssertEqual(XCTWaiter.wait(for: [expectation], timeout: 5), .completed)
    }

    @MainActor private func capture(_ name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }
}
