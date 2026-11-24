import XCTest
@testable import Panels

/// Fixtures transcribe web/tests/support/navigation/fixtures.ts and the synthetic
/// records of web/tests/navigation-*.test.ts and workspace-rail-projection.test.ts,
/// so the phone's derivation is checked against the cases desktop already pins.
final class NavigationViewTests: XCTestCase {
    // MARK: Frame application (web/src/lib/navigation/views/applyFrame.ts)

    func testSettledWindowKeepsLeanMembersAndSignalsFromItsOwnSnapshot() throws {
        let first: [String: Any] = ["kind": "manager", "id": "first", "value": ["title": "First", "created_at": 1, "updated_at": 2, "settled": true, "conversation_id": "c"]]
        let second: [String: Any] = ["kind": "manager", "id": "second", "value": ["title": "Second", "created_at": 2, "updated_at": 3, "settled": true, "conversation_id": NSNull()]]
        let window = viewWindow("settled", members: [["kind": "manager", "id": "second"], ["kind": "manager", "id": "first"]])
        let initial = try snapshot([first, second, parentManager(), conversation("c")], windows: [window])
        let rows = try XCTUnwrap(NavigationPresentation(initial.content.records).workspaceSettledManagers(in: initial))
        XCTAssertEqual(rows.map(\.id), ["second", "first"], "Window membership excludes another loaded Manager and owns order")
        XCTAssertEqual(rows.last?.project, "", "Lean records do not invent a project")
        XCTAssertEqual(rows.last?.conversationSignal?.messageForOwner, true)
        let read = try snapshot([first, second, parentManager(), conversation("c", message: false)], windows: [window])
        let updated = try XCTUnwrap(NavigationPresentation(read.content.records).workspaceSettledManagers(in: read))
        XCTAssertEqual(updated.last?.conversationSignal?.messageForOwner, false)
    }

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

    @MainActor func testCurrentOwnerReferencesDistinguishUnknownFromClearedAndReplacement() async throws {
        let base = URL(string: "https://owner-references-\(UUID().uuidString).invalid")!
        let unknown = AppStore(baseURL: base)
        let owners = [ConversationOwner.chief, .manager("item"), .ticket("source-ticket")]
        for owner in owners { XCTAssertNil(unknown.currentConversationID(owner)) }
        for expected in ["old", nil, "new"] as [String?] {
            var manager = sourceManager
            manager["value"] = (sourceManager["value"] as! [String: Any]).merging(["conversation_id": expected as Any? ?? NSNull()]) { $1 }
            let value = try snapshot([chiefRecord(expected), manager,
                sourceTicket(["conversation_id": expected as Any? ?? NSNull()]), project("project", "Project")])
            let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
            try await LocalStore.shared.cacheResponse(server: LocalStore.server(base),
                id: base.absoluteString + "/api/navigation/view/snapshot#latest", data: encoder.encode(value))
            let restored = AppStore(baseURL: base)
            for owner in owners {
                let current = try XCTUnwrap(restored.currentConversationID(owner), "The view carries this owner even when its link clears")
                XCTAssertEqual(current, expected)
            }
        }
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

        let agentApproval = sourceTicket(["stage": "done", "terminal": true, "ticket_status": "awaiting_approval", "ceiling_holder": ["kind": "owner", "id": "another"]])
        let parked = try XCTUnwrap(NavigationPresentation(try snapshot([agentApproval]).content.records).tickets.first)
        XCTAssertFalse(parked.awaitingApproval); XCTAssertTrue(parked.awaitingAgentApproval)
        XCTAssertNil(parked.agentState, "No conversation record: unknown, never idle")
        XCTAssertEqual(parked.summary.agentState, "idle")

    }

