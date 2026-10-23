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

}
