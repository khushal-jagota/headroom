import XCTest
@testable import Panels

final class FeedbackTests: XCTestCase {
    func testWireContractGroupsAndAttentionPrecedence() throws {
        let response = try APIClient.decoder().decode(FeedbackResponse.self, from: feedbackFixture)
        XCTAssertEqual(response.openCount, 1)
        XCTAssertEqual(response.handledCount, 2)
        XCTAssertEqual(response.orderedHandledGroups.first?.ticket?.stateLabel, "Needs your approval")
        XCTAssertEqual(response.orderedHandledGroups.last?.notes.first?.pageAddress, nil)
        XCTAssertEqual(response.open.first?.createdAt, 1_792_690_000)
    }

    func testPageContextStaysInKnownNativeDestinations() {
        XCTAssertEqual(FeedbackPageDestination.parse("#/workspace/manager/si_1/t_2?field=plan"), .route(.ticket("t_2")))
        XCTAssertEqual(FeedbackPageDestination.parse("#/workspace/utility/feedback"), .route(.feedback))
        XCTAssertEqual(FeedbackPageDestination.parse("#/workspace/chief-of-staff"), .route(.chief))
        XCTAssertNil(FeedbackPageDestination.parse("https://untrusted.example"))
        XCTAssertNil(FeedbackPageDestination.parse("#/unknown"))
        XCTAssertNil(FeedbackPageDestination.parse("#/workspace bad"))
    }

    @MainActor func testFailedWriteRetainsNotesAndSuccessfulWriteRefreshes() async throws {
        let stub = try FeedbackStub()
        let model = FeedbackModel()
        await model.load(using: stub)
        let note = try XCTUnwrap(model.response?.open.first)
        let failed = await model.update(note, action: .dismiss, using: stub)
        XCTAssertFalse(failed)
        XCTAssertEqual(model.response?.open.first?.id, note.id)
        XCTAssertNotNil(model.error)
        XCTAssertNil(model.pendingNoteID)
        await stub.allowWrite()
        let succeeded = await model.update(note, action: .dismiss, using: stub)
        XCTAssertTrue(succeeded)
        XCTAssertEqual(model.response?.openCount, 0)
        XCTAssertNil(model.error)
    }

    func testMutationUsesPostAndEncodesOneIdentifierComponent() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [FeedbackURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "https://feedback-stub.invalid")!, session: session)
        let note = try await client.updateFeedback("note/a", action: .reopen)
        XCTAssertEqual(note.id, "n1")
    }
}

private actor FeedbackStub: FeedbackServing {
    var response: FeedbackResponse
    var fails = true
    init() throws { response = try APIClient.decoder().decode(FeedbackResponse.self, from: feedbackFixture) }
    func allowWrite() { fails = false }
    func feedback() async throws -> FeedbackResponse { response }
    func updateFeedback(_ id: String, action: FeedbackAction) async throws -> FeedbackNote {
        if fails { throw URLError(.notConnectedToInternet) }
        let note = response.open[0]
        response = FeedbackResponse(openCount: 0, open: [], handledGroups: [FeedbackHandledGroup(ticket: nil, notes: [note])])
        return note
    }
}

private final class FeedbackURLProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard request.httpMethod == "POST",
              request.url?.absoluteString == "https://feedback-stub.invalid/api/feedback/note%2Fa/reopen" else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL)); return
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(feedbackNoteJSON.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private let feedbackNoteJSON = ##"{"id":"n1","text":"Keep the selection visible.","page_address":"#/workspace/chief-of-staff","page_label":"Chief of Staff","state":"open","ticket_id":null,"created_at":1792690000,"updated_at":1792690000,"handled_at":null}"##
private let feedbackFixture = Data("""
{"open_count":1,"open":[\(feedbackNoteJSON)],"handled_groups":[
{"ticket":null,"notes":[{"id":"n2","text":"Dismissed note","state":"handled","created_at":1792690000,"updated_at":1792690000}]},
{"ticket":{"id":"t1","title":"Polish feedback","stage":"execution","ticket_status":"agent","awaiting_approval":true,"awaiting_answer":true,"assigned":true,"agent_state":"working"},"notes":[\(feedbackNoteJSON)]}
]}
""".utf8)
