import XCTest
import Vision

/// Reuses the conversation stub and UI host: native lazy layout, keyboard insets,
/// and animation landing cannot be proved by the host-only scroll policy tests.
final class ConversationLatestTests: XCTestCase {
    @MainActor func testLatestAgainstStreamingStub() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_CONVERSATION_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let baseline = ProcessInfo.processInfo.environment["PANELS_LATEST_BASELINE"] == "1"
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-smoke", "--conversation-stub", base]
        app.launch()
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 8))
        line.tap()
        let composer = app.descendants(matching: .any).matching(identifier: "conversation-composer-region").firstMatch
        app.buttons["conversation-actions"].tap()
        app.buttons["Full"].tap()
        try await Task.sleep(for: .seconds(2))
        let thread = app.scrollViews.firstMatch
        let latest = app.buttons["Latest"]
        try await proof(base, "start/latest-distance")
        // Touch-down should not offer a jump when the newest message is in sight.
        thread.coordinate(withNormalizedOffset: CGVector(dx: 0.97, dy: 0.7)).press(forDuration: 0.8)
        let start = thread.coordinate(withNormalizedOffset: CGVector(dx: 0.75, dy: 0.4))
        start.press(forDuration: 0.1, thenDragTo: start.withOffset(CGVector(dx: 0, dy: 24)), withVelocity: .slow, thenHoldForDuration: 0.2)
        try await Task.sleep(for: .seconds(1))
        if !baseline { XCTAssertFalse(latest.exists, "A tiny scroll should not show Latest") }
        thread.swipeDown(velocity: .slow)
        XCTAssertTrue(latest.waitForExistence(timeout: 3))
        XCTAssertLessThan(latest.frame.maxY, composer.frame.minY, "Latest overlaps the composer")
        latest.tap()
        try await Task.sleep(for: .seconds(2))
        if !baseline { XCTAssertFalse(latest.exists, "Latest did not land") }
        try await proof(base, "stop")

        try await proof(base, "start/latest-stream")
        thread.swipeDown(velocity: .slow)
        XCTAssertTrue(latest.waitForExistence(timeout: 3))
        let anchor = try XCTUnwrap(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Review '")).allElementsBoundByIndex.first(where: { $0.isHittable }))
        let before = anchor.frame.minY
        try await proof(base, "stream")
        try await Task.sleep(for: .seconds(3))
        XCTAssertEqual(anchor.frame.minY, before, accuracy: 3, "Streaming moved the reader")
        if !baseline { XCTAssertEqual(latest.value as? String, "New output") }
        latest.tap()
        try await Task.sleep(for: .seconds(3))
        if !baseline { XCTAssertFalse(latest.exists, "Streaming lost the return to Latest") }
        try await proof(base, "finish")
        try await Task.sleep(for: .seconds(2))
        if !baseline {
            XCTAssertFalse(latest.exists, "Completion lost bottom following")
            try assertLastLineVisible(app, above: composer)
        }
        try await proof(base, "stop")

        try await proof(base, "start/latest-keyboard")
        thread.swipeDown(velocity: .slow)
        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        input.tap(); input.typeText("Keep my draft")
        XCTAssertTrue(app.keyboards.firstMatch.exists)
        XCTAssertTrue(latest.waitForExistence(timeout: 3))
        XCTAssertLessThan(latest.frame.maxY, composer.frame.minY)
        latest.tap()
        try await Task.sleep(for: .seconds(2))
        XCTAssertTrue(app.keyboards.firstMatch.exists, "Latest dismissed the keyboard")
        XCTAssertEqual(input.value as? String, "Keep my draft")
        if !baseline { XCTAssertFalse(latest.exists, "Latest failed with the keyboard open") }
        if !baseline { try assertLastLineVisible(app, above: composer) }
        let capture = XCTAttachment(screenshot: app.screenshot())
        capture.name = "latest-keyboard-landing"; capture.lifetime = .keepAlways; add(capture)
        try await proof(base, "stop")
    }

    @MainActor private func assertLastLineVisible(_ app: XCUIApplication, above composer: XCUIElement) throws {
        // SwiftUI's single multiline accessibility frame can extend beyond its
        // rendered text. Measure the visible final glyphs in the screenshot.
        let capture = app.screenshot()
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        try VNImageRequestHandler(cgImage: XCTUnwrap(capture.image.cgImage)).perform([request])
        let last = try XCTUnwrap(request.results?.first {
            $0.topCandidates(1).first?.string.contains("End of streamed reply.") == true
        }, "The final line is not visible")
        let bottom = app.frame.height * (1 - last.boundingBox.minY)
        XCTAssertLessThan(bottom, composer.frame.minY, "Latest text is below the viewport")
        XCTAssertGreaterThan(bottom, composer.frame.minY - 60, "Latest landed with excess empty space")
    }

    @MainActor private func proof(_ base: String, _ operation: String) async throws {
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + operation)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }
}
