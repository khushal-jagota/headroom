import XCTest
@testable import Panels

/// No live writes. The real adapter runs against URLProtocol's in-memory transport.
@MainActor final class ReviewDecisionTests: XCTestCase {
    // A stale deployment must never look healthy or reserve permanent screen space.
    func testDeploymentStatusExpiresAndPreservesFailure() {
        let now = Date(timeIntervalSince1970: 1_000)
        func status(_ state: String, expiry: String? = "1970-01-01T00:20:00Z", outcome: String? = nil) -> DeploymentStatus {
            DeploymentStatus(state: state, deployedSha: nil, targetSha: nil, outcome: outcome, detail: nil, validUntil: expiry, successDisplayUntil: expiry)
        }
        func visible(_ value: DeploymentStatus, connected: Bool = true, at date: Date = Date(timeIntervalSince1970: 1_000)) -> DeploymentStatusPresentation? {
            DeploymentStatusPresentation.resolve(value, connected: connected, readFailed: false, now: date)
        }
        XCTAssertNil(visible(status("idle")))
        XCTAssertEqual(visible(status("preparing")), .deploying)
        XCTAssertEqual(visible(status("preparing", expiry: nil)), .reconnecting)
        XCTAssertEqual(visible(status("back_up")), .deployed)
        XCTAssertNil(visible(status("back_up"), at: now.addingTimeInterval(201)))
        XCTAssertEqual(visible(status("back_up"), connected: false), .reconnecting)
        XCTAssertEqual(visible(status("idle", outcome: "rolled_back")), .problem)
    }

    private func fixture(_ name: String) throws -> Data {
#if SWIFT_PACKAGE
        try Data(contentsOf: XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")))
#else
        try Data(contentsOf: XCTUnwrap(Bundle(for: Self.self).url(forResource: name, withExtension: "json")))
#endif
    }
    private func assertSharedDecisionBody(_ body: [String: Any], named name: String) throws {
        let requests = try XCTUnwrap(JSONSerialization.jsonObject(with: fixture("review-decision-requests")) as? [String: Any])
        let expected = try XCTUnwrap(requests[name] as? NSDictionary)
        XCTAssertEqual(body as NSDictionary, expected, "Actual native request must match the server contract fixture")
    }
    private func assertProtectedDecisionBody(_ body: [String: Any], named name: String,
                                             decision: String, field: String) throws {
        var legacyShape = body
        XCTAssertEqual(legacyShape.removeValue(forKey: "decision") as? String, decision)
        XCTAssertEqual(legacyShape.removeValue(forKey: "field") as? String, field)
        try assertSharedDecisionBody(legacyShape, named: name)
    }
    private func setupDecision() async throws -> (ReviewDecision, APIClient, ReviewItem) {
        for kind in ["cache", "draft", "outbox"] {
            for entry in try LocalStore.shared.records(server: "https://review.invalid", kind: kind) {
                try LocalStore.shared.remove(server: "https://review.invalid", kind: kind, id: entry.key.components(separatedBy: "\u{1f}").last!)
            }
        }
        let ticket = try fixture("ticket-full")
        let detail = try APIClient.decoder().decode(TicketDetail.self, from: ticket)
        let proposal = try XCTUnwrap(detail.pendingProposal)
        ReviewStub.state.reset(ticket: ticket, manifest: Data("""
        {"worker_types":[{"worker_type":"\(detail.workerType ?? "")","stages":[{"id":"\(detail.stage)","label":"Current"},{"id":"needs_implementation","label":"Next gate"},{"id":"done","label":"Done"}],"advance":{"\(detail.stage)":"needs_implementation"},"fields":[{"id":"\(proposal.field)","label":"Proposal"}],"ceiling_range":["\(detail.stage)","needs_implementation","done"]}]}
        """.utf8))
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ReviewStub.self]
        let api = APIClient(baseURL: URL(string: "https://review.invalid")!, session: URLSession(configuration: configuration))
        let item = ReviewItem(reviewItemType: "proposal", ticketId: detail.id, field: proposal.field, title: detail.title, waitingSince: proposal.createdAt)
        let decision = ReviewDecision()
        await decision.load(item: item, api: api)
        XCTAssertTrue(decision.ready)
        return (decision, api, item)
    }

    /// The card never names the connection: a dropped read keeps the loaded proposal
    /// ready, with no error under its buttons, and Approve still saves at once.
    func testDroppedTicketReadShowsNoErrorAndKeepsTheCardReady() async throws {
        let (decision, api, item) = try await setupDecision()
        ReviewStub.state.failNextTicketRead()
        await decision.load(item: item, api: api)
        XCTAssertNil(decision.error)
        XCTAssertTrue(decision.ready)
        let saved = await decision.approve(api: api, ceiling: "needs_implementation", holder: Principal(kind: "owner", id: "owner"))
        XCTAssertTrue(saved)
        XCTAssertNil(decision.error)
    }

