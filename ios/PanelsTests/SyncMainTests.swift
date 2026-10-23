import XCTest
@testable import Panels

final class SyncMainTests: XCTestCase {
    func testSuccessRequiresBothDeadlinesAndConnection() throws {
        let now = Date(timeIntervalSince1970: 1000)
        func visible(_ valid: String?, _ display: String?, connected: Bool = true) -> DeploymentStatusPresentation? {
            let status = DeploymentStatus(state: "back_up", deployedSha: nil, targetSha: nil, outcome: "succeeded", detail: nil, validUntil: valid, successDisplayUntil: display)
            return DeploymentStatusPresentation.resolve(status, connected: connected, readFailed: false, now: now)
        }
        let future = "1970-01-01T00:20:00Z"
        let past = "1970-01-01T00:16:40Z"
        XCTAssertEqual(visible(future, future), .deployed)
        XCTAssertNil(visible(future, past))
        XCTAssertNil(visible(past, future))
        XCTAssertNil(visible(future, nil))
        XCTAssertNil(visible(future, "invalid"))
        XCTAssertEqual(visible(future, future, connected: false), .reconnecting)
    }

    func testDependencyBlockedAgentGoesInBlockedOnPageButStaysOutOfRail() throws {
        let json = #"{"id":"t1","title":"Blocked","worker_type":"coding","stage":"implementation","ticket_status":"agent","is_backlogged":false,"blocked":true,"recap_preview":"","awaiting_reply":false,"awaiting_answer":false,"awaiting_approval":false,"awaiting_agent_approval":false,"assigned":false,"agent_state":"working"}"#
        let ticket = try APIClient.decoder().decode(TicketSummary.self, from: Data(json.utf8))
        XCTAssertEqual(WorkerGroup.classify(ticket), .blocked)
        XCTAssertNil(WorkspaceGrouping.section(for: ticket))
    }

    func testInterruptionAccountAndQueuedRestartDoNotClaimResumption() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [InterruptionStub.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let api = APIClient(baseURL: URL(string: "https://interruption.invalid")!, session: session)
        let summary = try await api.deploymentInterruptions()
        XCTAssertEqual(summary.accounts.first?.captureState, "unavailable")
        XCTAssertEqual(summary.accounts.first?.entries.first?.recoveryLabel, "Unable to resume")
        XCTAssertNil(summary.accounts.first?.entries.first?.conversationId)
        let result = try await api.restartInterruptedWorker("t1")
        XCTAssertFalse(result.started)
        XCTAssertEqual(result.message, "Restart queued. The agent has not resumed yet.")
    }
}

private final class InterruptionStub: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let reply: String
        switch (request.httpMethod, request.url?.path) {
        case ("GET", "/api/deployment-interruptions"):
            reply = #"{"accounts":[{"deployment_id":"deploy1","capture_state":"unavailable","detail":"Missing capture","entries":[{"interruption_id":"i1","conversation_id":null,"display_name":"Worker","owner_kind":"ticket","owner_id":"t1","recovery_state":"unable_to_resume","detail":"Interrupted","can_restart":false}]}]}"#
        case ("POST", "/api/tickets/t1/restart-worker"):
            guard request.value(forHTTPHeaderField: "Content-Type") == "application/json" else {
                client?.urlProtocol(self, didFailWithError: URLError(.badURL)); return
            }
            reply = #"{"started":false,"delivery_fate":"queued","not_started_because":null}"#
        default: client?.urlProtocol(self, didFailWithError: URLError(.badURL)); return
        }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(reply.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
