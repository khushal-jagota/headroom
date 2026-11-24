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
        XCTAssertEqual(artifactsRow.value as? String, "3 files, collapsed")
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

    /// The three docks on a bad connection, and the saved copies under them. Each artifact
    /// is opened once online, where the HTML page loads from the server and its own write
    /// lands. Then the stub drops every connection: the proposal's, the
    /// message's and the conversation's viewers open again from their saved copies, with
    /// their docks and no cover, banner or status note. The HTML page keeps the screenshot
    /// it loads from its own Ticket. After a relaunch, still offline, the Review card and the
    /// Ticket page's saved file list open the same copies at once. Back online, a changed
    /// file replaces its copy.
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
        let markdown = app.staticTexts["Dock artifact proof"]
        let page = app.webViews.staticTexts["Inline artifact proof"]
        let screenshot = app.webViews.staticTexts["Screenshot loaded"]
        let postLanded = app.webViews.staticTexts["Page post landed"]
        // Built at each use: a first match stays bound to the process it resolved in.
        var ticketFile: XCUIElement {
            app.otherElements.matching(NSPredicate(format: "identifier == 'ticket-artifacts' AND label == 'dock-proof.md'")).firstMatch
        }
        /// A saved copy draws at once: well inside the viewer's 200 ms placeholder delay
        /// plus a frame, and before any read could time out.
        func drawsFromCopy(_ content: XCUIElement, _ screen: String) {
            XCTAssertTrue(content.waitForExistence(timeout: 3), "\(screen) draws its saved copy")
            XCTAssertFalse(placeholder.exists, "\(screen) shows no placeholder")
        }
        func openTicketFiles() {
            app.buttons["review.openTicket"].tap()
            let artifactsRow = app.buttons["Artifacts"].firstMatch
            XCTAssertTrue(artifactsRow.waitForExistence(timeout: 10)); artifactsRow.tap()
            XCTAssertTrue(ticketFile.waitForExistence(timeout: 8), "The Ticket page lists its files")
        }
        func openConversationArtifact(online: Bool) {
            app.buttons["review.openTicket"].tap()
            XCTAssertTrue(line.waitForExistence(timeout: 10)); line.tap()
            XCTAssertTrue(evidence.waitForExistence(timeout: 10)); evidence.tap()
            XCTAssertTrue(viewer.waitForExistence(timeout: 8))
            if online { XCTAssertTrue(markdown.waitForExistence(timeout: 8)) }
            else { drawsFromCopy(markdown, "The conversation's artifact") }
            XCTAssertTrue(app.textFields["conversation-input"].firstMatch.waitForExistence(timeout: 5), "The conversation's composer docks below")
        }
        func closeConversationArtifact() {
            app.backButton.tap(); XCTAssertTrue(evidence.waitForExistence(timeout: 8))
            app.backButton.tap(); app.backButton.tap()
            XCTAssertTrue(approve.waitForExistence(timeout: 10))
        }

        // Online: open the proposal's artifact, the Ticket page's file list and the message's
        // HTML page once. Opening the conversation reads the message, so its artifact comes last.
        app.openReviewCards()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 10)); dockLink.tap()
        XCTAssertTrue(markdown.waitForExistence(timeout: 10))
        app.backButton.tap()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 8))
        openTicketFiles()
        app.backButton.tap()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 8)); skip.tap()
        XCTAssertTrue(message.waitForExistence(timeout: 10))
        XCTAssertTrue(html.waitForExistence(timeout: 5)); html.tap()
        XCTAssertTrue(viewer.waitForExistence(timeout: 8))
        XCTAssertTrue(page.waitForExistence(timeout: 10)); XCTAssertTrue(screenshot.waitForExistence(timeout: 10))
        // Online, the page loads from the server's own address, so its write is accepted.
        XCTAssertTrue(postLanded.waitForExistence(timeout: 10), "The page's POST lands online")
        let (posts, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/page-posts")!)
        let origins = try XCTUnwrap(JSONSerialization.jsonObject(with: posts) as? [Any])
        XCTAssertEqual(origins.count, 1)
        XCTAssertFalse(origins.contains { ($0 as? String)?.hasPrefix("panels-artifact") == true }, "The write carries the server's origin")
        app.backButton.tap()
        XCTAssertTrue(message.waitForExistence(timeout: 8))

        // Bad connection: both again from their copies, each with its dock.
        try await proof("offline")
        html.tap()
        XCTAssertTrue(viewer.waitForExistence(timeout: 8), "The message's viewer opens offline")
        // The app has not yet seen the drop, so the page tries the server first and falls
        // back to its copy when that load fails. After the relaunch below it opens at once.
        XCTAssertTrue(page.waitForExistence(timeout: 10), "The HTML page falls back to its saved copy")
        XCTAssertTrue(screenshot.waitForExistence(timeout: 3), "The page's screenshot comes from its copy")
        XCTAssertFalse(postLanded.exists, "The copy forwards nothing, so the page's write does not land")
        XCTAssertTrue(app.textFields["conversation-input"].firstMatch.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["review.markRead"].exists)
        noCover("Message artifact"); capture("offline-message-artifact-dock")
        // Back online, the same viewer returns to the server's page though the copy is unchanged.
        try await proof("online")
        XCTAssertTrue(postLanded.waitForExistence(timeout: 20), "The server's page replaces the copy after the reconnect")
        try await proof("offline")
        app.backButton.tap()
        XCTAssertTrue(message.waitForExistence(timeout: 8)); skip.tap()
        XCTAssertTrue(app.staticTexts["Historical message"].waitForExistence(timeout: 10)); skip.tap()
        app.openReviewCards()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 10)); dockLink.tap()
        XCTAssertTrue(viewer.waitForExistence(timeout: 8), "The proposal's viewer opens offline")
        drawsFromCopy(markdown, "The proposal's artifact")
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

        // Relaunch, still offline: the card and the Ticket page's saved list open the copy.
        app.terminate(); app.launch()
        app.openReviewCards()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 10)); dockLink.tap()
        drawsFromCopy(markdown, "The proposal's artifact after a relaunch")
        noCover("Proposal artifact after a relaunch"); capture("relaunch-offline-proposal-artifact")
        app.backButton.tap()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 8))
        openTicketFiles()
        capture("relaunch-offline-ticket-files")
        ticketFile.tap()
        drawsFromCopy(markdown, "The Ticket page's artifact after a relaunch")
        noCover("Ticket artifact after a relaunch")
        app.backButton.tap(); XCTAssertTrue(ticketFile.waitForExistence(timeout: 8))
        app.backButton.tap(); XCTAssertTrue(dockLink.waitForExistence(timeout: 8))

        // Back online, the server's file changed: the viewer swaps it in with no sign.
        try await proof("artifact-revision")
        try await proof("online")
        dockLink.tap()
        drawsFromCopy(markdown, "The proposal's artifact back online")
        XCTAssertTrue(app.staticTexts["Revised dock evidence"].waitForExistence(timeout: 10), "The changed file replaces the copy")
        noCover("Revised proposal artifact"); capture("online-revised-artifact")
        app.backButton.tap()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 8))

        // A cold launch online: the app is still connecting, and the page opens from the
        // server, not from its copy. A link that fails leaves the server's page in place.
        app.terminate(); app.launch()
        // The walk read the message, so the page opens from the Ticket page's file list.
        app.openReviewCards()
        XCTAssertTrue(dockLink.waitForExistence(timeout: 10))
        openTicketFiles()
        app.otherElements.matching(NSPredicate(format: "identifier == 'ticket-artifacts' AND label == 'proof.html'")).firstMatch.tap()
        XCTAssertTrue(postLanded.waitForExistence(timeout: 10), "After a cold launch online the page loads from the server")
        let broken = app.webViews.links["Broken link"]
        XCTAssertTrue(broken.waitForExistence(timeout: 5)); broken.tap()
        // The failed link would reload the copy, whose write never lands; wait out that load.
        XCTAssertFalse(postLanded.waitForNonExistence(timeout: 5), "A failed link does not switch the page to its copy")
        capture("cold-launch-online-html")
        app.backButton.tap()
        XCTAssertTrue(ticketFile.waitForExistence(timeout: 8))
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
