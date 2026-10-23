import XCTest
@testable import Panels

final class NotificationTests: XCTestCase {
    func testExistingWebRoutesAndReviewItemBecomeNativeDestinations() {
        XCTAssertEqual(NotificationRouting.destination("/#/workspace/t_42"), .ticket("t_42"))
        XCTAssertEqual(NotificationRouting.destination("/#/workspace/item/m_9"), .orchestrator("m_9"))
        XCTAssertEqual(NotificationRouting.destination("/#/agents/chief-of-staff"), .chief)
        XCTAssertEqual(NotificationRouting.destination("/#/review?ticket=t_42"), .review(ticketID: "t_42"))
        XCTAssertEqual(NotificationRouting.destination("/#/review"), .review(ticketID: nil))
        for route in ["https://evil.example/#/workspace/t_42", "//#/workspace/t_42", "/#/workspace/a%2Fb", "/#/workspace/..", "/#/workspace/", "/#/review?ticket=", "/#/unknown", "/#/workspace/t_42?redirect=evil"] {
            XCTAssertNil(NotificationRouting.destination(route), route)
        }
    }

    @MainActor func testColdLaunchRetainsTapAndRejectsAnotherServer() throws {
        let controller = NotificationController()
        let payload: [AnyHashable: Any] = ["server_url": "https://panels.example:443/", "route": "/#/workspace/t_42"]
        controller.receivedTap(NotificationRouting.parse(payload))
        XCTAssertNotNil(controller.pendingOpen)
        XCTAssertEqual(controller.consumeOpen(for: URL(string: "https://panels.example")!), .ticket("t_42"))
        XCTAssertNil(controller.consumeOpen(for: URL(string: "https://panels.example")!))
        controller.receivedTap(NotificationRouting.parse(payload))
        XCTAssertNil(controller.consumeOpen(for: URL(string: "https://other.example")!))
        XCTAssertNotNil(controller.routingError)
        XCTAssertNil(NotificationRouting.parse(["route": "/#/workspace/t_42"]))
        XCTAssertNil(NotificationRouting.parse(["server_url": "https://user:pass@panels.example", "route": "/#/review"]))
    }

    func testRegistrationEncodesNativeContractAndSurfacesRejection() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [NativePushURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let device = NativePushRegistration(deviceToken: "0011aaff", bundleId: "app.panels.ios.pios-notify", environment: "development")
        try await NativePushAPI(client: APIClient(baseURL: URL(string: "https://push-stub.invalid")!, session: session)).register(device)
        try await NativePushAPI(client: APIClient(baseURL: URL(string: "https://push-stub.invalid")!, session: session)).unregister(device)
        do {
            try await NativePushAPI(client: APIClient(baseURL: URL(string: "https://rejected.invalid")!, session: session)).register(device)
            XCTFail("A refused registration must not be reported as connected")
        } catch let error as APIError { XCTAssertEqual(error.status, 503) }
    }
}

private final class NativePushURLProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var body = request.httpBody ?? Data()
        if let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                body.append(buffer, count: count)
            }
        }
        guard ["PUT", "DELETE"].contains(request.httpMethod ?? ""), request.url?.path == "/api/notifications/apns/devices",
              request.value(forHTTPHeaderField: "Content-Type") == "application/json",
              let payload = try? JSONSerialization.jsonObject(with: body) as? [String: String],
              payload == ["device_token": "0011aaff", "bundle_id": "app.panels.ios.pios-notify", "environment": "development"] else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL)); return
        }
        let status = request.url?.host == "rejected.invalid" ? 503 : 204
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
