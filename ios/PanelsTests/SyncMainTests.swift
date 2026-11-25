import XCTest
@testable import Panels

final class SyncMainTests: XCTestCase {
    func testDependencyBlockedAgentGoesInBlockedOnPage() throws {
        let json = #"{"id":"t1","title":"Blocked","locale":"macos","worker_type":null,"stage":"implementation","ticket_status":"agent_owned","is_backlogged":false,"blocked":true,"recap_preview":"","awaiting_reply":false,"awaiting_answer":false,"awaiting_approval":false,"awaiting_agent_approval":false,"assigned":false,"agent_state":"working"}"#
        let ticket = try APIClient.decoder().decode(TicketSummary.self, from: Data(json.utf8))
        XCTAssertEqual(ticket.locale, "macos")
        XCTAssertNil(ticket.workerType)
        XCTAssertEqual(WorkerGroup.classify(ticket), .blocked)
    }

}

extension SyncMainTests {
    func testNativeBuildRejectionBlocksRequestsAndSurvivesClientRecreation() throws {
        let server = URL(string: "https://\(UUID().uuidString).example")!
        let client = APIClient(baseURL: server)
        let request = try client.request(try client.url("/api/review"))
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Panels-iOS-Build"), NativeBuildGate.build)
        let identityData = try XCTUnwrap(request.value(forHTTPHeaderField: "X-Panels-Client")).data(using: .utf8)!
        let identity = try XCTUnwrap(JSONSerialization.jsonObject(with: identityData) as? [String: Any])
        XCTAssertEqual(identity["kind"] as? String, "ios")
        XCTAssertEqual(identity["version"] as? String, NativeBuildGate.build)
        XCTAssertEqual(identity["build"] as? Int, Int(NativeBuildGate.build))
        XCTAssertNotNil(UUID(uuidString: try XCTUnwrap(identity["device_id"] as? String)))
        let another = APIClient(baseURL: server)
        let anotherRequest = try another.request(try another.url("/api/changes"))
        let anotherData = try XCTUnwrap(anotherRequest.value(forHTTPHeaderField: "X-Panels-Client")).data(using: .utf8)!
        let anotherIdentity = try XCTUnwrap(JSONSerialization.jsonObject(with: anotherData) as? [String: Any])
        XCTAssertEqual(identity["device_id"] as? String, anotherIdentity["device_id"] as? String)
        let response = HTTPURLResponse(url: server.appendingPathComponent("api/review"), statusCode: 426, httpVersion: nil, headerFields: nil)!
        XCTAssertThrowsError(try APIClient.validate(response, data: Data(#"{"error":{"code":"ios_update_required","message":"Update Panels from TestFlight"}}"#.utf8))) {
            XCTAssertTrue(($0 as? APIError)?.isUpdateRequired == true)
        }
        let recreated = APIClient(baseURL: server)
        XCTAssertThrowsError(try recreated.request(try recreated.url("/api/tickets/t1/accept/brief")))
        XCTAssertFalse(NativeBuildGate.requiresUpdate(URL(string: "https://different.example")!))
    }

}
