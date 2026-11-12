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

    /// Risk: the Tailscale state reads the navigation view and settings failures. An
    /// unreachable server shows it without dropping the saved view, other failures do
    /// not, and a recovered read clears it.
    func testUnreachableReadsKeepSavedViewAndClearOnRecovery() async throws {
        RefreshProtocol.state.withLock { $0 = RefreshFixture() }
        let store = AppStore(baseURL: URL(string: "https://refresh.invalid")!, session: RefreshProtocol.session)
        await store.refresh()
        XCTAssertNotNil(store.review)
        RefreshProtocol.state.withLock { $0.failure = .cannotFindHost }
        await store.refresh()
        XCTAssertTrue(store.serverUnreachable)
        XCTAssertTrue(store.errors["Navigation"]?.isServerUnreachable == true)
        XCTAssertNotNil(store.review, "The unavailable screen must not delete the saved view")
        RefreshProtocol.state.withLock { $0.failure = .badServerResponse }
        await store.refresh()
        XCTAssertFalse(store.serverUnreachable, "Other request failures retain the current refresh behavior")
        XCTAssertNotNil(store.errors["Navigation"])
        RefreshProtocol.state.withLock { $0.failure = nil }
        await store.refresh()
        XCTAssertFalse(store.serverUnreachable)
        XCTAssertTrue(store.errors.isEmpty)
    }

    func testHealthFailureDoesNotCoverSuccessfulOrdinaryReads() async throws {
        RefreshProtocol.state.withLock { $0 = RefreshFixture(); $0.healthFailure = .cannotFindHost }
        let store = AppStore(baseURL: URL(string: "https://refresh.invalid")!, session: RefreshProtocol.session)
        await store.refresh()
        XCTAssertNotNil(store.chief)
        XCTAssertTrue(store.errors.isEmpty)
        XCTAssertTrue(store.resources.handle(ChiefResources.executorHealth).failure?.isServerUnreachable == true)
        XCTAssertFalse(store.serverUnreachable, "Health remains diagnostic while navigation and settings succeed")
    }

    func testVersionedLiveTransportDoesNotDoubleNetworkFailures() async throws {
        RefreshProtocol.state.withLock { $0 = RefreshFixture(); $0.failure = .timedOut }
        let api = APIClient(baseURL: URL(string: "https://refresh.invalid")!, session: RefreshProtocol.session)
        let shared = ResourceOwner(api: api)
        let handle = shared.handle(ChiefResources.settings)
        await handle.refresh()
        XCTAssertNotNil(handle.error)
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.paths }, ["/api/resource-body"], "A timeout gets one request, not another ordinary GET")
        RefreshProtocol.state.withLock { $0.paths = []; $0.failure = nil; $0.supportsVersions = false }
        await handle.refresh()
        XCTAssertEqual(handle.value?.label, "Chief 1")
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.paths }, ["/api/resource-body", "/api/workers/chief-of-staff/settings"], "Only explicit unsupported endpoints use fallback")
        RefreshProtocol.state.withLock { $0.paths = []; $0.supportsVersions = true }
        let body = try await ResourceTransport.live.read(api, ChiefResources.settings.key, true)
        let object = try JSONSerialization.jsonObject(with: body.data)
        XCTAssertEqual(body.data, try JSONSerialization.data(withJSONObject: object, options: [.fragmentsAllowed, .sortedKeys]))
        shared.stop()
    }

    func testRefreshReadsOneScopedViewAndSettingsFollowVersions() async throws {
        RefreshProtocol.state.withLock { $0 = RefreshFixture() }
        let store = AppStore(baseURL: URL(string: "https://refresh.invalid")!, session: RefreshProtocol.session)
        let chief = store.resources.handle(ChiefResources.settings)
        let deployment = store.resources.handle(ChiefResources.deployment)
        chief.acquire(); deployment.acquire()
        defer { chief.release(); deployment.release(); store.resources.stop() }
        await store.refresh()
        XCTAssertTrue(store.errors.isEmpty)
        XCTAssertEqual(store.chief?.label, "Chief 1")
        await LocalStore.shared.flushCache()
        let cached = try XCTUnwrap(LocalStore.shared.record(server: "https://refresh.invalid", kind: "cache", id: ResourceCacheEnvelope.identity(chief.key))?.payload)
        let saved = try JSONDecoder().decode(ResourceCacheEnvelope.self, from: cached)
        XCTAssertEqual(saved.epoch, RefreshProtocol.state.withLock { $0.epoch })
        XCTAssertEqual((try JSONSerialization.jsonObject(with: saved.data) as? [String: Any])?["future_field"] as? String, "preserved")
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
        chief.entry.lastAttempt = .distantPast; deployment.entry.lastAttempt = .distantPast
        await store.readSettings(probing: true)
        XCTAssertTrue(store.errors.isEmpty)
        XCTAssertTrue(RefreshProtocol.state.withLock { $0.paths.contains("/api/workers/chief-of-staff/settings") })
        XCTAssertTrue(RefreshProtocol.state.withLock { $0.paths.contains("/api/deployment-status") })
    }

    /// Risk: the phone follows the events stream instead of reading after each change.
    /// Patches must apply in order without a read, a gap must open a fresh stream rather
    /// than guess, a new scope must open its own stream unless the stream already carries
    /// it, and a record's reload identity must move only with that record.
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
        XCTAssertEqual(store.presentation?.ticketsById["B"]?.title, "B\u{2028}two", "A line separator inside a frame does not split it")
        XCTAssertNotNil(store.managerStamp("m"))
        // Persist the reduced patch, not the initial frame. Restoration grants no runtime authority.
        await store.saveNavigationDisplay()
        await LocalStore.shared.flushCache()
        let restored = AppStore(baseURL: URL(string: "https://refresh.invalid")!, session: RefreshProtocol.session)
        XCTAssertEqual(restored.view?.contentVersion, "2")
        XCTAssertEqual(restored.presentation?.ticketsById["B"]?.title, "B\u{2028}two")
        XCTAssertNil(restored.presentation?.ticketsById["A"]?.signal.working)
        let savedView = try XCTUnwrap(restored.view)
        XCTAssertEqual(savedView.mode, "saved")
        let checkpoint = ViewCheckpoint(viewId: savedView.viewId, scopeGeneration: savedView.scopeGeneration,
            orderingGeneration: savedView.orderingGeneration, coverage: savedView.coverage, previousCoverage: savedView.coverage,
            contentVersion: savedView.contentVersion, authority: savedView.authority)
        if case .success = NavigationViewFrames.apply(savedView, .checkpoint(checkpoint)) {
            XCTFail("Restored display cannot seed stream admission")
        }
        // The gap opens stream 2, whose first frame is a fresh snapshot.
        try await until { store.view?.contentVersion == "7" }
        XCTAssertEqual(store.connection, "Following changes")
        var events = RefreshProtocol.state.withLock { $0.events }
        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(events.map(\.generation), [0, 0])
        XCTAssertFalse(RefreshProtocol.state.withLock { $0.paths.contains("/api/navigation/view/snapshot") })

        // A Ticket the stream already carries waits for the next stream: no read in front of its page's own.
        store.setNavigationDemand("ticket:A", selected: ViewOwner(kind: "ticket", id: "A"))
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.events.count }, 2)
        // When Ticket A then leaves the stream's windows, a stream with A selected opens,
        // so the page keeps following its record.
        try await until { RefreshProtocol.state.withLock { $0.events.count } == 3 }
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.events.last?.selected }, "ticket:A")
        try await until { store.view?.contentVersion == "8" }
        XCTAssertNotNil(store.ticketStamp("A"))
        // An owner outside the view opens a stream for the new scope; the last view stays meanwhile.
        store.setNavigationDemand("ticket:Z", selected: ViewOwner(kind: "ticket", id: "Z"))
        XCTAssertEqual(store.view?.contentVersion, "8")
        try await until { store.view?.scopeGeneration == 2 }
        events = RefreshProtocol.state.withLock { $0.events }
        XCTAssertEqual(events.count, 4)
        XCTAssertEqual(events.last?.generation, 2)
        XCTAssertEqual(events.last?.selected, "ticket:Z")
        // A withdrawal narrows only the next stream.
        store.withdrawNavigationDemand("ticket:Z")
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.events.count }, 4)
    }

    /// SwiftUI starts the next run before the cancelled one unwinds, as when the server
    /// address changes. The old run's exit must not stop the new run from opening streams.
    func testCancelledRunUnwindingAfterNewRunKeepsItFollowing() async throws {
        RefreshProtocol.state.withLock { $0 = RefreshFixture() }
        let store = AppStore(baseURL: URL(string: "https://refresh.invalid")!, session: RefreshProtocol.session)
        let old = Task { await store.run() }
        // Stream 1 ends at its gap; the old run then waits a second before it reconnects.
        try await until { store.view?.contentVersion == "2" }
        let new = Task { await store.run() }
        defer { new.cancel() }
        try await until { RefreshProtocol.state.withLock { $0.events.count } == 2 }
        old.cancel()
        await old.value
        // The new scope opens its stream at once, not when stream 2's first frame arrives 1 s later.
        store.setNavigationDemand("ticket:Z", selected: ViewOwner(kind: "ticket", id: "Z"))
        try await until(within: .milliseconds(500)) { RefreshProtocol.state.withLock { $0.events.count } == 3 }
        XCTAssertEqual(RefreshProtocol.state.withLock { $0.events.last?.selected }, "ticket:Z")
        XCTAssertNotEqual(store.view?.contentVersion, "7")
    }

    private func until(within limit: Duration = .seconds(10), _ condition: @MainActor () -> Bool) async throws {
        let deadline = ContinuousClock.now + limit
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
    var failure: URLError.Code?
    var healthFailure: URLError.Code?
    var sections: [String] = []
    static var authority: [String: Any] { ["delivery_session_id": "d", "availability": ["state": "current", "generation": "1", "reason": NSNull()], "permit": ["scope": "s", "runtime_epoch": "r", "installation_id": "i", "generation": "1"]] }
    static func coverage(_ revision: String) -> [String: Any] {
        ["scope": "s", "schema_version": 10, "semantics": "attention-knowledge-v10-ticket-workflows", "epoch": "e", "revision": revision]
    }
    static var counts: [String: Any] { ["running_conversations": 0, "out_claims": 0, "review": 0, "unread": 0, "settled": 0] }

    static func recordValues(working: Bool, ticketBUpdated: Double) -> [[String: Any]] {
        func ticket(_ id: String, updated: Double, conversation: String) -> [String: Any] {
            // The server leaves U+2028 unescaped in its JSON; the changed B title carries one.
            ["kind": "ticket", "id": id, "value": ["title": id == "B" && updated == 2 ? "B\u{2028}two" : id, "recap": "", "recap_preview": "", "worker_type": "coding", "stage": "plan", "ticket_status": "empty",
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
        // Written raw, as the server's json.dumps(ensure_ascii=False) writes it.
        let json = String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self).replacingOccurrences(of: "\\u2028", with: "\u{2028}")
        return Data("event: \(value["event"]!)\r\ndata: \(json)\r\n\r\n".utf8)
    }

    /// Stream 1: snapshot, patch, gap. Stream 2: a fresh snapshot, delayed so stream 1's
    /// patched state can be observed, then a patch that drops Ticket A from the view.
    /// Later streams: a snapshot. Streams 2+ stay open.
    mutating func events(_ url: URL) throws -> (schedule: [(delay: Double, chunks: [Data])], finish: Bool) {
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
        func patch(from: String, to: String, upserts: [[String: Any]], evictions: [[String: Any]] = [], members: [[String: Any]] = members) -> [String: Any] {
            ["event": "patch", "wire_version": 3, "projection_version": 7, "view_id": "v", "scope_generation": generation, "ordering_generation": "o",
             "coverage": Self.coverage(to), "previous_coverage": Self.coverage(from), "content_version": to, "previous_content_version": from,
             "authority": Self.authority, "upserts": upserts, "evictions": evictions, "windows": Self.windows(windowRequests, members: members),
             "counts": Self.counts, "review_context": [], "review_summary": ["skipped": 0, "new": 0]]
        }
        switch events.count {
        case 1:
            let changed = Self.recordValues(working: true, ticketBUpdated: 2)
            return ([(0, [try Self.frame(snapshot("1", working: false, updated: 1)),
                          try Self.frame(patch(from: "1", to: "2", upserts: [changed[3], changed[4]])),
                          try Self.frame(patch(from: "5", to: "6", upserts: []))])], true)
        case 2:
            let evictA = try Self.frame(patch(from: "7", to: "7b", upserts: [], evictions: [["kind": "ticket", "id": "A"]], members: [["kind": "ticket", "id": "B"]]))
            return ([(1, [Data(": heartbeat\n\n".utf8), try Self.frame(snapshot("7", working: true, updated: 2))]), (2.5, [evictA])], false)
        default: return ([(0, [try Self.frame(snapshot("8", working: true, updated: 2))])], false)
        }
    }

    mutating func response(_ url: URL) throws -> Data {
        paths.append(url.path)
        if let failure { throw URLError(failure) }
        if url.path == "/api/executor/health", let healthFailure { throw URLError(healthFailure) }
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
            if !supportsVersions { throw APIError(status: 404, message: "Not Found") }
            return try JSONSerialization.data(withJSONObject: ["epoch": epoch, "resources": ["/api/workers/chief-of-staff/settings", "/api/deployment-status"].map { ["key": $0, "revision": revision] }])
        }
        let key = url.path == "/api/resource-body" ? URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.first!.value! : url.path
        if url.path == "/api/resource-body", !supportsVersions { throw APIError(status: 404, message: "Not Found") }
        let body: Any
        switch key {
        case "/api/workers/chief-of-staff/settings": body = ["future_field": "preserved", "employee_id": "chief", "label": "Chief " + revision, "recap": "", "skill": ["name": "chief", "description": "", "markdown_body": ""], "launch_defaults": ["employee_backend": "codex"]] as [String: Any]
        case "/api/deployment-status": body = ["state": "idle"]
        case "/api/executor/health": body = ["mode": "remote", "connection_state": "ready"]
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
                let (schedule, finish) = try Self.state.withLock { $0.paths.append(url.path); return try $0.events(url) }
                client?.urlProtocol(self, didReceive: ok, cacheStoragePolicy: .notAllowed)
                for (index, step) in schedule.enumerated() {
                    DispatchQueue.global().asyncAfter(deadline: .now() + step.delay) { [self] in
                        guard !stopped.withLock({ $0 }) else { return }
                        for chunk in step.chunks { client?.urlProtocol(self, didLoad: chunk) }
                        if finish, index == schedule.count - 1 { client?.urlProtocolDidFinishLoading(self) }
                    }
                }
            } catch { client?.urlProtocol(self, didFailWithError: error) }
            return
        }
        do {
            let data = try Self.state.withLock { try $0.response(url) }
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch let error as APIError {
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: url, statusCode: error.status, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(error.message.utf8))
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() { stopped.withLock { $0 = true } }
}

/// These cases exercise shared admission, not a second HTTP host. The controlled
/// transport deliberately completes a canceled read to prove the generation fence.
@MainActor final class ResourceOwnerTests: XCTestCase {
    private func owner(_ fixture: OwnerTransport, cacheStore: LocalStore? = nil, api: APIClient? = nil) -> ResourceOwner {
        ResourceOwner(api: api ?? APIClient(baseURL: URL(string: "https://owner.invalid")!), transport: ResourceTransport(
            versions: { _, keys in await fixture.probe(keys) },
            read: { api, key, _ in try await fixture.read(api.baseURL.absoluteString, key) }),
            cacheStore: cacheStore ?? LocalStore(url: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("owner.store")))
    }
    private func until(_ condition: @escaping () async -> Bool) async throws {
        for _ in 0..<200 {
            if await condition() { return }
            try await Task.sleep(for: .milliseconds(5))
        }
        XCTFail("Controlled transport did not reach its boundary")
    }
    func testForegroundAndNetworkReturnResetOnlyActiveReadBackoff() async throws {
        let transport = OwnerTransport(); await transport.failReads()
        let api = APIClient(baseURL: URL(string: "https://refresh.invalid")!, session: RefreshProtocol.session)
        let shared = owner(transport, api: api)
        let active = shared.handle(ResourceRegistration<String>(key: "/api/recovery"))
        let inactive = shared.handle(ResourceRegistration<String>(key: "/api/inactive-recovery"))
        active.acquire(); defer { active.release(); shared.stop() }
        await active.refresh(); await inactive.refresh()
        let inactiveRetry = inactive.entry.retryAt
        shared.start()
        try await until { active.entry.retryAt > Date() }
        await transport.recover()
        shared.start() // The previous foreground run may still be unwinding.
        XCTAssertLessThan(active.entry.retryAt, Date())
        try await until { active.value == "1" && active.error == nil }
        XCTAssertEqual(inactive.entry.retryAt, inactiveRetry)
        await transport.failReads(); await active.refresh()
        XCTAssertGreaterThan(active.entry.retryAt.timeIntervalSinceNow, 25)
        await transport.recover(); shared.resumeActiveReads()
        XCTAssertLessThan(active.entry.retryAt, Date())
        try await until { active.error == nil }
        XCTAssertLessThan(active.entry.retryAt, Date())
    }

    func testStartRestoresCacheForDemandWhoseRestoreWasCancelledByStop() async throws {
        let cache = LocalStore(url: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("owner.store"))
        let key = "/api/cancelled-restore", server = "https://refresh.invalid"
        let saved = ResourceCacheEnvelope(schemaIdentity: ResourceCacheEnvelope.schema, server: server,
                                          key: key, data: Data("\"Saved display\"".utf8), epoch: "e", revision: "1")
        try await cache.cacheResponse(server: server, id: ResourceCacheEnvelope.identity(key), data: JSONEncoder().encode(saved))
        let transport = OwnerTransport(); await transport.failReads()
        let shared = owner(transport, cacheStore: cache, api: APIClient(baseURL: URL(string: server)!, session: RefreshProtocol.session))
        let handle = shared.handle(ResourceRegistration<String>(key: key))
        handle.acquire(); let cancelled = handle.entry.restoring
        shared.stop(); await cancelled?.value
        XCTAssertNil(handle.value)
        shared.start(); await handle.entry.restoring?.value
        XCTAssertEqual(handle.value, "Saved display")
        XCTAssertTrue(handle.isStale)
        handle.release(); shared.stop()
    }

    func testBodyNewerThanEarlierProbeIsConfirmedAndAcceptedWithoutAnotherBodyRead() async throws {
        let transport = OwnerTransport(); await transport.holdFirst()
        let shared = owner(transport)
        let lookup = Task { try await shared.read(ResourceRegistration<String>(key: "/api/newer-body")) }
        try await until { await transport.count == 1 }
        await transport.setRevision("2"); await shared.reconcile(waitForReads: false)
        await transport.setRevision("3"); await transport.finishFirst(revision: "3")
        let value = try await lookup.value
        XCTAssertEqual(value, "3")
        let count = await transport.count
        XCTAssertEqual(count, 1, "A coherent body newer than the earlier probe needs confirmation, not another body")
        shared.stop()
    }

    func testNewerBodySurvivesLateOlderProbeDuringConfirmation() async throws {
        let transport = OwnerTransport(); await transport.holdFirst()
        let shared = owner(transport)
        let lookup = Task { try await shared.read(ResourceRegistration<String>(key: "/api/newer-confirmation")) }
        try await until { await transport.count == 1 }
        await transport.setRevision("2"); await shared.reconcile(waitForReads: false)
        await transport.setRevision("4"); await transport.holdProbe()
        await transport.finishFirst(revision: "4")
        try await until { await transport.probeHeld }
        await transport.overrideNextProbeRevision("3")
        await shared.reconcile(waitForReads: false)
        await transport.finishProbe()
        let value = try await lookup.value
        XCTAssertEqual(value, "4")
        let count = await transport.count
        XCTAssertEqual(count, 1, "A late older probe cannot reject a coherently confirmed newer body")
        shared.stop()
    }

    func testQueuedRecoveryDoesNotIssueReadsAfterStop() async throws {
        let transport = OwnerTransport()
        let shared = owner(transport, api: APIClient(baseURL: URL(string: "https://refresh.invalid")!, session: RefreshProtocol.session))
        let handle = shared.handle(ResourceRegistration<String>(key: "/api/stopped-recovery"))
        handle.acquire(); await handle.refresh()
        let reads = await transport.count, probes = await transport.probeBatches.count
        shared.start(); shared.resumeActiveReads(); shared.stop()
        for _ in 0..<10 { await Task.yield() }
        let stoppedReads = await transport.count, stoppedProbes = await transport.probeBatches.count
        XCTAssertEqual(stoppedReads, reads)
        XCTAssertEqual(stoppedProbes, probes)
        handle.release()
    }

    func testBothReadAttemptsObsoletedByProbesDoNotStartOutageBackoff() async throws {
        let transport = OwnerTransport(); await transport.holdFirst()
        let shared = owner(transport)
        let registration = ResourceRegistration<String>(key: "/api/changing-body")
        let handle = shared.handle(registration)
        let lookup = Task { try await shared.read(registration) }
        try await until { await transport.count == 1 }
        await transport.setRevision("2"); await shared.reconcile(waitForReads: false)
        await transport.holdFirst(); await transport.finishFirst()
        try await until { await transport.count == 2 }
        await transport.setRevision("3"); await shared.reconcile(waitForReads: false)
        await transport.finishFirst()
        do { _ = try await lookup.value; XCTFail("Both obsolete attempts require a fresh read") }
        catch { XCTAssertTrue(error.localizedDescription.contains("changed during refresh")) }
        XCTAssertLessThan(handle.entry.retryAt, Date())
        XCTAssertEqual(handle.entry.failures, 0)
        await handle.refresh()
        XCTAssertEqual(handle.value, "3", "A fresh retry is immediately available")
        shared.stop()
    }

    func testSharedDemandRejectsStaleBodyAndDoesNotReadInactiveKeys() async throws {
        let transport = OwnerTransport(); await transport.holdFirst()
        let owner = owner(transport)
        let one = owner.handle(ResourceRegistration<String>(key: "/api/example"))
        let two = owner.handle(ResourceRegistration<String>(key: "/api/example"))
        let inactive = owner.handle(ResourceRegistration<String>(key: "/api/inactive"))
        one.acquire(); two.acquire()
        defer { one.release(); two.release(); owner.stop() }
        try await until { await transport.count == 1 }
        XCTAssertTrue(one.entry === two.entry, "Two screens share the same accepted entry")
        await transport.setRevision("2")
        await owner.reconcile(waitForReads: false)
        await transport.finishFirst()
        await one.refresh()
        XCTAssertEqual(two.value, "2")
        try await Task.sleep(for: .milliseconds(10))
        XCTAssertEqual(one.value, "2", "A canceled V1 body cannot replace the V2 body")
        let keys = await transport.readKeys
        XCTAssertEqual(keys, ["/api/example", "/api/example"])
        XCTAssertNil(inactive.value)
        one.release(); two.release()
        let before = await transport.count
        await owner.reconcile()
        let after = await transport.count
        XCTAssertEqual(before, after, "A global wake does not fetch an inactive body")
    }
    func testTwoActiveGoalDocumentsRefreshOnlyTheChangedGoalAndList() async throws {
        let transport = OwnerTransport()
        let shared = self.owner(transport)
        let handles = ["/api/goals", "/api/goals/A", "/api/goals/B"].map { shared.handle(ResourceRegistration<String>(key: $0)) }
        handles.forEach { $0.acquire() }; defer { handles.forEach { $0.release() }; shared.stop() }
        for handle in handles { await handle.refresh() }
        await transport.clearReads()
        await transport.setRevision("2", keys: ["/api/goals", "/api/goals/A"])
        await shared.reconcile()
        let keys = await transport.readKeys
        XCTAssertEqual(Set(keys), ["/api/goals", "/api/goals/A"])
        XCTAssertEqual(handles[2].value, "1")
    }
    func testBurstCoalescesReconciliationAndBoundsProbeBatchesAndReads() async throws {
        let transport = OwnerTransport(); await transport.delayReads()
        let owner = self.owner(transport)
        let handles = (0..<65).map { owner.handle(ResourceRegistration<String>(key: "/api/resource/\($0)")) }
        handles.forEach { $0.acquire() }
        defer { handles.forEach { $0.release() }; owner.stop() }
        let first = Task { await owner.reconcile() }
        try await until { await transport.probeBatches.count == 2 }
        await withTaskGroup(of: Void.self) { group in
            for _ in 0..<10 { group.addTask { await owner.reconcile() } }
        }
        await first.value
        let batches = await transport.probeBatches, concurrency = await transport.maxConcurrent
        XCTAssertEqual(batches.count, 4, "A burst retains one active and one pending reconciliation")
        XCTAssertTrue(batches.allSatisfy { $0.count <= 64 })
        XCTAssertLessThanOrEqual(concurrency, 3)
        XCTAssertTrue(handles.allSatisfy { $0.value == "1" })
    }
    func testDeadlineResumesAfterDemandReleaseAndReacquisition() async throws {
        let transport = OwnerTransport()
        let shared = self.owner(transport)
        let deadline = Date().timeIntervalSince1970 + 0.15
        let handle = shared.handle(ResourceRegistration<String>(key: "/api/deadline", nextRefreshAt: { _ in deadline }))
        handle.acquire(); await handle.refresh(); handle.release()
        handle.acquire(); await handle.refresh()
        let count = await transport.count
        try await until { await transport.count > count }
        handle.release(); shared.stop()
    }
    func testServerRoundTripRejectsOldCallbackAndFailedRefreshRetainsContent() async throws {
        let transport = OwnerTransport(); await transport.holdFirst()
        let owner = owner(transport)
        let handle = owner.handle(ResourceRegistration<String>(key: "/api/example"))
        handle.acquire(); defer { handle.release(); owner.stop() }
        try await until { await transport.count == 1 }
        owner.configure(api: APIClient(baseURL: URL(string: "https://other.invalid")!))
        owner.configure(api: APIClient(baseURL: URL(string: "https://owner.invalid")!))
        await transport.setRevision("3"); await handle.refresh()
        await transport.finishFirst()
        try await Task.sleep(for: .milliseconds(10))
        XCTAssertEqual(handle.value, "3", "A→B→A does not restore an old generation's authority")
        await transport.failReads(); await handle.refresh()
        XCTAssertEqual(handle.value, "3", "A failed refresh retains admitted live content")
        XCTAssertNotNil(handle.error)
        let invalidOwner = ResourceOwner(api: APIClient(baseURL: URL(string: "https://bad.invalid")!), transport: ResourceTransport(
            versions: { _, _ in throw URLError(.timedOut) },
            read: { _, _, _ in ResourceRead(data: Data("42".utf8), epoch: "e", revision: "r") }))
        let invalid = invalidOwner.handle(ResourceRegistration<String>(key: "/api/bad"))
        await invalid.refresh()
        XCTAssertNil(invalid.value)
        XCTAssertNotNil(invalid.error)
        XCTAssertTrue(invalid.isStale, "An undecodable body cannot acquire freshness")
    }
    /// Risks: body getters used to decode on main, and identical bodies republished.
    /// One real owner host proves decode placement/count and stable lookup cost.
    func testDecodesOnceOffMainRetainsTypedValueAndSkipsUnchangedPublication() async throws {
        let shared = owner(OwnerTransport())
        let state = OSAllocatedUnfairLock(initialState: (count: 0, main: false))
        let registration = ResourceRegistration<String>(key: "/api/typed-cost", decode: { data in
            state.withLock { $0.count += 1; $0.main = $0.main || Thread.isMainThread }
            return try JSONDecoder().decode(String.self, from: data)
        })
        let handle = shared.handle(registration)
        await handle.refresh()
        let publication = handle.entry.publication
        let started = ContinuousClock.now
        for _ in 0..<10_000 {
            XCTAssertTrue(shared.handle(registration) === handle)
            XCTAssertEqual(handle.value, "1")
        }
        print("RESOURCE_HANDLE_LOOKUP_10000 \(started.duration(to: .now))")
        await handle.refresh()
        XCTAssertEqual(handle.entry.publication, publication)
        XCTAssertEqual(state.withLock { $0.count }, 1)
        XCTAssertFalse(state.withLock { $0.main })
        shared.stop()
    }

    /// A body can hang while version probes and an independent short-lived lookup
    /// remain live. The probe records a target instead of canceling the caller.
    func testOneShotSurvivesProbeAndProbeDoesNotWaitForHungBody() async throws {
        let transport = OwnerTransport(); await transport.holdFirst()
        let shared = owner(transport)
        let read = Task { try await shared.read(ResourceRegistration<String>(key: "/api/one-shot")) }
        try await until { await transport.count == 1 }
        await transport.setRevision("2")
        let start = ContinuousClock.now
        await shared.reconcile(waitForReads: false)
        let probeDuration = start.duration(to: .now)
        print("RESOURCE_PROBE_WITH_HELD_BODY \(probeDuration)")
        XCTAssertLessThan(probeDuration, .milliseconds(100))
        await transport.finishFirst()
        let value = try await read.value
        XCTAssertEqual(value, "2")
        let count = await transport.count
        XCTAssertEqual(count, 2, "Only the obsolete response needs one follow-up")
        shared.stop()
    }

    func testLaterProbeDuringDecodeCannotPublishObsoleteBody() async throws {
        let transport = OwnerTransport(), gate = DispatchSemaphore(value: 0)
        let decoding = OSAllocatedUnfairLock(initialState: false)
        let shared = owner(transport)
        let registration = ResourceRegistration<String>(key: "/api/decode-race", decode: { data in
            if data == Data("\"1\"".utf8) {
                decoding.withLock { $0 = true }
                gate.wait()
            }
            return try JSONDecoder().decode(String.self, from: data)
        })
        let lookup = Task { try await shared.read(registration) }
        try await until { decoding.withLock { $0 } }
        await transport.setRevision("2")
        await shared.reconcile(waitForReads: false)
        gate.signal()
        let value = try await lookup.value
        XCTAssertEqual(value, "2")
        XCTAssertEqual(shared.handle(registration).entry.publication, 1, "The obsolete decode never publishes")
        shared.stop()
    }

    func testAutomaticOutageRetriesAreBoundedAndManualRetryRemainsImmediate() async throws {
        let transport = OwnerTransport(); await transport.failReads()
        let shared = owner(transport)
        let handle = shared.handle(ResourceRegistration<String>(key: "/api/offline", versioned: false, signalRefreshInterval: 30))
        handle.acquire(); defer { handle.release(); shared.stop() }
        await handle.refresh()
        let count = await transport.count
        for _ in 0..<20 { await shared.reconcile(includeUnversioned: true) }
        let automaticCount = await transport.count
        XCTAssertEqual(automaticCount, count, "Repeated wakes and reconnects honor the failed-read backoff")
        XCTAssertGreaterThan(handle.entry.retryAt.timeIntervalSinceNow, 25)
        await handle.refresh()
        let manualCount = await transport.count
        XCTAssertEqual(manualCount, count + 1, "A person can retry without waiting for the automatic timer")
    }

    func testObservedNewRevisionMarksRetainedBodyStaleWithoutRepublishingEqualBytes() async throws {
        let transport = OwnerTransport()
        let actual = owner(transport)
        let handle = actual.handle(ResourceRegistration<String>(key: "/api/stale-until-admitted"))
        handle.acquire(); defer { handle.release(); actual.stop() }
        await handle.refresh()
        let publication = handle.entry.publication
        XCTAssertFalse(handle.isStale)
        await transport.setRevision("2"); await transport.setBody("1"); await transport.holdFirst()
        await actual.reconcile(waitForReads: false)
        try await until { await transport.count == 2 }
        XCTAssertTrue(handle.isStale, "A known changed revision revokes freshness while its body is pending")
        XCTAssertEqual(handle.value, "1")
        await transport.finishFirst(); await handle.refresh()
        XCTAssertFalse(handle.isStale)
        XCTAssertEqual(handle.entry.revision, "2")
        XCTAssertEqual(handle.entry.publication, publication, "Equal accepted bytes retain the typed value and publication")
    }

    func testPlainSignalFallbackIsTrailingBoundedAndCatalogsStayQuiet() async throws {
        let transport = OwnerTransport()
        let actual = owner(transport)
        let volatile = actual.handle(ResourceRegistration<String>(key: "/api/volatile", versioned: false, signalRefreshInterval: 0.05))
        let catalog = actual.handle(ResourceRegistration<String>(key: "/api/catalog", versioned: false))
        volatile.acquire(); catalog.acquire()
        defer { volatile.release(); catalog.release(); actual.stop() }
        await volatile.refresh(); await catalog.refresh()
        let count = await transport.count
        await transport.setRevision("2")
        await actual.reconcile()
        let immediateCount = await transport.count
        XCTAssertEqual(immediateCount, count, "An early change queues one trailing fallback")
        try await until { volatile.value == "2" }
        XCTAssertEqual(catalog.value, "1", "A catalog without signal demand waits for mount/reconnect/manual refresh")
        let finalCount = await transport.count
        XCTAssertEqual(finalCount, count + 1)
    }

    func testTerminalFailureDiscardsBodyButTransientFailureRetainsIt() async throws {
        let transport = OwnerTransport()
        let cache = LocalStore(url: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("owner.store"))
        let actual = owner(transport, cacheStore: cache)
        let handle = actual.handle(ResourceRegistration<String>(key: "/api/terminal", discardOnFailure: {
            ($0 as? APIError)?.message.hasPrefix("Broken Ticket:") == true
        }))
        await handle.refresh(); await cache.flushCache()
        XCTAssertEqual(handle.value, "1")
        let saved = await cache.cachedResponse(server: actual.server, id: ResourceCacheEnvelope.identity(handle.key))
        XCTAssertNotNil(saved)
        await transport.failReads(); await handle.refresh()
        XCTAssertEqual(handle.value, "1")
        await transport.failTerminal(); await handle.refresh()
        XCTAssertNil(handle.value)
        XCTAssertNotNil(handle.error)
        await transport.failTransientOnly()
        handle.acquire()
        await handle.refresh(); await handle.entry.restoring?.value
        XCTAssertNil(handle.value, "Revisiting offline cannot restore the disk body discarded after a terminal failure")
        XCTAssertNotNil(handle.error)
        handle.release()
        await transport.recover(); await handle.refresh()
        XCTAssertEqual(handle.value, "1", "A successful fresh body can admit content again")
        await transport.failReads(); await handle.refresh()
        XCTAssertEqual(handle.value, "1", "Transient failures still retain the newly accepted content")
        actual.stop()
    }

    func testSharedReadDeduplicatesAndPublishesAcceptedMutation() async throws {
        let transport = OwnerTransport(); await transport.holdFirst()
        let owner = owner(transport)
        let handle = owner.handle(ResourceRegistration<String>(key: "/api/example"))
        async let first: () = handle.refresh()
        async let second: () = handle.refresh()
        try await until { await transport.count == 1 }
        await transport.finishFirst(); _ = await (first, second)
        let count = await transport.count
        XCTAssertEqual(count, 1)
        handle.accept("confirmed")
        XCTAssertEqual(handle.value, "confirmed", "Accepted mutation publication is observable without a second response copy")
        owner.stop()
    }
}
private actor OwnerTransport {
    var count = 0
    var readKeys: [String] = []
    private var revision = "1"
    private var body: String?
    private var revisions: [String: String] = [:]
    private var hold = false
    private var delay = false
    private var failure = false
    private var terminal = false
    private var active = 0
    var maxConcurrent = 0
    var probeBatches: [[String]] = []
    private var holdNextProbe = false
    var probeHeld = false
    private var probeOverride: String?
    private var pendingProbe: CheckedContinuation<ResourceVersions, Never>?
    private var savedProbe: ResourceVersions?
    private var first: CheckedContinuation<ResourceRead, Never>?
    private var saved: ResourceRead?
    func holdFirst() { hold = true }
    func delayReads() { delay = true }
    func failReads() { failure = true }
    func failTerminal() { terminal = true }
    func failTransientOnly() { terminal = false; failure = true }
    func recover() { terminal = false; failure = false }
    func setRevision(_ value: String) { revision = value }
    func setBody(_ value: String) { body = value }
    func setRevision(_ value: String, keys: [String]) { for key in keys { revisions[key] = value } }
    func clearReads() { readKeys = [] }
    func holdProbe() { holdNextProbe = true }
    func overrideNextProbeRevision(_ revision: String) { probeOverride = revision }
    func finishProbe() { if let savedProbe { pendingProbe?.resume(returning: savedProbe) }; pendingProbe = nil; savedProbe = nil; probeHeld = false }
    func probe(_ keys: [String]) async -> ResourceVersions {
        probeBatches.append(keys)
        let snapshot = ResourceVersions(epoch: "epoch", resources: keys.map { .init(key: $0, revision: probeOverride ?? revisions[$0] ?? revision) })
        probeOverride = nil
        if holdNextProbe {
            holdNextProbe = false; savedProbe = snapshot; probeHeld = true
            return await withCheckedContinuation { pendingProbe = $0 }
        }
        return snapshot
    }
    func read(_ server: String, _ key: String) async throws -> ResourceRead {
        count += 1; readKeys.append(key)
        active += 1; maxConcurrent = max(maxConcurrent, active)
        defer { active -= 1 }
        if terminal { throw APIError(status: 400, message: "Broken Ticket: invalid") }
        if failure { throw URLError(.timedOut) }
        if delay { try? await Task.sleep(for: .milliseconds(20)) }
        let revision = revisions[key] ?? revision
        let result = ResourceRead(data: Data("\"\(body ?? revision)\"".utf8), epoch: "epoch", revision: revision)
        if hold { hold = false; saved = result; return await withCheckedContinuation { first = $0 } }
        return result
    }
    func finishFirst(revision: String? = nil) {
        if let revision { first?.resume(returning: ResourceRead(data: Data("\"\(revision)\"".utf8), epoch: "epoch", revision: revision)) }
        else if let saved { first?.resume(returning: saved) }
        first = nil; saved = nil
    }
}
