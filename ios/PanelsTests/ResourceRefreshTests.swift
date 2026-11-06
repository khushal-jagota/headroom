import Foundation
import os
import XCTest
@testable import Panels

@MainActor final class ResourceRefreshTests: XCTestCase {
    func testOnlyUnreachableTransportFailuresRequireTailscaleState() {
        for code in [URLError.cannotFindHost, .dnsLookupFailed, .cannotConnectToHost, .notConnectedToInternet] {
            XCTAssertTrue(ServerReadFailure(URLError(code)).isServerUnreachable, "\(code)")
            XCTAssertTrue(ServerReadFailure(NSError(domain: NSURLErrorDomain, code: code.rawValue)).isServerUnreachable)
        }
        for code in [URLError.timedOut, .networkConnectionLost, .cancelled, .secureConnectionFailed, .badServerResponse] {
            XCTAssertFalse(ServerReadFailure(URLError(code)).isServerUnreachable, "\(code)")
        }
        let failure = ServerReadFailure(APIError(status: 503, message: "Request failed on the reachable server."))
        XCTAssertFalse(failure.isServerUnreachable)
        XCTAssertEqual(failure.message, "Request failed on the reachable server.")
        XCTAssertFalse(ServerReadFailure(NSError(domain: NSPOSIXErrorDomain, code: URLError.cannotFindHost.rawValue)).isServerUnreachable)
    }

    func testUnreachableReadsKeepSavedResponsesAndClearOnRecovery() async throws {
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
        XCTAssertNotNil(store.review)
        RefreshProtocol.state.withLock { $0.failure = .cannotFindHost }
        await store.refresh()
        XCTAssertTrue(store.serverUnreachable)
        XCTAssertTrue(store.errors["Review"]?.isServerUnreachable == true)
        XCTAssertNotNil(store.review, "The unavailable screen must not delete cached data")
        RefreshProtocol.state.withLock { $0.failure = .badServerResponse }
        await store.refresh()
        XCTAssertFalse(store.serverUnreachable, "Other request failures retain the current refresh behavior")
        XCTAssertNotNil(store.errors["Review"])
        RefreshProtocol.state.withLock { $0.failure = nil }
        await store.refresh()
        XCTAssertFalse(store.serverUnreachable)
        XCTAssertTrue(store.errors.isEmpty)
    }

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
        XCTAssertTrue(store.executorHealth?.activityKnown == true)
        XCTAssertEqual(store.chief?.label, "Chief 1")
        let cached = try XCTUnwrap(LocalStore.shared.record(server: "https://refresh.invalid", kind: "cache", id: "https://refresh.invalid/api/workers/chief-of-staff/settings")?.payload)
        XCTAssertEqual((try JSONSerialization.jsonObject(with: cached) as? [String: Any])?["future_field"] as? String, "preserved")
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
    var failure: URLError.Code?
    mutating func response(_ url: URL) throws -> Data {
        paths.append(url.path)
        if let failure { throw URLError(failure) }
        if url.path == "/api/resource-versions" {
            if !supportsVersions { throw URLError(.unsupportedURL) }
            return try JSONSerialization.data(withJSONObject: ["epoch": epoch, "resources": ["/api/workers/chief-of-staff/settings", "/api/deployment-status"].map { ["key": $0, "revision": revision] }])
        }
        let key = url.path == "/api/resource-body" ? URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.first!.value! : url.path
        if url.path == "/api/resource-body", !supportsVersions { throw URLError(.unsupportedURL) }
        let body: Any
        switch key {
        case "/api/managers", "/api/tickets": return try Data(contentsOf: fixtures.appendingPathComponent(String(key.dropFirst(5)) + ".json"))
        case "/api/executor/health": body = ["mode":"remote", "identity":["executor_incarnation":"current", "connection_generation":1], "connection_state":"ready", "last_contact_at_ms":1000] as [String:Any]
        case "/api/review": body = ["items": [], "running_worker_count": 0]
        case "/api/workers/chief-of-staff/settings": body = ["future_field": "preserved", "employee_id": "chief", "label": "Chief " + revision, "recap": "", "skill": ["name": "chief", "description": "", "markdown_body": ""], "launch_defaults": ["employee_backend": "codex"]] as [String: Any]
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
