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
            // Review is the root at launch.
            XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 10))
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
        // Review is the root at launch.
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 10))
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

    @MainActor func testFinalPageChiefDraftAndSend() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        // A normal launch opens Review as the root; its fixture cards follow.
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 10))
        try skipFixtureCards(app)
        let caughtUp = app.staticTexts["You’re caught up."]
        XCTAssertTrue(caughtUp.waitForExistence(timeout: 8))
        let revisit = app.buttons["review.revisitSkipped"]
        XCTAssertTrue(revisit.waitForExistence(timeout: 5))
        capture(app, "review-final-page-with-revisit")
        revisit.tap()
        XCTAssertTrue(app.staticTexts["First unread message"].waitForExistence(timeout: 8))
        try skipFixtureCards(app)
        XCTAssertTrue(caughtUp.waitForExistence(timeout: 8))
        XCTAssertTrue(revisit.exists)
        try await proof(base, "review-final")
        XCTAssertTrue(revisit.waitForNonExistence(timeout: 8))

        let input = app.descendants(matching: .any).matching(identifier: "conversation-input").firstMatch
        let attachment = app.descendants(matching: .any).matching(identifier: "conversation-attachment").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertGreaterThan(input.frame.minY, caughtUp.frame.minY, "Message Chief must follow the final-page text")
        input.tap(); input.typeText("Shared Chief draft")
        XCTAssertTrue(app.keyboards.firstMatch.exists)
        let send = app.buttons["Send message"]
        XCTAssertTrue(send.waitForExistence(timeout: 5))
        if !send.isHittable { app.swipeUp() }
        XCTAssertTrue(send.isHittable, "The keyboard must not hide Send")
        UIPasteboard.general.image = UIGraphicsImageRenderer(size: CGSize(width: 90, height: 60)).image { context in
            UIColor.systemTeal.setFill(); context.fill(CGRect(x: 0, y: 0, width: 90, height: 60))
        }
        input.press(forDuration: 1.2)
        XCTAssertTrue(app.menuItems["Paste"].waitForExistence(timeout: 3))
        app.menuItems["Paste"].tap()
        XCTAssertTrue(attachment.waitForExistence(timeout: 5))
        capture(app, "review-final-chief-keyboard-and-attachment")

        // A fresh process reads the same durable Chief words and attachments.
        app.terminate(); app.launch()
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 10))
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, "Shared Chief draft")
        XCTAssertTrue(attachment.waitForExistence(timeout: 5))
        app.openDestination("sidebar-chief", timeout: 8)
        let chiefScreen = app.descendants(matching: .any)["chief-screen"]
        XCTAssertTrue(chiefScreen.waitForExistence(timeout: 8))
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, "Shared Chief draft")
        XCTAssertTrue(attachment.waitForExistence(timeout: 5))
        input.tap(); input.typeText(" from Chief")
        let changedDraft = try XCTUnwrap(input.value as? String)
        XCTAssertTrue(changedDraft.contains(" from Chief"))
        app.openDestination("sidebar-review", timeout: 8)
        XCTAssertTrue(chiefScreen.waitForNonExistence(timeout: 5))
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, changedDraft)
        capture(app, "review-final-chief-shared-draft")

        if !send.isHittable { app.swipeUp() }
        XCTAssertTrue(send.isHittable)
        send.tap()
        let sent = app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'Sent to Chief'")).firstMatch
        XCTAssertTrue(sent.waitForExistence(timeout: 8))
        capture(app, "review-final-chief-sent")
        let writes = try await recordedWrites(base)
        XCTAssertEqual(writes.count, 1, "Revisit and draft edits must not send messages")
        let message = try XCTUnwrap(writes.first as? [String: Any])
        XCTAssertEqual(message["conversation_id"] as? String, "stub")
        XCTAssertNil(message["owner_read_through_sequence"], "A final-page send must not acknowledge unread Review content")
        let pieces = try XCTUnwrap(message["content"] as? [[String: Any]])
        XCTAssertEqual(pieces.first?["text"] as? String, changedDraft.trimmingCharacters(in: .whitespacesAndNewlines))
        XCTAssertTrue(pieces.contains { $0["piece"] as? String == "image" })

        // Appearance is a one-off review, rather than a permanent geometry gate.
        app.terminate()
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        input.tap(); input.typeText("Large text draft")
        if !send.isHittable { app.swipeUp() }
        XCTAssertTrue(send.isHittable)
        capture(app, "review-final-chief-accessibility-text-keyboard")
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
        // Review is the root at launch.
        XCTAssertTrue(app.sidebarButton.waitForExistence(timeout: 10))
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

    /// One-off keyboard and decision-flow review on the existing fixture host.
    @MainActor func testPinnedDesignDecisions() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        // Recreate the application and queries for each process. Retained query snapshots
        // in the failed review trace referred to the terminated accessibility-size process.
        @MainActor func launch(_ arguments: [String]) -> XCUIApplication {
            let application = XCUIApplication()
            application.launchArguments = arguments
            application.launch()
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

    @MainActor private func skipFixtureCards(_ app: XCUIApplication) throws {
        for element in [app.staticTexts["First unread message"], app.buttons["review.approve"], app.staticTexts["Historical message"]] {
            XCTAssertTrue(element.waitForExistence(timeout: 8))
            let skip = app.buttons["review.skip"]
            XCTAssertTrue(skip.waitForExistence(timeout: 5)); skip.tap()
            XCTAssertTrue(element.waitForNonExistence(timeout: 5))
        }
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
