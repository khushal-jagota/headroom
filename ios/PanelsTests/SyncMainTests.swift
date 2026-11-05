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

    func testDeploymentDistinguishesUnconfirmedInstallationFromUnconfirmedReport() {
        let now = Date(timeIntervalSince1970: 1000)
        var status = DeploymentStatus(state: "preparing", deployedSha: nil, targetSha: nil, outcome: nil, detail: nil, validUntil: "1970-01-01T00:16:00Z")
        XCTAssertEqual(DeploymentStatusPresentation.resolve(status, connected: true, readFailed: false, now: now), .unknown)
        XCTAssertTrue(DeploymentStatusPresentation.detail(status, state: .unknown)!.contains("Chief review is required"))
        status.installationConfirmed = true
        status.unknownDisplayUntil = "1970-01-01T00:20:00Z"
        XCTAssertEqual(DeploymentStatusPresentation.resolve(status, connected: true, readFailed: false, now: now), .unknown)
        XCTAssertTrue(DeploymentStatusPresentation.detail(status, state: .unknown)!.contains("installed and available"))
        status.unknownDisplayUntil = "1970-01-01T00:16:40Z"
        XCTAssertNil(DeploymentStatusPresentation.resolve(status, connected: true, readFailed: false, now: now))
        XCTAssertEqual(DeploymentStatusPresentation.resolve(status, connected: false, readFailed: false, now: now), .reconnecting)
    }

    func testDependencyBlockedAgentGoesInBlockedOnPageButStaysOutOfRail() throws {
        let json = #"{"id":"t1","title":"Blocked","locale":"macos","worker_type":null,"stage":"implementation","ticket_status":"agent","is_backlogged":false,"blocked":true,"recap_preview":"","awaiting_reply":false,"awaiting_answer":false,"awaiting_approval":false,"awaiting_agent_approval":false,"assigned":false,"agent_state":"working"}"#
        let ticket = try APIClient.decoder().decode(TicketSummary.self, from: Data(json.utf8))
        XCTAssertEqual(ticket.locale, "macos")
        XCTAssertNil(ticket.workerType)
        XCTAssertEqual(WorkerGroup.classify(ticket), .blocked)
        XCTAssertNil(WorkspaceGrouping.section(for: ticket))
    }

}

extension SyncMainTests {
    func testNativeBuildRejectionBlocksRequestsAndSurvivesClientRecreation() throws {
        let server = URL(string: "https://\(UUID().uuidString).example")!
        let client = APIClient(baseURL: server)
        let request = try client.request(try client.url("/api/review"))
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Panels-iOS-Build"), NativeBuildGate.build)
        let response = HTTPURLResponse(url: server.appendingPathComponent("api/review"), statusCode: 426, httpVersion: nil, headerFields: nil)!
        XCTAssertThrowsError(try APIClient.validate(response, data: Data(#"{"error":{"code":"ios_update_required","message":"Update Panels from TestFlight"}}"#.utf8))) {
            XCTAssertTrue(($0 as? APIError)?.isUpdateRequired == true)
        }
        let recreated = APIClient(baseURL: server)
        XCTAssertThrowsError(try recreated.request(try recreated.url("/api/tickets/t1/accept/brief")))
        XCTAssertFalse(NativeBuildGate.requiresUpdate(URL(string: "https://different.example")!))
    }

    func testGroupedReviewRetainsProposalAndMessageIdentity() throws {
        let json = #"{"running_worker_count":0,"items":[{"review_item_type":"ticket","ticket_id":"t1","title":"Work","waiting_since":1,"proposal":{"review_item_type":"proposal","ticket_id":"t1","field":"implementation","title":"Work","waiting_since":2},"messages":[{"review_item_type":"message","conversation_id":"c1","address_id":"a1","owner_kind":"ticket","owner_id":"t1","title":"Work","waiting_since":1}]}]}"#
        let response = try APIClient.decoder().decode(ReviewResponse.self, from: Data(json.utf8))
        XCTAssertEqual(response.items.count, 2)
        XCTAssertEqual(response.items[0].field, "implementation")
        XCTAssertEqual(response.items[0].waitingSince, 2)
        XCTAssertEqual(response.items[1].addressId, "a1")
        XCTAssertEqual(response.items[1].owner, .ticket("t1"))
    }
}