    func testTicketLocaleSurvivesDecodeAndMappingBeforeRuntimeMark() throws {
        // The canonical navigation Ticket fixture identifies its machine even when
        // the server supplies current conversation facts. The phone's executor
        // health still gates the Mac working mark; durable unread survives.
        let records = try snapshot([ticket(), conversation("a-conversation")]).content.records
        guard case .ticket(_, let decoded) = records[0] else { return XCTFail("Expected Ticket") }
        XCTAssertEqual(decoded.locale, "macos")
        let presented = try XCTUnwrap(NavigationPresentation(records).tickets.first)
        XCTAssertEqual(presented.locale, "macos")
        let summary = presented.summary
        XCTAssertEqual(summary.locale, "macos")
        XCTAssertEqual(summary.machineId, decoded.machineId, "The rail keeps the exact machine identity from its view record")
        let unavailable = try XCTUnwrap(TicketMachine.signal(summary.conversationSignal, locale: summary.locale, health: nil))
        XCTAssertFalse(unavailable.working)
        XCTAssertTrue(unavailable.messageForOwner)
        let ready = ExecutorHealth(mode: "remote", identity: .init(executorIncarnation: "mac", connectionGeneration: 2), connectionState: "ready", lastContactAtMs: 1000)
        XCTAssertTrue(try XCTUnwrap(TicketMachine.signal(summary.conversationSignal, locale: summary.locale, health: ready)).working)

        XCTAssertFalse(TicketMachine.conversationActivityUnknown(nil, owner: .chief, health: nil))
        XCTAssertFalse(TicketMachine.conversationActivityUnknown(nil, owner: .manager("manager"), health: nil))
        XCTAssertTrue(TicketMachine.conversationActivityUnknown(nil, owner: .ticket("ticket"), health: ready))

        for locale in ["future-machine", nil] as [String?] {
            var record = ticket()
            var value = record["value"] as! [String: Any]
            value["locale"] = locale
            record["value"] = value
            let mapped = try XCTUnwrap(NavigationPresentation(try snapshot([record, conversation("a-conversation")]).content.records).tickets.first).summary
            XCTAssertEqual(mapped.locale, locale)
            let shown = try XCTUnwrap(TicketMachine.signal(mapped.conversationSignal, locale: mapped.locale, health: ready))
            XCTAssertFalse(shown.working, "Unknown machine identity grants no runtime authority")
            XCTAssertTrue(shown.messageForOwner)
        }
    }

    // MARK: Review queue (presentation.ts, ReviewRoute.svelte)

