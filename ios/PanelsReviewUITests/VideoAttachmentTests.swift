import XCTest

/// One-off review: the real Photos picker and file-backed outbox across an offline relaunch.
/// Reuses the conversation loopback host. Camera capture needs a physical device.
final class VideoAttachmentTests: XCTestCase {
    @MainActor func testOfflineVideoSurvivesRelaunchAndLandsOnce() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_CONVERSATION_STUB_URL"])
        func proof(_ path: String) async throws -> Data {
            let (data, _) = try await URLSession.shared.data(from: URL(string: base + path)!)
            return data
        }
        _ = try await proof("/proof/reset")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-smoke", "--conversation-stub", base]
        app.launch()
        func openConversation() {
            let line = app.buttons["conversation-line"]
            XCTAssertTrue(line.waitForExistence(timeout: 10)); line.tap()
            XCTAssertTrue(app.buttons["Add attachment or command"].waitForExistence(timeout: 10))
        }
        func capture(_ name: String) {
            let shot = XCTAttachment(screenshot: app.screenshot())
            shot.name = name; shot.lifetime = .keepAlways; add(shot)
        }
        openConversation()
        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        input.tap(); input.typeText("Offline video proof")
        _ = try await proof("/proof/offline")
        app.buttons["Add attachment or command"].tap()
        app.buttons["Photo Library"].tap()
        capture("video-photo-library")
        // The owned Simulator library contains the imported short video fixture.
        let video = app.images.matching(NSPredicate(format: "label CONTAINS[c] 'Video'")).firstMatch
        if video.waitForExistence(timeout: 8) { video.tap() }
        else {
            let cell = app.cells.firstMatch
            XCTAssertTrue(cell.waitForExistence(timeout: 5), app.debugDescription)
            cell.tap()
        }
        let tile = app.descendants(matching: .any).matching(identifier: "conversation-attachment").firstMatch
        XCTAssertTrue(tile.waitForExistence(timeout: 20), app.debugDescription)
        let send = app.buttons["Send message"]
        XCTAssertTrue(send.waitForExistence(timeout: 5))
        let ready = NSPredicate(format: "enabled == true")
        await fulfillment(of: [expectation(for: ready, evaluatedWith: send)], timeout: 20)
        capture("video-saved-offline")
        send.tap()
        await fulfillment(of: [expectation(for: NSPredicate(format: "value == %@", ""), evaluatedWith: input)], timeout: 10)
        app.terminate()
        app.launch()
        openConversation()
        capture("video-restored-after-relaunch")
        _ = try await proof("/proof/online")
        var sends: [[String: Any]] = []
        for _ in 0..<30 {
            let value = try JSONSerialization.jsonObject(with: await proof("/proof/videos")) as! [String: Any]
            sends = value["sends"] as? [[String: Any]] ?? []
            if sends.count == 1 { break }
            try await Task.sleep(for: .seconds(1))
        }
        XCTAssertEqual(sends.count, 1)
        let content = try XCTUnwrap(sends.first?["content"] as? [[String: Any]])
        XCTAssertEqual(content.filter { $0["piece"] as? String == "attachment_upload" }.count, 1)
        XCTAssertFalse(content.contains { $0["data"] != nil })
        try await Task.sleep(for: .seconds(4))
        let final = try JSONSerialization.jsonObject(with: await proof("/proof/videos")) as! [String: Any]
        XCTAssertEqual(final["own_rows"] as? Int, 1)
        capture("video-landed-once")
    }
}
