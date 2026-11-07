import Foundation
import os
import XCTest
@testable import Panels

@MainActor final class ResourceRefreshTests: XCTestCase {
    func testRefreshReadsOneScopedViewAndSettingsFollowVersions() async throws {
        RefreshProtocol.state.withLock { $0 = RefreshFixture() }
        let store = AppStore(baseURL: URL(string: "https://refresh.invalid")!, session: RefreshProtocol.session)
        await store.refresh()
        XCTAssertTrue(store.errors.isEmpty)
        XCTAssertEqual(store.chief?.label, "Chief 1")
        let cached = try XCTUnwrap(LocalStore.shared.record(server: "https://refresh.invalid", kind: "cache", id: "https://refresh.invalid/api/workers/chief-of-staff/settings")?.payload)
        XCTAssertEqual((try JSONSerialization.jsonObject(with: cached) as? [String: Any])?["future_field"] as? String, "preserved")
        // Without a foreground stream, one view snapshot replaces the Review, manager and ticket list reads.
        store.setNavigationDemand("workspace", [ViewWindowRequest(section: "rail_parents", limit: 60), ViewWindowRequest(section: "rail_tickets", owner: .chief, limit: 30)])
        RefreshProtocol.state.withLock { $0.paths = [] }
        await store.refresh()
        let paths = RefreshProtocol.state.withLock { $0.paths }
        XCTAssertEqual(paths.filter { $0 == "/api/navigation/view/snapshot" }.count, 1)
        XCTAssertTrue(Set(paths).isDisjoint(with: ["/api/review", "/api/managers", "/api/tickets", "/api/workers"]))
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.sections }, ["review", "rail_parents", "rail_tickets"])
        XCTAssertTrue(store.workspaceLoaded)
        XCTAssertNotNil(store.review)
        // A change wake probes versions: unchanged versions read no body.
        RefreshProtocol.state.withLock { $0.paths = [] }
        await store.readSettings(probing: true)
        XCTAssertFalse(RefreshProtocol.state.withLock { $0.paths.contains("/api/resource-body") })
        RefreshProtocol.state.withLock { $0.paths = []; $0.revision = "2" }
        await store.readSettings(probing: true)
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.paths.filter { $0 == "/api/resource-body" }.count }, 2)
        XCTAssertEqual(store.chief?.label, "Chief 2")
        // Epoch changes invalidate even an identical revision token.
        RefreshProtocol.state.withLock { $0.paths = []; $0.epoch = "restarted" }
        await store.readSettings(probing: true)
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.paths.filter { $0 == "/api/resource-body" }.count }, 2)
        // Failed probes / unsupported version API preserve ordinary reads.
        RefreshProtocol.state.withLock { $0.paths = []; $0.supportsVersions = false }
        await store.readSettings(probing: true)
        XCTAssertTrue(store.errors.isEmpty)
        XCTAssertTrue(RefreshProtocol.state.withLock { $0.paths.contains("/api/workers/chief-of-staff/settings") })
        XCTAssertTrue(RefreshProtocol.state.withLock { $0.paths.contains("/api/deployment-status") })
    }

    /// Risk: the phone follows the events stream instead of reading after each change.
    /// Patches must apply in order without a read, a gap must open a fresh stream rather
    /// than guess, a new scope must open its own stream, and a record's reload identity
    /// must move only with that record.
    func testViewStreamAppliesPatchesResnapshotsAfterGapAndFollowsScope() async throws {
        RefreshProtocol.state.withLock { $0 = RefreshFixture() }
        let store = AppStore(baseURL: URL(string: "https://refresh.invalid")!, session: RefreshProtocol.session)
        let run = Task { await store.run() }
        defer { run.cancel() }
        let original = try RefreshFixture.records(working: false, ticketBUpdated: 1)
        func stamp(_ id: String, in records: [ViewRecord]) -> String? { records.first { $0.key == RecordKey(kind: "ticket", id: id) }?.stamp }

        // Stream 1: snapshot 1, then a patch changing A's conversation and B's record, then a gap.
        try await until { store.view?.contentVersion == "2" }
        XCTAssertEqual(store.presentation?.ticketsById["A"]?.signal.working, true, "The patch applied without a read")
        XCTAssertEqual(store.ticketStamp("A"), stamp("A", in: original), "A conversation mark does not reload A's page")
        XCTAssertNotEqual(store.ticketStamp("B"), stamp("B", in: original), "B's own change reloads B's page")
        XCTAssertNotNil(store.managerStamp("m"))
        // The gap opens stream 2, whose first frame is a fresh snapshot.
        try await until { store.view?.contentVersion == "7" }
        XCTAssertEqual(store.connection, "Following changes")
        var events = RefreshProtocol.state.withLock { $0.events }
        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(events.map(\.generation), [0, 0])
        XCTAssertFalse(RefreshProtocol.state.withLock { $0.paths.contains("/api/navigation/view/snapshot") })

        // A detail screen's owner opens a stream for the new scope; the last view stays meanwhile.
        store.setNavigationDemand("ticket:A", selected: ViewOwner(kind: "ticket", id: "A"))
        try await until { store.view?.scopeGeneration == 1 }
        events = RefreshProtocol.state.withLock { $0.events }
        XCTAssertEqual(events.count, 3)
        XCTAssertEqual(events.last?.generation, 1)
        XCTAssertEqual(events.last?.selected, "ticket:A")
        // A withdrawal narrows only the next stream.
        store.withdrawNavigationDemand("ticket:A")
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.events.count }, 3)
    }

    private func until(_ condition: @MainActor () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(10)
        while !condition() {
            guard ContinuousClock.now < deadline else { return XCTFail("Timed out") }
            try await Task.sleep(for: .milliseconds(10))
        }
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
    struct EventsRequest { let generation: Int; let selected: String? }
    var paths: [String] = []
    var events: [EventsRequest] = []
    var revision = "1"
    var epoch = "initial"
    var supportsVersions = true
    var sections: [String] = []
    static var authority: [String: Any] { ["delivery_session_id": "d", "availability": ["state": "current", "generation": "1", "reason": NSNull()], "permit": ["scope": "s", "runtime_epoch": "r", "installation_id": "i", "generation": "1"]] }
    static func coverage(_ revision: String) -> [String: Any] {
        ["scope": "s", "schema_version": 10, "semantics": "attention-knowledge-v10-ticket-workflows", "epoch": "e", "revision": revision]
    }
    static var counts: [String: Any] { ["running_conversations": 0, "out_claims": 0, "review": 0, "unread": 0, "settled": 0] }

    static func recordValues(working: Bool, ticketBUpdated: Double) -> [[String: Any]] {
        func ticket(_ id: String, updated: Double, conversation: String) -> [String: Any] {
            ["kind": "ticket", "id": id, "value": ["title": id, "recap": "", "recap_preview": "", "worker_type": "coding", "stage": "plan", "ticket_status": "empty",
                                                    "is_backlogged": false, "created_at": 1, "updated_at": updated, "project_id": NSNull(), "manager_kind": "manager",
                                                    "manager_id": "m", "conversation_id": conversation, "blocked": false, "terminal": false] as [String: Any]]
        }
        let attention: [String: Any] = ["current": ["signal": ["working": working, "message_for_owner": false], "awaiting_answer": false, "last_turn_failed": false],
                                        "durable": ["message_for_owner": false, "last_turn_failed": false]]
        return [
            ["kind": "chief", "id": "chief", "value": ["label": "Chief", "conversation_id": NSNull()]],
            ["kind": "manager", "id": "m", "value": ["title": "M", "created_at": 1, "updated_at": 1, "settled": false, "conversation_id": NSNull(), "recap": "", "recap_preview": "", "project_id": "p"]],
            ticket("A", updated: 1, conversation: "cA"), ticket("B", updated: ticketBUpdated, conversation: "cB"),
            ["kind": "conversation", "id": "cA", "value": ["first_unread_owner_message_at": NSNull(), "owner_read_through_sequence": 0, "error_acknowledged_through_sequence": 0, "attention": attention]],
        ]
    }
    static func records(working: Bool, ticketBUpdated: Double) throws -> [ViewRecord] {
        try APIClient.decoder().decode([ViewRecord].self, from: JSONSerialization.data(withJSONObject: recordValues(working: working, ticketBUpdated: ticketBUpdated)))
    }
    static func windows(_ request: [[String: Any]], members: [[String: Any]] = []) -> [[String: Any]] {
        request.map { window in
            let children = window["section"] as? String == "children"
            return ["window_id": window["section"]!, "request": window, "members": children ? members : [], "total": children ? members.count : 0,
                    "previous": NSNull(), "next": NSNull(), "forward_overlap": NSNull(), "backward_overlap": NSNull()]
        }
    }
    static func frame(_ value: [String: Any]) throws -> Data {
        Data("event: \(value["event"]!)\ndata: ".utf8) + (try JSONSerialization.data(withJSONObject: value)) + Data("\n\n".utf8)
    }

    /// Stream 1: snapshot, patch, gap. Stream 2: a fresh snapshot, delayed so stream 1's
    /// patched state can be observed. Later streams: a snapshot. Streams 2+ stay open.
    mutating func events(_ url: URL) throws -> (chunks: [Data], delay: Double, finish: Bool) {
        let raw = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.first { $0.name == "view" }!.value!
        let request = try JSONSerialization.jsonObject(with: Data(raw.utf8)) as! [String: Any]
        let generation = request["scope_generation"] as! Int
        let selected = (request["selected"] as? [String: Any]).map { "\($0["kind"]!):\($0["id"]!)" }
        events.append(EventsRequest(generation: generation, selected: selected))
        let windowRequests = (request["windows"] as! [[String: Any]]) + [["section": "children", "owner": ["kind": "manager", "id": "m"], "group": "all", "child_filter": "all", "anchor": NSNull(), "direction": "after", "limit": 60]]
        let members: [[String: Any]] = [["kind": "ticket", "id": "A"], ["kind": "ticket", "id": "B"]]
        func snapshot(_ version: String, working: Bool, updated: Double) -> [String: Any] {
            ["event": "snapshot", "wire_version": 3, "projection_version": 7, "view_id": "v", "scope_generation": generation, "ordering_generation": "o",
             "coverage": Self.coverage(version), "content_version": version, "mode": "current", "authority": Self.authority,
             "content": ["records": Self.recordValues(working: working, ticketBUpdated: updated), "windows": Self.windows(windowRequests, members: members),
                         "counts": Self.counts, "review_context": [], "review_summary": ["skipped": 0, "new": 0]] as [String: Any]]
        }
        func patch(from: String, to: String, upserts: [[String: Any]]) -> [String: Any] {
            ["event": "patch", "wire_version": 3, "projection_version": 7, "view_id": "v", "scope_generation": generation, "ordering_generation": "o",
             "coverage": Self.coverage(to), "previous_coverage": Self.coverage(from), "content_version": to, "previous_content_version": from,
             "authority": Self.authority, "upserts": upserts, "evictions": [], "windows": Self.windows(windowRequests, members: members),
             "counts": Self.counts, "review_context": [], "review_summary": ["skipped": 0, "new": 0]]
        }
        switch events.count {
        case 1:
            let changed = Self.recordValues(working: true, ticketBUpdated: 2)
            return ([try Self.frame(snapshot("1", working: false, updated: 1)),
                     try Self.frame(patch(from: "1", to: "2", upserts: [changed[3], changed[4]])),
                     try Self.frame(patch(from: "5", to: "6", upserts: []))], 0, true)
        case 2: return ([Data(": heartbeat\n\n".utf8), try Self.frame(snapshot("7", working: true, updated: 2))], 1, false)
        default: return ([try Self.frame(snapshot("8", working: true, updated: 2))], 0, false)
        }
    }

    mutating func response(_ url: URL) throws -> Data {
        paths.append(url.path)
        if url.path == "/api/navigation/view/snapshot" {
            let raw = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.first { $0.name == "view" }!.value!
            let request = try JSONSerialization.jsonObject(with: Data(raw.utf8)) as! [String: Any]
            let windows = request["windows"] as! [[String: Any]]
            sections = windows.map { $0["section"] as! String }
            return try JSONSerialization.data(withJSONObject: [
                "event": "snapshot", "wire_version": 3, "projection_version": 7, "view_id": "v", "scope_generation": request["scope_generation"]!, "ordering_generation": "o",
                "coverage": Self.coverage(revision), "content_version": revision, "mode": "current", "authority": Self.authority,
                "content": ["records": [["kind": "chief", "id": "chief", "value": ["label": "Chief", "conversation_id": NSNull()]]],
                            "windows": Self.windows(windows), "counts": Self.counts, "review_context": [], "review_summary": ["skipped": 0, "new": 0]]] as [String: Any])
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
    static var session: URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RefreshProtocol.self]
        return URLSession(configuration: config)
    }
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "refresh.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    private let stopped = OSAllocatedUnfairLock(initialState: false)
    override func startLoading() {
        let url = request.url!
        let ok = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "text/event-stream"])!
        // Streams stay open until the client cancels them.
        if url.path == "/api/changes" {
            client?.urlProtocol(self, didReceive: ok, cacheStoragePolicy: .notAllowed)
            return
        }
        if url.path == "/api/navigation/view/events" {
            do {
                let (chunks, delay, finish) = try Self.state.withLock { $0.paths.append(url.path); return try $0.events(url) }
                client?.urlProtocol(self, didReceive: ok, cacheStoragePolicy: .notAllowed)
                DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [self] in
                    guard !stopped.withLock({ $0 }) else { return }
                    for chunk in chunks { client?.urlProtocol(self, didLoad: chunk) }
                    if finish { client?.urlProtocolDidFinishLoading(self) }
                }
            } catch { client?.urlProtocol(self, didFailWithError: error) }
            return
        }
        do {
            let data = try Self.state.withLock { try $0.response(url) }
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() { stopped.withLock { $0 = true } }
}
