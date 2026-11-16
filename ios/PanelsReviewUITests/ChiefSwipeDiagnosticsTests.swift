import XCTest

/// One-off diagnostic proof. The existing loopback fixture and real UIKit host
/// prove touch observation, Settings wiring and system sharing; model tests cannot.
/// This method stays outside the permanent release-screen suite.
final class ChiefSwipeDiagnosticsTests: XCTestCase {
    @MainActor func testLocalCaptureSwitchShareAndClear() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }

        func element(_ id: String) -> XCUIElement {
            app.descendants(matching: .any).matching(identifier: id).firstMatch
        }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name
            attachment.lifetime = .keepAlways
            add(attachment)
        }
        func settings() {
            let more = app.tabBars.buttons["More"]
            XCTAssertTrue(more.waitForExistence(timeout: 10))
            more.tap()
            let settings = app.buttons["Settings"]
            XCTAssertTrue(settings.waitForExistence(timeout: 5))
            settings.tap()
            XCTAssertTrue(app.switches["chief-swipe-enabled"].waitForExistence(timeout: 5))
        }
        func leaveSettings() {
            let back = app.navigationBars.buttons.element(boundBy: 0)
            XCTAssertTrue(back.waitForExistence(timeout: 5))
            back.tap()
            XCTAssertTrue(app.tabBars.buttons["Workspace"].waitForExistence(timeout: 5))
        }
        func count() throws -> Int {
            let count = element("chief-swipe-count")
            XCTAssertTrue(count.waitForExistence(timeout: 5))
            return try XCTUnwrap(Int(try XCTUnwrap(count.value as? String)))
        }
        func exerciseChief(_ label: String) {
            app.tabBars.buttons["Workspace"].tap()
            let chief = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Chief of Staff")).firstMatch
            XCTAssertTrue(chief.waitForExistence(timeout: 10))
            chief.tap()
            let table = element("goal-table-scroll-proof")
            XCTAssertTrue(table.waitForExistence(timeout: 10))
            let caption = app.staticTexts["Table scroll proof"]
            XCTAssertTrue(caption.waitForExistence(timeout: 5))
            let header = element("table-column-scroll-proof-1")
            let cell = element("table-cell-scroll-proof-0-1")
            let headerX = header.frame.minX
            let cellX = cell.frame.minX
            let pageX = caption.frame.minX
            table.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.6)).press(
                forDuration: 0.1,
                thenDragTo: table.coordinate(withNormalizedOffset: CGVector(dx: 0.6, dy: 0.6)),
                withVelocity: .slow,
                thenHoldForDuration: 0.4
            )
            let delta = header.frame.minX - headerX
            XCTAssertLessThan(delta, -20, "The diagnostic observer must preserve horizontal table scrolling")
            XCTAssertEqual(delta, cell.frame.minX - cellX, accuracy: 2)
            XCTAssertEqual(caption.frame.minX, pageX, accuracy: 1)
            capture("swipe-logger-\(label)-horizontal-pan")

            // A small edge attempt cancels. It must still yield diagnostic evidence
            // when enabled, even though it never completes a pop.
            let y = table.frame.midY / app.frame.height
            let edge = app.coordinate(withNormalizedOffset: CGVector(dx: 0.005, dy: y))
            edge.press(
                forDuration: 0.1,
                thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.12, dy: y)),
                withVelocity: .slow,
                thenHoldForDuration: 0.5
            )
            XCTAssertTrue(table.waitForExistence(timeout: 5), "The short edge swipe must leave Chief open")
            capture("swipe-logger-\(label)-cancelled-edge")

            // Native vertical drags and the ordinary back button remain usable.
            let page = app.scrollViews["chief-screen"].firstMatch
            page.swipeUp(velocity: .slow)
            page.swipeDown(velocity: .slow)
            let back = app.buttons["detail-back"]
            if !back.isHittable { page.swipeDown(velocity: .fast) }
            XCTAssertTrue(back.isHittable)
            back.tap()
            let workspace = app.tabBars.buttons["Workspace"]
            XCTAssertTrue(workspace.waitForExistence(timeout: 5))
            XCTAssertTrue(workspace.isHittable)

            // A completed pop exercises transition completion and observer detachment.
            XCTAssertTrue(chief.waitForExistence(timeout: 5))
            chief.tap()
            XCTAssertTrue(table.waitForExistence(timeout: 5))
            let fullY = table.frame.midY / app.frame.height
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.005, dy: fullY)).press(
                forDuration: 0.1,
                thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: fullY)),
                withVelocity: .slow,
                thenHoldForDuration: 0.1
            )
            XCTAssertTrue(workspace.waitForExistence(timeout: 5))
            XCTAssertTrue(workspace.isHittable, "The diagnostic observer must preserve the full back swipe")
            XCTAssertTrue(table.waitForNonExistence(timeout: 5))
        }

        settings()
        let enabled = app.switches["chief-swipe-enabled"]
        func toggleCapture() {
            // XCUI exposes the whole Toggle row as a switch. Tap its native
            // trailing control rather than the center of the label region.
            enabled.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()
        }
        XCTAssertEqual(enabled.value as? String, "0", "The logger must default off on a fresh install")
        app.buttons["chief-swipe-clear"].tap()
        XCTAssertEqual(try count(), 0)
        capture("swipe-logger-settings-default-off")
        leaveSettings()
        exerciseChief("off")
        settings()
        XCTAssertEqual(try count(), 0, "Disabled capture must write nothing")

        toggleCapture()
        XCTAssertEqual(enabled.value as? String, "1")
        leaveSettings()
        exerciseChief("on")
        settings()
        XCTAssertGreaterThan(try count(), 0, "A horizontal Chief pan must produce local records")
        capture("swipe-logger-settings-recorded")
        app.buttons["chief-swipe-clear"].tap()
        XCTAssertEqual(try count(), 0, "Clear must remove the saved diagnostic records")
        toggleCapture()
        XCTAssertEqual(enabled.value as? String, "0")
        capture("swipe-logger-settings-cleared-off")
        leaveSettings()
        exerciseChief("disabled-after-capture")
        settings()
        XCTAssertEqual(try count(), 0, "Turning the switch off must stop later writes")

        // Leave a final exported snapshot for the owning runner to inspect in the
        // app container before it erases the lane. No share destination is selected.
        toggleCapture()
        leaveSettings()
        exerciseChief("export")
        settings()
        XCTAssertGreaterThan(try count(), 0)
        toggleCapture()
        XCTAssertEqual(enabled.value as? String, "0")
        app.buttons["chief-swipe-share"].tap()
        let shareSheet = element("ShareSheet.RemoteContainerView")
        XCTAssertTrue(shareSheet.waitForExistence(timeout: 8), "Share must present the native activity sheet")
        let preview = shareSheet.descendants(matching: .any).matching(identifier: "LP.CaptionBar.TopCaption").firstMatch
        XCTAssertEqual(preview.label, "ChiefSwipeLog", "The activity sheet must preview the exported diagnostic file")
        let copy = shareSheet.descendants(matching: .any).matching(NSPredicate(format: "label == %@", "Copy")).firstMatch
        XCTAssertTrue(copy.exists, "The native activity sheet must offer Copy for the exported file")
        capture("swipe-logger-native-share-sheet")
        let hierarchy = XCTAttachment(string: app.debugDescription)
        hierarchy.name = "swipe-logger-share-sheet-hierarchy"
        hierarchy.lifetime = .keepAlways
        add(hierarchy)

        let (data, response) = try await URLSession.shared.data(from: try XCTUnwrap(URL(string: base + "/proof/writes")))
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        let writes = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [Any])
        for raw in writes {
            let entry = try XCTUnwrap(raw as? [String: Any], "Only automatic owner-read writes are allowed")
            XCTAssertEqual(Set(entry.keys), Set(["through_sequence"]), "No message, decision or other mutation belongs to this proof")
            let through = try XCTUnwrap(entry["through_sequence"] as? Int)
            XCTAssertGreaterThanOrEqual(through, 0)
        }
    }
}
