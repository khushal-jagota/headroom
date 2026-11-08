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
        // The row label also carries the Chief's unread mark from the navigation view.
        let chiefRow = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Chief of Staff")).firstMatch
        func writes() async throws -> [[String: Any]] {
            let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
            return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        }
        let words = app.buttons["workspace-goal-words"]
        XCTAssertTrue(words.waitForExistence(timeout: 15))
        let (_, recording) = try await URLSession.shared.data(from: URL(string: base + "/proof/start/goals-native-motion")!)
        XCTAssertEqual((recording as? HTTPURLResponse)?.statusCode, 200)
        capture("goals-workspace")
        chiefRow.tap()
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
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 3))
        XCTAssertFalse((input.value as? String ?? "").hasSuffix(".0"), "Whole readings must not show a decimal")
        // The field selects its whole reading on focus, so typing replaces it.
        input.typeText("750.25")
        input.press(forDuration: 1)
        XCTAssertFalse(app.menuItems.firstMatch.exists, "No edit menu may cover the reading: \(app.menuItems.allElementsBoundByIndex.map(\.label))")
        capture("goal-before-save")
        let save = app.toolbars.buttons["Done"]
        XCTAssertTrue(save.isHittable)
        XCTAssertFalse(save.frame.intersects(readingButton.frame), "Done must not crowd the reading")
        save.tap()
        capture("goal-after-save")
        try await Task.sleep(for: .seconds(0.5))
        let replacement = try await writes()
        XCTAssertTrue(replacement.compactMap { $0["body"] as? [String: Any] }.contains { ($0["value"] as? Double) == 750.25 })
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
        XCTAssertFalse(app.buttons["conversation-actions"].exists, "Saving a Goal reading must leave the Goal page open")
        if !app.buttons["detail-back"].isHittable { app.scrollViews["goal-screen"].swipeDown() }
        capture("goals-manual-daily")
        capture("goal-before-back")
        XCTAssertTrue(app.buttons["detail-back"].isHittable)
        XCTAssertTrue(app.frame.contains(app.buttons["detail-back"].frame))
        app.buttons["detail-back"].tap()
        capture("goal-after-back")
        let firstBackReturned = app.buttons["workspace-goal-release"].waitForExistence(timeout: 3)

        XCTAssertTrue(firstBackReturned, "Back must return on the first tap after editing")
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
            if support.exists && support.isHittable && support.frame.maxY < app.buttons["conversation-line"].frame.minY { break }
            app.scrollViews["goal-screen"].coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.65)).press(forDuration: 0.01, thenDragTo: app.scrollViews["goal-screen"].coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.15)))
        }
        capture("goals-supporting-scroll-position")
        XCTAssertTrue(support.isHittable)
        XCTAssertEqual(support.value as? String, "Collapsed")
        support.tap()
        XCTAssertEqual(support.value as? String, "Expanded")
        let week = element("goal-chart-activation").buttons["Week"]
        XCTAssertTrue(week.waitForExistence(timeout: 3))
        // The scroll view's frame extends behind its conversation inset. Keep
        // the footer above that inset before delivering the range tap.
        for _ in 0..<6 {
            if week.frame.maxY < app.buttons["conversation-line"].frame.minY && week.isHittable { break }
            let scroll = app.scrollViews["goal-screen"]
            scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6)).press(forDuration: 0.01, thenDragTo: scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.35)))
        }
        XCTAssertLessThan(week.frame.maxY, app.buttons["conversation-line"].frame.minY)
        week.tap()
        XCTAssertTrue(week.isSelected)
        support.tap()
        XCTAssertEqual(support.value as? String, "Collapsed")
        support.tap()
        XCTAssertTrue(week.isSelected)
        capture("goals-chart-catalogue")
        for _ in 0..<18 {
            if app.buttons["detail-back"].isHittable { break }
            app.scrollViews["goal-screen"].swipeDown()
        }
        app.buttons["detail-back"].tap()
        chiefRow.tap()
        XCTAssertTrue(app.buttons["All goals"].waitForExistence(timeout: 10))
        app.buttons["All goals"].tap()
        XCTAssertTrue(element("all-goals-screen").waitForExistence(timeout: 10))
        capture("goals-all-filters")
        app.buttons["Goal"].tap()
        let filter = app.textFields["Goal contains"]
        XCTAssertTrue(filter.waitForExistence(timeout: 3)); filter.tap(); filter.typeText("release")
        app.buttons["Apply"].tap()
        XCTAssertTrue(app.staticTexts["Release the app"].waitForExistence(timeout: 3))
        XCTAssertFalse(app.staticTexts["Write 1,000 words"].exists)
        app.buttons["Goal"].tap(); app.buttons["Clear"].tap(); app.buttons["Apply"].tap()
        XCTAssertTrue(app.staticTexts["Write 1,000 words"].waitForExistence(timeout: 3))
        if app.buttons["BackButton"].exists { app.buttons["BackButton"].tap() }
        else { app.navigationBars.buttons.firstMatch.tap() }
        // Review must load Goal messages and the shared composer natively.
        app.buttons["detail-back"].firstMatch.tap()
        app.buttons["Review"].tap()
        XCTAssertTrue(app.buttons["review.openConversation"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.links["Open on web"].exists)
        capture("goals-review-message")
        app.buttons["review.openConversation"].tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.tabBars.firstMatch.isHittable)
        capture("goals-review-conversation")
    }
}
