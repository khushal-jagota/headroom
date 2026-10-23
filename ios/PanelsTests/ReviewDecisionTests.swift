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
        {"worker_types":[{"worker_type":"\(detail.workerType)","stages":[{"id":"\(detail.stage)","label":"Current"},{"id":"next_gate","label":"Next gate"},{"id":"done","label":"Done"}],"advance":{"\(detail.stage)":"next_gate"},"fields":[{"id":"\(proposal.field)","label":"Proposal"}],"ceiling_range":["next_gate","done"]}]}
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

    func testEditedApprovalCarriesExactRevisionAndNextGate() async throws {
        let (decision, api, _) = try await setupDecision()
        let revision = try XCTUnwrap(decision.proposal?.revision)
        decision.draft = "Edited proposal\n\nWith context."
        XCTAssertEqual(decision.approvalStages.first?.id, "next_gate")
        let accepted = await decision.approve(api: api, ceiling: "next_gate", holder: Principal(kind: "owner", id: "owner"))
        XCTAssertTrue(accepted)
        await settle(api)
        let request = try XCTUnwrap(ReviewStub.state.writes.first)
        XCTAssertTrue(request.url!.path.hasSuffix("/accept/wireframe"))
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: Any])
        XCTAssertEqual(body["expected_proposal_revision"] as? Int, revision)
        XCTAssertEqual(body["edited_body"] as? String, decision.draft)
        XCTAssertEqual(body["next_ceiling"] as? String, "next_gate")
        XCTAssertEqual(body["next_holder"] as? [String: String], ["kind": "owner", "id": "owner"])
        let repeated = await decision.approve(api: api, ceiling: "next_gate", holder: Principal(kind: "owner", id: "owner"))
        XCTAssertFalse(repeated)
        XCTAssertEqual(ReviewStub.state.writes.count, 1)
    }

    func testChangedProposalKeepsDraftAndCannotBeSentBackOrApproved() async throws {
        let (decision, api, item) = try await setupDecision()
        decision.draft = "Keep my unsaved edit"
        ReviewStub.state.bumpRevision()
        await decision.load(item: item, api: api)
        XCTAssertTrue(decision.changed)
        XCTAssertEqual(decision.draft, "Keep my unsaved edit")
        let returned = await decision.sendBack(api: api, message: "Revise it")
        let approved = await decision.approve(api: api, ceiling: "next_gate", holder: Principal(kind: "owner", id: "owner"))
        XCTAssertFalse(returned); XCTAssertFalse(approved)
        XCTAssertTrue(ReviewStub.state.writes.isEmpty)
        await decision.load(item: item, api: api, discardDraft: true)
        XCTAssertTrue(decision.ready)
        XCTAssertEqual(decision.draft, decision.proposal?.body)
        ReviewStub.state.bumpRevision()
        let staleGesture = await decision.approve(api: api, ceiling: "next_gate", holder: Principal(kind: "owner", id: "owner"))
        XCTAssertTrue(staleGesture) // Saved locally; the drainer owns the fresh revision check.
        await settle(api)
        XCTAssertEqual(Outbox.shared.entries("https://review.invalid").last?.state, .changed)
        XCTAssertTrue(ReviewStub.state.writes.isEmpty)
    }

    func testSendBackTrimsGuidanceAndRefusalKeepsSavedDecision() async throws {
        let (decision, api, _) = try await setupDecision()
        ReviewStub.state.failWrites = true
        let saved = await decision.sendBack(api: api, message: "  Clarify the transition.\n ")
        XCTAssertTrue(saved)
        await settle(api)
        let request = try XCTUnwrap(ReviewStub.state.writes.first)
        XCTAssertTrue(request.url!.path.hasSuffix("/reject"))
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: Any])
        XCTAssertEqual(body["message"] as? String, "Clarify the transition.")
        XCTAssertEqual(body["expected_proposal_revision"] as? Int, decision.proposal?.revision)
        let work = try XCTUnwrap(Outbox.shared.entries("https://review.invalid").last)
        XCTAssertEqual(work.state, .changed)
        XCTAssertEqual(work.guidance, "Clarify the transition.")
        XCTAssertEqual(work.note, "This changed, review again")
        // Workspace uses its own adapter and must carry the same visible revision.
        do {
            try await api.returnTicketProposal(decision.ticket!.id, message: "Workspace guidance", expectedProposalRevision: decision.proposal!.revision)
            XCTFail("Changed work must require another review")
        } catch let error as APIError {
            XCTAssertEqual(error.localizedDescription, "This changed, review again")
        }
        let workspaceRequest = try XCTUnwrap(ReviewStub.state.writes.last)
        let workspaceBody = try XCTUnwrap(JSONSerialization.jsonObject(with: workspaceRequest.httpBody!) as? [String: Any])
        XCTAssertEqual(workspaceBody["expected_proposal_revision"] as? Int, decision.proposal?.revision)
        XCTAssertEqual(workspaceBody["message"] as? String, "Workspace guidance")
    }

    func testRestoredSendBackCarriesOriginalRevisionAndHoldsServerRefusal() async throws {
        let (decision, api, _) = try await setupDecision()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("outbox.store")
        let server = LocalStore.server(api.baseURL)
        let proposal = try XCTUnwrap(decision.proposal)
        try Outbox(store: LocalStore(url: url)).save(PendingWork(id: "restored", created: .now,
            kind: .sendBack, ticketID: decision.ticket!.id, proposal: proposal, guidance: "Keep these words"), server: server)
        let restored = Outbox(store: LocalStore(url: url))
        // The preflight read still matches. The server refuses a change after that read.
        ReviewStub.state.failWrites = true
        await restored.drain(api: api)
        await restored.drain(api: api)
        let request = try XCTUnwrap(ReviewStub.state.writes.first)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: Any])
        XCTAssertEqual(body["expected_proposal_revision"] as? Int, proposal.revision)
        XCTAssertEqual(ReviewStub.state.writes.count, 1)
        let saved = try XCTUnwrap(Outbox(store: LocalStore(url: url)).entries(server).first)
        XCTAssertEqual(saved.state, .changed)
        XCTAssertEqual(saved.note, "This changed, review again")
        XCTAssertEqual(saved.guidance, "Keep these words")
        XCTAssertEqual(APIError(status: 400, message: "the visible proposal changed; read it again before accepting").localizedDescription, "This changed, review again")
        XCTAssertEqual(APIError(status: 400, message: "Guidance is empty").localizedDescription, "Guidance is empty")
    }

    private func settle(_ api: APIClient) async {
        await Outbox.shared.drain(api: api)
        for _ in 0..<100 {
            if !Outbox.shared.entries("https://review.invalid").contains(where: { [.waiting, .sending].contains($0.state) }) { return }
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

    func testStaleReviewModelCannotOverwriteNewTranscriptionWhenEditingProposal() async throws {
        let (decision, _, _) = try await setupDecision()
        let id = try XCTUnwrap(decision.revisionDraftID)
        LocalStore.shared.saveDraft("Recovered recording", server: "https://review.invalid", id: id)
        decision.draft += " An edit."
        decision.revisionDraft = "" // SwiftUI may set the existing binding value on appearance.
        XCTAssertEqual(LocalStore.shared.draft(server: "https://review.invalid", id: id), "Recovered recording")
        decision.refreshVoiceDraft()
        XCTAssertEqual(decision.revisionDraft, "Recovered recording")
    }

    func testSkipIsLocalAndNewProposalIdentityReturns() {
        let old = ReviewItem(reviewItemType: "proposal", ticketId: "a", field: "brief", title: "First", waitingSince: 1)
        let new = ReviewItem(reviewItemType: "proposal", ticketId: "a", field: "brief", title: "First", waitingSince: 2)
        var queue = ReviewQueue()
        queue.skip(old)
        XCTAssertNil(queue.current(in: [old]))
        XCTAssertEqual(queue.current(in: [new])?.id, new.id)
        queue.revisit()
        XCTAssertEqual(queue.current(in: [old])?.id, old.id)
    }
}

private final class ReviewStubState: @unchecked Sendable {
    private let lock = NSLock()
    private var ticket = Data()
    private var manifest = Data()
    private var recorded: [URLRequest] = []
    private var failing = false
    var writes: [URLRequest] { lock.withLock { recorded } }
    var failWrites: Bool {
        get { lock.withLock { failing } }
        set { lock.withLock { failing = newValue } }
    }
    func reset(ticket: Data, manifest: Data) {
        lock.withLock { self.ticket = ticket; self.manifest = manifest; recorded = []; failing = false }
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
            if request.httpMethod == "POST" {
                if request.httpBody == nil, let stream = request.httpBodyStream {
                    stream.open(); defer { stream.close() }
                    var data = Data(); var buffer = [UInt8](repeating: 0, count: 4096)
                    while stream.hasBytesAvailable { let count = stream.read(&buffer, maxLength: buffer.count); if count <= 0 { break }; data.append(buffer, count: count) }
                    request.httpBody = data
                }
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
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
