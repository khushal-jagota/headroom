import XCTest
@testable import Panels

@MainActor final class IdeasTests: XCTestCase {
    func testBlankCaptureDoesNotWriteAndFailureKeepsDraft() async {
        let model = IdeasModel()
        let service = IdeasStub(fails: true)
        model.title = " \n "
        let blankResult = await model.capture(using: service)
        XCTAssertFalse(blankResult)
        let blankCount = await service.captures.count
        XCTAssertEqual(blankCount, 0)
        model.title = " Keep this "
        model.detail = "Detail"
        model.projectID = "project-1"
        let failedResult = await model.capture(using: service)
        XCTAssertFalse(failedResult)
        XCTAssertEqual(model.title, " Keep this ")
        XCTAssertEqual(model.detail, "Detail")
        XCTAssertEqual(model.projectID, "project-1")
        XCTAssertNotNil(model.captureError)
        XCTAssertFalse(model.isCapturing)
    }

    func testAcknowledgedCaptureUsesServerRecordAndClearsDraft() async throws {
        let model = IdeasModel()
        let service = IdeasStub()
        model.title = " New idea \n"
        model.detail = "  Detail stays formatted  "
        model.projectID = "project-1"
        let result = await model.capture(using: service)
        XCTAssertTrue(result)
        let captures = await service.captures
        let sent = try XCTUnwrap(captures.first)
        XCTAssertEqual(sent.title, "New idea")
        XCTAssertEqual(sent.body, "  Detail stays formatted  ")
        XCTAssertEqual(sent.projectID, "project-1")
        XCTAssertEqual(model.ideas.first?.id, "server-id")
        XCTAssertEqual(model.ideas.first?.project, "Panels")
        XCTAssertEqual(model.title, "")
        XCTAssertEqual(model.detail, "")
        XCTAssertNil(model.projectID)
    }

    func testCaptureAdapterUsesWireContractAndStubTransportOnly() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [IdeaRequestStub.self]
        let client = APIClient(baseURL: URL(string: "https://ideas.invalid")!, session: URLSession(configuration: configuration))
        let idea = try await client.captureIdea(IdeaCapture(title: "New idea", body: nil, projectID: "project-1"))
        XCTAssertEqual(idea.id, "server-id")
        XCTAssertEqual(idea.project, "Panels")
        let request = try XCTUnwrap(IdeaRequestStub.lastRequest)
        XCTAssertEqual(request.url?.absoluteString, "https://ideas.invalid/api/ideas")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        let stream = try XCTUnwrap(request.httpBodyStream)
        stream.open()
        defer { stream.close() }
        var bytes = [UInt8](repeating: 0, count: 1024)
        let count = stream.read(&bytes, maxLength: bytes.count)
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(bytes.prefix(count))) as? [String: String])
        XCTAssertEqual(payload, ["title": "New idea", "project_id": "project-1"])
    }
}

private actor IdeasStub: IdeasServing {
    let fails: Bool
    var captures: [IdeaCapture] = []
    init(fails: Bool = false) { self.fails = fails }
    func ideas() async throws -> IdeasResponse { IdeasResponse(ideas: []) }
    func ideaProjects() async throws -> IdeaProjectsResponse { IdeaProjectsResponse(projects: []) }
    func captureIdea(_ capture: IdeaCapture) async throws -> Idea {
        captures.append(capture)
        if fails { throw APIError(status: 503, message: "Try again later.") }
        return Idea(id: "server-id", title: capture.title, body: capture.body ?? "", projectId: capture.projectID, project: "Panels", createdAt: 1, updatedAt: 1)
    }
}

private final class IdeaRequestStub: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var lastRequest: URLRequest?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lastRequest = request
        let data = Data(#"{"id":"server-id","title":"New idea","body":"","project_id":"project-1","project":"Panels","created_at":1,"updated_at":1}"#.utf8)
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 201, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
