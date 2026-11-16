import Foundation
import os
import XCTest
@testable import Panels

final class OwnerCookieTests: XCTestCase {
    func testBootstrapLoadsTheWebAppPageOnlyWhenTheOwnerCookieIsMissing() async throws {
        let base = try XCTUnwrap(URL(string: "https://owner-cookie.invalid"))
        let storage = HTTPCookieStorage()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [OwnerCookieStub.self]
        configuration.httpCookieStorage = storage
        let client = APIClient(baseURL: base, session: URLSession(configuration: configuration))
        OwnerCookieStub.requests.withLock { $0 = [] }

        await OwnerCookieBootstrap().ensure(client, storage: storage)
        let first = OwnerCookieStub.requests.withLock { $0 }
        XCTAssertEqual(first.map(\.httpMethod), ["GET"])
        XCTAssertEqual(first.first?.url?.path, "/")
        XCTAssertNotNil(first.first?.value(forHTTPHeaderField: "X-Panels-iOS-Build"))

        storage.setCookie(try XCTUnwrap(HTTPCookie(properties: [
            .name: OwnerCookieBootstrap.cookieName, .value: "x", .domain: "owner-cookie.invalid", .path: "/",
        ])))
        await OwnerCookieBootstrap().ensure(client, storage: storage)
        XCTAssertEqual(OwnerCookieStub.requests.withLock { $0 }.count, 1)
    }
}

private final class OwnerCookieStub: URLProtocol, @unchecked Sendable {
    static let requests = OSAllocatedUnfairLock<[URLRequest]>(initialState: [])
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.requests.withLock { $0.append(request) }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
