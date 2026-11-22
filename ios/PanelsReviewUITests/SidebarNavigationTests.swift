import XCTest

/// One-off walk of the shell on the Workspace fixture: the Sidebar's cards, destination
/// switches, the system Back swipe over a root page, an edge-only Sidebar swipe, one
/// composer under an open conversation, and a Chief draft kept across destinations.
/// Real navigation, gestures and keyboard layout need the app; model tests cannot prove them.
final class SidebarNavigationTests: XCTestCase {
    @MainActor func testDestinationsBackSwipeAndOneComposer() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/workspace-fixture")!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 12), "Review is the root at launch")
        XCTAssertFalse(app.tabBars.firstMatch.exists)
        capture(app, "sidebar-01-review-root")

        // Cards: Chief first, active Managers with their goal tag, Settled collapsed, no ticket rows.
        app.openSidebar()
        let wren = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'sidebar-manager-' AND label CONTAINS %@", "Wren release")).firstMatch
        let science = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'sidebar-manager-' AND label CONTAINS %@", "Science site")).firstMatch
        XCTAssertTrue(app.buttons["sidebar-chief"].waitForExistence(timeout: 8))
        XCTAssertTrue(wren.waitForExistence(timeout: 8)); XCTAssertTrue(science.exists)
        XCTAssertTrue(wren.label.contains("Off track"), wren.label)
        XCTAssertTrue(science.label.contains("On track"), science.label)
        XCTAssertLessThan(app.buttons["sidebar-chief"].frame.minY, wren.frame.minY, "Chief comes first")
        XCTAssertFalse(app.staticTexts["Choose the release notes"].exists, "Cards carry no ticket rows")
        XCTAssertTrue(app.buttons["sidebar-settings"].exists)
        let reopen = app.buttons["Reopen Previous release"]
        XCTAssertFalse(reopen.exists, "Settled starts collapsed")
        capture(app, "sidebar-02-open")
        app.buttons["sidebar-settled"].tap()
        XCTAssertTrue(reopen.waitForExistence(timeout: 8))
        capture(app, "sidebar-03-settled-open")
        app.buttons["sidebar-settled"].tap()
        XCTAssertTrue(reopen.waitForNonExistence(timeout: 5))

        // Chief: a docked draft, the composer above the keyboard, then the full conversation.
        app.buttons["sidebar-chief"].tap()
        let chiefScreen = app.descendants(matching: .any)["chief-screen"]
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 8))
        let inputs = app.textFields.matching(identifier: "conversation-input")
        let input = inputs.firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap(); input.typeText("Kept across destinations")
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        XCTAssertLessThanOrEqual(input.frame.maxY, keyboard.frame.minY + 1, "The docked composer stays above the keyboard")
        capture(app, "sidebar-04-chief-composer-keyboard")
        app.buttons["conversation-line"].tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 8))
        XCTAssertTrue(keyboard.waitForNonExistence(timeout: 5))
        XCTAssertEqual(inputs.count, 1, "An open conversation has one composer, with none under it")
        XCTAssertEqual(input.value as? String, "Kept across destinations")
        XCTAssertFalse(app.sidebarButton.exists, "A pushed page shows Back, not the Sidebar button")
        XCTAssertTrue(app.backButton.exists)
        capture(app, "sidebar-05-full-conversation")

        // The system edge swipe goes back from a page pushed on a root.
        let edge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.005, dy: 0.5))
        edge.press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)))
        XCTAssertTrue(app.buttons["conversation-actions"].waitForNonExistence(timeout: 5), "The Back swipe did not finish")
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 5))
        XCTAssertEqual(inputs.count, 1)
        XCTAssertEqual(input.value as? String, "Kept across destinations")

        // Only the leading edge opens the Sidebar; a drag across the page does not.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.35, dy: 0.45))
            .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.45)))
        XCTAssertFalse(app.buttons["sidebar-review"].waitForExistence(timeout: 2), "A mid-page drag opened the Sidebar")
        edge.press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.5)))
        XCTAssertTrue(app.buttons["sidebar-review"].waitForExistence(timeout: 5), "The edge swipe did not open the Sidebar")
        capture(app, "sidebar-06-edge-open")

        // A Manager destination shows its page with its sole goal inline.
        wren.tap()
        XCTAssertTrue(app.descendants(matching: .any)["orchestrator-screen"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.buttons["goal-check-first"].waitForExistence(timeout: 8))
        capture(app, "sidebar-07-manager")

        // Settings holds the old More places; a pushed place keeps native Back.
        app.openDestination("sidebar-settings")
        for place in ["Ideas", "All goals", "Usage", "VPS"] {
            XCTAssertTrue(app.buttons[place].waitForExistence(timeout: 8), "Settings has no \(place)")
        }
        XCTAssertFalse(app.buttons["Feedback"].exists)
        XCTAssertTrue(app.descendants(matching: .any)["settings.deployment"].exists)
        capture(app, "sidebar-08-settings")
        app.buttons["All goals"].tap()
        XCTAssertTrue(app.backButton.waitForExistence(timeout: 8))
        app.backButton.tap()
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 5))

        // The Chief draft survives the trip through other destinations.
        app.openDestination("sidebar-chief")
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 8))
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, "Kept across destinations")
        app.openDestination("sidebar-review")
        XCTAssertFalse(chiefScreen.exists)
        capture(app, "sidebar-09-back-on-review")
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }
}
