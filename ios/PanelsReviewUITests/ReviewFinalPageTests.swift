import XCTest
import UIKit

/// One-off review tools. These methods stay outside the permanent release suite.
/// The existing loopback host proves real SwiftUI placement, keyboard and live updates.
final class ReviewFinalPageTests: XCTestCase {
    /// One-off proof of the Markdown-to-view boundary that parser tests cannot establish.
    @MainActor func testArtifactLinksOpenOnlyAfterTap() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base]
        app.launch()
        defer { app.terminate() }
        let review = app.tabBars.buttons["Review"]
        XCTAssertTrue(review.waitForExistence(timeout: 10))
        review.tap()
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
        capture(app, "review-text-artifact-after-tap")
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
        let review = app.tabBars.buttons["Review"]
        XCTAssertTrue(review.waitForExistence(timeout: 10))
        XCTAssertTrue(review.isSelected, "A normal launch must open Review")
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
        XCTAssertTrue(review.waitForExistence(timeout: 10))
        XCTAssertTrue(review.isSelected)
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, "Shared Chief draft")
        XCTAssertTrue(attachment.waitForExistence(timeout: 5))
        app.tabBars.buttons["Workspace"].tap()
        let chief = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Chief of Staff'")).firstMatch
        XCTAssertTrue(chief.waitForExistence(timeout: 8)); chief.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        XCTAssertEqual(input.value as? String, "Shared Chief draft")
        XCTAssertTrue(attachment.waitForExistence(timeout: 5))
        input.tap(); input.typeText(" from Chief")
        let changedDraft = try XCTUnwrap(input.value as? String)
        XCTAssertTrue(changedDraft.contains(" from Chief"))
        app.buttons["detail-back"].tap()
        XCTAssertTrue(review.waitForExistence(timeout: 5)); review.tap()
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
        let review = app.tabBars.buttons["Review"]
        XCTAssertTrue(review.waitForExistence(timeout: 10))
        XCTAssertTrue(review.isSelected)
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
        XCTAssertTrue(review.isSelected, "Historical status must update without navigation")
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
        capture(app, "design-how-far")
        stage.tap()
        var reviewer: XCUIElement { app.buttons["decision.reviewer.owner"] }
        XCTAssertTrue(reviewer.waitForExistence(timeout: 5))
        capture(app, "design-who-reviews")
        // Dismiss without a decision, then prove the exact note survives relaunch.
        app.swipeDown()
        app.terminate(); app = launch(["--conversation-stub", base])
        XCTAssertTrue(note.waitForExistence(timeout: 12))
        XCTAssertEqual(note.value as? String, "Keep this proposal note")
        app.buttons["review.openTicket"].tap()
        XCTAssertTrue(app.buttons["ticket-actions"].waitForExistence(timeout: 8))
        XCTAssertTrue(approve.waitForExistence(timeout: 8))
        capture(app, "design-ticket-pinned")
        XCTAssertEqual(note.value as? String, "Keep this proposal note")
        app.buttons["detail-back"].tap()
        // Open ticket uses Workspace's existing navigation stack.
        app.tabBars.buttons["Review"].tap()
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
        // An earlier-stage choice changes only settings and keeps the proposal pending.
        approve.tap()
        let earlierStage = app.buttons["decision.stage.needs_brief"]
        XCTAssertTrue(waitForDecisionStage(earlierStage)); earlierStage.tap()
        XCTAssertTrue(reviewer.waitForExistence(timeout: 5)); reviewer.tap()
        let scopeConfirmation = app.staticTexts["decision.scopeConfirmation"]
        XCTAssertTrue(scopeConfirmation.waitForExistence(timeout: 5))
        XCTAssertTrue(scopeConfirmation.label.contains("No proposal approval was sent."))
        capture(app, "design-earlier-stage-settings-confirmed")
        app.buttons["decision.scopeDone"].tap()
        XCTAssertTrue(approve.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Make the work that needs your attention easy to read."].exists)
        XCTAssertEqual(note.value as? String, "Keep this proposal note")
        let (beforeDecision, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        XCTAssertEqual((try JSONSerialization.jsonObject(with: beforeDecision) as? [String]), [])
        let (scopeData, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/scopes")!)
        let scopeWrites = try XCTUnwrap(try JSONSerialization.jsonObject(with: scopeData) as? [[String: Any]])
        XCTAssertEqual(scopeWrites.count, 1)
        let scopeBody = try XCTUnwrap(scopeWrites.first?["body"] as? [String: Any])
        XCTAssertEqual(scopeBody["ceiling"] as? String, "needs_brief")
        approve.tap()
        XCTAssertTrue(waitForDecisionStage(stage)); stage.tap()
        XCTAssertTrue(reviewer.waitForExistence(timeout: 5)); reviewer.tap()
        let next = app.staticTexts["Match the iPhone app to the web"]
        XCTAssertTrue(next.waitForExistence(timeout: 8))
        let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/writes")!)
        XCTAssertEqual((try JSONSerialization.jsonObject(with: data) as? [String]), ["approve"])
    }

    /// One connected proof for adjacent native targets, interactive sheets and scoped windows.
    /// Reuses the DESIGN_REVIEW server; screenshots are review artifacts, not pixel assertions.
    @MainActor func testWorkspaceSidebarParity() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        try await proof(base, "workspace-fixture")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base, "-panels.goals-sidebar-rows", "NO"]
        app.launch()
        defer { app.terminate() }
        let workspace = app.tabBars.buttons["Workspace"]
        XCTAssertTrue(workspace.waitForExistence(timeout: 10)); workspace.tap()
        let wren = app.descendants(matching: .any).matching(identifier: "workspace-orchestrator")
            .matching(NSPredicate(format: "label CONTAINS %@", "Wren release")).firstMatch
        let science = app.descendants(matching: .any).matching(identifier: "workspace-orchestrator")
            .matching(NSPredicate(format: "label CONTAINS %@", "Science site")).firstMatch
        let tag = app.descendants(matching: .any).matching(identifier: "workspace-goal-status-release").firstMatch
        let notes = app.descendants(matching: .any).matching(identifier: "workspace-ticket-notes").firstMatch
        XCTAssertTrue(tag.waitForExistence(timeout: 10))
        let workspaceTree = XCTAttachment(string: app.debugDescription)
        workspaceTree.name = "workspace-parity-initial-accessibility-tree"
        workspaceTree.lifetime = .keepAlways; add(workspaceTree)
        XCTAssertTrue(wren.exists); XCTAssertTrue(science.exists)
        XCTAssertTrue(app.descendants(matching: .any).matching(identifier: "workspace-ticket-weekly").firstMatch.exists)
        XCTAssertTrue(app.descendants(matching: .any).matching(identifier: "workspace-ticket-onboarding").firstMatch.exists)
        XCTAssertTrue(notes.exists)
        XCTAssertTrue(app.descendants(matching: .any).matching(identifier: "workspace-ticket-testers").firstMatch.exists, "Focus must retain unread Agent work")
        XCTAssertFalse(app.descendants(matching: .any).matching(identifier: "workspace-ticket-build").firstMatch.exists)
        XCTAssertFalse(app.descendants(matching: .any).matching(identifier: "workspace-ticket-article").firstMatch.exists)
        XCTAssertFalse(app.descendants(matching: .any).matching(identifier: "workspace-goal-release").firstMatch.exists, "Ordinary goal rows start off")
        XCTAssertFalse(app.buttons["Focus"].exists); XCTAssertFalse(app.switches["Focus"].exists)
        XCTAssertTrue(app.tabBars.buttons["Review"].exists)
        XCTAssertTrue(app.tabBars.buttons["More"].exists)
        XCTAssertTrue(workspace.isSelected)
        capture(app, "workspace-parity-default")

        tag.tap()
        XCTAssertTrue(app.descendants(matching: .any)["goal-screen"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.buttons["goal-check-first"].waitForExistence(timeout: 8))
        XCTAssertFalse(app.descendants(matching: .any)["orchestrator-screen"].exists)
        app.buttons["detail-back"].tap()
        XCTAssertTrue(wren.waitForExistence(timeout: 8)); wren.tap()
        XCTAssertTrue(app.descendants(matching: .any)["orchestrator-screen"].waitForExistence(timeout: 8))
        app.buttons["detail-back"].tap()
        XCTAssertTrue(notes.waitForExistence(timeout: 8))
        wren.press(forDuration: 1)
        let done = app.buttons["Done"]
        XCTAssertTrue(done.waitForExistence(timeout: 8))
        let firstCheck = app.buttons["goal-check-first"]
        XCTAssertTrue(firstCheck.waitForExistence(timeout: 8))
        let previewTree = XCTAttachment(string: app.debugDescription)
        previewTree.name = "workspace-parity-preview-accessibility-tree"
        previewTree.lifetime = .keepAlways; add(previewTree)
        XCTAssertEqual(app.buttons.matching(identifier: "goal-check-first").count, 1,
                       "The preview must expose one checkbox, with no retained detail duplicate")
        if !firstCheck.isHittable { app.swipeUp() }
        let checkboxReady = NSPredicate { _, _ in
            MainActor.assumeIsolated { firstCheck.exists && firstCheck.isHittable }
        }
        XCTAssertEqual(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: checkboxReady, object: nil)], timeout: 5), .completed,
                       "Wait for the sheet's expansion/scroll gesture to finish before tapping")
        firstCheck.tap()
        XCTAssertTrue(app.staticTexts["Explain the change."].waitForExistence(timeout: 8))
        capture(app, "workspace-parity-interactive-checklist")
        let goalWrites = try await recordedWrites(base)
        XCTAssertEqual(goalWrites.count, 1, "Preview must only perform the requested checklist write")
        let goalWrite = try XCTUnwrap(goalWrites.first as? [String: Any])
        XCTAssertEqual(goalWrite["path"] as? String, "/api/goals/release")
        let goalBody = try XCTUnwrap(goalWrite["body"] as? [String: Any])
        XCTAssertEqual(Set(goalBody.keys), ["page", "expected_revision"])
        XCTAssertEqual(goalBody["expected_revision"] as? Int, 1)
        let page = try XCTUnwrap(goalBody["page"] as? [String: Any])
        let blocks = try XCTUnwrap(page["blocks"] as? [[String: Any]])
        let items = try XCTUnwrap(blocks.first?["items"] as? [[String: Any]])
        XCTAssertEqual(items.first?["id"] as? String, "first")
        XCTAssertEqual(items.first?["state"] as? String, "done")
        XCTAssertEqual(items.last?["state"] as? String, "blocked")

        try await proof(base, "workspace-update")
        // The existing loopback stream emits each second. Admit two frames while held.
        try await Task.sleep(for: .milliseconds(2200))
        XCTAssertTrue(done.exists, "A structural snapshot must not tear down the open preview")
        XCTAssertTrue(app.buttons["Open"].isHittable, "The held preview remains interactive")
        done.tap()
        XCTAssertTrue(wren.waitForNonExistence(timeout: 8), "The removed holder leaves only after the preview is dismissed")
        capture(app, "workspace-parity-after-preview-release")
        try await proof(base, "workspace-restore")
        XCTAssertTrue(wren.waitForExistence(timeout: 8))

        XCTAssertTrue(science.exists)
        if !science.isHittable { app.swipeUp() }
        science.press(forDuration: 1)
        XCTAssertTrue(done.waitForExistence(timeout: 8))
        XCTAssertTrue(app.descendants(matching: .any)["goal-chart-words"].waitForExistence(timeout: 8))
        capture(app, "workspace-parity-chart-preview")
        done.tap()

        try await proof(base, "workspace-pages")
        let next = app.buttons["Next"].firstMatch
        XCTAssertTrue(next.waitForExistence(timeout: 8))
        if !next.isHittable { app.swipeUp() }
        XCTAssertTrue(next.isHittable); next.tap()
        XCTAssertTrue(app.descendants(matching: .any).matching(identifier: "workspace-ticket-page-two").firstMatch.waitForExistence(timeout: 8))
        let (requestsData, _) = try await URLSession.shared.data(from: URL(string: base + "/proof/workspace-requests")!)
        let requests = try XCTUnwrap(try JSONSerialization.jsonObject(with: requestsData) as? [[String: Any]])
        XCTAssertTrue(requests.contains { request in
            let owner = request["owner"] as? [String: Any]
            return request["section"] as? String == "rail_tickets" && owner?["kind"] as? String == "manager"
                && owner?["id"] as? String == "wren" && request["anchor"] as? String == "workspace-next"
        })
        app.descendants(matching: .any).matching(identifier: "workspace-settled").firstMatch.tap()
        let settled = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", "Previous release")).firstMatch
        XCTAssertTrue(settled.waitForExistence(timeout: 8)); settled.tap()
        XCTAssertTrue(app.descendants(matching: .any)["orchestrator-screen"].waitForExistence(timeout: 8))
        app.buttons["detail-back"].tap()
        let reopen = app.buttons["Reopen Previous release"]
        XCTAssertTrue(reopen.waitForExistence(timeout: 8)); reopen.tap()
        XCTAssertTrue(reopen.waitForNonExistence(timeout: 8))
        let writes = try await recordedWrites(base)
        XCTAssertEqual(writes.count, 2)
        let reopening = try XCTUnwrap(writes.last as? [String: Any])
        XCTAssertEqual(reopening["path"] as? String, "/api/managers/settled-one/settled")
        let reopeningBody = try XCTUnwrap(reopening["body"] as? [String: Any])
        XCTAssertEqual(Set(reopeningBody.keys), ["settled"])
        XCTAssertEqual(reopeningBody["settled"] as? Bool, false)
        capture(app, "workspace-parity-settled-dock")

        // One-off visual review in the same host, using the existing standard text-size argument.
        app.terminate()
        try await proof(base, "workspace-fixture")
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        XCTAssertTrue(workspace.waitForExistence(timeout: 10)); workspace.tap()
        XCTAssertTrue(tag.waitForExistence(timeout: 8))
        capture(app, "workspace-parity-accessibility-text")
        if !wren.isHittable { app.swipeUp() }
        XCTAssertTrue(wren.isHittable); wren.press(forDuration: 1)
        XCTAssertTrue(done.waitForExistence(timeout: 8))
        capture(app, "workspace-parity-accessibility-preview")
        XCTAssertTrue(done.isHittable)
        done.tap()
        // System Reduce Motion is reviewed separately in the parent's admitted lane window.
        app.terminate()
        try await proof(base, "workspace-reset")
    }

    /// One-off explicit-Open and real system Reduce Motion review; no new test host.
    @MainActor func testWorkspacePreviewOpenAndSystemMotion() async throws {
        continueAfterFailure = false
        let base = try XCTUnwrap(ProcessInfo.processInfo.environment["PANELS_OFFLINE_STUB_URL"])
        XCTAssertEqual(URL(string: base)?.host, "127.0.0.1")
        try await proof(base, "workspace-fixture")
        let app = XCUIApplication()
        app.launchArguments = ["--conversation-stub", base, "-panels.goals-sidebar-rows", "NO"]
        defer { app.terminate() }
        let wren = app.descendants(matching: .any).matching(identifier: "workspace-orchestrator")
            .matching(NSPredicate(format: "label CONTAINS %@", "Wren release")).firstMatch

        try await configureSystemReduceMotion(false)
        app.launch()
        let workspace = app.tabBars.buttons["Workspace"]
        XCTAssertTrue(workspace.waitForExistence(timeout: 10)); workspace.tap()
        XCTAssertTrue(wren.waitForExistence(timeout: 8)); wren.press(forDuration: 1)
        let open = app.buttons["Open"]
        XCTAssertTrue(open.waitForExistence(timeout: 8))
        XCTAssertFalse(app.descendants(matching: .any)["orchestrator-screen"].exists,
                       "Long press must leave the rail under the sheet")
        XCTAssertEqual(app.buttons.matching(identifier: "goal-check-first").count, 1)
        capture(app, "workspace-explicit-open-before")
        open.tap()
        XCTAssertTrue(app.descendants(matching: .any)["orchestrator-screen"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.buttons["Done"].waitForNonExistence(timeout: 8))
        capture(app, "workspace-explicit-open-destination")
        app.buttons["detail-back"].tap()
        XCTAssertTrue(wren.waitForExistence(timeout: 8))

        for reduced in [false, true] {
            if reduced {
                app.terminate()
                try await configureSystemReduceMotion(true)
                app.launch()
                XCTAssertTrue(workspace.waitForExistence(timeout: 10)); workspace.tap()
                XCTAssertTrue(wren.waitForExistence(timeout: 8))
            }
            let mode = reduced ? "reduced" : "normal"
            // Settings is the preference authority; this app was relaunched after the change.
            // The UI-test runner's UIKit getter is not the Panels process's environment.
            capture(app, "workspace-motion-" + mode + "-before")
            try await proof(base, "start/workspace-motion-" + mode)
            try await proof(base, "workspace-update")
            XCTAssertTrue(wren.waitForNonExistence(timeout: 8))
            capture(app, "workspace-motion-" + mode + "-removed")
            try await proof(base, "workspace-restore")
            XCTAssertTrue(wren.waitForExistence(timeout: 8))
            capture(app, "workspace-motion-" + mode + "-restored")
            try await proof(base, "stop")
        }
        app.terminate()
        try await configureSystemReduceMotion(false)
        try await proof(base, "workspace-reset")
    }

    @MainActor private func configureSystemReduceMotion(_ enabled: Bool) async throws {
        let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences")
        settings.launch()
        defer { settings.terminate() }
        let reduceMotion = settings.switches["Reduce Motion"]
        if !reduceMotion.exists {
            let motion = settings.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Motion")).firstMatch
            if !motion.exists {
                let accessibility = settings.descendants(matching: .any)
                    .matching(NSPredicate(format: "label == %@", "Accessibility")).firstMatch
                for _ in 0..<4 where !accessibility.isHittable { settings.swipeUp() }
                XCTAssertTrue(accessibility.waitForExistence(timeout: 5)); accessibility.tap()
            }
            XCTAssertTrue(motion.waitForExistence(timeout: 5)); motion.tap()
        }
        XCTAssertTrue(reduceMotion.waitForExistence(timeout: 5))
        let switchBefore = XCTAttachment(string: "Before toggle: value=\(reduceMotion.value ?? "nil"); frame=\(reduceMotion.frame)\n" + settings.debugDescription)
        switchBefore.name = enabled ? "workspace-system-motion-on-before-tree" : "workspace-system-motion-off-before-tree"
        switchBefore.lifetime = .keepAlways; add(switchBefore)
        let expectedValue = enabled ? "1" : "0"
        if reduceMotion.value as? String != expectedValue {
            // Settings exposes the labelled row as a Switch; touch its trailing thumb.
            reduceMotion.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()
        }
        let actualPreference = NSPredicate { _, _ in
            MainActor.assumeIsolated {
                reduceMotion.value as? String == expectedValue
            }
        }
        let preferenceResult = await XCTWaiter.fulfillment(of: [XCTNSPredicateExpectation(predicate: actualPreference, object: nil)], timeout: 5)
        let preferenceEvidence = XCTAttachment(string: "Settings Reduce Motion switch=\(reduceMotion.value ?? "nil"); frame=\(reduceMotion.frame); expected=\(enabled); UI-test runner UIKit getter=\(UIAccessibility.isReduceMotionEnabled) (diagnostic only; not the Panels process)")
        preferenceEvidence.name = enabled ? "workspace-system-motion-on-values" : "workspace-system-motion-off-values"
        preferenceEvidence.lifetime = .keepAlways; add(preferenceEvidence)
        capture(settings, enabled ? "workspace-system-reduce-motion-on" : "workspace-system-reduce-motion-off")
        XCTAssertEqual(preferenceResult, .completed,
                       "The actual Settings switch must confirm system Reduce Motion before relaunching Panels")
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
