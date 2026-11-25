import XCTest

/// One-off walk of the shell on the Workspace fixture: the Sidebar's cards, destination
/// switches, the system Back swipe over a root page, an edge-only Sidebar swipe, one
/// composer under an open conversation, and a Chief draft kept across destinations.
/// Real navigation, gestures and keyboard layout need the app; model tests cannot prove them.
final class SidebarNavigationTests: XCTestCase {
    /// One-off output capture using the existing loopback host. Resolution and
    /// refresh behavior have Foundation coverage; this method captures actual labels.
    @MainActor func testMachineNamesAndUnknownActivityFollowRename() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
        }
        app.openReviewCards(timeout: 15)
        let openTicket = app.buttons["review.openTicket"]
        XCTAssertTrue(openTicket.waitForExistence(timeout: 10)); openTicket.tap()
        let namedLabel = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", "Runs on Mac 1")).firstMatch
        XCTAssertTrue(namedLabel.waitForExistence(timeout: 10))
        capture("machine-name-ticket-mac-1")
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 10)); line.tap()
        XCTAssertTrue(app.staticTexts["Current activity on Mac 1 is unknown."].waitForExistence(timeout: 10))
        capture("machine-name-conversation-mac-1")
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/machine-name/rename")!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        // Wait for the existing bounded resource refresh with the conversation open.
        XCTAssertTrue(app.staticTexts["Current activity on Studio is unknown."].waitForExistence(timeout: 45))
        capture("machine-name-conversation-renamed-studio")
    }

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
        for place in ["All goals", "Usage", "Diagnostics"] {
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

    /// The Sidebar follows the thumb from both sides. Each drag holds part way before it lets
    /// go, so a recording of this walk shows the panel under the finger; the assertions check
    /// where each release lands. Run once with Reduce Motion on as well: the landings match.
    @MainActor func testSidebarFollowsTheThumb() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/workspace-fixture")!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 12))
        let chief = app.buttons["sidebar-chief"]
        let chiefScreen = app.descendants(matching: .any)["chief-screen"]
        func at(_ x: CGFloat, _ y: CGFloat) -> XCUICoordinate { app.coordinate(withNormalizedOffset: CGVector(dx: x, dy: y)) }
        /// A drag that holds still before it lets go, so the release speed is zero.
        func slowDrag(_ from: XCUICoordinate, _ to: XCUICoordinate) {
            from.press(forDuration: 0.05, thenDragTo: to, withVelocity: 400, thenHoldForDuration: 0.8)
        }
        func flick(_ from: XCUICoordinate, _ to: XCUICoordinate) {
            from.press(forDuration: 0.05, thenDragTo: to, withVelocity: 2500, thenHoldForDuration: 0)
        }
        func step(_ name: String) { print("SIDEBAR-STEP \(name) \(Date().timeIntervalSince1970)") }
        app.openSidebar()
        let resting = chief.frame.minX
        // VoiceOver keeps the modal Sidebar layer, so its close must sit inside it.
        let layer = app.descendants(matching: .any)["sidebar-layer"]
        XCTAssertTrue(layer.exists, "The Sidebar layer is not one accessibility container")
        XCTAssertTrue(layer.descendants(matching: .any)["Close sidebar"].exists, "VoiceOver has no close inside the Sidebar")
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        XCTAssertTrue(chief.waitForNonExistence(timeout: 5), "A tap on the dim did not close the Sidebar")
        /// The panel is back at its resting place within the settle time.
        func restsOpen(_ message: String) {
            let deadline = Date().addingTimeInterval(2)
            while Date() < deadline, !(chief.exists && abs(chief.frame.minX - resting) < 1) { usleep(50_000) }
            XCTAssertTrue(chief.exists, message)
            XCTAssertEqual(chief.frame.minX, resting, accuracy: 1, message)
        }
        func closes(_ message: String) {
            XCTAssertTrue(chief.waitForNonExistence(timeout: 5), message)
            XCTAssertFalse(chiefScreen.exists, "\(message): a card opened")
        }

        step("edge-short")
        slowDrag(at(0.005, 0.5), at(0.3, 0.5))
        XCTAssertTrue(chief.waitForNonExistence(timeout: 5), "An edge drag released short of half stayed open")
        step("edge-flick")
        flick(at(0.005, 0.5), at(0.25, 0.5))
        restsOpen("A short fast edge flick did not open the Sidebar")

        step("panel-short")
        slowDrag(at(0.6, 0.6), at(0.4, 0.6))
        restsOpen("A drag on the panel released short of half did not return open")
        step("panel-stretch")
        slowDrag(at(0.3, 0.6), at(0.95, 0.6))
        restsOpen("A drag past fully open did not return to rest")
        step("panel-vertical")
        slowDrag(at(0.4, 0.6), at(0.42, 0.3))
        restsOpen("A vertical drag on the panel moved the Sidebar")
        step("panel-long")
        slowDrag(at(0.7, 0.6), at(0.1, 0.6))
        closes("A long drag on the panel did not close the Sidebar")

        app.openSidebar()
        step("card-long")
        slowDrag(chief.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.5)), at(0.05, chief.frame.midY / app.frame.height))
        closes("A drag that starts on a card did not close the Sidebar")

        app.openSidebar()
        step("dim-short")
        slowDrag(at(0.97, 0.5), at(0.8, 0.5))
        restsOpen("A short drag on the dim did not return open")
        step("dim-long")
        slowDrag(at(0.97, 0.5), at(0.25, 0.5))
        closes("A long drag on the dim did not close the Sidebar")

        app.openSidebar()
        step("panel-flick")
        flick(at(0.6, 0.6), at(0.45, 0.6))
        closes("A short fast flick on the panel did not close the Sidebar")

        // A rotation while open keeps the panel fully open at the new width.
        app.openSidebar()
        step("rotate")
        XCUIDevice.shared.orientation = .landscapeLeft
        defer { XCUIDevice.shared.orientation = .portrait }
        XCTAssertTrue(app.buttons["sidebar-settings"].waitForExistence(timeout: 5))
        usleep(1_000_000)
        XCTAssertGreaterThanOrEqual(chief.frame.minX, 0, "After a rotation the panel is cut off at the leading edge")
        XCUIDevice.shared.orientation = .portrait
        restsOpen("After rotating back the panel is out of place")

        // A card still opens on a tap.
        step("card-tap")
        chief.tap()
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 8), "A tap on a card did not open it")
        XCTAssertTrue(chief.waitForNonExistence(timeout: 5))
        step("done")
    }

    /// The same destinations on a bad connection. Each page is read once online, then the
    /// stub drops every connection: each page shows its saved copy with no cover, banner or
    /// status note, and the app moves between them as before. Back online, Review reads again.
    /// This fixture has no Review cards; OfflineOperationTests acts on cards offline.
    @MainActor func testDestinationsOnABadConnection() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        func proof(_ operation: String) async throws {
            let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + operation)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200, operation)
        }
        try await proof("workspace-fixture")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        let wren = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'sidebar-manager-' AND label CONTAINS %@", "Wren release")).firstMatch
        let chiefScreen = app.descendants(matching: .any)["chief-screen"]
        let managerScreen = app.descendants(matching: .any)["orchestrator-screen"]
        let headline = app.staticTexts["review.summary.headline"]
        func noCover(_ screen: String, except allowed: Set<String> = []) {
            for text in ["Offline", "offline", "Delivery uncertain", "Slow connection", "Reconnecting", "Not connected"] where !allowed.contains(text) {
                XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", text)).firstMatch.exists,
                               "\(screen) shows \"\(text)\"")
            }
        }
        // Online: read each page once, so the phone has its saved copy.
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 12))
        XCTAssertTrue(headline.waitForExistence(timeout: 12))
        app.openDestination("sidebar-chief")
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 8))
        app.buttons["conversation-line"].tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 8))
        app.backButton.tap()
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 8))
        app.openSidebar()
        XCTAssertTrue(wren.waitForExistence(timeout: 8)); wren.tap()
        XCTAssertTrue(managerScreen.waitForExistence(timeout: 8))
        app.openDestination("sidebar-settings")
        XCTAssertTrue(app.buttons["All goals"].waitForExistence(timeout: 8))

        // Bad connection: every request now drops.
        try await proof("offline")
        app.openDestination("sidebar-review")
        XCTAssertTrue(headline.waitForExistence(timeout: 8), "Review shows its saved summary offline")
        noCover("Review"); capture(app, "offline-01-review-summary")
        app.openSidebar()
        XCTAssertTrue(app.buttons["sidebar-chief"].waitForExistence(timeout: 8))
        XCTAssertTrue(wren.exists, "The Sidebar keeps its cards offline")
        noCover("Sidebar"); capture(app, "offline-03-sidebar")
        app.buttons["sidebar-chief"].tap()
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 8), "Chief shows its saved copy offline")
        noCover("Chief"); capture(app, "offline-04-chief")
        app.buttons["conversation-line"].tap()
        XCTAssertTrue(app.buttons["conversation-actions"].waitForExistence(timeout: 8), "The conversation opens offline")
        noCover("Conversation"); capture(app, "offline-04b-chief-conversation")
        app.backButton.tap()
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 8))
        app.openSidebar()
        XCTAssertTrue(wren.waitForExistence(timeout: 8)); wren.tap()
        XCTAssertTrue(managerScreen.waitForExistence(timeout: 8), "The Manager page shows its saved copy offline")
        noCover("Manager"); capture(app, "offline-05-manager")
        app.openDestination("sidebar-settings")
        XCTAssertTrue(app.buttons["All goals"].waitForExistence(timeout: 8))
        // The Panels server row reports the connection in place, as the design asks.
        let server = app.descendants(matching: .any).matching(identifier: "settings.deployment").firstMatch
        XCTAssertTrue(server.waitForExistence(timeout: 5))
        noCover("Settings", except: ["Reconnecting"]); capture(app, "offline-06-settings")

        // Back online: Review reads again and its card is still there to act on.
        try await proof("online")
        app.openDestination("sidebar-review")
        XCTAssertTrue(headline.waitForExistence(timeout: 12))
        noCover("Review after the return"); capture(app, "offline-07-review-back-online")
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }
}
