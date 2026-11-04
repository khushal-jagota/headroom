import XCTest

/// One connected proof reuses the offline harness. It exercises native context
/// menus, chart controls, checklist geometry and Goal Review/conversation routing;
/// Foundation tests cannot prove that SwiftUI delivers these touches to writers.
final class GoalFlowTests: XCTestCase {
    @MainActor func testGoalsFromWorkspaceThroughNativeEditsAndReview() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch(); defer { app.terminate() }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; self.add(attachment)
        }
        func element(_ id: String) -> XCUIElement { app.descendants(matching: .any).matching(identifier: id).firstMatch }
        func writes() async throws -> [[String: Any]] {
            let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
            return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        }
        let words = app.buttons["workspace-goal-words"]
        XCTAssertTrue(words.waitForExistence(timeout: 15))
        capture("goals-workspace")
        app.buttons["Chief of Staff"].tap()
        XCTAssertTrue(app.buttons["detail-back"].waitForExistence(timeout: 10))
        capture("chief-before-bounds-check")
        let readingButton = element("goal-manual-value-words")
        XCTAssertTrue(readingButton.waitForExistence(timeout: 5))
        XCTAssertLessThan(readingButton.frame.height, 64, "Digit wheels must not cover other controls")
        XCTAssertFalse(readingButton.frame.intersects(app.buttons["detail-back"].frame))
        capture("chief-before-back")
        XCTAssertTrue(app.buttons["detail-back"].isHittable)
        app.buttons["detail-back"].tap()
        capture("chief-after-back")
        XCTAssertTrue(words.waitForExistence(timeout: 8))
        words.press(forDuration: 1)
        let increment = app.buttons["Add 100 words"]
        XCTAssertTrue(increment.waitForExistence(timeout: 5))
        capture("goals-native-peek")
        increment.tap()
        try await Task.sleep(for: .seconds(1))
        let peekWrites = try await writes()
        XCTAssertEqual(peekWrites.last?["path"] as? String, "/api/measures/words/manual-writes")
        words.tap()
        XCTAssertTrue(element("goal-chart-words").waitForExistence(timeout: 10))
        let add = app.buttons["+100"]
        XCTAssertTrue(add.waitForExistence(timeout: 10)); add.tap()
        try await Task.sleep(for: .seconds(1))
        let manualWrites = try await writes()
        let bodies = manualWrites.compactMap { $0["body"] as? [String: Any] }
        XCTAssertEqual(bodies.filter { ($0["delta"] as? Int) == 100 }.count, 2)
        let valueButton = element("goal-manual-value-words").buttons.firstMatch
        valueButton.tap()
        let input = app.textFields["Words written"]
        XCTAssertTrue(input.waitForExistence(timeout: 3))
        input.tap()
        input.press(forDuration: 1)
        if app.menuItems["Select All"].exists { app.menuItems["Select All"].tap() }
        else { input.tap(withNumberOfTaps: 3, numberOfTouches: 1) }
        input.typeText("750.25")
        app.buttons["Save"].tap()
        try await Task.sleep(for: .seconds(0.5))
        let replacement = try await writes()
        XCTAssertTrue(replacement.compactMap { $0["body"] as? [String: Any] }.contains { ($0["value"] as? Double) == 750.25 })
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
        app.scrollViews.firstMatch.swipeDown()
        capture("goals-manual-daily")
        capture("goal-before-back")
        XCTAssertTrue(app.buttons["detail-back"].isHittable)
        XCTAssertTrue(app.frame.contains(app.buttons["detail-back"].frame))
        app.buttons["detail-back"].tap()
        capture("goal-after-back")
        XCTAssertTrue(app.buttons["workspace-goal-release"].waitForExistence(timeout: 8))
        app.buttons["workspace-goal-release"].tap()
        XCTAssertTrue(app.buttons["goal-check-first"].waitForExistence(timeout: 10))
        capture("goals-checklist-open")
        let firstCircle = app.buttons["goal-check-first"]
        XCTAssertGreaterThanOrEqual(firstCircle.frame.width, 36)
        XCTAssertGreaterThanOrEqual(firstCircle.frame.height, 44)
        XCTAssertTrue(firstCircle.isHittable)
        firstCircle.tap()
        capture("goals-checklist-after-tick")
        XCTAssertTrue(app.staticTexts["Explain the change."].waitForExistence(timeout: 8))
        capture("goals-checklist-next-item")
        let checklistWrites = try await writes()
        XCTAssertTrue(checklistWrites.contains { ($0["path"] as? String) == "/api/goals/release" })
        app.buttons["goal-check-next"].tap()
        XCTAssertTrue(app.staticTexts["All items done"].waitForExistence(timeout: 5))
        capture("goals-checklist-completed")
        app.buttons["detail-back"].tap()
        app.buttons["workspace-goal-kit"].tap()
        XCTAssertTrue(element("goal-chart-trend").waitForExistence(timeout: 10))
        capture("goals-primary-completion-and-kit")
        // A supporting chart opens in place and retains its range and open state.
        let support = element("goal-chart-activation").buttons.firstMatch
        for _ in 0..<18 {
            if support.exists && support.isHittable { break }
            app.scrollViews.firstMatch.swipeUp()
        }
        XCTAssertTrue(support.isHittable)
        XCTAssertEqual(support.value as? String, "Collapsed")
        support.tap()
        XCTAssertEqual(support.value as? String, "Expanded")
        let week = element("goal-chart-activation").buttons["Week"]
        XCTAssertTrue(week.waitForExistence(timeout: 3)); week.tap()
        support.tap()
        XCTAssertEqual(support.value as? String, "Collapsed")
        support.tap()
        XCTAssertTrue(week.isSelected)
        capture("goals-chart-catalogue")
        for _ in 0..<18 {
            if app.buttons["detail-back"].isHittable { break }
            app.scrollViews.firstMatch.swipeDown()
        }
        app.buttons["detail-back"].tap()
        app.buttons["Chief of Staff"].tap()
        XCTAssertTrue(app.buttons["All goals"].waitForExistence(timeout: 10))
        app.buttons["All goals"].tap()
        XCTAssertTrue(element("all-goals-screen").waitForExistence(timeout: 10))
        capture("goals-all-filters")
        app.navigationBars.buttons.firstMatch.tap()
        // Review must load Goal messages and the shared composer natively.
        app.buttons["detail-back"].firstMatch.tap()
        app.tabBars.buttons["Review"].tap()
        XCTAssertTrue(app.buttons["review.openConversation"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.links["Open on web"].exists)
        capture("goals-review-message")
        app.buttons["review.openConversation"].tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.tabBars.firstMatch.isHittable)
        capture("goals-review-conversation")
    }
}