    func testReviewQueueUsesCanonicalAddressesOldestWaitAndWindowOrder() throws {
        let pending = ticket(["ticket_status": "awaiting_approval", "recap": "Worker recap", "proposal_field": "plan", "proposal_revision": 1, "proposal_created_at": 2])
        let historical = conversation("historical", unreadAt: 2, awaiting: false)
        let current = conversation("a-conversation", unreadAt: 1, awaiting: false)
        let addresses = [
            address("old-worker", owner: ("ticket", "a"), conversation: "historical", current: false, parent: ("manager", "parent")),
            address("current-manager", owner: ("manager", "parent"), conversation: "historical", current: true, parent: nil),
            address("current-worker", owner: ("ticket", "a"), conversation: "a-conversation", current: true, parent: ("manager", "parent")),
        ]
        let source = [pending, current, historical, parentManager(), project("p", "Project")] + addresses
        let entries = NavigationPresentation(try snapshot(source).content.records).review
        let nativeSnapshot = try snapshot(source)
        let nativePart = try XCTUnwrap(NavigationPresentation(nativeSnapshot.content.records).reviewQueue(in: nativeSnapshot).items.first { $0.isProposal })
        XCTAssertEqual(nativePart.proposalRevision, 1)
        let newerSource = source.map { $0["kind"] as? String == "ticket" ? ticket(["ticket_status": "awaiting_approval", "proposal_field": "plan", "proposal_revision": 2, "proposal_created_at": 2]) : $0 }
        let newerSnapshot = try snapshot(newerSource)
        let newerPart = try XCTUnwrap(NavigationPresentation(newerSnapshot.content.records).reviewQueue(in: newerSnapshot).items.first { $0.isProposal })
        XCTAssertEqual(newerPart.waitingSince, nativePart.waitingSince)
        XCTAssertEqual(newerPart.proposalRevision, 2)
        XCTAssertNotEqual(newerPart.id, nativePart.id)
        let message = NavigationReviewMessage(conversationId: "a-conversation", addressId: "current-worker", addressCurrent: true, ownerKind: "ticket", ownerId: "a", title: "A", waitingSince: 1, recap: "Worker recap", ownerReadThroughSequence: 0)
        XCTAssertEqual(entries, [
            NavigationReviewEntry(member: RecordKey(kind: "ticket", id: "a"), waitingSince: 1,
                proposal: NavigationReviewProposal(ticketId: "a", field: "plan", title: "A", waitingSince: 2, proposalField: "plan", proposalRevision: 1), messages: [message]),
            NavigationReviewEntry(member: RecordKey(kind: "address", id: "current-manager"), waitingSince: 2,
                messages: [NavigationReviewMessage(conversationId: "historical", addressId: "current-manager", addressCurrent: true, ownerKind: "manager", ownerId: "parent", title: "Parent", waitingSince: 2, recap: "", ownerReadThroughSequence: 0)]),
        ])
        // Without the Manager's address, the historical Ticket address is canonical and read only.
        let replaced = NavigationPresentation(try snapshot(source.filter { $0["id"] as? String != "current-manager" }).content.records).review
        XCTAssertEqual(replaced.count, 1)
        XCTAssertEqual(replaced[0].messages.map(\.conversationId), ["a-conversation", "historical"])
        XCTAssertEqual(replaced[0].messages.last?.addressCurrent, false)
        XCTAssertEqual(replaced[0].messages.last?.recap, "Worker recap")
        // A settled canonical Manager excludes its message and never falls back to the former Ticket.
        let settled = source.map { $0["id"] as? String == "parent" ? parentManager(settled: true) : $0 }
        let liveChild = NavigationPresentation(try snapshot(settled).content.records).review
        XCTAssertEqual(liveChild.count, 1)
        XCTAssertEqual(liveChild[0].messages.map(\.conversationId), ["a-conversation"])
        func with(_ changes: [String: Any]) throws -> [NavigationReviewEntry] {
            NavigationPresentation(try snapshot(settled.map { $0["kind"] as? String == "ticket" ? ticket(["ticket_status": "awaiting_approval", "recap": "Worker recap", "proposal_field": "plan", "proposal_revision": 1, "proposal_created_at": 2].merging(changes) { $1 }) : $0 }).content.records).review
        }
        XCTAssertEqual(try with(["is_backlogged": true]), [])
        XCTAssertEqual(try with(["stage": "done", "ticket_status": "empty"]), [])
        let messageOnly = try with(["ticket_status": "empty", "proposal_field": NSNull(), "proposal_created_at": NSNull()])
        XCTAssertNil(messageOnly.first?.proposal); XCTAssertEqual(messageOnly.first?.messages.map(\.conversationId), ["a-conversation"])

        // Server window order wins, and each entry splits into one card per purpose,
        // proposal first, each keeping the native identity that saved decisions use.
        let window = viewWindow("review", members: [["kind": "address", "id": "current-manager"], ["kind": "ticket", "id": "a"]], total: 3)
        let queue = NavigationPresentation(try snapshot(source).content.records).reviewQueue(in: try snapshot(source, windows: [window]))
        XCTAssertEqual(queue.items.map(\.id), ["message:historical:2.0", "a:plan:2.0:revision:1", "message:a-conversation:1.0"])
        XCTAssertEqual(queue.items.map(\.historyKey), [RecordKey(kind: "conversation", id: "historical"), RecordKey(kind: "ticket", id: "a"), RecordKey(kind: "ticket", id: "a")])
        XCTAssertEqual(queue.items[0].owner, .manager("parent"))
        XCTAssertEqual(queue.unloadedCount, 1)
        XCTAssertEqual(queue.runningWorkerCount, 1)

        // A decided proposal clears its entry from the badge only when nothing else waits on it.
        let withMessage = NavigationPresentation(try snapshot(source).content.records)
        XCTAssertEqual(withMessage.reviewBadge(count: 2, pendingDecisionTickets: ["a"]), 2, "The Ticket entry still holds a message")
        XCTAssertEqual(withMessage.reviewBadge(count: 2, pendingDecisionTickets: ["outside"]), 1, "An entry outside the view clears")
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
            conversation("conversation", awaiting: false), manager, project("project", "Project"), sourceAddress,
            conversation("historical"), conversation("unlinked"), conversation("manager-current"), chiefRecord("conversation"),
            address("history", owner: ("ticket", "a"), conversation: "historical", current: false, parent: ("chief", "chief")),
        ]
        let cards = NavigationPresentation(try snapshot(records).content.records).review
        XCTAssertEqual(cards.map(\.member.id), ["a", "earliest", "c"])
        XCTAssertEqual(cards[1].proposal?.field, "implementation", "The Review field is the gating field")
        XCTAssertEqual(cards[1].proposal?.proposalField, "plan")
    }

    /// Risk: an ask the server admits to the review window never reaches the phone, so the
    /// badge counts work the queue cannot show. A Ticket's ask is its own card after its
    /// proposal and messages; a Manager's or the Chief's joins its conversation's address.
    /// A saved view keeps no runtime facts, so there a member with nothing else waiting
    /// is shown as its owner's ask.
    func testEveryEntrySplitsIntoOneCardPerPurposeIncludingAskOnlyMembers() throws {
        let worker = address("worker", owner: ("ticket", "a"), conversation: "a-conversation", current: true, parent: ("manager", "parent"))
        let chiefAddress = address("chief-address", owner: ("chief", "chief"), conversation: "chief-conversation", current: true, parent: nil)
        let records = [ticket(["ticket_status": "awaiting_approval", "proposal_field": "plan", "proposal_revision": 1, "proposal_created_at": 2]),
                       conversation("a-conversation", unreadAt: 1, askAt: 3), worker, parentManager(), project("p", "Project"),
                       chiefRecord("chief-conversation"), conversation("chief-conversation", message: false, askAt: 4), chiefAddress]
        let window = viewWindow("review", members: [["kind": "ticket", "id": "a"], ["kind": "address", "id": "chief-address"]])
        let live = try snapshot(records, windows: [window])
        let queue = NavigationPresentation(live.content.records).reviewQueue(in: live)
        XCTAssertEqual(queue.items.map(\.reviewItemType), ["proposal", "message", "ask", "ask"])
        XCTAssertEqual(queue.items.map(\.id), ["a:plan:2.0:revision:1", "message:a-conversation:1.0", "ask:a-conversation:3.0", "ask:chief-conversation:4.0"])
        XCTAssertEqual(queue.items[2].owner, .ticket("a"))
        XCTAssertEqual(queue.items[3].owner, .chief)
        XCTAssertEqual(queue.items[3].historyKey, RecordKey(kind: "conversation", id: "chief-conversation"))

        // Answered: the live view knows no ask waits, and a member alone shows nothing.
        let answered = try snapshot(records.map { $0["id"] as? String == "chief-conversation" ? conversation("chief-conversation", message: false, awaiting: false, askAt: 4) : $0 }, windows: [window])
        XCTAssertEqual(NavigationPresentation(answered.content.records).reviewQueue(in: answered).items.map(\.reviewItemType), ["proposal", "message", "ask"])

        // The saved copy at launch: no runtime facts, so only the ask-only member shows its ask.
        let saved = live.savedDisplay
        let restored = NavigationPresentation(saved.content.records).reviewQueue(in: saved)
        XCTAssertEqual(restored.items.map(\.id), ["a:plan:2.0:revision:1", "message:a-conversation:1.0", "ask:chief-conversation:4.0"])
    }

    /// Risk: offline, a card the phone never read can act only on what the saved view kept.
    /// The Mark read bound and the waiting ask must survive the saved copy's encode, its
    /// runtime-fact strip and its decode at launch, and reach each card. A record without
    /// them, from an older server, decodes as before.
    func testSavedViewKeepsTheReadBoundAndTheAskForUnreadCards() throws {
        let ask: [String: Any] = ["kind": "user_input", "request_id": "request-1", "questions": [["question_id": "colour", "header": "", "question": "Which colour?",
            "options": [["label": "Blue", "description": "Calm"]], "multi_select": false, "allow_other": true]]]
        let worker = address("worker", owner: ("ticket", "a"), conversation: "a-conversation", current: true, parent: ("manager", "parent"))
        let chiefAddress = address("chief-address", owner: ("chief", "chief"), conversation: "chief-conversation", current: true, parent: nil)
        let records = [ticket([:]), conversation("a-conversation", unreadAt: 1, latestOwnerMessage: 7), worker, parentManager(), project("p", "Project"),
                       chiefRecord("chief-conversation"), conversation("chief-conversation", message: false, askAt: 4, pendingAsk: ask), chiefAddress]
        let window = viewWindow("review", members: [["kind": "ticket", "id": "a"], ["kind": "address", "id": "chief-address"]])
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        let saved = try APIClient.decoder().decode(ViewSnapshot.self, from: encoder.encode(try snapshot(records, windows: [window]).savedDisplay)).savedDisplay
        let items = NavigationPresentation(saved.content.records).reviewQueue(in: saved).items
        XCTAssertEqual(items.map(\.id), ["message:a-conversation:1.0", "ask:chief-conversation:4.0"])
        XCTAssertEqual(items[0].latestOwnerMessageSequence, 7)
        let question = try XCTUnwrap(items[1].pendingAsk?.pendingUserInput?.questions.first)
        XCTAssertEqual(items[1].pendingAsk?.pendingUserInput?.requestId, "request-1")
        XCTAssertEqual(question.options.map(\.label), ["Blue"])
        XCTAssertTrue(question.allowOther)
        XCTAssertEqual(items[1].pendingAsk?.isRunning, true)
        XCTAssertNil(try conversationValue("a-conversation").latestOwnerMessageSequence)
        XCTAssertNil(try conversationValue("a-conversation").pendingAsk)
    }

    // MARK: Request (owner.ts refreshDemand, read.py request)

    func testRequestSendsEveryFieldAndStaysWithinTheRowBudget() throws {
        let chiefChildren = ["active", "backlogged", "done"].map { filter -> ViewWindowRequest in
            var window = ViewWindowRequest(section: "children", owner: .chief, limit: 30); window.childFilter = filter; return window
        }
        let windows = [ViewWindowRequest(section: "settled", limit: 60)] + chiefChildren
            + (0..<8).map { ViewWindowRequest(section: "rail_tickets", owner: .manager("o\($0)"), limit: 60) }
        let initial = ViewRequest(windows: NavigationScope.baseWindows)
        let history = ReviewHistory(skipped: [RecordKey(kind: "ticket", id: "a"), RecordKey(kind: "conversation", id: "c")], priority: [RecordKey(kind: "ticket", id: "a")])
        let request = NavigationScope.request(demands: ["rail": windows], reviewHistory: history, previous: initial)
        XCTAssertLessThanOrEqual(request.windows.reduce(0) { $0 + $1.limit }, 256)
        XCTAssertTrue(request.reviewConversationIds.isEmpty)
        XCTAssertEqual(request.windows.filter { $0.section == "children" }.map(\.limit), [30, 30, 30])
        XCTAssertEqual(request.windows.first { $0.section == "settled" }?.limit, 60)
        XCTAssertEqual(request.scopeGeneration, 1)
        XCTAssertEqual(NavigationScope.request(demands: ["rail": windows], reviewHistory: history, previous: request).scopeGeneration, 1, "An unchanged scope keeps its generation")

        let json = try JSONSerialization.jsonObject(with: Data(try request.encoded().utf8)) as! [String: Any]
        XCTAssertEqual(Set(json.keys), ["projection_version", "scope_generation", "locale", "selected", "selected_conversation_id", "review_conversation_ids", "review_skipped_keys", "review_priority_keys", "windows"])
        XCTAssertEqual(json["review_skipped_keys"] as? [[String: String]], [["kind": "ticket", "id": "a"], ["kind": "conversation", "id": "c"]])
        XCTAssertEqual(json["review_priority_keys"] as? [[String: String]], [["kind": "ticket", "id": "a"]])
        let released = NavigationScope.request(demands: ["rail": windows], previous: request)
        XCTAssertTrue(released.reviewSkippedKeys.isEmpty && released.reviewPriorityKeys.isEmpty)
        XCTAssertEqual(released.scopeGeneration, 2)
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
        ["scope": "workspace", "schema_version": 11, "semantics": "attention-knowledge-v11-recap-freshness", "epoch": "e1", "revision": revision]
    }
    private func counts(review: Int = 0) -> [String: Any] { ["running_conversations": 1, "out_claims": 0, "review": review, "unread": 0, "settled": 0] }
    private func identity(_ event: String, session: String = "delivery") -> [String: Any] {
        var authority = self.authority; authority["delivery_session_id"] = session
        return ["event": event, "wire_version": 3, "projection_version": 7, "view_id": "view-0", "scope_generation": 0, "ordering_generation": "fixture", "authority": authority]
    }
    private func snapshot(_ records: [[String: Any]], windows: [[String: Any]] = [], reviewContext: [[String: Any]] = []) throws -> ViewSnapshot {
        let value = identity("snapshot").merging(["coverage": coverage("0"), "content_version": "one", "mode": "current",
            "content": ["records": records, "windows": windows, "counts": counts(), "review_context": reviewContext, "review_summary": ["skipped": 0, "new": 0]]]) { $1 }
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
    private func conversation(_ id: String, read: Int = 0, message: Bool = true, failure: Bool = true, unreadAt: Int = 1, awaiting: Bool = true, askAt: Int? = nil,
                              latestOwnerMessage: Int? = nil, pendingAsk: [String: Any]? = nil) -> [String: Any] {
        var value: [String: Any] = ["first_unread_owner_message_at": message ? unreadAt as Any : NSNull(), "owner_read_through_sequence": read, "error_acknowledged_through_sequence": 0,
            "latest_ask_at": askAt as Any? ?? NSNull(),
            "attention": ["current": ["signal": ["working": true, "message_for_owner": message], "awaiting_answer": awaiting, "last_turn_failed": failure],
                          "durable": ["message_for_owner": message, "last_turn_failed": failure]]]
        if let latestOwnerMessage { value["latest_owner_message_sequence"] = latestOwnerMessage }
        if let pendingAsk { value["pending_ask"] = pendingAsk }
        return ["kind": "conversation", "id": id, "value": value]
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
        let value: [String: Any] = ["title": "A", "recap": "", "recap_preview": "", "recap_updated_at": NSNull(), "recap_stage_changed": false, "machine_id": "ee17b608-a449-4966-a808-a791ba7df1c7", "worker_type": "coding", "stage_label": "Plan", "workflow_label": "Coding", "stage": "needs_plan",
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
        let value: [String: Any] = ["title": "Source Ticket", "recap": "Full source recap.\n" + String(repeating: "Body continues. ", count: 20), "recap_preview": "Source recap", "recap_updated_at": NSNull(), "recap_stage_changed": false, "machine_id": "75a49e78-97de-4e28-8dc5-79646761c46f",
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
