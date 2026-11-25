import XCTest
import UIKit

/// One-off review tools. These methods stay outside the permanent release suite.
/// The existing loopback host proves real SwiftUI placement, keyboard and live updates.
final class ReviewFinalPageTests: XCTestCase {
    /// One-off assembled Consequences boundary review on the existing loopback host.
    /// Foundation coverage cannot prove the real Ticket controls and Review sheet choices.
    @MainActor func testConsequencesReviewBoundary() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        try await proof(base, "consequences-review/implementation")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        defer { app.terminate() }
        @MainActor func launchReview() {
            app.launch()
            app.openReviewCards()
            XCTAssertTrue(app.buttons["review.approve"].waitForExistence(timeout: 8))
        }
        @MainActor func oracle(_ stage: String, pending: Bool, count: Int) async throws -> [[String: Any]] {
            let url = URL(string: base + "/proof/consequences-review/state")!
            // Queue exit is optimistic. Wait for the existing outbox's actual host write.
            let applied = NSPredicate { _, _ in
                guard let data = try? Data(contentsOf: url),
                      let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let decisions = value["decisions"] as? [[String: Any]] else { return false }
                return decisions.count == count
            }
            let result = await XCTWaiter.fulfillment(of: [XCTNSPredicateExpectation(predicate: applied, object: nil)], timeout: 5)
            XCTAssertEqual(result, .completed, "The decision must reach the host, not only leave the visible queue")
            let (data, _) = try await URLSession.shared.data(from: url)
            let value = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
            let ticket = try XCTUnwrap(value["ticket"] as? [String: Any])
            XCTAssertEqual(ticket["stage"] as? String, stage, "Check canonical fixture state, not only an optimistic receipt")
            XCTAssertEqual(ticket["pending_proposal"] is [String: Any], pending)
            if stage != "needs_implementation" { XCTAssertEqual(ticket["ceiling"] as? String, "needs_consequences") }
            let decisions = try XCTUnwrap(value["decisions"] as? [[String: Any]])
            XCTAssertEqual(decisions.count, count)
            return decisions
        }
        launchReview()
        app.buttons["review.approve"].tap()
        let consequences = app.buttons["decision.stage.needs_consequences"]
        XCTAssertTrue(waitForDecisionStage(consequences))
        // Approval offers only stages after the waiting one, as the web does.
        XCTAssertFalse(app.buttons["decision.stage.needs_implementation"].exists)
        capture(app, "consequences-implementation-approval-choices")
        consequences.tap()
        app.buttons["sheet-confirm"].tap()
        XCTAssertTrue(app.buttons["review.approve"].waitForNonExistence(timeout: 8))
        let first = try await oracle("needs_consequences", pending: false, count: 1)
        XCTAssertEqual(first.first?["next_ceiling"] as? String, "needs_consequences")
        app.terminate()

        // Model the worker's next proposal explicitly; approval never manufactures it.
        try await proof(base, "consequences-review/proposal")
        launchReview()
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(app.buttons["review.sendBack"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.buttons["review.sendBack"].exists)
        XCTAssertTrue(app.buttons["review.approve"].exists)
        capture(app, "consequences-ticket-decision-controls")
        // Open ticket pushes the Ticket on Review, so Back returns to the card.
        app.backButton.tap()
        let note = app.descendants(matching: .any).matching(identifier: "review.revision").firstMatch
        let sendBack = app.buttons["review.sendBack"]
        XCTAssertTrue(sendBack.waitForExistence(timeout: 8)); sendBack.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        note.typeText("Clarify the integration evidence")
        capture(app, "consequences-review-send-back")
        sendBack.tap()
        XCTAssertTrue(sendBack.waitForNonExistence(timeout: 8))
        let returned = try await oracle("needs_consequences", pending: false, count: 2)
        XCTAssertEqual(returned.last?["decision"] as? String, "send_back")
        app.terminate()

        try await proof(base, "consequences-review/revision")
        launchReview()
        capture(app, "consequences-final-approval")
        app.buttons["review.approve"].tap()
        XCTAssertTrue(app.buttons["review.approve"].waitForNonExistence(timeout: 8))
        XCTAssertFalse(app.buttons["decision.stage.done"].exists)
        let final = try await oracle("done", pending: false, count: 3)
        XCTAssertEqual(final.last?["decision"] as? String, "approve")
        XCTAssertTrue(final.last?["next_ceiling"] == nil || final.last?["next_ceiling"] is NSNull)
        capture(app, "consequences-approved-done")
    }

