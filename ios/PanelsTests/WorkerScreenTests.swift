import XCTest
@testable import Panels

final class WorkerScreenTests: XCTestCase {
    private func worker(_ changes: [String: Any] = [:]) throws -> ManagerTicket {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "manager", withExtension: "json"))
        let root = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        let tickets = try XCTUnwrap(root["tickets"] as? [[String: Any]])
        var row = try XCTUnwrap(tickets.first)
        row.merge(changes) { _, new in new }
        return try APIClient.decoder().decode(ManagerTicket.self, from: JSONSerialization.data(withJSONObject: row))
    }

    func testPageSectionsAndAttentionPriorityMatchWeb() throws {
        XCTAssertEqual(WorkerGroup.classify(try worker(["stage": "done", "is_backlogged": true, "awaiting_approval": true])), .done)
        XCTAssertEqual(WorkerGroup.classify(try worker(["is_backlogged": true, "awaiting_approval": true])), .backlogged)
        XCTAssertEqual(WorkerGroup.classify(try worker(["awaiting_approval": true, "awaiting_answer": true, "assigned": true])), .approval)
        XCTAssertEqual(WorkerGroup.classify(try worker(["awaiting_answer": true, "assigned": true])), .answer)
        XCTAssertEqual(WorkerGroup.classify(try worker(["assigned": true])), .yours)
        XCTAssertEqual(WorkerGroup.classify(try worker(["agent_state": "idle", "awaiting_reply": true])), .agent, "Durable worker ownership survives an idle turn and an unread reply")
        XCTAssertEqual(WorkerGroup.classify(try worker(["agent_state": "idle", "ticket_status": "blocked"])), .blocked)
        XCTAssertEqual(WorkerGroup.classify(try worker(["agent_state": "idle", "ticket_status": "empty", "awaiting_agent_approval": true])), .agentApproval)
    }

    func testWorkerOrderUsesActivityBeforeTitleAndPreservesSignal() throws {
        let older = try worker(["id": "older", "title": "A", "activity_at": 1])
        let newer = try worker(["id": "newer", "title": "Z", "activity_at": 2, "conversation_signal": ["working": true, "message_for_owner": true]])
        XCTAssertEqual(WorkerGroup.ordered([older, newer]).map(\.id), ["newer", "older"])
        XCTAssertTrue(try XCTUnwrap(newer.conversationSignal).messageForOwner)
    }

    func testRecapRequestContractAndNonDeliveryAreNotSuccess() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [WorkerScreenMockProtocol.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let api = APIClient(baseURL: URL(string: "https://panels.invalid")!, session: session)
        let accepted = try await api.requestRecap(target: Principal(kind: "chief", id: "chief"))
        XCTAssertTrue(accepted.accepted)
        XCTAssertNil(accepted.note)
        let refused = try await api.requestRecap(target: Principal(kind: "manager", id: "refused"))
        XCTAssertFalse(refused.accepted)
        XCTAssertEqual(refused.note, "Not delivered · backend did not start")
        let uncertain = try await api.requestRecap(target: Principal(kind: "manager", id: "uncertain"))
        XCTAssertFalse(uncertain.accepted)
        XCTAssertEqual(uncertain.note, "Delivery uncertain · do not resend")
    }

    func testApprovedPageActionsUseExistingWriteContracts() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [WorkerScreenMockProtocol.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let api = APIClient(baseURL: URL(string: "https://panels.invalid")!, session: session)
        try await api.setManagerSettled("manager", settled: true)
        try await api.saveManagerGoal("manager", goal: "goal text")
        try await api.saveAgentRecap(target: Principal(kind: "manager", id: "manager"), recap: "recap text")
        try await api.saveAgentRecap(target: Principal(kind: "chief", id: "chief"), recap: "recap text")
        try await api.setWorkerBacklog("worker", backlogged: true)
        try await api.setWorkerBacklog("worker", backlogged: false)
    }

    func testChiefRefreshRetainsLoadedWindowAndScopesEveryPage() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [WorkerScreenMockProtocol.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let api = APIClient(baseURL: URL(string: "https://panels.invalid")!, session: session)
        let result = try await api.chiefWorkers(filter: .done, through: 30)
        XCTAssertEqual(result.tickets.map(\.id), ["first", "second"])
        XCTAssertEqual(result.page.offset, 30)
        XCTAssertNil(result.page.nextOffset)
    }
}

