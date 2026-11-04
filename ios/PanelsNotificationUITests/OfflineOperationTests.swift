import XCTest

/// The connected boundary is termination/relaunch, disk audio and the real SwiftUI controls.
/// The server is a localhost fixture; host tests own conflict and receipt race cases.
final class OfflineOperationTests: XCTestCase {
    @MainActor func testOfflineRecoveryAndSlowColdLoad() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        addUIInterruptionMonitor(withDescription: "Microphone") { alert in
            let allow = alert.buttons["Allow"]
            if allow.exists { allow.tap(); return true }
            let okay = alert.buttons["OK"]
            if okay.exists { okay.tap(); return true }
            return false
        }
        try await proof(base, "start/cold-slow-load")
        app.launch()
        XCTAssertTrue(app.otherElements["Loading workspace"].firstMatch.waitForExistence(timeout: 3) || app.staticTexts["Loading workspace"].firstMatch.exists)
        try await proof(base, "fast")
        let chief = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Chief of Staff'")).firstMatch
        XCTAssertTrue(chief.waitForExistence(timeout: 12))
        try await proof(base, "stop")
        app.tabBars.buttons["Review"].tap()
        XCTAssertTrue(app.buttons["review.approve"].waitForExistence(timeout: 10))
        // Preload the three proposal cards and their manifests before losing connectivity.
        try await Task.sleep(for: .seconds(3))
        app.tabBars.buttons["Workspace"].tap()
        chief.tap()
        let input = app.textFields["Message"]
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        try await Task.sleep(for: .seconds(2))
        try await proof(base, "offline")
        try await proof(base, "start/offline-actions")
        input.tap(); input.typeText("Saved on the train")
        app.buttons["Send message"].tap()
        app.buttons.matching(NSPredicate(format: "label CONTAINS 'Saved on the train'")).firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Waiting for connection"].firstMatch.waitForExistence(timeout: 5))
        app.buttons["Close conversation"].tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.tabBars.buttons["Review"].tap()
        app.buttons["review.approve"].tap()
        let approval = app.buttons["review.confirmApproval"]
        XCTAssertTrue(approval.waitForExistence(timeout: 5))
        approval.tap()
        let sendBack = app.buttons["review.sendBack"]
        XCTAssertTrue(sendBack.waitForExistence(timeout: 8))
        sendBack.tap()
        let guidanceAny = app.descendants(matching: .any).matching(identifier: "review.revision").firstMatch
        XCTAssertTrue(guidanceAny.waitForExistence(timeout: 5))
        guidanceAny.tap(); guidanceAny.typeText("Keep this send-back guidance")
        app.buttons["review.confirmReturn"].tap()
        XCTAssertTrue(sendBack.waitForExistence(timeout: 8))
        sendBack.tap()
        app.buttons["Record a revision"].tap()
        try await Task.sleep(for: .seconds(2))
        if app.alerts.firstMatch.exists { app.alerts.firstMatch.buttons.element(boundBy: 1).tap() }
        app.tap()
        let stop = app.buttons["Stop and transcribe"]
        XCTAssertTrue(stop.waitForExistence(timeout: 8))
        try await Task.sleep(for: .seconds(2))
        stop.tap()
        XCTAssertTrue(app.staticTexts["recording saved on this phone"].waitForExistence(timeout: 8))
        try await proof(base, "stop")
        try await proof(base, "start/relaunch-and-reconnect")
        app.terminate(); app.launch()
        XCTAssertTrue(chief.waitForExistence(timeout: 3), "Cached Workspace did not restore offline")
        app.tabBars.buttons["Review"].tap()
        XCTAssertTrue(app.staticTexts["Waiting for connection"].firstMatch.waitForExistence(timeout: 5))
        try await proof(base, "online")
        for _ in 0..<20 {
            let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
            if (try JSONDecoder().decode([String].self, from: data)).count == 4 { break }
            try await Task.sleep(for: .seconds(1))
        }
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        XCTAssertEqual(try JSONDecoder().decode([String].self, from: data), ["message", "approve", "sendBack", "voice"])
        XCTAssertTrue(sendBack.waitForExistence(timeout: 8))
        sendBack.tap()
        XCTAssertTrue(guidanceAny.waitForExistence(timeout: 5))
        for _ in 0..<10 {
            if guidanceAny.value as? String == "Recorded guidance restored after reconnect." { break }
            try await Task.sleep(for: .milliseconds(500))
        }
        XCTAssertEqual(guidanceAny.value as? String, "Recorded guidance restored after reconnect.")
        try await Task.sleep(for: .seconds(4))
        let (again, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        XCTAssertEqual(try JSONDecoder().decode([String].self, from: again).count, 4)
        try await proof(base, "stop")
    }
    @MainActor private func proof(_ base: String, _ action: String) async throws {
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + action)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }
}
