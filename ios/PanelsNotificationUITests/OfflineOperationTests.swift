import XCTest

/// The connected boundary is termination/relaunch and explicit recovery through real SwiftUI controls.
/// The localhost fixture accepts and drops connections; this is uncertain delivery, not airplane mode.
final class OfflineOperationTests: XCTestCase {
    @MainActor func testDroppedConnectionRequiresExplicitRecoveryAndSlowColdLoad() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        try await proof(base, "fast")
        app.launch()
        defer { app.terminate() }
        let chief = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Chief of Staff'")).firstMatch
        XCTAssertTrue(chief.waitForExistence(timeout: 12))
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
        input.tap(); input.typeText("Saved on the train")
        app.buttons["Send message"].tap()
        let pending = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Saved on the train'")).firstMatch
        XCTAssertTrue(pending.waitForExistence(timeout: 5))
        pending.tap()
        XCTAssertTrue(app.staticTexts["Delivery uncertain · check before another attempt"].firstMatch.waitForExistence(timeout: 5))
        // The conversation page and the Chief page each have the shared Back control.
        app.buttons["detail-back"].tap()
        XCTAssertTrue(app.staticTexts["Chief of Staff"].waitForExistence(timeout: 8))
        app.buttons["detail-back"].tap()
        app.tabBars.buttons["Review"].tap()
        let approve = app.buttons["review.approve"]
        XCTAssertTrue(approve.waitForExistence(timeout: 10))
        approve.tap()
        let approval = app.buttons["review.confirmApproval"]
        XCTAssertTrue(approval.waitForExistence(timeout: 5))
        approval.tap()
        let sendBack = app.buttons["review.sendBack"]
        XCTAssertTrue(sendBack.waitForExistence(timeout: 8))
        sendBack.tap()
        let guidanceAny = app.descendants(matching: .any).matching(identifier: "review.revision").firstMatch
        XCTAssertTrue(guidanceAny.waitForExistence(timeout: 5))
        guidanceAny.tap(); guidanceAny.typeText("Keep this send-back guidance")
        let confirmReturn = app.buttons["review.confirmReturn"]
        XCTAssertTrue(confirmReturn.waitForExistence(timeout: 5))
        confirmReturn.tap()
        app.terminate(); app.launch()
        XCTAssertTrue(chief.waitForExistence(timeout: 3), "Cached Workspace did not restore offline")
        app.tabBars.buttons["Review"].tap()
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label CONTAINS 'Record offline'")).firstMatch.waitForExistence(timeout: 5), "Cached Review did not restore the remaining ask offline")
        // Returning connectivity cannot authorize a second attempt for the unknown result.
        app.tabBars.buttons["Workspace"].tap()
        try await proof(base, "online")
        for _ in 0..<20 {
            if try await writes(base).count == 2 { break }
            try await Task.sleep(for: .seconds(1))
        }
        let beforeRecovery = try await writes(base)
        XCTAssertEqual(Set(beforeRecovery), Set(["approve", "sendBack"]))
        XCTAssertEqual(beforeRecovery.count, 2)
        chief.tap()
        XCTAssertTrue(pending.waitForExistence(timeout: 8))
        pending.tap()
        let sendAgain = app.buttons["Send again"]
        XCTAssertTrue(sendAgain.waitForExistence(timeout: 8))
        sendAgain.tap()
        let confirmation = app.sheets.firstMatch
        XCTAssertTrue(confirmation.waitForExistence(timeout: 5))
        XCTAssertTrue(confirmation.staticTexts["The earlier result is unknown. Another attempt can send this message twice."].exists)
        let beforeConfirmation = try await writes(base)
        XCTAssertEqual(Set(beforeConfirmation), Set(["approve", "sendBack"]))
        XCTAssertEqual(beforeConfirmation.count, 2)
        confirmation.buttons["Send again"].tap()
        for _ in 0..<20 {
            if try await writes(base).count == 3 { break }
            try await Task.sleep(for: .seconds(1))
        }
        let afterConfirmation = try await writes(base)
        XCTAssertEqual(Set(afterConfirmation), Set(["approve", "sendBack", "message"]))
        XCTAssertEqual(afterConfirmation.count, 3)
    }
    @MainActor private func writes(_ base: String) async throws -> [String] {
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        return try JSONDecoder().decode([String].self, from: data)
    }
    @MainActor private func proof(_ base: String, _ action: String) async throws {
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + action)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }
}