/// An in-process transport only: any unexpected URL, verb, body or scope fails.
private final class WorkerScreenMockProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}
    private func bodyObject() throws -> [String: Any] {
        let body: Data
        if let value = request.httpBody { body = value }
        else if let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var collected = Data(); var bytes = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&bytes, maxLength: bytes.count)
                guard count >= 0 else { throw URLError(.cannotDecodeRawData) }
                if count == 0 { break }
                collected.append(contentsOf: bytes.prefix(count))
            }
            body = collected
        } else { throw URLError(.badServerResponse) }
        return try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
    }
    override func startLoading() {
        do {
            guard let url = request.url, url.host == "panels.invalid" else { throw URLError(.badURL) }
            let data: Data
            if url.path == "/api/recap-requests" {
                guard request.httpMethod == "POST", request.value(forHTTPHeaderField: "Content-Type") == "application/json" else { throw URLError(.badServerResponse) }
                let object = try bodyObject()
                let target = try XCTUnwrap(object["target"] as? [String: String])
                guard Set(object.keys) == ["target"], Set(target.keys) == ["kind", "id"] else { throw URLError(.badServerResponse) }
                switch (target["kind"], target["id"]) {
                case ("chief", "chief"): data = Data(#"{"fate":"queued","queue_position":1}"#.utf8)
                case ("manager", "refused"): data = Data(#"{"fate":"refused","refusal_reason":"backend_did_not_start"}"#.utf8)
                case ("manager", "uncertain"): data = Data(#"{"fate":"uncertain"}"#.utf8)
                default: throw URLError(.badServerResponse)
                }
            } else if url.path.hasPrefix("/api/managers/") || url.path == "/api/workers/chief-of-staff/recap" || url.path.hasPrefix("/api/tickets/worker/") {
                let object = try XCTUnwrap(try bodyObject() as? [String: AnyHashable])
                let valid: Bool
                switch (url.path, request.httpMethod) {
                case ("/api/managers/manager/settled", "PATCH"): valid = object == ["settled": true]
                case ("/api/managers/manager", "PATCH"): valid = object == ["goal": "goal text"] || object == ["recap": "recap text"]
                case ("/api/workers/chief-of-staff/recap", "PUT"): valid = object == ["recap": "recap text"]
                case ("/api/tickets/worker/backlog", "POST"), ("/api/tickets/worker/resume", "POST"): valid = object.isEmpty
                default: valid = false
                }
                guard valid, request.value(forHTTPHeaderField: "Content-Type") == "application/json" else { throw URLError(.badServerResponse) }
                data = Data("{}".utf8)
            } else if url.path == "/api/tickets" {
                let query = Dictionary(uniqueKeysWithValues: (URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []).map { ($0.name, $0.value ?? "") })
                guard request.httpMethod == "GET", query["manager_kind"] == "chief", query["stage"] == "done", query["include_terminal"] == "true" else { throw URLError(.badServerResponse) }
                let fixture = try XCTUnwrap(Bundle(for: WorkerScreenTests.self).url(forResource: "tickets", withExtension: "json"))
                let root = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: fixture)) as? [String: Any])
                var row = try XCTUnwrap((root["tickets"] as? [[String: Any]])?.first)
                let offset = query["offset"] == "30" ? 30 : 0
                row["id"] = offset == 0 ? "first" : "second"
                data = try JSONSerialization.data(withJSONObject: ["tickets": [row], "page": ["match_count": 2, "return_count": 1, "offset": offset, "limit": 30, "complete": offset == 30, "next_offset": offset == 0 ? 30 : NSNull()]])
            } else { throw URLError(.badURL) }
            let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
}
