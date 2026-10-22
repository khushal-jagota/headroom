import XCTest
@testable import Panels

/// No live writes. The real adapter runs against URLProtocol's in-memory transport.
@MainActor final class ReviewDecisionTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        try Data(contentsOf: XCTUnwrap(Bundle(for: Self.self).url(forResource: name, withExtension: "json")))
    }
    private func setupDecision() async throws -> (ReviewDecision, APIClient, ReviewItem) {
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
        XCTAssertFalse(staleGesture)
        XCTAssertTrue(ReviewStub.state.writes.isEmpty)
    }

    func testSendBackTrimsGuidanceAndFailedWriteKeepsDecision() async throws {
        let (decision, api, _) = try await setupDecision()
        XCTAssertTrue(decision.canReturn)
        ReviewStub.state.failWrites = true
        let failed = await decision.sendBack(api: api, message: "  Clarify the transition.\n ")
        XCTAssertFalse(failed)
        XCTAssertFalse(decision.resolved)
        XCTAssertNotNil(decision.error)
        let request = try XCTUnwrap(ReviewStub.state.writes.first)
        XCTAssertTrue(request.url!.path.hasSuffix("/reject"))
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: String])
        XCTAssertEqual(body["message"], "Clarify the transition.")
        ReviewStub.state.failWrites = false
        let success = await decision.sendBack(api: api, message: "Clarify the transition.")
        XCTAssertTrue(success)
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
                return failing ? (409, Data("{\"error\":{\"message\":\"Proposal changed\"}}".utf8)) : (200, Data("{}".utf8))
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
