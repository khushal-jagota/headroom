import Foundation
import os
import XCTest
@testable import Panels

@MainActor final class ResourceRefreshTests: XCTestCase {
    func testChangeRefreshReadsOneScopedViewAndCoherentVersions() async throws {
        RefreshProtocol.state.withLock { $0 = RefreshFixture() }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RefreshProtocol.self]
        let store = AppStore(baseURL: URL(string: "https://refresh.invalid")!, session: URLSession(configuration: config))
        await store.refresh()
        XCTAssertTrue(store.errors.isEmpty)
        XCTAssertEqual(store.chief?.label, "Chief 1")
        let cached = try XCTUnwrap(LocalStore.shared.record(server: "https://refresh.invalid", kind: "cache", id: "https://refresh.invalid/api/workers/chief-of-staff/settings")?.payload)
        XCTAssertEqual((try JSONSerialization.jsonObject(with: cached) as? [String: Any])?["future_field"] as? String, "preserved")
        // One view snapshot replaces the Review, manager and ticket list reads.
        store.setNavigationDemand("workspace", [ViewWindowRequest(section: "rail_parents", limit: 60), ViewWindowRequest(section: "rail_tickets", owner: .chief, limit: 30)])
        RefreshProtocol.state.withLock { $0.paths = [] }
        await store.refresh(changesOnly: true)
        var paths = RefreshProtocol.state.withLock { $0.paths }
        XCTAssertFalse(paths.contains("/api/resource-body"))
        XCTAssertEqual(paths.filter { $0 == "/api/navigation/view/snapshot" }.count, 1)
        XCTAssertTrue(Set(paths).isDisjoint(with: ["/api/review", "/api/managers", "/api/tickets", "/api/workers"]))
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.sections }, ["review", "rail_parents", "rail_tickets"])
        XCTAssertTrue(store.workspaceLoaded)
        XCTAssertNotNil(store.review)
        // A withdrawn screen leaves the scope; Workspace keeps its last coherent rows.
        store.setNavigationDemand("workspace", nil)
        RefreshProtocol.state.withLock { $0.paths = []; $0.revision = "2" }
        await store.refresh(changesOnly: true)
        paths = RefreshProtocol.state.withLock { $0.paths }
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.sections }, ["review"])
        XCTAssertTrue(store.workspaceLoaded)
        XCTAssertEqual(paths.filter { $0 == "/api/resource-body" }.count, 2)
        XCTAssertEqual(store.chief?.label, "Chief 2")
        // Epoch changes invalidate even an identical revision token.
        RefreshProtocol.state.withLock { $0.paths = []; $0.epoch = "restarted" }
        await store.refresh(changesOnly: true)
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.paths.filter { $0 == "/api/resource-body" }.count }, 2)
        // An explicit refresh reads the view again.
        RefreshProtocol.state.withLock { $0.paths = [] }
        await store.refresh()
        XCTAssertTrue(RefreshProtocol.state.withLock { $0.paths.contains("/api/navigation/view/snapshot") })
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
    var paths: [String] = []
    var revision = "1"
    var epoch = "initial"
    var supportsVersions = true
    var sections: [String] = []
    mutating func response(_ url: URL) throws -> Data {
        paths.append(url.path)
        if url.path == "/api/navigation/view/snapshot" {
            let raw = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.first { $0.name == "view" }!.value!
            let request = try JSONSerialization.jsonObject(with: Data(raw.utf8)) as! [String: Any]
            let windows = request["windows"] as! [[String: Any]]
            sections = windows.map { $0["section"] as! String }
            let authority: [String: Any] = ["delivery_session_id": "d", "availability": ["state": "current", "generation": "1", "reason": NSNull()], "permit": ["scope": "s", "runtime_epoch": "r", "installation_id": "i", "generation": "1"]]
            return try JSONSerialization.data(withJSONObject: [
                "event": "snapshot", "wire_version": 3, "projection_version": 7, "view_id": "v", "scope_generation": request["scope_generation"]!, "ordering_generation": "o",
                "coverage": ["scope": "s", "schema_version": 10, "semantics": "attention-knowledge-v10-ticket-workflows", "epoch": "e", "revision": revision],
                "content_version": revision, "mode": "current", "authority": authority,
                "content": ["records": [["kind": "chief", "id": "chief", "value": ["label": "Chief", "conversation_id": NSNull()]]],
                            "windows": windows.map { ["window_id": $0["section"]!, "request": $0, "members": [], "total": 0, "previous": NSNull(), "next": NSNull(), "forward_overlap": NSNull(), "backward_overlap": NSNull()] },
                            "counts": ["running_conversations": 0, "out_claims": 0, "review": 0, "unread": 0, "settled": 0], "review_context": [], "review_summary": ["skipped": 0, "new": 0]]] as [String: Any])
        }
        if url.path == "/api/resource-versions" {
            if !supportsVersions { throw URLError(.unsupportedURL) }
            return try JSONSerialization.data(withJSONObject: ["epoch": epoch, "resources": ["/api/workers/chief-of-staff/settings", "/api/deployment-status"].map { ["key": $0, "revision": revision] }])
        }
        let key = url.path == "/api/resource-body" ? URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.first!.value! : url.path
        if url.path == "/api/resource-body", !supportsVersions { throw URLError(.unsupportedURL) }
        let body: Any
        switch key {
        case "/api/workers/chief-of-staff/settings": body = ["future_field": "preserved", "employee_id": "chief", "label": "Chief " + revision, "recap": "", "skill": ["name": "chief", "description": "", "markdown_body": ""], "launch_defaults": ["employee_backend": "codex"]] as [String: Any]
        case "/api/deployment-status": body = ["state": "idle"]
        default: throw URLError(.unsupportedURL)
        }
        return try JSONSerialization.data(withJSONObject: url.path == "/api/resource-body" ? ["epoch": epoch, "key": key, "revision": revision, "body": body] : body)
    }
}
private final class RefreshProtocol: URLProtocol, @unchecked Sendable {
    static let state = OSAllocatedUnfairLock(initialState: RefreshFixture())
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
