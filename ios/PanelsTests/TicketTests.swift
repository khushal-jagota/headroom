import XCTest
@testable import Panels

final class TicketTests: XCTestCase {
    func testLinkedArtifactKeepsItsActualOwner() {
        let entry = ArtifactEntry(key: "linked", path: "artifacts/proof.html", label: "Proof", kind: "html",
                                  href: "/files/tickets/t_other/artifacts/proof.html", directory: nil, childCount: 0)
        XCTAssertEqual(TicketArtifactRoute.destination(entry, owner: ArtifactOwner(kind: .ticket, id: "t_here")),
                       .artifact(owner: ArtifactOwner(kind: .ticket, id: "t_other"), path: "artifacts/proof.html", title: "Proof"))
    }

    private func ticket() throws -> TicketDetail {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "ticket-full", withExtension: "json"))
        return try APIClient.decoder().decode(TicketDetail.self, from: Data(contentsOf: url))
    }

    func testManifestControlsCompletionAndApprovalFloor() throws {
        let manifest = TicketWorkerType(workerType: "product_design", label: "Product design", stages: [
            .init(id: "needs_brief", label: "Kickoff", gatingField: "brief", isTerminal: false, ownershipMode: "worker"),
            .init(id: "needs_wireframe", label: "Draw it", gatingField: "wireframe", isTerminal: false, ownershipMode: "worker"),
            .init(id: "done", label: "Finished", gatingField: nil, isTerminal: true, ownershipMode: nil)
        ], fields: [.init(id: "brief", label: "Kickoff"), .init(id: "wireframe", label: "Drawing")])
        let presentation = TicketPresentation(ticket: try ticket(), manifest: manifest)
        XCTAssertEqual(presentation.completedCount, 1)
        XCTAssertEqual(presentation.ceilingOptions.map(\.id), ["needs_wireframe", "done"])
        XCTAssertEqual(presentation.ceilingLabel, "Draw it")
        XCTAssertEqual(presentation.heading, "Currently working on Drawing")
        XCTAssertTrue(presentation.working)
    }

    func testWritesUseExistingEndpointsAndKeepReviewerOnlyChangeNarrow() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [TicketStubProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "https://ticket-stub.invalid")!, session: session)
        let ticket = try ticket()
        try await client.setTicketBacklogged(ticket, backlogged: true)
        try await client.setTicketBacklogged(ticket, backlogged: false)
        try await client.setTicketApprovalLimit(ticket.id, ceiling: nil, holder: Principal(kind: "chief", id: "chief"))
        try await client.setTicketApprovalLimit(ticket.id, ceiling: "done", holder: Principal(kind: "owner", id: "owner"))
        let delivery = try await client.requestTicketRecap(ticket.id)
        XCTAssertEqual(delivery.note, "Delivery uncertain · do not resend")
    }

    func testWriteFailureRemainsAnErrorAndRefusedRecapIsNotSuccess() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [TicketStubProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "https://refusal-stub.invalid")!, session: session)
        do {
            try await client.setTicketBacklogged(try ticket(), backlogged: true)
            XCTFail("A stale record must not appear to be backlogged")
        } catch let error as APIError {
            XCTAssertEqual(error.status, 409)
            XCTAssertEqual(error.message, "Ticket changed. Refresh before trying again.")
        }
        XCTAssertEqual(TicketRecapDelivery(fate: "refused", refusalReason: "backend_did_not_start").note,
                       "Not delivered · the backend would not start")
        XCTAssertNil(TicketRecapDelivery(fate: "queued", refusalReason: nil).note)
    }
}

private final class TicketStubProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let url = try XCTUnwrap(request.url)
            XCTAssertTrue(url.host?.hasSuffix("-stub.invalid") == true, "Tests must never reach Panels")
            var data = request.httpBody ?? Data()
            if data.isEmpty, let stream = request.httpBodyStream {
                stream.open()
                defer { stream.close() }
                var buffer = [UInt8](repeating: 0, count: 1024)
                while stream.hasBytesAvailable {
                    let count = stream.read(&buffer, maxLength: buffer.count)
                    if count <= 0 { break }
                    data.append(buffer, count: count)
                }
            }
            let body = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            var response = "{}"
            var status = 200
            if url.host == "refusal-stub.invalid" {
                status = 409
                response = #"{"error":{"message":"Ticket changed. Refresh before trying again."}}"#
            } else if url.path.hasSuffix("/backlog") || url.path.hasSuffix("/resume") {
                XCTAssertEqual(request.httpMethod, "POST")
                XCTAssertTrue(["/api/tickets/t_02zd37tz/backlog", "/api/tickets/t_02zd37tz/resume"].contains(url.path))
                XCTAssertEqual(body.keys.sorted(), ["expected_record_sha256"])
                XCTAssertEqual(body["expected_record_sha256"] as? String, "998834d0844dabf07484e0b0fef88efa3c4534797237ba9fdd94aae65f52f16e")
            } else if url.path == "/api/recap-requests" {
                XCTAssertEqual(request.httpMethod, "POST")
                let target = try XCTUnwrap(body["target"] as? [String: String])
                XCTAssertEqual(target, ["kind": "ticket", "id": "t_02zd37tz"])
                response = #"{"fate":"uncertain"}"#
            } else {
                XCTAssertEqual(request.httpMethod, "PATCH")
                XCTAssertEqual(url.path, "/api/tickets/t_02zd37tz")
                let holder = try XCTUnwrap(body["ceiling_holder"] as? [String: String])
                if holder["kind"] == "chief" {
                    XCTAssertNil(body["ceiling"])
                    XCTAssertEqual(holder["id"], "chief")
                } else {
                    XCTAssertEqual(body["ceiling"] as? String, "done")
                    XCTAssertEqual(holder, ["kind": "owner", "id": "owner"])
                }
            }
            let http = try XCTUnwrap(HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil))
            client?.urlProtocol(self, didReceive: http, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(response.utf8))
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}
