import XCTest

/// One-off walk for the dock under an opened artifact and the one Markdown display. Run it
/// alone with `PANELS_ARTIFACT_DOCK_SMOKE=1`. Host tests cannot prove which dock SwiftUI
/// mounts under a pushed viewer, that a decision there pops every viewer and moves the
/// queue on, or how block Markdown lays out and scrolls in a real conversation.
final class ArtifactDockTests: XCTestCase {
    @MainActor func testDocksFollowWhereTheArtifactWasOpened() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        _ = try await URLSession.shared.data(from: URL(string: base + "/proof/review-owner/ticket-split")!)
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
        }
        let viewer = app.buttons["Artifact actions"]
        let approve = app.buttons["review.approve"]
        let skip = app.buttons["review.skip"]
        let note = app.textFields["review.revision"].firstMatch
        let message = app.staticTexts["First unread message"]

        let back = app.buttons["review.return"]
        XCTAssertTrue(back.waitForExistence(timeout: 12)); back.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 10))

        // The proposal card: the artifact docks the card's note and decision row, on the
        // card's own draft, and shows its table, code and chart.
        let dockLink = app.links["dock evidence"]
        XCTAssertTrue(dockLink.waitForExistence(timeout: 8)); dockLink.tap()
        XCTAssertTrue(app.staticTexts["Dock artifact proof"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Simulator walk"].exists, "The artifact draws its table")
        XCTAssertTrue(app.staticTexts["Merged PRs per week"].exists, "The artifact draws its chart")
        XCTAssertTrue(approve.waitForExistence(timeout: 5)); XCTAssertTrue(skip.exists)
        XCTAssertFalse(app.textFields["conversation-input"].exists, "Only the card's dock is live")
        note.tap(); note.typeText("Note typed under the artifact")
        capture("proposal-artifact-dock")
        app.backButton.tap()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 8))
        XCTAssertEqual(note.value as? String, "Note typed under the artifact", "The viewer's note is the card's draft")

        // Two viewers deep, Skip pops both and the queue moves to the message card.
        dockLink.tap()
        let nested = app.links["Nested dock artifact"]
        XCTAssertTrue(nested.waitForExistence(timeout: 10)); nested.tap()
        XCTAssertTrue(app.staticTexts["Nested dock proof"].waitForExistence(timeout: 10))
        XCTAssertTrue(approve.waitForExistence(timeout: 5), "The top viewer mounts the dock")
        capture("nested-artifact-dock")
        skip.tap()
        XCTAssertTrue(message.waitForExistence(timeout: 10))
        XCTAssertFalse(viewer.exists, "Skip closes every viewer")

        // The message card: the artifact docks the reply and Mark read; Mark read there
        // closes the viewer and the queue moves on.
        let html = app.links.matching(NSPredicate(format: "label CONTAINS %@", "proof.html")).firstMatch
        XCTAssertTrue(html.waitForExistence(timeout: 5)); html.tap()
        XCTAssertTrue(viewer.waitForExistence(timeout: 8))
        XCTAssertTrue(app.textFields["conversation-input"].firstMatch.waitForExistence(timeout: 5))
        let markRead = app.buttons["review.markRead"]
        XCTAssertTrue(markRead.exists)
        capture("message-artifact-dock")
        markRead.tap()
        XCTAssertTrue(app.staticTexts["Historical message"].waitForExistence(timeout: 10))
        XCTAssertFalse(viewer.exists)
        skip.tap()
        XCTAssertTrue(back.waitForExistence(timeout: 10)); back.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 10))

        // The Ticket page: its files wait behind the collapsed Artifacts row, its proposal
        // note takes attachments, and an artifact opened from the Ticket's conversation docks
        // that conversation's composer. Opening the conversation reads the message, so last.
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(app.buttons["review.attach"].waitForExistence(timeout: 10), "The Ticket page note takes attachments")
        let artifactsRow = app.buttons["Artifacts"].firstMatch
        XCTAssertTrue(artifactsRow.waitForExistence(timeout: 8))
        XCTAssertEqual(artifactsRow.value as? String, "2 files, collapsed")
        capture("ticket-note-attach")
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 8)); line.tap()
        let evidence = app.links["Conversation evidence"].firstMatch
        XCTAssertTrue(evidence.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Merged PRs per week"].firstMatch.exists, "A conversation row draws its chart")
        capture("conversation-block-markdown")
        evidence.tap()
        XCTAssertTrue(viewer.waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts["Dock artifact proof"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.textFields["conversation-input"].firstMatch.waitForExistence(timeout: 5), "The conversation's composer docks below")
        XCTAssertFalse(approve.exists)
        capture("conversation-artifact-composer-dock")
        app.backButton.tap()
        XCTAssertTrue(evidence.waitForExistence(timeout: 8))
        XCTAssertTrue(app.textFields["conversation-input"].firstMatch.exists)
        app.backButton.tap(); app.backButton.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 10))

        // The skipped proposal again: Approve under the artifact saves it and returns to Review.
        XCTAssertTrue(dockLink.waitForExistence(timeout: 10)); dockLink.tap()
        XCTAssertTrue(app.staticTexts["Dock artifact proof"].waitForExistence(timeout: 10))
        XCTAssertTrue(approve.waitForExistence(timeout: 5)); approve.tap()
        XCTAssertTrue(viewer.waitForNonExistence(timeout: 10), "A decision closes the viewer")
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 5))
        capture("after-approve-under-artifact")
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        let writes = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [Any])
        // Mark read under the artifact reads the card's run once; the full conversation
        // visit later reads its own rows.
        XCTAssertEqual(writes.compactMap { ($0 as? [String: Any])?["through_sequence"] as? Int }.first, 3, "Mark read lands once")
        XCTAssertEqual(writes.compactMap { $0 as? String }, ["approve"], "Approve lands once")
    }

    /// The three docks on a bad connection. Each artifact is opened once online, then the
    /// stub drops every connection: the proposal's, the message's and the conversation's
    /// viewers open again with their docks, with no cover, banner or status note. The viewer
    /// keeps no saved copy of an artifact, so offline it shows its quiet placeholder until
    /// the next connection reads the file.
    @MainActor func testDocksOnABadConnection() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        func proof(_ name: String) async throws {
            let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + name)!)
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200, name)
        }
        try await proof("review-owner/ticket-split")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        func capture(_ name: String) {
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
        }
        func noCover(_ screen: String) {
            for text in ["Offline", "offline", "Delivery uncertain", "Slow connection", "Reconnecting", "Not connected"] {
                XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", text)).firstMatch.exists,
                               "\(screen) shows \"\(text)\"")
            }
        }
        let viewer = app.buttons["Artifact actions"]
        let approve = app.buttons["review.approve"]
        let skip = app.buttons["review.skip"]
        let message = app.staticTexts["First unread message"]
        let dockLink = app.links["dock evidence"]
        let html = app.links.matching(NSPredicate(format: "label CONTAINS %@", "proof.html")).firstMatch
        let line = app.buttons["conversation-line"]
        let evidence = app.links["Conversation evidence"].firstMatch
        let placeholder = app.staticTexts["Opening artifact…"]
        func openConversationArtifact(online: Bool) {
            app.buttons["review.openTicket"].tap()
            XCTAssertTrue(line.waitForExistence(timeout: 10)); line.tap()
            XCTAssertTrue(evidence.waitForExistence(timeout: 10)); evidence.tap()
            XCTAssertTrue(viewer.waitForExistence(timeout: 8))
            if online { XCTAssertTrue(app.staticTexts["Dock artifact proof"].waitForExistence(timeout: 8)) }
            else { XCTAssertTrue(placeholder.waitForExistence(timeout: 8), "No saved copy: the quiet placeholder") }
            XCTAssertTrue(app.textFields["conversation-input"].firstMatch.waitForExistence(timeout: 5), "The conversation's composer docks below")
        }
        func closeConversationArtifact() {
            app.backButton.tap(); XCTAssertTrue(evidence.waitForExistence(timeout: 8))
            app.backButton.tap(); app.backButton.tap()
            XCTAssertTrue(approve.waitForExistence(timeout: 10))
        }

        // Online: open the proposal's and the message's artifacts once. Opening the
        // conversation reads the message, so the conversation's artifact comes last.
        app.openReviewCards()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 10)); dockLink.tap()
        XCTAssertTrue(app.staticTexts["Dock artifact proof"].waitForExistence(timeout: 10))
        app.backButton.tap()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 8)); skip.tap()
        XCTAssertTrue(message.waitForExistence(timeout: 10))
        XCTAssertTrue(html.waitForExistence(timeout: 5)); html.tap()
        XCTAssertTrue(viewer.waitForExistence(timeout: 8))
        app.backButton.tap()
        XCTAssertTrue(message.waitForExistence(timeout: 8))

        // Bad connection: both again, each with its dock.
        try await proof("offline")
        html.tap()
        XCTAssertTrue(viewer.waitForExistence(timeout: 8), "The message's viewer opens offline")
        XCTAssertTrue(app.textFields["conversation-input"].firstMatch.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["review.markRead"].exists)
        noCover("Message artifact"); capture("offline-message-artifact-dock")
        app.backButton.tap()
        XCTAssertTrue(message.waitForExistence(timeout: 8)); skip.tap()
        XCTAssertTrue(app.staticTexts["Historical message"].waitForExistence(timeout: 10)); skip.tap()
        app.openReviewCards()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 10)); dockLink.tap()
        XCTAssertTrue(viewer.waitForExistence(timeout: 8), "The proposal's viewer opens offline")
        XCTAssertTrue(placeholder.waitForExistence(timeout: 8), "No saved copy: the quiet placeholder")
        XCTAssertTrue(approve.waitForExistence(timeout: 5)); XCTAssertTrue(skip.exists)
        noCover("Proposal artifact"); capture("offline-proposal-artifact-dock")
        app.backButton.tap()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 8))

        // The conversation's artifact: once online, then again offline.
        try await proof("online")
        openConversationArtifact(online: true); closeConversationArtifact()
        try await proof("offline")
        openConversationArtifact(online: false)
        noCover("Conversation artifact"); capture("offline-conversation-artifact-composer-dock")
        closeConversationArtifact()
        try await proof("online")
    }

    /// A long conversation whose rows carry tables, code and charts still scrolls freely.
    @MainActor func testLongBlockMarkdownConversationScrolls() throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        // Review's cards belong to the Ticket, so the bar opens the Ticket and its conversation.
        _ = try Data(contentsOf: XCTUnwrap(URL(string: base + "/proof/review-owner/ticket-split")))
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        let back = app.buttons["review.return"]
        XCTAssertTrue(back.waitForExistence(timeout: 12)); back.tap()
        let openTicket = app.buttons["review.openTicket"]
        XCTAssertTrue(openTicket.waitForExistence(timeout: 10)); openTicket.tap()
        let line = app.buttons["conversation-line"]
        XCTAssertTrue(line.waitForExistence(timeout: 10)); line.tap()
        let thread = app.scrollViews["conversation-thread"]
        XCTAssertTrue(app.staticTexts["Rich update 60"].waitForExistence(timeout: 10))
        let options = XCTMeasureOptions(); options.iterationCount = 3
        measure(metrics: [XCTOSSignpostMetric.scrollingAndDecelerationMetric], options: options) {
            thread.swipeDown(velocity: .fast)
            thread.swipeDown(velocity: .fast)
            thread.swipeUp(velocity: .fast)
            thread.swipeUp(velocity: .fast)
        }
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "long-conversation-after-scroll"; attachment.lifetime = .keepAlways; add(attachment)
    }
}
