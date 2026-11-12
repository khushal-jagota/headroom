import XCTest
@testable import Panels

final class WorkspacePresentationTests: XCTestCase {
    private func ticket(_ changes: [String: Any] = [:]) throws -> TicketSummary {
        let url = try XCTUnwrap(Bundle(for: ModelDecodingTests.self).url(forResource: "tickets", withExtension: "json"))
        let envelope = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        var row = try XCTUnwrap((envelope["tickets"] as? [[String: Any]])?.first)
        row["assigned"] = false
        for (key, value) in changes { row[key] = value }
        return try APIClient.decoder().decode(TicketSummary.self, from: JSONSerialization.data(withJSONObject: row))
    }

    func testActionsUseWireContractAndRejectUncertainDelivery() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [WorkspaceURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "https://workspace-stub.invalid")!, session: session)
        try await client.workspaceAction(.backlog, route: .ticket("t1"))
        try await client.workspaceAction(.resume, route: .ticket("t1"))
        try await client.workspaceAction(.settle, route: .orchestrator("m1"))
        try await client.workspaceAction(.recap, route: .chief)
        do {
            try await client.workspaceAction(.recap, route: .ticket("uncertain"))
            XCTFail("Uncertain delivery must not show success")
        } catch { XCTAssertTrue(error.localizedDescription.contains("uncertain")) }
    }

    func testAttentionPrecedenceAndUnreadDoesNotCreateASection() throws {
        let overlap = try ticket(["awaiting_approval": true, "awaiting_answer": true, "assigned": true, "agent_state": "working"])
        XCTAssertEqual(WorkspaceGrouping.section(for: overlap), .approval)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_answer": true, "assigned": true])), .answer)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_reply": true, "ticket_status": "agent"])), .agent)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_reply": true])), .other)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["agent_state": "errored", "ticket_status": "agent"])), .errored)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["waiting_to_closeout": true, "awaiting_agent_approval": true])), .consequences)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_agent_approval": true])), .agentApproval)
    }

    func testRailExcludesHiddenWorkEvenWhenItNeedsApproval() throws {
        for override: [String: Any] in [["is_backlogged": true], ["blocked": true], ["ticket_status": "blocked"], ["stage": "done"]] {
            var changes = override
            changes["awaiting_approval"] = true
            XCTAssertNil(WorkspaceGrouping.section(for: try ticket(changes)))
        }
    }

    func testSurvivorsKeepPositionAndArrivalsAppendWhenSectionsChange() throws {
        let alpha = try ticket(["id": "a", "title": "Alpha", "assigned": true])
        let beta = try ticket(["id": "b", "title": "Beta", "assigned": true])
        let previous = WorkspaceGrouping.groups([beta, alpha])
        let newArrival = try ticket(["id": "c", "title": "A new arrival", "assigned": true])
        let refresh = WorkspaceGrouping.groups([newArrival, beta, alpha], previous: previous)
        XCTAssertEqual(refresh.first?.tickets.map(\.id), ["a", "b", "c"])
        let moved = try ticket(["id": "a", "title": "Alpha", "ticket_status": "agent"])
        let next = WorkspaceGrouping.groups([moved, beta, newArrival], previous: refresh)
        XCTAssertEqual(next.map(\.section), [.yours, .agent])
        XCTAssertEqual(next.first?.tickets.map(\.id), ["b", "c"])
        XCTAssertEqual(next.last?.tickets.map(\.id), ["a"])
    }
}

private final class WorkspaceURLProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var data = request.httpBody ?? Data()
        if data.isEmpty, let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(buffer, count: count)
            }
        }
        let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        let path = request.url!.path
        var reply = "{}"
        let valid: Bool
        switch path {
        case "/api/tickets/t1/backlog", "/api/tickets/t1/resume":
            valid = request.httpMethod == "POST" && body?.isEmpty == true
        case "/api/managers/m1/settled":
            valid = request.httpMethod == "PATCH" && body?["settled"] as? Bool == true
        case "/api/recap-requests":
            let target = body?["target"] as? [String: String]
            valid = request.httpMethod == "POST" && (target == ["kind": "chief", "id": "chief"] || target == ["kind": "ticket", "id": "uncertain"])
            reply = target?["id"] == "uncertain" ? #"{"fate":"uncertain"}"# : #"{"fate":"queued"}"#
        default: valid = false
        }
        guard valid else { client?.urlProtocol(self, didFailWithError: URLError(.badURL)); return }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(reply.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
