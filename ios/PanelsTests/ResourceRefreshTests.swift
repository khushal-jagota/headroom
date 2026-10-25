import Foundation
import os
import XCTest
@testable import Panels

@MainActor final class ResourceRefreshTests: XCTestCase {
    func testChangeRefreshUsesCoherentVersionsAndKeepsOnlyVisibleWorkspaceReads() async throws {
#if SWIFT_PACKAGE
        let fixtures = Bundle.module.url(forResource: "Fixtures", withExtension: nil)!
#else
        let fixtures = Bundle(for: Self.self).resourceURL!
#endif
        RefreshProtocol.state.withLock { $0 = RefreshFixture(fixtures: fixtures) }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RefreshProtocol.self]
        let store = AppStore(baseURL: URL(string: "https://refresh.invalid")!, session: URLSession(configuration: config))
        await store.refresh()
        XCTAssertTrue(store.errors.isEmpty)
        XCTAssertEqual(store.chief?.label, "Chief 1")
        RefreshProtocol.state.withLock { $0.paths = [] }
        await store.refresh(changesOnly: true)
        var paths = RefreshProtocol.state.withLock { $0.paths }
        XCTAssertFalse(paths.contains("/api/resource-body"))
        XCTAssertTrue(paths.contains("/api/managers"))
        XCTAssertTrue(paths.contains("/api/tickets"))
        XCTAssertTrue(paths.contains("/api/review"))
        store.workspaceVisible = false
        RefreshProtocol.state.withLock { $0.paths = []; $0.revision = "2" }
        await store.refresh(changesOnly: true)
        paths = RefreshProtocol.state.withLock { $0.paths }
        XCTAssertFalse(paths.contains("/api/managers"))
        XCTAssertFalse(paths.contains("/api/tickets"))
        XCTAssertEqual(paths.filter { $0 == "/api/resource-body" }.count, 2)
        XCTAssertEqual(store.chief?.label, "Chief 2")
        // Epoch changes invalidate even an identical revision token.
        RefreshProtocol.state.withLock { $0.paths = []; $0.epoch = "restarted" }
        await store.refresh(changesOnly: true)
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.paths.filter { $0 == "/api/resource-body" }.count }, 2)
        // Returning to Workspace or an explicit refresh reads its full loaded window.
        RefreshProtocol.state.withLock { $0.paths = [] }
        await store.refresh()
        XCTAssertTrue(RefreshProtocol.state.withLock { $0.paths.contains("/api/managers") })
        XCTAssertTrue(RefreshProtocol.state.withLock { $0.paths.contains("/api/tickets") })
        // Failed probes / unsupported version API preserve ordinary reads.
        RefreshProtocol.state.withLock { $0.paths = []; $0.supportsVersions = false }
        await store.refresh(changesOnly: true)
        XCTAssertTrue(store.errors.isEmpty)
        XCTAssertTrue(RefreshProtocol.state.withLock { $0.paths.contains("/api/workers/chief-of-staff/settings") })
        XCTAssertTrue(RefreshProtocol.state.withLock { $0.paths.contains("/api/deployment-status") })
    }
    func testUnknownVersionAndUnacceptedBodyNeverSuppressRefresh() {
        var state = ResourceVersionState()
        let key = "/api/example"
        let probe = ResourceVersions(epoch: "a", resources: [.init(key: key, revision: "2")])
        XCTAssertFalse(state.isCurrent(key, probe: probe))
        state.accept(ResourceBody(epoch: "a", key: key, revision: "1", body: "old"))
        XCTAssertFalse(state.isCurrent(key, probe: probe), "The probe cannot certify an older body")
        state.accept(ResourceBody(epoch: "a", key: key, revision: "2", body: "current"))
        XCTAssertTrue(state.isCurrent(key, probe: probe))
        XCTAssertFalse(state.isCurrent(key, probe: ResourceVersions(epoch: "a", resources: [.init(key: key, revision: nil)])))
        XCTAssertFalse(state.isCurrent(key, probe: nil))
        state.invalidate(key)
        XCTAssertFalse(state.isCurrent(key, probe: probe))
    }
}

private struct RefreshFixture {
    var fixtures: URL
    var paths: [String] = []
    var revision = "1"
    var epoch = "initial"
    var supportsVersions = true
    mutating func response(_ url: URL) throws -> Data {
        paths.append(url.path)
        if url.path == "/api/resource-versions" {
            if !supportsVersions { throw URLError(.unsupportedURL) }
            return try JSONSerialization.data(withJSONObject: ["epoch": epoch, "resources": ["/api/workers/chief-of-staff/settings", "/api/deployment-status"].map { ["key": $0, "revision": revision] }])
        }
        let key = url.path == "/api/resource-body" ? URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.first!.value! : url.path
        if url.path == "/api/resource-body", !supportsVersions { throw URLError(.unsupportedURL) }
        let body: Any
        switch key {
        case "/api/managers", "/api/tickets": return try Data(contentsOf: fixtures.appendingPathComponent(String(key.dropFirst(5)) + ".json"))
        case "/api/review": body = ["items": [], "running_worker_count": 0]
        case "/api/workers/chief-of-staff/settings": body = ["employee_id": "chief", "label": "Chief " + revision, "recap": "", "skill": ["name": "chief", "description": "", "markdown_body": ""], "launch_defaults": ["employee_backend": "codex"]] as [String: Any]
        case "/api/deployment-status": body = ["state": "idle"]
        default: throw URLError(.unsupportedURL)
        }
        return try JSONSerialization.data(withJSONObject: url.path == "/api/resource-body" ? ["epoch": epoch, "key": key, "revision": revision, "body": body] : body)
    }
}
private final class RefreshProtocol: URLProtocol, @unchecked Sendable {
    static let state = OSAllocatedUnfairLock(initialState: RefreshFixture(fixtures: URL(fileURLWithPath: "/unused")))
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "refresh.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let data = try Self.state.withLock { try $0.response(request.url!) }
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}