    func testReadOnlyApprovalIgnoresLegacyEditsAndCarriesExactRevisionAndNextGate() async throws {
        let (original, api, item) = try await setupDecision()
        let revision = try XCTUnwrap(original.proposal?.revision)
        let identity = DecisionGuidance.draftIdentity(ticketID: item.ticketId, proposal: try XCTUnwrap(original.proposal))
        LocalStore.shared.saveDraft("An old proposal edit", server: "https://review.invalid", id: identity + ":edit")
        let decision = ReviewDecision()
        await decision.load(item: item, api: api)
        XCTAssertEqual(decision.approvalStages.map(\.id), decision.manifest?.stages.map(\.id) ?? [])
        XCTAssertEqual(decision.nextStage, "needs_implementation")
        let accepted = await decision.approve(api: api, ceiling: "done", holder: Principal(kind: "owner", id: "owner"))
        XCTAssertTrue(accepted)
        await settle(api)
        let request = try XCTUnwrap(ReviewStub.state.protectedWrites.first)
        XCTAssertTrue(request.url!.path.contains("/decision-operations/"))
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: Any])
        XCTAssertEqual(body["expected_proposal_revision"] as? Int, revision)
        let command = try APIClient.decoder().decode(DecisionCommand.self, from: XCTUnwrap(request.httpBody))
        XCTAssertEqual(command.decision, "approve")
        XCTAssertEqual(command.field, "wireframe")
        XCTAssertNil(command.editedBody, "Read-only proposals never submit a legacy edit")
        XCTAssertEqual(command.nextCeiling, "done")
        XCTAssertEqual(command.nextHolder?.kind, "owner")
        XCTAssertEqual(command.nextHolder?.id, "owner")
        let saved = try XCTUnwrap(Outbox.shared.entries("https://review.invalid").last)
        XCTAssertEqual(saved.ticketTitle, decision.ticket?.title)
        XCTAssertEqual(saved.proposalFieldLabel, "Proposal")
        XCTAssertEqual(saved.protectedDecisionBody, request.httpBody)
        XCTAssertNil(saved.proposal); XCTAssertNil(saved.approval); XCTAssertNil(saved.guidance)
        // Previously saved operations omit both optional attribution fields.
        var legacy = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(saved)) as? [String: Any])
        legacy.removeValue(forKey: "ticketTitle"); legacy.removeValue(forKey: "proposalFieldLabel")
        let restored = try JSONDecoder().decode(PendingWork.self, from: JSONSerialization.data(withJSONObject: legacy))
        XCTAssertEqual(restored.ticketID, saved.ticketID)
        XCTAssertNil(restored.ticketTitle)
        let repeated = await decision.approve(api: api, ceiling: "needs_implementation", holder: Principal(kind: "owner", id: "owner"))
        XCTAssertFalse(repeated)
        XCTAssertEqual(ReviewStub.state.protectedWrites.count, 1)
        XCTAssertTrue(ReviewStub.state.writes.isEmpty)

    }

    func testScopeChoiceAndReviewerOnlyChangeReadBackWithoutApprovingProposal() async throws {
        let (decision, api, _) = try await setupDecision()
        let ticket = try XCTUnwrap(decision.ticket)
        let proposal = try XCTUnwrap(decision.proposal)
        let policy = try APIClient.decoder().decode(ReviewWorkerManifest.self, from: Data("""
        {"stages":[{"id":"earlier","label":"Earlier"},{"id":"waiting","label":"Waiting"},{"id":"next","label":"Next"},{"id":"done","label":"Done"}],"advance":{"waiting":"next"},"fields":[],"ceiling_range":["earlier","waiting","next","done"]}
        """.utf8))
        XCTAssertFalse(policy.selectionApprovesPending(ceiling: "earlier", waitingStage: "waiting"))
        XCTAssertFalse(policy.selectionApprovesPending(ceiling: "waiting", waitingStage: "waiting"))
        XCTAssertTrue(policy.selectionApprovesPending(ceiling: "next", waitingStage: "waiting"))
        XCTAssertTrue(policy.selectionApprovesPending(ceiling: "done", waitingStage: "waiting"))
        XCTAssertEqual(policy.approvalStages(after: "waiting").map(\.id), ["next", "done"], "The approve sheet offers only stages that approve the waiting proposal")
        decision.revisionDraft = "Keep this proposal note"
        let readCount = ReviewStub.state.scopeTicketReads.count
        let chief = Principal(kind: "chief", id: "chief")
        let current = try await decision.scopeChange.save(api: api, ticket: ticket, ceiling: ticket.stage, holder: chief)
        decision.acceptScopeRead(current)
        XCTAssertEqual(current.ceiling, ticket.stage)
        XCTAssertEqual(current.ceilingHolder, chief)
        XCTAssertEqual(current.pendingProposal?.revision, proposal.revision)
        XCTAssertEqual(decision.revisionDraft, "Keep this proposal note")
        XCTAssertFalse(decision.resolved)
        XCTAssertTrue(decision.ready)
        XCTAssertTrue(decision.scopeChange.holdingProposal, "Keep the card visible while the sheet is open, because a reviewer transfer removes the live queue card")
        XCTAssertEqual(ReviewStub.state.scopeTicketReads.count, readCount + 1)
        let readRequest = try XCTUnwrap(ReviewStub.state.scopeTicketReads.last)
        XCTAssertEqual(readRequest.cachePolicy, .reloadIgnoringLocalCacheData)
        XCTAssertTrue(readRequest.url!.query!.contains("detail=full"))
        let scopeWrite = try XCTUnwrap(ReviewStub.state.writes.last)
        XCTAssertEqual(scopeWrite.httpMethod, "PATCH")
        let scopeBody = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(scopeWrite.httpBody)) as? [String: Any])
        XCTAssertEqual(Set(scopeBody.keys), Set(["ceiling", "ceiling_holder"]))
        decision.scopeChange.releasePresentation()
        XCTAssertFalse(decision.scopeChange.holdingProposal)
        // Changing only the reviewer omits the ceiling.
        let reviewerOnly = try await decision.scopeChange.save(api: api, ticket: current, ceiling: nil, holder: Principal(kind: "owner", id: "owner"))
        XCTAssertEqual(reviewerOnly.ceiling, current.ceiling)
        let reviewerWrite = try XCTUnwrap(ReviewStub.state.writes.last)
        let reviewerBody = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(reviewerWrite.httpBody)) as? [String: Any])
        XCTAssertNil(reviewerBody["ceiling"])
        XCTAssertTrue(ReviewStub.state.protectedWrites.isEmpty)
        XCTAssertTrue(Outbox.shared.entries("https://review.invalid").isEmpty)
    }

    func testApprovalSendsOnlyTheDisplayedGuidanceSnapshotAndPreservesNewerDraft() async throws {
        let (decision, api, _) = try await setupDecision()
        let note = "  Keep the full note.\n\nInclude Unicode 🪶.  \n"
        decision.revisionDraft = note
        let displayed = try XCTUnwrap(decision.approvalGuidance(api: api))
        XCTAssertEqual(displayed.text, note)
        let draftID = try XCTUnwrap(decision.revisionDraftID)
        decision.revisionDraft = "A newer draft behind the approval sheet"
        let accepted = await decision.approve(api: api, ceiling: "needs_implementation",
            holder: Principal(kind: "owner", id: "owner"), guidance: displayed)
        XCTAssertTrue(accepted)
        await settle(api)
        let request = try XCTUnwrap(ReviewStub.state.protectedWrites.first)
        let command = try APIClient.decoder().decode(DecisionCommand.self, from: XCTUnwrap(request.httpBody))
        XCTAssertEqual(command.message, displayed.text, "Only text shown by the approval sheet enters the request")
        XCTAssertEqual(LocalStore.shared.draft(server: displayed.server, id: draftID), "A newer draft behind the approval sheet")
        XCTAssertTrue(ReviewStub.state.writes.isEmpty, "Approval notes never use a separate chat send")
    }

    func testApprovalDoesNotInferANoteFromHiddenGuidance() async throws {
        let (decision, api, _) = try await setupDecision()
        decision.revisionDraft = "A saved note that no approval sheet displays"
        let draftID = try XCTUnwrap(decision.revisionDraftID)
        let accepted = await decision.approve(api: api, ceiling: "needs_implementation", holder: .init(kind: "owner", id: "owner"))
        XCTAssertTrue(accepted)
        await settle(api)
        let work = try XCTUnwrap(Outbox.shared.entries("https://review.invalid").last)
        let command = try APIClient.decoder().decode(DecisionCommand.self, from: XCTUnwrap(work.protectedDecisionBody))
        XCTAssertNil(command.message)
        XCTAssertNil(work.draftID)
        XCTAssertEqual(LocalStore.shared.draft(server: "https://review.invalid", id: draftID), decision.revisionDraft)
    }

    func testApprovalGuidanceCannotCrossServerOrProposalIdentity() async throws {
        let (decision, api, item) = try await setupDecision()
        decision.revisionDraft = "Visible guidance"
        let displayed = try XCTUnwrap(decision.approvalGuidance(api: api))
        let proposal = try XCTUnwrap(decision.proposal)
        XCTAssertNil(DecisionGuidance.capture(text: displayed.text, server: displayed.server, ticketID: displayed.ticketID,
            proposal: proposal, loadedServer: "https://other.invalid", loadedIdentity: DecisionGuidance.draftIdentity(ticketID: displayed.ticketID, proposal: proposal)))
        XCTAssertNil(DecisionGuidance.capture(text: displayed.text, server: displayed.server, ticketID: displayed.ticketID,
            proposal: proposal, loadedServer: displayed.server, loadedIdentity: "an old proposal"))
        let other = APIClient(baseURL: URL(string: "https://other.invalid")!)
        let foreign = await decision.approve(api: other, ceiling: "needs_implementation", holder: .init(kind: "owner", id: "owner"), guidance: displayed)
        XCTAssertFalse(foreign)
        ReviewStub.state.bumpRevision()
        await decision.load(item: item, api: api, discardDraft: true)
        let stale = await decision.approve(api: api, ceiling: "needs_implementation", holder: .init(kind: "owner", id: "owner"), guidance: displayed)
        XCTAssertFalse(stale)
        XCTAssertTrue(ReviewStub.state.protectedWrites.isEmpty)
        XCTAssertEqual(LocalStore.shared.draft(server: displayed.server, id: displayed.draftID), displayed.text)
    }

    func testChangedProposalKeepsDraftAndCannotBeSentBackOrApproved() async throws {
        let (decision, api, item) = try await setupDecision()
        decision.revisionDraft = "Keep my unsaved note"
        let exactOld = ReviewItem(reviewItemType: "proposal", ticketId: item.ticketId, field: item.field,
            title: item.title, waitingSince: item.waitingSince, proposalRevision: try XCTUnwrap(decision.proposal?.revision))
        ReviewStub.state.bumpRevision()
        await decision.load(item: item, api: api)
        // GET caching is asynchronous; prove the replacement cache, not a still-valid A cache.
        await LocalStore.shared.flushCache()
        let strictOld = ReviewDecision()
        await strictOld.load(item: exactOld, api: api)
        XCTAssertTrue(strictOld.changed, "A same-second replacement cannot bind the old queue item")
        XCTAssertNil(strictOld.ticket, "Neither cached nor live replacement may populate the old item")
        XCTAssertTrue(decision.changed)
        XCTAssertEqual(decision.revisionDraft, "Keep my unsaved note")
        let returned = await decision.sendBack(api: api, message: "Revise it")
        let approved = await decision.approve(api: api, ceiling: "needs_implementation", holder: Principal(kind: "owner", id: "owner"))
        XCTAssertFalse(returned); XCTAssertFalse(approved)
        XCTAssertTrue(ReviewStub.state.writes.isEmpty)
        await decision.load(item: item, api: api, discardDraft: true)
        XCTAssertTrue(decision.ready)
        XCTAssertEqual(decision.revisionDraft, "")
        ReviewStub.state.bumpRevision()
        let staleGesture = await decision.approve(api: api, ceiling: "needs_implementation", holder: Principal(kind: "owner", id: "owner"))
        XCTAssertTrue(staleGesture) // Saved locally; the protected server compares this exact revision.
        await settle(api)
        XCTAssertEqual(Outbox.shared.entries("https://review.invalid").last?.state, .failed)
        XCTAssertEqual(ReviewStub.state.protectedWrites.count, 1)
        XCTAssertTrue(ReviewStub.state.writes.isEmpty)
    }

    func testSendBackPreservesGuidanceAndRefusalKeepsSavedDecision() async throws {
        let (decision, api, item) = try await setupDecision()
        ReviewStub.state.failWrites = true
        decision.revisionDraft = "  Clarify the transition.\n "
        let saved = await decision.sendBack(api: api, message: decision.revisionDraft)
        XCTAssertTrue(saved)
        await settle(api)
        let request = try XCTUnwrap(ReviewStub.state.protectedWrites.first)
        XCTAssertTrue(request.url!.path.contains("/decision-operations/"))
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: Any])
        try assertProtectedDecisionBody(body, named: "send_back", decision: "send_back", field: "wireframe")
        let work = try XCTUnwrap(Outbox.shared.entries("https://review.invalid").last)
        XCTAssertEqual(work.state, .failed)
        let command = try APIClient.decoder().decode(DecisionCommand.self, from: XCTUnwrap(work.protectedDecisionBody))
        XCTAssertEqual(command.message, "  Clarify the transition.\n ")
        XCTAssertNil(work.proposal); XCTAssertNil(work.approval); XCTAssertNil(work.guidance)
        XCTAssertEqual(work.ticketTitle, decision.ticket?.title)
        XCTAssertEqual(work.proposalFieldLabel, "Proposal")
        XCTAssertEqual(work.note, "Not sent · the visible proposal changed; read it again before sending it back")
        // Later terminal facts for other identities, and success for this identity, are not errors.
        let proposal = try XCTUnwrap(decision.proposal)
        let server = LocalStore.server(api.baseURL)
        for (id, ticketID, field, revision, created, state) in [
            ("success", item.ticketId, proposal.field, proposal.revision, proposal.createdAt, PendingWork.State.sent),
            ("field", item.ticketId, "other-field", proposal.revision, proposal.createdAt, .failed),
            ("revision", item.ticketId, proposal.field, proposal.revision + 10, proposal.createdAt, .failed),
            ("time", item.ticketId, proposal.field, proposal.revision, proposal.createdAt + 100, .failed),
            ("ticket", "other-ticket", proposal.field, proposal.revision, proposal.createdAt, .failed),
        ] {
            let other = TicketProposal(field: field, body: "Other", proposedBy: "worker", createdAt: created, revision: revision)
            try Outbox.shared.save(PendingWork(id: id, created: .now, kind: .sendBack, state: state,
                note: "Unrelated \(id)", ticketID: ticketID, proposal: other), server: server)
        }
        try Outbox.shared.save(PendingWork(id: "foreign", created: .now, kind: .sendBack, state: .failed,
            note: "Other server", ticketID: item.ticketId, proposal: proposal), server: server + "-foreign")
        defer { try? LocalStore.shared.remove(server: server + "-foreign", kind: "outbox", id: "foreign") }
        let restored = ReviewDecision()
        await restored.load(item: item, api: api)
        XCTAssertEqual(restored.error, work.note)
        XCTAssertEqual(restored.revisionDraft, decision.revisionDraft)
        XCTAssertTrue(restored.ready)
        ReviewStub.state.bumpRevision()
        let replacement = ReviewDecision()
        await replacement.load(item: item, api: api, discardDraft: true)
        XCTAssertNil(replacement.error, "A replacement proposal must not inherit an old decision error")
        XCTAssertEqual(replacement.revisionDraft, "")

    }

    func testRestoredSendBackCarriesOriginalRevisionAndHoldsServerRefusal() async throws {
        let (decision, api, _) = try await setupDecision()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("outbox.store")
        let server = LocalStore.server(api.baseURL)
        let proposal = try XCTUnwrap(decision.proposal)
        let command = DecisionCommand(decision: "send_back", field: proposal.field,
            expectedProposalRevision: proposal.revision, message: "Keep these words")
        let body = try APIClient.encodeReviewDecision(command)
        try Outbox(store: LocalStore(url: url)).save(PendingWork(id: "restored", created: .now,
            kind: .sendBack, state: .waiting, ticketID: decision.ticket!.id,
            protectedDecisionBody: body, protectedProposalCreatedAt: proposal.createdAt), server: server)
        let restored = Outbox(store: LocalStore(url: url))
        // Restored attempted work looks up its operation before any current proposal read.
        ReviewStub.state.failWrites = true
        await restored.drain(api: api)
        await restored.drain(api: api)
        let request = try XCTUnwrap(ReviewStub.state.protectedWrites.first)
        let posted = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: Any])
        XCTAssertEqual(posted["expected_proposal_revision"] as? Int, proposal.revision)
        XCTAssertEqual(ReviewStub.state.protectedWrites.count, 1)
        let saved = try XCTUnwrap(Outbox(store: LocalStore(url: url)).entries(server).first)
        XCTAssertEqual(saved.state, .failed)
        XCTAssertEqual(saved.note, "Not sent · the visible proposal changed; read it again before sending it back")
        XCTAssertEqual(saved.protectedDecisionBody, body)
        XCTAssertNil(saved.guidance)
        XCTAssertEqual(ReviewStub.state.protectedRequests.map(\.httpMethod), ["GET", "POST"])
        XCTAssertEqual(APIError(status: 400, message: "the visible proposal changed; read it again before accepting").localizedDescription, "This changed, review again")
        XCTAssertEqual(APIError(status: 400, message: "Guidance is empty").localizedDescription, "Guidance is empty")
    }

    private func protectedAPI() -> APIClient {
        ReviewStub.state.reset(ticket: Data(), manifest: Data())
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ReviewStub.self]
        return APIClient(baseURL: URL(string: "https://review.invalid")!,
                         session: URLSession(configuration: configuration))
    }

    private func protectedResponse() -> [String: Any] {
        ["receipt_contract": 1, "receipt": [
            "operation_id": "protected-operation", "ticket_id": "protected-ticket",
            "decision": "approve", "field": "wireframe", "expected_proposal_revision": 15,
            "outcome": "applied", "created_at": 100,
            "transition": ["stage": "needs_implementation", "ceiling": "needs_implementation",
                           "ceiling_holder": ["kind": "owner", "id": "owner"],
                           "ticket_status": "ready", "pending_proposal_revision": 16],
            "error": NSNull(),
        ]]
    }

    func testProtectedDecisionsReuseFrozenBytesAndLookupWithoutCache() async throws {
        let api = protectedAPI()
        let command = DecisionCommand(decision: "approve", field: "wireframe", expectedProposalRevision: 15,
            editedBody: "Edited proposal\n\nWith context.", nextCeiling: "needs_implementation",
            nextHolder: DecisionHolder(kind: "owner", id: "owner"))
        let encoded = try APIClient.encodeReviewDecision(command)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        XCTAssertEqual(encoded, try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]))
        XCTAssertEqual(object.removeValue(forKey: "decision") as? String, "approve")
        XCTAssertEqual(object.removeValue(forKey: "field") as? String, "wireframe")
        try assertSharedDecisionBody(object, named: "approve")
        // A persisted payload can retain insignificant JSON whitespace. Never re-encode it.
        let frozen = Data(" \n".utf8) + encoded + Data("\n".utf8)
        let differentHolder = DecisionCommand(decision: "approve", field: "wireframe", expectedProposalRevision: 15,
            editedBody: command.editedBody, nextCeiling: command.nextCeiling,
            nextHolder: DecisionHolder(kind: "chief", id: "chief"))
        do {
            _ = try await api.submitReviewDecision(ticketID: "protected-ticket", operationID: "protected-operation",
                                                   command: differentHolder, frozenBody: frozen)
            XCTFail("Matching operation metadata must not hide a different frozen transition")
        } catch {}
        XCTAssertTrue(ReviewStub.state.protectedRequests.isEmpty)
        let response = try JSONSerialization.data(withJSONObject: protectedResponse())
        ReviewStub.state.setProtectedResponse(status: 200, data: response)
        let first = try await api.submitReviewDecision(ticketID: "protected-ticket", operationID: "protected-operation",
                                                       command: command, frozenBody: frozen)
        let retried = try await api.submitReviewDecision(ticketID: "protected-ticket", operationID: "protected-operation",
                                                         command: command, frozenBody: frozen)
        XCTAssertEqual(first, retried)
        let found = try await api.lookupReviewDecision(ticketID: "protected-ticket", operationID: "protected-operation", command: command)
        XCTAssertEqual(found, first)
        ReviewStub.state.setProtectedResponse(status: 200, data: Data("{\"receipt_contract\":1,\"receipt\":null}".utf8))
        let absent = try await api.lookupReviewDecision(ticketID: "protected-ticket", operationID: "protected-operation", command: command)
        XCTAssertNil(absent)
        let requests = ReviewStub.state.protectedRequests
        XCTAssertEqual(requests.map(\.httpMethod), ["POST", "POST", "GET", "GET"])
        for request in requests {
            XCTAssertEqual(request.url?.path, "/api/tickets/protected-ticket/decision-operations/protected-operation")
            XCTAssertEqual(request.cachePolicy, .reloadIgnoringLocalCacheData)
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-Panels-iOS-Build"), NativeBuildGate.build)
        }
        XCTAssertEqual(requests[0].httpBody, frozen)
        XCTAssertEqual(requests[1].httpBody, frozen)
        XCTAssertEqual(requests[0].value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertTrue(ReviewStub.state.writes.isEmpty)
    }

    func testProtectedReceiptIdentityAndUnknownResponsesCannotSettleOrFallBack() async throws {
        let api = protectedAPI()
        let command = DecisionCommand(decision: "approve", field: "wireframe", expectedProposalRevision: 15)
        let original = protectedResponse()
        for (key, value) in [("operation_id", "other"), ("ticket_id", "other"), ("decision", "send_back"),
                             ("field", "other"), ("expected_proposal_revision", 16)] as [(String, Any)] {
            var changed = original
            var receipt = try XCTUnwrap(changed["receipt"] as? [String: Any])
            receipt[key] = value; changed["receipt"] = receipt
            let decoded = try APIClient.decoder().decode(DecisionOperationResponse.self,
                from: JSONSerialization.data(withJSONObject: changed))
            XCTAssertThrowsError(try APIClient.validateReviewDecisionResponse(decoded, ticketID: "protected-ticket",
                operationID: "protected-operation", command: command, allowMissingReceipt: true))
        }
        let failure: [String: Any] = ["code": "validation", "message": "Stale", "detail": [:]]
        for (outcome, transition, error) in [
            ("applied", NSNull(), NSNull()), ("applied", originalReceiptTransition(original), failure),
            ("refused", NSNull(), NSNull()), ("refused", originalReceiptTransition(original), failure),
        ] as [(String, Any, Any)] {
            var changed = original
            var receipt = try XCTUnwrap(changed["receipt"] as? [String: Any])
            receipt["outcome"] = outcome; receipt["transition"] = transition; receipt["error"] = error
            changed["receipt"] = receipt
            let decoded = try APIClient.decoder().decode(DecisionOperationResponse.self,
                from: JSONSerialization.data(withJSONObject: changed))
            XCTAssertThrowsError(try APIClient.validateReviewDecisionResponse(decoded, ticketID: "protected-ticket",
                operationID: "protected-operation", command: command, allowMissingReceipt: false))
        }
        var refused = original
        var receipt = try XCTUnwrap(refused["receipt"] as? [String: Any])
        receipt["outcome"] = "refused"; receipt["transition"] = NSNull(); receipt["error"] = failure
        refused["receipt"] = receipt
        let refusal = try APIClient.decoder().decode(DecisionOperationResponse.self,
            from: JSONSerialization.data(withJSONObject: refused))
        XCTAssertEqual(try APIClient.validateReviewDecisionResponse(refusal, ticketID: "protected-ticket",
            operationID: "protected-operation", command: command, allowMissingReceipt: false)?.outcome, "refused")
        let unsupported: [(Int, String)] = [
            (404, "{}"), (405, "{}"), (200, "{}"), (200, "{\"receipt_contract\":2,\"receipt\":null}"),
            (200, "{\"receipt_contract\":1}"), (200, "{\"receipt_contract\":1,\"receipt\":null}"),
        ]
        let frozen = try APIClient.encodeReviewDecision(command)
        for (status, body) in unsupported {
            ReviewStub.state.setProtectedResponse(status: status, data: Data(body.utf8))
            do {
                _ = try await api.submitReviewDecision(ticketID: "protected-ticket", operationID: "protected-operation",
                                                       command: command, frozenBody: frozen)
                XCTFail("Unknown POST response cannot settle a decision")
            } catch {}
        }
        ReviewStub.state.setProtectedResponse(status: 200, data: Data("{}".utf8))
        do {
            _ = try await api.lookupReviewDecision(ticketID: "protected-ticket", operationID: "protected-operation", command: command)
            XCTFail("Unsupported lookup is not evidence of absence")
        } catch {}
        XCTAssertEqual(ReviewStub.state.protectedRequests.count, unsupported.count + 1)
        XCTAssertTrue(ReviewStub.state.writes.isEmpty)
    }

    private func originalReceiptTransition(_ response: [String: Any]) -> Any {
        (response["receipt"] as! [String: Any])["transition"]!
    }

    private func settle(_ api: APIClient) async {
        await Outbox.shared.drain(api: api)
        for _ in 0..<100 {
            if !Outbox.shared.entries("https://review.invalid").contains(where: { $0.state == .waiting }) { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("Outbox did not finish the stub request")
    }

    func testVoiceUsesExistingTranscriptionContractAndAppendsWithoutLosingDraft() async throws {
        let (_, api, _) = try await setupDecision()
        let audio = Data([1, 2, 3])
        let transcript = try await api.transcribeReviewAudio(audio)
        XCTAssertEqual(transcript, "Add the opened state.")
        let request = try XCTUnwrap(ReviewStub.state.writes.first)
        XCTAssertEqual(request.url?.path, "/api/conversation/voice-transcriptions")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: String])
        XCTAssertEqual(body, ["audio": "AQID", "media_type": "audio/mp4"])
#if os(iOS)
        XCTAssertEqual(ReviewVoiceCapture.append(transcript, to: "Typed guidance."), "Typed guidance.\n\nAdd the opened state.")
        XCTAssertEqual(ReviewVoiceCapture.append("", to: " Keep this "), " Keep this ")
#endif
    }

    func testGuidanceAloneIsKeptWhenProposalChanges() async throws {
        let (decision, api, item) = try await setupDecision()
        decision.revisionDraft = "Keep my send-back words"
        ReviewStub.state.bumpRevision()
        await decision.load(item: item, api: api)
        XCTAssertTrue(decision.changed)
        XCTAssertEqual(decision.revisionDraft, "Keep my send-back words")
        XCTAssertFalse(decision.ready)
    }

    func testStaleReviewModelCannotOverwriteNewTranscriptionOnNoteAppearance() async throws {
        let (decision, _, _) = try await setupDecision()
        let id = try XCTUnwrap(decision.revisionDraftID)
        LocalStore.shared.saveDraft("Recovered recording", server: "https://review.invalid", id: id)
        decision.revisionDraft = "" // SwiftUI may set the existing binding value on appearance.
        XCTAssertEqual(LocalStore.shared.draft(server: "https://review.invalid", id: id), "Recovered recording")
        decision.refreshVoiceDraft()
        XCTAssertEqual(decision.revisionDraft, "Recovered recording")
    }

    func testSkipIsLocalAndNewProposalIdentityReturns() {
        func card(_ parts: ReviewItem...) -> ReviewCard { ReviewCard(id: "ticket:a", ticketId: "a", parts: parts) }
        let old = ReviewItem(reviewItemType: "proposal", ticketId: "a", field: "brief", title: "First", waitingSince: 1)
        let new = ReviewItem(reviewItemType: "proposal", ticketId: "a", field: "brief", title: "First", waitingSince: 2)
        var queue = ReviewQueue()
        queue.skip(card(old))
        XCTAssertNil(queue.current(in: [card(old)]))
        XCTAssertEqual(queue.skippedCount(in: [card(old)]), 1)
        XCTAssertEqual(queue.skippedCount(in: [card(new)]), 0, "A removed or replaced ask cannot be revisited")
        XCTAssertEqual(queue.current(in: [card(new)])?.id, "ticket:a")
        queue.revisit()
        XCTAssertEqual(queue.current(in: [card(old)])?.id, "ticket:a")
        XCTAssertEqual(queue.skippedCount(in: [card(old)]), 0)
        queue.skip(card(old))
        queue.resolve(old)
        XCTAssertEqual(queue.skippedCount(in: [card(old)]), 0, "An ask resolved during refresh cannot be revisited")
    }

    /// One Ticket card holds a proposal and a message. Skip takes the whole card and
    /// counts once; deciding one part leaves the card current until the other is handled.
    func testTicketCardSkipsAndResolvesAsOneEntry() {
        let proposal = ReviewItem(reviewItemType: "proposal", ticketId: "a", field: "plan", title: "A", waitingSince: 2)
        let message = ReviewItem(reviewItemType: "message", title: "A", waitingSince: 1, conversationId: "c", ownerKind: "ticket", ownerId: "a")
        let ticket = ReviewCard(id: "ticket:a", ticketId: "a", parts: [proposal, message])
        let chief = ReviewItem(reviewItemType: "message", title: "Chief", waitingSince: 3, conversationId: "chief", ownerKind: "chief")
        let lone = ReviewCard(id: chief.id, parts: [chief])
        var queue = ReviewQueue()
        queue.skip(ticket)
        XCTAssertEqual(queue.current(in: [ticket, lone])?.id, lone.id)
        XCTAssertEqual(queue.skippedCount(in: [ticket, lone]), 1)
        XCTAssertEqual(queue.skippedCardCount, 1)
        queue.revisit()
        queue.resolve(proposal)
        XCTAssertEqual(queue.current(in: [ticket, lone])?.id, ticket.id)
        XCTAssertEqual(queue.pending(ticket).map(\.id), [message.id])
        queue.resolve(message)
        XCTAssertEqual(queue.current(in: [ticket, lone])?.id, lone.id)
        // A new part on a skipped card brings the card back with every waiting part.
        let later = ReviewItem(reviewItemType: "message", title: "A", waitingSince: 4, conversationId: "d", ownerKind: "ticket", ownerId: "a")
        let first = ReviewCard(id: "ticket:b", ticketId: "b", parts: [proposal])
        var skipped = ReviewQueue()
        skipped.skip(first)
        let grown = ReviewCard(id: "ticket:b", ticketId: "b", parts: [proposal, later])
        XCTAssertEqual(skipped.current(in: [grown])?.id, grown.id)
        XCTAssertEqual(skipped.pending(grown).count, 2)
    }
}

