import XCTest

/// One connected proof reuses the offline harness. It exercises native context
/// menus, chart controls, checklist geometry and Goal Review/conversation routing;
/// Foundation tests cannot prove that SwiftUI delivers these touches to writers.
final class GoalFlowTests: XCTestCase {
    /// One-off regression review reuses the existing loopback host. Only the real
    /// SwiftUI navigation and touch stack can prove horizontal-pan and edge-pop
    /// priority plus tab restoration across Review's direct artifact destination.
    @MainActor func testSharedTableScrollAndReviewArtifactTabs() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch(); defer { app.terminate() }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
        }
        func element(_ id: String) -> XCUIElement {
            app.descendants(matching: .any).matching(identifier: id).firstMatch
        }
        func backFromArtifact() {
            app.navigationBars.buttons.element(boundBy: 0).tap()
        }
        let review = app.tabBars.buttons["Review"]
        XCTAssertTrue(review.waitForExistence(timeout: 15)); review.tap()
        let evidence = app.links["Open table evidence"]
        XCTAssertTrue(evidence.waitForExistence(timeout: 10)); evidence.tap()
        XCTAssertTrue(app.staticTexts["Table artifact proof"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.tabBars.firstMatch.isHittable)
        capture("review-artifact-tabs-hidden")
        let nested = app.links["Next artifact"]
        XCTAssertTrue(nested.waitForExistence(timeout: 5)); nested.tap()
        XCTAssertTrue(app.staticTexts["Nested artifact proof"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.tabBars.firstMatch.isHittable)
        capture("nested-artifact-tabs-hidden")
        backFromArtifact()
        XCTAssertTrue(app.staticTexts["Table artifact proof"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.tabBars.firstMatch.isHittable)
        backFromArtifact()
        XCTAssertTrue(review.waitForExistence(timeout: 5)); XCTAssertTrue(review.isHittable)
        capture("review-tabs-restored")

        let workspace = app.tabBars.buttons["Workspace"]
        workspace.tap()
        let chief = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Chief of Staff")).firstMatch
        XCTAssertTrue(chief.waitForExistence(timeout: 10))
        for scrolled in [false, true] {
            chief.tap()
            let table = element("goal-table-scroll-proof")
            XCTAssertTrue(table.waitForExistence(timeout: 10))
            let caption = app.staticTexts["Table scroll proof"]
            XCTAssertTrue(caption.waitForExistence(timeout: 5))
            let header = element("table-column-scroll-proof-1")
            let cell = element("table-cell-scroll-proof-0-1")
            XCTAssertTrue(header.exists); XCTAssertTrue(cell.exists)
            let missingDataError = app.staticTexts.matching(NSPredicate(
                format: "label CONTAINS[c] %@ AND label CONTAINS[c] %@", "data", "missing"
            )).firstMatch
            XCTAssertFalse(missingDataError.exists, "The fixture must supply a complete Chief readings response")
            let pageX = caption.frame.minX
            capture(scrolled ? "table-before-shared-pan" : "table-initial")
            if scrolled {
                let headerX = header.frame.minX, cellX = cell.frame.minX
                let start = table.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.6))
                let finish = table.coordinate(withNormalizedOffset: CGVector(dx: 0.6, dy: 0.6))
                start.press(forDuration: 0.1, thenDragTo: finish, withVelocity: .slow, thenHoldForDuration: 0.4)
                let headerDelta = header.frame.minX - headerX
                let cellDelta = cell.frame.minX - cellX
                XCTAssertLessThan(headerDelta, -20, "The wide table must move horizontally")
                XCTAssertEqual(headerDelta, cellDelta, accuracy: 2, "Headers and rows must share the same horizontal offset")
                XCTAssertEqual(caption.frame.minX, pageX, accuracy: 1, "The page must stay fixed while its table moves")
                capture("table-after-shared-pan")
            }
            let y = table.frame.midY / app.frame.height
            let edge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.005, dy: y))
            edge.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: y)))
            XCTAssertTrue(workspace.waitForExistence(timeout: 5))
            XCTAssertTrue(workspace.isHittable, "The edge swipe must pop the Chief page at either table offset")
            XCTAssertTrue(table.waitForNonExistence(timeout: 5))
            capture(scrolled ? "edge-back-from-scrolled-table" : "edge-back-from-initial-table")
        }
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        let writes = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        XCTAssertFalse(writes.contains { entry in
            let path = entry["path"] as? String ?? ""
            return ["/messages", "/reply", "/send"].contains { path.hasSuffix($0) }
        }, "This review never sends messages")
    }

    @MainActor func testGoalsFromWorkspaceThroughNativeEditsAndReview() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base, "-panels.goals-sidebar-rows", "YES"]
        app.launch(); defer { app.terminate() }
        let workspace = app.tabBars.buttons["Workspace"]
        XCTAssertTrue(workspace.waitForExistence(timeout: 15)); workspace.tap()
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
        // Retain the conflicted draft across leaving and recreating this page.
        // A newer accepted value must remain intact after an explicit retry.
        app.staticTexts["Keep the next step clear."].firstMatch.tap()
        let explanation = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", "Why this goal matters")).firstMatch
        XCTAssertTrue(explanation.waitForExistence(timeout: 5))
        explanation.typeText(" Edited locally")
        let (_, conflict) = try await URLSession.shared.data(from: URL(string: base + "/proof/goal-conflict")!)
        XCTAssertEqual((conflict as? HTTPURLResponse)?.statusCode, 200)
        app.buttons["Save"].tap()
        XCTAssertTrue(app.buttons["Retry save"].waitForExistence(timeout: 8))
        app.buttons["detail-back"].tap()
        XCTAssertTrue(words.waitForExistence(timeout: 8))
        words.tap()
        XCTAssertTrue(explanation.waitForExistence(timeout: 8))
        XCTAssertEqual(explanation.value as? String, "Keep the next step clear. Edited locally")
        let (beforeData, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/goal-state")!)
        let before = try XCTUnwrap(JSONSerialization.jsonObject(with: beforeData) as? [String: Any])
        let attempts = try XCTUnwrap(before["attempts"] as? Int)
        app.buttons["Retry save"].tap()
        var accepted: [String: Any] = [:]
        for _ in 0..<40 {
            let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/goal-state")!)
            accepted = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            if (accepted["attempts"] as? Int ?? 0) > attempts && app.buttons["Discard edit"].isEnabled { break }
            try await Task.sleep(for: .milliseconds(200))
        }
        XCTAssertEqual(accepted["attempts"] as? Int, attempts + 1)
        XCTAssertTrue(app.buttons["Discard edit"].isEnabled)
        XCTAssertEqual((accepted["goal"] as? [String: Any])?["why"] as? String, "Newer server explanation.")
        app.buttons["Discard edit"].tap()
        XCTAssertTrue(app.staticTexts["Newer server explanation."].waitForExistence(timeout: 8))
        capture("goals-conflict-survives-page-return")
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
