import XCTest

/// One connected proof reuses the offline harness. It exercises native chart
/// controls, checklist geometry and Goal Review/conversation routing;
/// Foundation tests cannot prove that SwiftUI delivers these touches to writers.
final class GoalFlowTests: XCTestCase {
    /// One one-off walk proves the mounted pages have no empty goal section or gap.
    /// It reuses the loopback host and also supplies the four requested page captures.
    @MainActor func testHolderPagesKeepTicketsFirstAndOmitEmptyGoals() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        defer { app.terminate() }
        func element(_ id: String) -> XCUIElement {
            app.descendants(matching: .any).matching(identifier: id).firstMatch
        }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
            XCTAssertFalse(app.buttons["screen-refresh-error.retry"].exists, "The page must not display a failed read")
            let missingData = app.staticTexts.matching(NSPredicate(
                format: "label CONTAINS[c] %@ AND label CONTAINS[c] %@", "data", "missing"
            )).firstMatch
            XCTAssertFalse(missingData.exists, "The fixture must supply the complete holder readings contract")
        }
        func select(_ phase: String) async throws {
            app.terminate()
            let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/holder-pages/" + phase)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
            app.launch()
            XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 15))
        }
        func open(_ manager: Bool) {
            app.openDestination(manager ? "sidebar-manager-wren" : "sidebar-chief", timeout: 10)
            XCTAssertTrue(element(manager ? "orchestrator-screen" : "chief-screen").waitForExistence(timeout: 10))
            XCTAssertTrue(app.staticTexts["Tickets"].waitForExistence(timeout: 10))
            XCTAssertTrue(app.buttons[manager ? "worker-build" : "worker-weekly"].waitForExistence(timeout: 10))
        }
        func ticketsBottom() -> CGFloat {
            // Bind the final disclosure button as the fixed list boundary.
            let done = app.buttons["worker-group-Done"]
            XCTAssertTrue(done.waitForExistence(timeout: 5))
            return done.frame.maxY
        }
        func noGoalSection() {
            XCTAssertTrue(element("holder-goal-cards").waitForNonExistence(timeout: 5))
            XCTAssertFalse(element("holder-goal-content").exists)
            XCTAssertFalse(app.staticTexts["Goals"].exists)
            XCTAssertFalse(app.staticTexts["Active goals"].exists)
            XCTAssertFalse(app.buttons["All goals"].exists)
            XCTAssertEqual(app.staticTexts.matching(identifier: "Tickets").count, 1, "Retired authored Tickets blocks cannot duplicate the fixed list")
        }
        func reveal(_ control: XCUIElement) {
            let dock = app.buttons["conversation-line"]
            for _ in 0..<6 {
                if control.exists && control.isHittable && (!dock.exists || control.frame.maxY < dock.frame.minY) { break }
                app.swipeUp(velocity: .slow)
            }
            XCTAssertTrue(control.isHittable)
        }
        try await select("goals")
        open(false)
        XCTAssertTrue(element("holder-goal-cards").waitForExistence(timeout: 10))
        let markDone = app.buttons["Mark done"]
        XCTAssertTrue(markDone.waitForExistence(timeout: 10))
        XCTAssertLessThan(ticketsBottom(), element("holder-goal-cards").frame.minY)
        capture("holder-chief-with-goals")
        reveal(markDone); markDone.tap()
        XCTAssertTrue(app.buttons["Mark not done"].waitForExistence(timeout: 5))

        open(true)
        XCTAssertTrue(element("holder-goal-content").waitForExistence(timeout: 10))
        let check = app.buttons["goal-check-first"]
        XCTAssertTrue(check.waitForExistence(timeout: 10))
        XCTAssertLessThan(ticketsBottom(), element("holder-goal-content").frame.minY)
        capture("holder-manager-with-goal")
        reveal(check); check.tap()
        XCTAssertTrue(app.staticTexts["Explain the change."].waitForExistence(timeout: 5))
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        let writes = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        XCTAssertTrue(writes.contains { $0["path"] as? String == "/api/goals/holder-binary" && ($0["body"] as? [String: Any])?["completed"] as? Bool == true })
        XCTAssertTrue(writes.contains { $0["path"] as? String == "/api/goals/release" && ($0["body"] as? [String: Any])?["page"] != nil })

        try await select("none")
        for manager in [false, true] {
            open(manager)
            XCTAssertTrue(app.staticTexts[manager ? "Manager notes: none" : "Chief notes: none"].waitForExistence(timeout: 10))
            capture(manager ? "holder-manager-no-goals" : "holder-chief-no-goals")
            let hierarchy = XCTAttachment(string: app.debugDescription)
            hierarchy.name = manager ? "holder-manager-no-goals-hierarchy" : "holder-chief-no-goals-hierarchy"
            hierarchy.lifetime = .keepAlways; add(hierarchy)
            noGoalSection()
            let notes = app.staticTexts[manager ? "Manager notes: none" : "Chief notes: none"]
            let gap = notes.frame.minY - ticketsBottom()
            XCTAssertGreaterThanOrEqual(gap, 0)
            XCTAssertLessThanOrEqual(gap, 24, "An empty goal section must not reserve a second section gap")
        }
        try await select("multiple")
        open(true)
        XCTAssertTrue(element("holder-goal-cards").waitForExistence(timeout: 10))
        XCTAssertFalse(element("holder-goal-content").exists)
        XCTAssertLessThan(ticketsBottom(), element("holder-goal-cards").frame.minY)
        capture("holder-manager-multiple-goals")
        try await select("inactive")
        for manager in [false, true] {
            open(manager)
            XCTAssertTrue(app.staticTexts[manager ? "Manager notes: inactive" : "Chief notes: inactive"].waitForExistence(timeout: 10))
            noGoalSection()
        }
        try await select("empty")
        for manager in [false, true] {
            open(manager)
            noGoalSection()
            XCTAssertTrue(element("holder-document").waitForNonExistence(timeout: 5))
        }
    }

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
        app.openReviewCards(timeout: 15)
        let evidence = app.links["Open table evidence"]
        XCTAssertTrue(evidence.waitForExistence(timeout: 10)); evidence.tap()
        XCTAssertTrue(app.readableText("Table artifact proof").waitForExistence(timeout: 10))
        XCTAssertFalse(app.sidebarButton.exists, "A pushed artifact shows Back, not the Sidebar button")
        capture("review-artifact-sidebar-hidden")
        let nested = app.links["Next artifact"]
        XCTAssertTrue(nested.waitForExistence(timeout: 5)); nested.tap()
        XCTAssertTrue(app.readableText("Nested artifact proof").waitForExistence(timeout: 10))
        XCTAssertFalse(app.sidebarButton.exists)
        capture("nested-artifact-sidebar-hidden")
        backFromArtifact()
        XCTAssertTrue(app.readableText("Table artifact proof").waitForExistence(timeout: 5))
        XCTAssertFalse(app.sidebarButton.exists)
        backFromArtifact()
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 5)); XCTAssertTrue(app.sidebarButton.isHittable)
        capture("review-sidebar-restored")

        // Chief is a root page: the leading-edge swipe over its table opens the Sidebar.
        app.openDestination("sidebar-chief", timeout: 10)
        for scrolled in [false, true] {
            let table = element("goal-table-scroll-proof")
            XCTAssertTrue(table.waitForExistence(timeout: 10))
            let caption = app.readableText("Table scroll proof")
            XCTAssertTrue(caption.waitForExistence(timeout: 5))
            let header = element("table-column-scroll-proof-1")
            let cell = element("table-cell-scroll-proof-0-1")
            XCTAssertTrue(header.exists); XCTAssertTrue(cell.exists)
            let missingDataError = app.readableTexts.matching(NSPredicate(
                format: "(label CONTAINS[c] %@ AND label CONTAINS[c] %@) OR (value CONTAINS[c] %@ AND value CONTAINS[c] %@)", "data", "missing", "data", "missing"
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
            // The docked composer covers the bottom of the page: bring the card into the
            // upper part of the screen first.
            for _ in 0..<4 where card.frame.maxY > app.frame.height * 0.6 {
                app.swipeUp(velocity: .slow)
            }
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
        XCTAssertTrue(app.readableText("Explain the change.").waitForExistence(timeout: 8))
        capture("goals-checklist-next-item")
        let checklistWrites = try await writes()
        XCTAssertTrue(checklistWrites.contains { ($0["path"] as? String) == "/api/goals/release" })
        app.buttons["goal-check-next"].tap()
        XCTAssertTrue(app.readableText("All items done").waitForExistence(timeout: 5))
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
        app.openDestination("sidebar-settings")
        XCTAssertTrue(app.buttons["All goals"].waitForExistence(timeout: 10))
        app.buttons["All goals"].tap()
        XCTAssertTrue(element("all-goals-screen").waitForExistence(timeout: 10))
        capture("goals-all-filters")
        app.buttons["Goal"].tap()
        let filter = app.textFields["Goal contains"]
        XCTAssertTrue(filter.waitForExistence(timeout: 3)); filter.tap(); filter.typeText("release")
        app.buttons["Apply"].tap()
        XCTAssertTrue(app.readableText("Release the app").waitForExistence(timeout: 3))
        XCTAssertFalse(app.readableText("Write 1,000 words").exists)
        app.buttons["Goal"].tap(); app.buttons["Clear"].tap(); app.buttons["Apply"].tap()
        XCTAssertTrue(app.readableText("Write 1,000 words").waitForExistence(timeout: 3))
        app.backButton.tap()
        app.openDestination("sidebar-chief")
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 10))
        // Review must load Goal messages and the shared composer natively.
        app.openDestination("sidebar-review")
        app.openReviewCards()
        XCTAssertTrue(app.buttons["review.openConversation"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.links["Open on web"].exists)
        capture("goals-review-message")
        app.buttons["review.openConversation"].tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.sidebarButton.exists, "A pushed page shows Back, not the Sidebar button")
        capture("goals-review-conversation")
    }
}