private final class ReviewStubState: @unchecked Sendable {
    private let lock = NSLock()
    private var ticket = Data()
    private var manifest = Data()
    private var recorded: [URLRequest] = []
    private var failing = false
    private var operationResponse: (Int, Data)?
    private var operationRequests: [URLRequest] = []
    private var operationReceipts: [String: Data] = [:]
    private var failNextScopeRead = false
    private var ticketReadRequests: [URLRequest] = []
    var scopeTicketReads: [URLRequest] { lock.withLock { ticketReadRequests } }
    func failNextTicketRead() {
        lock.withLock { failNextScopeRead = true }
    }
    var protectedWrites: [URLRequest] { lock.withLock { operationRequests.filter { $0.httpMethod == "POST" } } }
    var protectedRequests: [URLRequest] { lock.withLock { operationRequests } }
    func setProtectedResponse(status: Int, data: Data) {
        lock.withLock { operationResponse = (status, data) }
    }
    var writes: [URLRequest] { lock.withLock { recorded } }
    var failWrites: Bool {
        get { lock.withLock { failing } }
        set { lock.withLock { failing = newValue } }
    }
    func reset(ticket: Data, manifest: Data) {
        lock.withLock { self.ticket = ticket; self.manifest = manifest; recorded = []; failing = false; operationResponse = nil; operationRequests = []; operationReceipts = [:]; failNextScopeRead = false; ticketReadRequests = [] }
    }
    func bumpRevision() {
        lock.withLock {
            var object = try! JSONSerialization.jsonObject(with: ticket) as! [String: Any]
            var proposal = object["pending_proposal"] as! [String: Any]
            proposal["revision"] = (proposal["revision"] as! Int) + 1
            object["pending_proposal"] = proposal
            ticket = try! JSONSerialization.data(withJSONObject: object)
        }
    }
    func response(_ input: URLRequest) -> (Int, Data) {
        lock.withLock {
            var request = input
            if request.httpMethod == "POST" || request.httpMethod == "PATCH" {
                if request.httpBody == nil, let stream = request.httpBodyStream {
                    stream.open(); defer { stream.close() }
                    var data = Data(); var buffer = [UInt8](repeating: 0, count: 4096)
                    while stream.hasBytesAvailable { let count = stream.read(&buffer, maxLength: buffer.count); if count <= 0 { break }; data.append(buffer, count: count) }
                    request.httpBody = data
                }
            }
            if request.httpMethod == "PATCH" {
                recorded.append(request)
                let body = try! JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
                var object = try! JSONSerialization.jsonObject(with: ticket) as! [String: Any]
                if let ceiling = body["ceiling"] { object["ceiling"] = ceiling }
                object["ceiling_holder"] = body["ceiling_holder"]
                ticket = try! JSONSerialization.data(withJSONObject: object)
                return (200, Data("{}".utf8))
            }
            if request.httpMethod == "GET", request.url!.path == "/api/tickets" {
                ticketReadRequests.append(request)
                if failNextScopeRead { failNextScopeRead = false; return (-1, Data()) }
            }
            if request.url!.path.contains("/decision-operations/") {
                operationRequests.append(request)
                if let operationResponse { return operationResponse }
                let id = request.url!.lastPathComponent
                if let receipt = operationReceipts[id] { return (200, receipt) }
                if request.httpMethod == "GET" {
                    return (200, Data("{\"receipt_contract\":1,\"receipt\":null}".utf8))
                }
                let command = try! JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
                let currentTicket = try! JSONSerialization.jsonObject(with: ticket) as! [String: Any]
                let current = currentTicket["pending_proposal"] as! [String: Any]
                let decision = command["decision"] as! String
                let revision = command["expected_proposal_revision"] as! Int
                let refused = failing || current["revision"] as? Int != revision
                let action = decision == "approve" ? "accepting" : "sending it back"
                let receipt: [String: Any] = [
                    "operation_id": id, "ticket_id": currentTicket["id"]!, "decision": decision,
                    "field": command["field"]!, "expected_proposal_revision": revision,
                    "outcome": refused ? "refused" : "applied", "created_at": 100,
                    "transition": refused ? NSNull() : [
                        "stage": "needs_implementation", "ceiling": "needs_implementation",
                        "ceiling_holder": ["kind": "owner", "id": "owner"],
                        "ticket_status": "ready", "pending_proposal_revision": revision + 1,
                    ],
                    "error": refused ? ["code": "validation",
                        "message": "the visible proposal changed; read it again before \(action)",
                        "detail": [:]] : NSNull(),
                ]
                let data = try! JSONSerialization.data(withJSONObject: ["receipt_contract": 1, "receipt": receipt])
                operationReceipts[id] = data
                return (200, data)
            }
            if request.httpMethod == "POST" {
                recorded.append(request)
                if request.url!.path.hasSuffix("voice-transcriptions") { return (200, Data("{\"transcript\":\"Add the opened state.\"}".utf8)) }
                return failing ? (400, Data("{\"error\":{\"code\":\"validation\",\"message\":\"the visible proposal changed; read it again before sending back\"}}".utf8)) : (200, Data("{}".utf8))
            }
            return (200, request.url!.path.contains("manifest") ? manifest : ticket)
        }
    }
}

private final class ReviewStub: URLProtocol, @unchecked Sendable {
    static let state = ReviewStubState()
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "review.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let (status, data) = Self.state.response(request)
        if status == -1 {
            client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost))
            return
        }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
