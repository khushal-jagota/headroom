import XCTest

/// One connected proof reuses the offline harness. It exercises native chart
/// controls, checklist geometry and Goal Review/conversation routing;
/// Foundation tests cannot prove that SwiftUI delivers these touches to writers.
final class GoalFlowTests: XCTestCase {
    /// One-off regression review reuses the existing loopback host. Only the real
    /// SwiftUI navigation and touch stack can prove horizontal-pan and edge-swipe
    /// priority plus the root bar's return across Review's direct artifact destination.
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
        // The system Back label names the previous artifact's title, so pick the bar's first button.
        func backFromArtifact() {
            app.navigationBars.buttons.element(boundBy: 0).tap()
        }
        // Review is the root at launch.
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 15))
        let evidence = app.links["Open table evidence"]
        XCTAssertTrue(evidence.waitForExistence(timeout: 10)); evidence.tap()
        XCTAssertTrue(app.staticTexts["Table artifact proof"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.sidebarButton.exists, "A pushed artifact shows Back, not the Sidebar button")
        capture("review-artifact-sidebar-hidden")
        let nested = app.links["Next artifact"]
        XCTAssertTrue(nested.waitForExistence(timeout: 5)); nested.tap()
        XCTAssertTrue(app.staticTexts["Nested artifact proof"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.sidebarButton.exists)
        capture("nested-artifact-sidebar-hidden")
        backFromArtifact()
        XCTAssertTrue(app.staticTexts["Table artifact proof"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.sidebarButton.exists)
        backFromArtifact()
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 5)); XCTAssertTrue(app.sidebarButton.isHittable)
        capture("review-sidebar-restored")

        // Chief is a root page: the leading-edge swipe over its table opens the Sidebar.
        app.openDestination("sidebar-chief", timeout: 10)
        for scrolled in [false, true] {
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
            let sidebarChief = app.buttons["sidebar-chief"]
            XCTAssertTrue(sidebarChief.waitForExistence(timeout: 5), "The edge swipe must open the Sidebar at either table offset")
            capture(scrolled ? "edge-sidebar-from-scrolled-table" : "edge-sidebar-from-initial-table")
            // Choosing the shown destination closes the Sidebar over the same Chief page.
            sidebarChief.tap()
            XCTAssertTrue(sidebarChief.waitForNonExistence(timeout: 5))
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
        app.launchArguments = ["--conversation-stub", base]
        app.launch(); defer { app.terminate() }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; self.add(attachment)
        }
        func element(_ id: String) -> XCUIElement { app.descendants(matching: .any).matching(identifier: id).firstMatch }
        // The fixture's goals are all held by Chief, whose page lists each as a card.
        // A card inlines its primary chart or checklist, so each Goal page is confirmed
        // by its own screen and Back before anything on it is checked.
        func goalCard(_ title: String) -> XCUIElement {
            app.buttons.matching(NSPredicate(format: "label CONTAINS %@", title)).firstMatch
        }
        let goalScreen = element("goal-screen")
        func openGoal(_ title: String) {
            let card = goalCard(title)
            XCTAssertTrue(card.waitForExistence(timeout: 10), "Chief has no goal \(title)")
            card.tap()
            XCTAssertTrue(goalScreen.waitForExistence(timeout: 10))
            XCTAssertTrue(app.backButton.waitForExistence(timeout: 5))
        }
        func writes() async throws -> [[String: Any]] {
            let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
            return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        }
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 15))
        app.openDestination("sidebar-chief", timeout: 15)
        let chiefScreen = element("chief-screen")
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 10))
        let words = goalCard("Write 1,000 words")
        XCTAssertTrue(words.waitForExistence(timeout: 15))
        let (_, recording) = try await URLSession.shared.data(from: URL(string: base + "/proof/start/goals-native-motion")!)
        XCTAssertEqual((recording as? HTTPURLResponse)?.statusCode, 200)
        capture("goals-chief")
        let readingButton = element("goal-manual-value-words")
        XCTAssertTrue(readingButton.waitForExistence(timeout: 5))
        XCTAssertLessThan(readingButton.frame.height, 64, "Digit wheels must not cover other controls")
        XCTAssertTrue(app.sidebarButton.isHittable)
        capture("chief-bounds-check")
        openGoal("Write 1,000 words")
        XCTAssertTrue(element("goal-chart-words").waitForExistence(timeout: 10))
        let add = app.buttons["+100"]
        XCTAssertTrue(add.waitForExistence(timeout: 10)); add.tap()
        try await Task.sleep(for: .seconds(1))
        let manualWrites = try await writes()
        XCTAssertEqual(manualWrites.last?["path"] as? String, "/api/measures/words/manual-writes")
        let bodies = manualWrites.compactMap { $0["body"] as? [String: Any] }
        XCTAssertEqual(bodies.filter { ($0["delta"] as? Int) == 100 }.count, 1)
        XCTAssertFalse(app.buttons["conversation-actions"].exists, "Adding to a Goal reading must leave the Goal page open")
        capture("goals-manual-daily")
        capture("goal-before-back")
        XCTAssertTrue(app.backButton.isHittable)
        app.backButton.tap()
        capture("goal-after-back")
        let firstBackReturned = goalScreen.waitForNonExistence(timeout: 3) && chiefScreen.waitForExistence(timeout: 3)

        XCTAssertTrue(firstBackReturned, "Back must return on the first tap after editing")
        openGoal("Release the app")
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
        app.backButton.tap()
        XCTAssertTrue(goalScreen.waitForNonExistence(timeout: 5))
        openGoal("Explore the page kit")
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
        app.backButton.tap()
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 10))
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
        app.backButton.tap()
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 10))
        // Review must load Goal messages and the shared composer natively.
        app.openDestination("sidebar-review")
        XCTAssertTrue(app.buttons["review.openConversation"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.links["Open on web"].exists)
        capture("goals-review-message")
        app.buttons["review.openConversation"].tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.sidebarButton.exists, "A pushed page shows Back, not the Sidebar button")
        capture("goals-review-conversation")
    }
}