    /// One-off proof of real Markdown links, URL-based HTML, native Share and navigation.
    /// Reuses the existing loopback host; model tests cannot prove WebKit loads or bar visibility.
    @MainActor func testArtifactLinksOpenOnlyAfterTap() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        app.openReviewCards()
        let link = app.links["Migration verification"]
        XCTAssertTrue(link.waitForExistence(timeout: 8), "The authored link text must remain in its paragraph")
        let photo = app.descendants(matching: .any).matching(identifier: "inline-artifact-image").firstMatch
        XCTAssertTrue(photo.waitForExistence(timeout: 8), "The photo exception must display a loaded image")
        XCTAssertEqual(photo.label, "Review photo")
        XCTAssertFalse(app.staticTexts["migration-verification.txt"].exists, "Review must not append a filename disclosure")
        let content = app.staticTexts["Migration verification body must appear only after a tap."]
        XCTAssertFalse(content.exists)
        let initialReads = try await artifactReads(base)
        XCTAssertEqual(initialReads["text"], 0, "A proposal must not fetch a linked text file automatically")
        XCTAssertGreaterThan(initialReads["photo"] ?? 0, 0)
        capture(app, "review-inline-txt-link-and-photo")

        link.tap()
        XCTAssertTrue(content.waitForExistence(timeout: 8), "A tap must open the existing artifact viewer")
        let openedReads = try await artifactReads(base)
        XCTAssertEqual(openedReads["text"], 1)
        XCTAssertFalse(app.sidebarButton.exists, "A pushed artifact shows Back, not the Sidebar button")
        capture(app, "review-text-artifact-after-tap")
        app.backButton.tap()
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 5))
        XCTAssertTrue(app.sidebarButton.isHittable)

        let html = app.links["HTML preview"]
        XCTAssertTrue(html.waitForExistence(timeout: 5)); html.tap()
        let firstPreview = app.webViews.staticTexts["Merged HTML preview 1"]
        XCTAssertTrue(firstPreview.waitForExistence(timeout: 8), "HTML must load from its managed URL")
        XCTAssertFalse(app.sidebarButton.exists)
        let htmlReads = try await artifactReads(base)
        XCTAssertEqual(htmlReads["html"], 1)
        capture(app, "merged-html-url-preview-sidebar-hidden")
        app.buttons["Artifact actions"].tap()
        app.buttons["Reload"].tap()
        XCTAssertTrue(app.webViews.staticTexts["Merged HTML preview 2"].waitForExistence(timeout: 8))
        let reloadedReads = try await artifactReads(base)
        XCTAssertEqual(reloadedReads["html"], 2, "Reload must request the managed HTML URL again")
        capture(app, "merged-html-url-preview-reloaded")
        app.buttons["Share artifact"].tap()
        let shareSheet = app.descendants(matching: .any).matching(identifier: "ShareSheet.RemoteContainerView").firstMatch
        XCTAssertTrue(shareSheet.waitForExistence(timeout: 8), "Share must present the native activity sheet")
        capture(app, "merged-html-native-share-sheet")
    }

    @MainActor private func artifactReads(_ base: String) async throws -> [String: Int] {
        let (data, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/artifact-reads")!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Int])
    }

    @MainActor func testVisibleReviewCardBecomesHistorical() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        try await proof(base, "replacement/old")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        app.openReviewCards()
        let originalMessage = app.staticTexts["First unread message"]
        XCTAssertTrue(originalMessage.waitForExistence(timeout: 8))
        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap(); input.typeText("Keep this Review draft")
        capture(app, "review-card-before-replacement")
        try await proof(base, "new-message")
        try await proof(base, "replacement/new")
        let historical = app.staticTexts["Historical conversation · read only"]
        XCTAssertTrue(historical.waitForExistence(timeout: 8))
        XCTAssertTrue(input.waitForNonExistence(timeout: 3))
        XCTAssertTrue(originalMessage.exists, "Replacement must retain the displayed unread run")
        XCTAssertFalse(app.staticTexts["Arrived after display"].exists, "Replacement must not expand the displayed read boundary")
        XCTAssertTrue(app.sidebarButton.exists, "Historical status must update without navigation")
        capture(app, "review-card-after-replacement")
        let writes = try await recordedWrites(base)
        XCTAssertTrue(writes.isEmpty)
        try await proof(base, "replacement/old")
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, "Keep this Review draft")
        XCTAssertFalse(app.staticTexts["Arrived after display"].exists)
        capture(app, "review-card-draft-retained")
    }

    /// Rare Ticket settings wait for the server inside their sheet and show a failure inline.
    /// Model tests cannot prove the sheet stays open with its spinner, or that no banner appears.
    @MainActor func testTicketSettingsWaitInTheirSheet() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        app.openReviewCards()
        XCTAssertTrue(app.buttons["review.openTicket"].waitForExistence(timeout: 12))
        // The approval limit row shows only when no proposal waits. The Ticket page reads
        // the Ticket when it opens, so remove the proposal first.
        try await proof(base, "design-state/settings")
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(app.buttons["ticket-actions"].waitForExistence(timeout: 8))
        let limit = app.buttons["ticket-approval-limit"]
        for _ in 0..<3 where !limit.waitForExistence(timeout: 4) {
            let window = app.windows.firstMatch
            window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.25))
                .press(forDuration: 0.05, thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.85)))
        }
        XCTAssertTrue(limit.waitForExistence(timeout: 4))

        // Approval limit: choose a ceiling, Save, and the sheet closes on success.
        limit.tap()
        let consequences = app.buttons["decision.stage.needs_consequences"]
        XCTAssertTrue(waitForDecisionStage(consequences)); consequences.tap()
        capture(app, "settings-approval-limit-sheet")
        app.buttons["sheet-confirm"].tap()
        XCTAssertTrue(consequences.waitForNonExistence(timeout: 8))
        XCTAssertTrue(limit.label.contains("Consequences"), limit.label)

        // Backlog ticket waits in the sheet with a spinner, then closes.
        app.buttons["ticket-actions"].tap()
        let backlog = app.buttons["ticket-action-backlog"]
        XCTAssertTrue(backlog.waitForExistence(timeout: 5)); backlog.tap()
        // While the server answers, the sheet stays open with a spinner and Cancel disabled.
        XCTAssertTrue(backlog.exists, "The sheet stays open while the server answers")
        XCTAssertFalse(app.buttons["Cancel"].isEnabled, "Cancel waits for the answer")
        capture(app, "settings-backlog-waiting")
        XCTAssertTrue(backlog.waitForNonExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts["Backlogged. Automatic work is paused."].waitForExistence(timeout: 8))
        capture(app, "settings-backlogged")

        // Ask for a recap succeeds and closes the sheet.
        app.buttons["ticket-actions"].tap()
        let recap = app.buttons["ticket-action-recap"]
        XCTAssertTrue(recap.waitForExistence(timeout: 5)); recap.tap()
        XCTAssertTrue(recap.waitForNonExistence(timeout: 8))

        // A refused recap keeps the sheet open with an inline error and no banner.
        try await proof(base, "design-state/refuse-recap")
        app.buttons["ticket-actions"].tap()
        XCTAssertTrue(recap.waitForExistence(timeout: 5)); recap.tap()
        let error = app.staticTexts["sheet-error"]
        XCTAssertTrue(error.waitForExistence(timeout: 8))
        XCTAssertTrue(error.label.hasPrefix("Not delivered"), error.label)
        XCTAssertTrue(recap.exists, "A failure keeps the sheet open")
        capture(app, "settings-recap-refused-inline")
        app.buttons["Cancel"].tap()
        XCTAssertTrue(recap.waitForNonExistence(timeout: 5))
        let banner = app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH %@", "Not delivered")).firstMatch
        XCTAssertFalse(banner.exists, "The failure shows only inside the sheet")
        capture(app, "settings-after-refusal-no-banner")

        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/settings-writes")!)
        let writes = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [[String: Any]]).compactMap { $0["path"] as? String }
        XCTAssertEqual(writes, ["/api/tickets/offline-1/backlog", "/api/recap-requests", "/api/recap-requests"])
        let (scopeData, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/scopes")!)
        let scopes = try XCTUnwrap(try JSONSerialization.jsonObject(with: scopeData) as? [[String: Any]])
        XCTAssertEqual((scopes.last?["body"] as? [String: Any])?["ceiling"] as? String, "needs_consequences")
    }

    /// One-off keyboard and decision-flow review on the existing fixture host.
    @MainActor func testPinnedDesignDecisions() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        // Recreate the application and queries for each process. Retained query snapshots
        // in the failed review trace referred to the terminated accessibility-size process.
        // Review opens on its summary, so each launch goes back to the cards first.
        @MainActor func launch(_ arguments: [String]) -> XCUIApplication {
            let application = XCUIApplication()
            application.launchArguments = arguments
            application.launch()
            let back = application.buttons["review.return"]
            if back.waitForExistence(timeout: 12) { back.tap() }
            return application
        }
        var app = launch(["--conversation-stub", base])
        defer { app.terminate() }
        var approve: XCUIElement { app.buttons["review.approve"] }
        XCTAssertTrue(approve.waitForExistence(timeout: 12))
        capture(app, "design-review-normal")
        var note: XCUIElement { app.descendants(matching: .any).matching(identifier: "review.revision").firstMatch }
        var sendBack: XCUIElement { app.buttons["review.sendBack"] }
        sendBack.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        let keyboardTip = app.buttons["Continue"]
        if keyboardTip.exists { keyboardTip.tap() }
        note.typeText("Keep this proposal note")
        XCTAssertTrue(approve.isHittable, "The keyboard must not cover pinned decisions")
        capture(app, "design-review-keyboard")
        approve.tap()
        var stage: XCUIElement { app.buttons["decision.stage.needs_implementation"] }
        XCTAssertTrue(waitForDecisionStage(stage))
        capture(app, "design-approve-sheet")
        // Dismiss without a decision, then prove the exact note survives relaunch.
        app.swipeDown()
        app.terminate(); app = launch(["--conversation-stub", base])
        XCTAssertTrue(note.waitForExistence(timeout: 12))
        XCTAssertEqual(note.value as? String, "Keep this proposal note")
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(app.buttons["ticket-actions"].waitForExistence(timeout: 8))
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        capture(app, "design-ticket-pinned")
        app.buttons["ticket-actions"].tap()
        XCTAssertTrue(app.buttons["ticket-action-backlog"].waitForExistence(timeout: 5))
        capture(app, "design-ticket-actions")
        app.buttons["Cancel"].tap()
        XCTAssertTrue(app.buttons["ticket-actions"].waitForExistence(timeout: 5))
        // The Ticket page opens the same Approve and Reviewer sheets as Review.
        approve.tap()
        XCTAssertTrue(waitForDecisionStage(stage))
        capture(app, "design-ticket-approve-sheet")
        app.buttons["Cancel"].tap()
        XCTAssertTrue(stage.waitForNonExistence(timeout: 5))
        app.buttons["decision.reviewer"].tap()
        XCTAssertTrue(app.buttons["decision.reviewer.chief"].waitForExistence(timeout: 5))
        capture(app, "design-ticket-reviewer-sheet")
        app.buttons["Cancel"].tap()
        XCTAssertTrue(app.buttons["decision.reviewer.chief"].waitForNonExistence(timeout: 5))
        XCTAssertEqual(note.value as? String, "Keep this proposal note")
        // Open ticket pushes the Ticket on Review, so Back returns to the card.
        app.backButton.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        // Capture the real app at the largest accessibility category, including its safe-area edge.
        app.terminate()
        app = launch(["--conversation-stub", base,
                      "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"])
        XCTAssertTrue(approve.waitForExistence(timeout: 12))
        XCTAssertTrue(approve.isHittable, "Large text must leave pinned decisions reachable")
        capture(app, "design-review-largest-text-normal")
        app.scrollViews.firstMatch.swipeUp()
        XCTAssertTrue(approve.isHittable, "The pinned decision must remain reachable while the proposal scrolls")
        capture(app, "design-review-largest-text-proposal")
        // Restore normal type before the final approval, without changing the saved note.
        app.terminate()
        app = launch(["--conversation-stub", base])
        XCTAssertTrue(approve.waitForExistence(timeout: 12))
        XCTAssertEqual(note.value as? String, "Keep this proposal note")
        capture(app, "design-review-restored-normal")
        let hierarchy = XCTAttachment(string: app.debugDescription)
        hierarchy.name = "design-review-restored-normal-hierarchy"
        hierarchy.lifetime = .keepAlways; add(hierarchy)
        // A reviewer change is not an approval: it waits in its sheet, then the proposal
        // leaves the owner's Review because Chief now holds it. The next card shows.
        app.buttons["decision.reviewer"].tap()
        let chief = app.buttons["decision.reviewer.chief"]
        XCTAssertTrue(chief.waitForExistence(timeout: 5))
        capture(app, "design-reviewer-sheet")
        chief.tap()
        XCTAssertTrue(chief.waitForNonExistence(timeout: 8))
        XCTAssertTrue(approve.waitForExistence(timeout: 5))
        let emptied = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", ""), object: note)
        let emptiedResult = await XCTWaiter.fulfillment(of: [emptied], timeout: 5)
        XCTAssertEqual(emptiedResult, .completed, "The next card has its own empty note; the handed-off proposal left Review")
        capture(app, "design-reviewer-changed")
        let (beforeDecision, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        XCTAssertEqual((try JSONSerialization.jsonObject(with: beforeDecision) as? [String]), [])
        let (scopeData, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/scopes")!)
        let scopeWrites = try XCTUnwrap(try JSONSerialization.jsonObject(with: scopeData) as? [[String: Any]])
        XCTAssertEqual(scopeWrites.count, 1)
        let scopeBody = try XCTUnwrap(scopeWrites.first?["body"] as? [String: Any])
        XCTAssertNil(scopeBody["ceiling"])
        XCTAssertEqual((scopeBody["ceiling_holder"] as? [String: Any])?["kind"] as? String, "chief")
        approve.tap()
        XCTAssertTrue(waitForDecisionStage(stage)); stage.tap()
        app.buttons["sheet-confirm"].tap()
        let next = app.staticTexts["Match the iPhone app to the web"]
        XCTAssertTrue(next.waitForExistence(timeout: 8))
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        XCTAssertEqual((try JSONSerialization.jsonObject(with: data) as? [String]), ["approve"])
    }

    /// A reviewer change to Chief saves while the store's refresh stalls, as on a slow
    /// connection. The sheet closes as soon as the change is saved, not when the refresh
    /// ends, and the handed-off card leaves: the three design cards share one title, so
    /// only two cards are left to skip before the summary.
    @MainActor func testReviewerSheetClosesWhenTheChangeIsSaved() async throws {
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        let approve = app.buttons["review.approve"]
        let back = app.buttons["review.return"]
        if !approve.waitForExistence(timeout: 12), back.exists { back.tap() }
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        try await proof(base, "stall-refresh")
        app.buttons["decision.reviewer"].tap()
        let chief = app.buttons["decision.reviewer.chief"]
        XCTAssertTrue(chief.waitForExistence(timeout: 5))
        chief.tap()
        var saved = false
        for _ in 0..<40 where !saved {
            let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/scopes")!)
            saved = ((try? JSONSerialization.jsonObject(with: data) as? [Any])?.count ?? 0) == 1
            if !saved { try await Task.sleep(for: .milliseconds(250)) }
        }
        XCTAssertTrue(saved, "The reviewer change saved")
        XCTAssertTrue(chief.waitForNonExistence(timeout: 3), "The sheet closes once the change is saved, while the refresh still stalls")
        capture(app, "reviewer-sheet-closed-while-refresh-stalls")
        let skip = app.buttons["review.skip"]
        let headline = app.staticTexts["review.summary.headline"]
        for _ in 0..<3 where !headline.exists {
            XCTAssertTrue(skip.waitForExistence(timeout: 8))
            skip.tap()
            _ = headline.waitForExistence(timeout: 3)
        }
        XCTAssertTrue(headline.waitForExistence(timeout: 5))
        XCTAssertEqual(headline.label, "2 skipped.", "The handed-off card left, so only the other two were skipped")
        try await proof(base, "unstall-refresh")
    }

    /// The design cards on a bad connection. The card, its Approve and Reviewer sheets,
    /// the Ticket page and its Actions sheet are read once online, then the stub drops every
    /// connection. Each one opens from its saved copy with no cover, banner or status note.
    /// No decision is sent: the permanent offline test covers delivery.
    @MainActor func testCardsSheetsAndTicketOnABadConnection() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        let approve = app.buttons["review.approve"]
        let back = app.buttons["review.return"]
        let stage = app.buttons["decision.stage.needs_implementation"]
        let reviewer = app.buttons["decision.reviewer"]
        let chief = app.buttons["decision.reviewer.chief"]
        let ticketActions = app.buttons["ticket-actions"]
        func noCover(_ screen: String) {
            for text in ["Offline", "offline", "Delivery uncertain", "Slow connection", "Reconnecting", "Not connected"] {
                XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", text)).firstMatch.exists,
                               "\(screen) shows \"\(text)\"")
            }
        }
        func sheets(_ place: String) {
            approve.tap()
            XCTAssertTrue(waitForDecisionStage(stage), "\(place): the Approve sheet opens")
            noCover("\(place) Approve sheet"); capture(app, "offline-\(place)-approve-sheet")
            app.buttons["Cancel"].tap()
            XCTAssertTrue(stage.waitForNonExistence(timeout: 5))
            reviewer.tap()
            XCTAssertTrue(chief.waitForExistence(timeout: 5), "\(place): the Reviewer sheet opens")
            noCover("\(place) Reviewer sheet"); capture(app, "offline-\(place)-reviewer-sheet")
            app.buttons["Cancel"].tap()
            XCTAssertTrue(chief.waitForNonExistence(timeout: 5))
        }
        // Online: read the card and its Ticket once.
        XCTAssertTrue(back.waitForExistence(timeout: 12)); back.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(ticketActions.waitForExistence(timeout: 8))
        app.backButton.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 8))

        // Bad connection: every request now drops.
        try await proof(base, "offline")
        noCover("Review card"); capture(app, "offline-review-card")
        sheets("review")
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(ticketActions.waitForExistence(timeout: 8), "The Ticket page opens from its saved copy")
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        noCover("Ticket"); capture(app, "offline-ticket")
        ticketActions.tap()
        XCTAssertTrue(app.buttons["ticket-action-backlog"].waitForExistence(timeout: 5))
        noCover("Ticket actions"); capture(app, "offline-ticket-actions")
        app.buttons["Cancel"].tap()
        XCTAssertTrue(ticketActions.waitForExistence(timeout: 5))
        sheets("ticket")
        app.backButton.tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 8), "Back returns to the card offline")
        try await proof(base, "online")
    }

    /// One-off fade evidence. Reuses the artifact host and changes no server decisions.
    /// Run the same walk before and after the source change, under each system appearance.
    @MainActor func testNoteDockFadeBoundary() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        try await proof(base, "review-owner/ticket-split")
        let app = XCUIApplication()
        defer { app.terminate() }
        func launch(large: Bool) {
            app.launchArguments = ["--conversation-stub", base]
            if large {
                app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
            }
            app.launch()
            app.openReviewCards()
            XCTAssertTrue(app.buttons["review.approve"].waitForExistence(timeout: 12))
        }
        func scroll(_ view: XCUIElement) {
            view.coordinate(withNormalizedOffset: CGVector(dx: 0.85, dy: 0.55))
                .press(forDuration: 0.05, thenDragTo: view.coordinate(withNormalizedOffset: CGVector(dx: 0.85, dy: 0.3)))
        }
        func shot(_ name: String) async throws {
            capture(app, name)
            try await proof(base, "screenshot/" + name)
        }
        func keyboardShot(_ name: String) async throws {
            let note = app.descendants(matching: .any).matching(identifier: "review.revision").firstMatch
            XCTAssertTrue(note.waitForExistence(timeout: 5))
            note.tap()
            XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
            let tip = app.buttons["Continue"]
            if tip.exists { tip.tap() }
            try await shot(name)
        }
        launch(large: false)
        // The artifact link starts above the long body, so open it before the scroll.
        let link = app.links["dock evidence"].firstMatch
        XCTAssertTrue(link.waitForExistence(timeout: 8)); link.tap()
        XCTAssertTrue(app.staticTexts["Dock artifact proof"].waitForExistence(timeout: 8))
        scroll(app.scrollViews.firstMatch)
        try await shot("fade-artifact-normal")
        app.backButton.tap()
        XCTAssertTrue(app.buttons["review.openTicket"].waitForExistence(timeout: 8))
        scroll(app.scrollViews["review-content"])
        try await shot("fade-review-normal")
        try await keyboardShot("fade-review-keyboard")
        // Navigation ends keyboard focus through the app's actual Open ticket action.
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(app.buttons["ticket-actions"].waitForExistence(timeout: 8))
        scroll(app.scrollViews.firstMatch)
        try await shot("fade-ticket-normal")
        try await keyboardShot("fade-ticket-keyboard")
        app.terminate()
        launch(large: true)
        scroll(app.scrollViews["review-content"])
        try await shot("fade-review-largest")
        try await keyboardShot("fade-review-largest-keyboard")
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(app.buttons["ticket-actions"].waitForExistence(timeout: 8))
        scroll(app.scrollViews.firstMatch)
        try await shot("fade-ticket-largest")
        try await keyboardShot("fade-ticket-largest-keyboard")
    }

    @MainActor private func waitForDecisionStage(_ stage: XCUIElement) -> Bool {
        // Existence alone admitted an infinite/zero frame in the earlier review run.
        let ready = NSPredicate { _, _ in
            MainActor.assumeIsolated {
                guard stage.exists else { return false }
                let frame = stage.frame
                guard frame.minX.isFinite && frame.minY.isFinite && frame.width.isFinite && frame.height.isFinite,
                      frame.width > 0 && frame.height > 0 else { return false }
                return stage.isHittable
            }
        }
        return XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: ready, object: nil)], timeout: 5) == .completed
    }

    @MainActor private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }

    @MainActor private func proof(_ base: String, _ operation: String) async throws {
        let (_, response) = try await URLSession.shared.data(from: URL(string: base + "/proof/" + operation)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }

    @MainActor private func recordedWrites(_ base: String) async throws -> [Any] {
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [Any])
    }
}
