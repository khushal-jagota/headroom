import XCTest
@testable import Panels

/// Fixtures transcribe web/tests/support/navigation/fixtures.ts and the synthetic
/// records of web/tests/navigation-*.test.ts and workspace-rail-projection.test.ts,
/// so the phone's derivation is checked against the cases desktop already pins.
final class NavigationViewTests: XCTestCase {
    // MARK: Frame application (web/src/lib/navigation/views/applyFrame.ts)

    func testFramesApplyCheckpointPatchAndRefuseGaps() throws {
        let lean: [String: Any] = ["kind": "manager", "id": "settled", "value": ["title": "Settled", "created_at": 1, "updated_at": 1, "settled": true, "conversation_id": "c"]]
        let initial = try snapshot([project("p", "one"), conversation("c"), lean])
        XCTAssertEqual(NavigationPresentation(initial.content.records).managers.count, 0, "A settled-only row is not a full manager")

        let checkpoint = try frame(["event": "checkpoint", "previous_coverage": coverage("0"), "coverage": coverage("1"), "content_version": "one"])
        let advanced = try NavigationViewFrames.apply(initial, checkpoint).get()
        XCTAssertEqual(advanced.coverage.revision, "1")
        XCTAssertEqual(advanced.contentVersion, "one")
        XCTAssertEqual(advanced.content.records.count, 3)

        var full = lean
        full["value"] = ["title": "Settled", "created_at": 1, "updated_at": 1, "settled": true, "conversation_id": "c", "recap": "Retained for selection", "recap_preview": "Retained", "project_id": "p", "recap_reference": NSNull()]
        let window = viewWindow("rail_parents", members: [["kind": "manager", "id": "settled"]])
        let patch = try frame(["event": "patch", "previous_coverage": coverage("1"), "coverage": coverage("2"), "content_version": "full", "previous_content_version": "one",
                               "upserts": [full], "evictions": [["kind": "conversation", "id": "c"]], "windows": [window], "counts": counts(review: 3),
                               "review_context": [], "review_summary": ["skipped": 0, "new": 0]])
        let promoted = try NavigationViewFrames.apply(advanced, patch).get()
        XCTAssertEqual(promoted.content.records.map(\.key.kind), ["project", "manager"], "Eviction removes; an upsert replaces its key in place")
        XCTAssertEqual(promoted.content.counts.review, 3)
        XCTAssertEqual(promoted.window("rail_parents")?.members.map(\.id), ["settled"])
        let presentation = NavigationPresentation(promoted.content.records)
        XCTAssertEqual(presentation.managersById["settled"]?.recap, "Retained for selection")
        XCTAssertEqual(presentation.managersById["settled"]?.project, "one")

        // Any discontinuity requires a fresh snapshot.
        XCTAssertEqual(refusal(NavigationViewFrames.apply(advanced, try frame(["event": "checkpoint", "previous_coverage": coverage("1"), "coverage": coverage("2"), "content_version": "other"]))), .checkpointChangedContent)
        XCTAssertEqual(refusal(NavigationViewFrames.apply(promoted, patch)), .obsolete, "A patch from an older coverage revision")
        let gap = try frame(["event": "patch", "previous_coverage": coverage("2"), "coverage": coverage("3"), "content_version": "later", "previous_content_version": "missed",
                             "upserts": [], "evictions": [], "windows": [], "counts": counts(), "review_context": [], "review_summary": ["skipped": 0, "new": 0]])
        XCTAssertEqual(refusal(NavigationViewFrames.apply(promoted, gap)), .contentGap)
        var foreign = try frame(["event": "checkpoint", "previous_coverage": coverage("2"), "coverage": coverage("3"), "content_version": "full"], session: "replaced")
        XCTAssertEqual(refusal(NavigationViewFrames.apply(promoted, foreign)), .obsolete, "Another delivery authority")
        foreign = try frame(["event": "checkpoint", "previous_coverage": coverage("2"), "coverage": coverage("3"), "content_version": "full"])
        XCTAssertEqual(refusal(NavigationViewFrames.apply(nil, foreign)), .obsolete)
        let reset = try APIClient.decoder().decode(ViewFrame.self, from: Data(#"{"event":"reset","wire_version":3,"view_id":"view","scope_generation":0,"reason":"overflow"}"#.utf8))
        XCTAssertEqual(refusal(NavigationViewFrames.apply(promoted, reset)), .reset("overflow"))
    }

    // MARK: Ticket and manager facts (presentation.ts)

    func testTicketFactsMatchDesktopDerivation() throws {
        let source = sourceTicket(["ticket_status": "awaiting_approval", "blocked": true, "stage_ownership": "user"])
        let records = [source, conversation("conversation"), sourceManager, project("project", "Project"), chiefRecord(nil), sourceAddress]
        let presentation = NavigationPresentation(try snapshot(records).content.records)
        let ticket = try XCTUnwrap(presentation.ticketsById["source-ticket"])
        XCTAssertTrue(ticket.blocked); XCTAssertTrue(ticket.assigned); XCTAssertTrue(ticket.awaitingApproval); XCTAssertFalse(ticket.awaitingAgentApproval)
        XCTAssertEqual(ticket.agentState, "working")
        XCTAssertEqual(ticket.awaitingReply, true)
        XCTAssertEqual(ticket.signal, NavigationSignal(ConversationKnowledge(try conversationValue("conversation"))))
        XCTAssertEqual(ticket.managerTitle, "Parent")
        XCTAssertEqual(ticket.project, "Project", "A Ticket's Project joins through its parent")
        XCTAssertEqual(presentation.managers.first?.updatedAt, 11)
        XCTAssertTrue(presentation.managers.first?.recap.hasPrefix("Full parent recap.") == true)
        XCTAssertNil(WorkspaceGrouping.section(for: ticket.summary), "Blocked work stays out of the rail")

        let agentApproval = sourceTicket(["stage": "done", "terminal": true, "ticket_status": "awaiting_approval", "ceiling_holder": ["kind": "owner", "id": "another"]])
        let parked = try XCTUnwrap(NavigationPresentation(try snapshot([agentApproval]).content.records).tickets.first)
        XCTAssertFalse(parked.awaitingApproval); XCTAssertTrue(parked.awaitingAgentApproval)
        XCTAssertNil(parked.agentState, "No conversation record: unknown, never idle")
        XCTAssertEqual(parked.summary.agentState, "idle")

        let consequences = sourceTicket(["conversation_id": NSNull(), "gating_field": "consequences"])
        let closing = try XCTUnwrap(NavigationPresentation(try snapshot([consequences, sourceManager, project("project", "Project")]).content.records).tickets.first)
        XCTAssertTrue(closing.waitingToCloseout)
        XCTAssertEqual(WorkspaceGrouping.section(for: closing.summary), .consequences)
    }

    // MARK: Review queue (presentation.ts, ReviewRoute.svelte)

    func testReviewQueueUsesCanonicalAddressesOldestWaitAndWindowOrder() throws {
        let pending = ticket(["ticket_status": "awaiting_approval", "recap": "Worker recap", "proposal_field": "plan", "proposal_revision": 1, "proposal_created_at": 2])
        let historical = conversation("historical", unreadAt: 2)
        let current = conversation("a-conversation", unreadAt: 1)
        let addresses = [
            address("old-worker", owner: ("ticket", "a"), conversation: "historical", current: false, parent: ("manager", "parent")),
            address("current-manager", owner: ("manager", "parent"), conversation: "historical", current: true, parent: nil),
            address("current-worker", owner: ("ticket", "a"), conversation: "a-conversation", current: true, parent: ("manager", "parent")),
        ]
        let source = [pending, current, historical, parentManager(), project("p", "Project")] + addresses
        let cards = NavigationPresentation(try snapshot(source).content.records).review
        let message = NavigationReviewMessage(conversationId: "a-conversation", addressId: "current-worker", addressCurrent: true, ownerKind: "ticket", ownerId: "a", title: "A", waitingSince: 1, recap: "Worker recap", ownerReadThroughSequence: 0)
        XCTAssertEqual(cards, [
            .ticket(id: "a", title: "A", waitingSince: 1, proposal: NavigationReviewProposal(ticketId: "a", field: "plan", title: "A", waitingSince: 2, proposalField: "plan", proposalRevision: 1), messages: [message]),
            .message(NavigationReviewMessage(conversationId: "historical", addressId: "current-manager", addressCurrent: true, ownerKind: "manager", ownerId: "parent", title: "Parent", waitingSince: 2, recap: "", ownerReadThroughSequence: 0)),
        ])
        // Without the Manager's address, the historical Ticket address is canonical and read only.
        let replaced = NavigationPresentation(try snapshot(source.filter { $0["id"] as? String != "current-manager" }).content.records).review
        guard case .ticket(_, _, _, _, let messages) = try XCTUnwrap(replaced.first) else { return XCTFail("Expected a Ticket card") }
        XCTAssertEqual(replaced.count, 1)
        XCTAssertEqual(messages.map(\.conversationId), ["a-conversation", "historical"])
        XCTAssertEqual(messages.last?.addressCurrent, false)
        XCTAssertEqual(messages.last?.recap, "Worker recap")
        // A settled canonical Manager excludes its message and never falls back to the former Ticket.
        let settled = source.map { $0["id"] as? String == "parent" ? parentManager(settled: true) : $0 }
        let liveChild = NavigationPresentation(try snapshot(settled).content.records).review
        XCTAssertEqual(liveChild.count, 1)
        guard case .ticket(_, _, _, _, let childMessages) = liveChild[0] else { return XCTFail("Expected a Ticket card") }
        XCTAssertEqual(childMessages.map(\.conversationId), ["a-conversation"])
        func with(_ changes: [String: Any]) throws -> [NavigationReviewCard] {
            NavigationPresentation(try snapshot(settled.map { $0["kind"] as? String == "ticket" ? ticket(["ticket_status": "awaiting_approval", "recap": "Worker recap", "proposal_field": "plan", "proposal_revision": 1, "proposal_created_at": 2].merging(changes) { $1 }) : $0 }).content.records).review
        }
        XCTAssertEqual(try with(["is_backlogged": true]), [])
        XCTAssertEqual(try with(["stage": "done", "ticket_status": "empty"]), [])
        let messageOnly = try with(["ticket_status": "empty", "proposal_field": NSNull(), "proposal_created_at": NSNull()])
        guard case .ticket(_, _, _, let proposal, let only) = try XCTUnwrap(messageOnly.first) else { return XCTFail("Expected a Ticket card") }
        XCTAssertNil(proposal); XCTAssertEqual(only.map(\.conversationId), ["a-conversation"])

        // Server window order wins; a Ticket card stays one entry, proposal then messages,
        // and each part keeps the native identity that resolve and saved decisions use.
        let window = viewWindow("review", members: [["kind": "address", "id": "current-manager"], ["kind": "ticket", "id": "a"]], total: 3)
        let queue = NavigationPresentation(try snapshot(source).content.records).reviewQueue(in: try snapshot(source, windows: [window]))
        XCTAssertEqual(queue.cards.map(\.id), ["message:historical:2.0", "ticket:a"])
        XCTAssertEqual(queue.cards.map { $0.parts.map(\.id) }, [["message:historical:2.0"], ["a:plan:2.0", "message:a-conversation:1.0"]])
        XCTAssertEqual(queue.items[0].owner, .manager("parent"))
        XCTAssertEqual(queue.unloadedCount, 1)
        XCTAssertEqual(queue.runningWorkerCount, 1)

        // A decided proposal clears its card from the badge only when no message waits on it.
        let withMessage = NavigationPresentation(try snapshot(source).content.records)
        XCTAssertEqual(withMessage.reviewBadge(count: 2, pendingDecisionTickets: ["a"]), 2, "The Ticket card still holds a message")
        XCTAssertEqual(withMessage.reviewBadge(count: 2, pendingDecisionTickets: ["outside"]), 1, "A card outside the view clears")
        let proposalOnly = NavigationPresentation(try snapshot([pending, parentManager(), project("p", "Project")]).content.records)
        XCTAssertEqual(proposalOnly.reviewBadge(count: 1, pendingDecisionTickets: ["a"]), 0)
    }

    func testReviewOrderExcludesBackloggedAndAgentHeldProposals() throws {
        func ask(_ id: String, _ stamp: Int, _ changes: [String: Any] = [:]) -> [String: Any] {
            sourceTicket(["ticket_status": "awaiting_approval", "proposal_field": "plan", "gating_field": "implementation", "proposal_created_at": stamp, "proposal_revision": 1].merging(changes) { $1 }, id: id)
        }
        var manager = sourceManager
        manager["value"] = (sourceManager["value"] as! [String: Any]).merging(["conversation_id": "manager-current"]) { $1 }
        let records: [[String: Any]] = [
            ask("b", 2, ["is_backlogged": true, "blocked": true, "terminal": true, "worker_step_claim_out": true]),
            ask("c", 2), ask("a", 2), ask("earliest", 1), ask("manager", 0, ["ceiling_holder": ["kind": "chief", "id": "chief"]]),
            ask("claim-only", 0, ["ticket_status": "empty", "worker_step_claim_out": true, "conversation_id": NSNull()]),
            conversation("conversation"), manager, project("project", "Project"), sourceAddress,
            conversation("historical"), conversation("unlinked"), conversation("manager-current"), chiefRecord("conversation"),
            address("history", owner: ("ticket", "a"), conversation: "historical", current: false, parent: ("chief", "chief")),
        ]
        let cards = NavigationPresentation(try snapshot(records).content.records).review
        XCTAssertEqual(cards.map(\.member.id), ["a", "earliest", "c"])
        guard case .ticket(_, _, _, let proposal, _) = cards[1] else { return XCTFail("Expected a Ticket card") }
        XCTAssertEqual(proposal?.field, "implementation", "The Review field is the gating field")
        XCTAssertEqual(proposal?.proposalField, "plan")
    }

    // MARK: Request (owner.ts refreshDemand, read.py request)

    func testRequestSendsEveryFieldAndStaysWithinTheRowBudget() throws {
        let chiefChildren = ["active", "backlogged", "done"].map { filter -> ViewWindowRequest in
            var window = ViewWindowRequest(section: "children", owner: .chief, limit: 30); window.childFilter = filter; return window
        }
        let windows = [ViewWindowRequest(section: "settled", limit: 60)] + chiefChildren
            + (0..<8).map { ViewWindowRequest(section: "rail_tickets", owner: .manager("o\($0)"), limit: 60) }
        let initial = ViewRequest(windows: NavigationScope.baseWindows)
        let request = NavigationScope.request(demands: ["rail": windows], previous: initial)
        XCTAssertLessThanOrEqual(request.windows.reduce(0) { $0 + $1.limit }, 256)
        XCTAssertEqual(request.windows.filter { $0.section == "children" }.map(\.limit), [30, 30, 30])
        XCTAssertEqual(request.windows.first { $0.section == "settled" }?.limit, 60)
        XCTAssertEqual(request.scopeGeneration, 1)
        XCTAssertEqual(NavigationScope.request(demands: ["rail": windows], previous: request).scopeGeneration, 1, "An unchanged scope keeps its generation")

        let json = try JSONSerialization.jsonObject(with: Data(try request.encoded().utf8)) as! [String: Any]
        XCTAssertEqual(Set(json.keys), ["projection_version", "scope_generation", "locale", "selected", "selected_conversation_id", "review_conversation_ids", "review_skipped_keys", "review_priority_keys", "windows"])
        XCTAssertTrue(json["selected"] is NSNull)
        XCTAssertEqual(json["projection_version"] as? Int, 7)
        let review = try XCTUnwrap((json["windows"] as! [[String: Any]]).first)
        XCTAssertEqual(Set(review.keys), ["section", "owner", "group", "child_filter", "anchor", "direction", "limit"])
        XCTAssertTrue(review["owner"] is NSNull); XCTAssertTrue(review["anchor"] is NSNull)
    }

    // MARK: Fixtures

    private let authority: [String: Any] = ["delivery_session_id": "delivery", "availability": ["state": "current", "generation": "1", "reason": NSNull()],
                                            "permit": ["scope": "signal-workspace", "runtime_epoch": "producer", "installation_id": "installed", "generation": "1"]]
    private func coverage(_ revision: String) -> [String: Any] {
        ["scope": "workspace", "schema_version": 10, "semantics": "attention-knowledge-v10-ticket-workflows", "epoch": "e1", "revision": revision]
    }
    private func counts(review: Int = 0) -> [String: Any] { ["running_conversations": 1, "out_claims": 0, "review": review, "unread": 0, "settled": 0] }
    private func identity(_ event: String, session: String = "delivery") -> [String: Any] {
        var authority = self.authority; authority["delivery_session_id"] = session
        return ["event": event, "wire_version": 3, "projection_version": 7, "view_id": "view-0", "scope_generation": 0, "ordering_generation": "fixture", "authority": authority]
    }
    private func snapshot(_ records: [[String: Any]], windows: [[String: Any]] = []) throws -> ViewSnapshot {
        let value = identity("snapshot").merging(["coverage": coverage("0"), "content_version": "one", "mode": "current",
            "content": ["records": records, "windows": windows, "counts": counts(), "review_context": [], "review_summary": ["skipped": 0, "new": 0]]]) { $1 }
        guard case .snapshot(let snapshot) = try frame(value) else { throw URLError(.cannotDecodeContentData) }
        return snapshot
    }
    private func frame(_ fields: [String: Any], session: String = "delivery") throws -> ViewFrame {
        let value = identity(fields["event"] as! String, session: session).merging(fields) { $1 }
        return try APIClient.decoder().decode(ViewFrame.self, from: JSONSerialization.data(withJSONObject: value))
    }
    private func refusal(_ result: Result<ViewSnapshot, NavigationViewFrames.Refusal>) -> NavigationViewFrames.Refusal? {
        if case .failure(let refusal) = result { return refusal }
        return nil
    }
    private func viewWindow(_ section: String, members: [[String: Any]], total: Int? = nil) -> [String: Any] {
        ["window_id": section, "request": ["section": section, "owner": NSNull(), "group": "all", "child_filter": "all", "anchor": NSNull(), "direction": "after", "limit": 30],
         "members": members, "total": total ?? members.count, "previous": NSNull(), "next": NSNull(), "forward_overlap": NSNull(), "backward_overlap": NSNull()]
    }
    /// fixtures.ts conversation(): working, awaiting an answer, unread and failed by default.
    private func conversation(_ id: String, read: Int = 0, message: Bool = true, failure: Bool = true, unreadAt: Int = 1) -> [String: Any] {
        ["kind": "conversation", "id": id, "value": ["first_unread_owner_message_at": message ? unreadAt as Any : NSNull(), "owner_read_through_sequence": read, "error_acknowledged_through_sequence": 0,
            "attention": ["current": ["signal": ["working": true, "message_for_owner": message], "awaiting_answer": true, "last_turn_failed": failure],
                          "durable": ["message_for_owner": message, "last_turn_failed": failure]]]]
    }
    private func conversationValue(_ id: String) throws -> ViewConversationValue {
        try APIClient.decoder().decode(ViewConversationValue.self, from: JSONSerialization.data(withJSONObject: conversation(id)["value"]!))
    }
    private func project(_ id: String, _ name: String) -> [String: Any] { ["kind": "project", "id": id, "value": ["name": name]] }
    private func chiefRecord(_ conversation: String?) -> [String: Any] { ["kind": "chief", "id": "chief", "value": ["label": "Chief", "conversation_id": conversation as Any? ?? NSNull()]] }
    private func address(_ id: String, owner: (String, String), conversation: String, current: Bool, parent: (String, String)?) -> [String: Any] {
        ["kind": "address", "id": id, "value": ["owner_kind": owner.0, "owner_id": owner.1, "conversation_id": conversation, "current": current,
                                                "parent_kind": parent?.0 as Any? ?? NSNull(), "parent_id": parent?.1 as Any? ?? NSNull()]]
    }
    /// navigation-application.test.ts `ticket` and its parent.
    private func ticket(_ changes: [String: Any] = [:]) -> [String: Any] {
        let value: [String: Any] = ["title": "A", "recap": "", "recap_preview": "", "worker_type": "coding", "stage_label": "Plan", "workflow_label": "Coding", "stage": "needs_plan",
            "ticket_status": "empty", "blocked": false, "created_at": 1, "updated_at": 1, "worker_step_claim_out": false, "deadline": NSNull(), "project_id": "p",
            "manager_kind": "manager", "manager_id": "parent", "conversation_id": "a-conversation", "is_backlogged": false, "ceiling": "needs_plan",
            "ceiling_holder": ["kind": "owner", "id": "owner"], "gating_field": "plan", "proposal_field": NSNull(), "proposal_revision": 0, "proposal_created_at": NSNull(),
            "stage_ownership": "worker", "terminal": false, "locale": "macos", "recap_reference": NSNull()]
        return ["kind": "ticket", "id": "a", "value": value.merging(changes) { $1 }]
    }
    private func parentManager(settled: Bool = false) -> [String: Any] {
        ["kind": "manager", "id": "parent", "value": ["title": "Parent", "recap": "", "recap_preview": "", "created_at": 1, "updated_at": 1, "project_id": "p", "settled": settled, "conversation_id": NSNull(), "recap_reference": NSNull()]]
    }
    /// workspace-rail-projection.test.ts `sourceTicket` and `sourceRecords`.
    private func sourceTicket(_ changes: [String: Any] = [:], id: String = "source-ticket") -> [String: Any] {
        let value: [String: Any] = ["title": "Source Ticket", "recap": "Full source recap.\n" + String(repeating: "Body continues. ", count: 20), "recap_preview": "Source recap",
            "worker_type": "coding", "stage_label": "Implementation", "workflow_label": "Coding", "stage": "needs_implementation", "ticket_status": "empty", "blocked": false,
            "created_at": 1, "updated_at": 11, "worker_step_claim_out": false, "deadline": NSNull(), "project_id": "project", "manager_kind": "manager", "manager_id": "item",
            "conversation_id": "conversation", "is_backlogged": false, "ceiling": "needs_implementation", "ceiling_holder": ["kind": "owner", "id": "owner"],
            "gating_field": "implementation", "proposal_field": NSNull(), "proposal_revision": 0, "proposal_created_at": NSNull(), "stage_ownership": "worker",
            "terminal": false, "locale": "en", "recap_reference": NSNull()]
        return ["kind": "ticket", "id": id, "value": value.merging(changes) { $1 }]
    }
    private var sourceManager: [String: Any] {
        ["kind": "manager", "id": "item", "value": ["title": "Parent", "recap": "Full parent recap.\n" + String(repeating: "Body continues. ", count: 20), "recap_preview": "Parent recap",
                                                    "created_at": 1, "updated_at": 11, "project_id": "project", "settled": false, "conversation_id": NSNull(), "recap_reference": NSNull()]]
    }
    private var sourceAddress: [String: Any] { address("address", owner: ("ticket", "source-ticket"), conversation: "conversation", current: true, parent: ("manager", "item")) }
}
