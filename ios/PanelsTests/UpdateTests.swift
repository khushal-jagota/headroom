import Foundation
import os
import XCTest
@testable import Panels

/// These cases cover native projection and owner-write boundaries. Existing resource
/// and outbox suites do not exercise update routes or retained machine targets.
@MainActor final class UpdateTests: XCTestCase {
    private func status(_ changes: [String: Any] = [:]) throws -> PanelsUpdateStatus {
        var value: [String: Any] = [
            "state": "available", "allowed_actions": ["update", "retry"], "app_sha": "running-sha",
            "available": ["app_sha": "offered-sha"],
            "attempt": ["attempt_id": "new-attempt", "state": "failed", "interruption": ["stops_agents": true, "agents": ["Earlier worker"], "machine_ids": []]],
            "interruption": ["stops_agents": false, "agents": [], "machine_ids": []],
            "machines": [["machine_id": "machine-uuid", "name": "Studio", "state": "failed", "present": true, "attempt_id": "old-attempt", "can_retry": true]],
            "available_machines": [["machine_id": "machine-uuid", "name": "Studio", "state": "available", "present": true, "can_retry": false]],
            "client_versions": [["kind": "ios", "device_id": "phone", "build": 1]]
        ]
        value.merge(changes) { _, new in new }
        return try APIClient.decoder().decode(PanelsUpdateStatus.self, from: JSONSerialization.data(withJSONObject: value))
    }
    private var api: APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [UpdateProtocol.self]
        return APIClient(baseURL: URL(string: "https://updates.invalid")!, session: URLSession(configuration: config))
    }
    override func setUp() { UpdateProtocol.state.withLock { $0 = .init() } }
    private func awaitRequests(_ count: Int) async throws {
        for _ in 0..<200 {
            if UpdateProtocol.state.withLock({ $0.requests.count >= count }) { return }
            try await Task.sleep(for: .milliseconds(5))
        }
        XCTFail("The controlled update request did not reach its transport boundary")
    }

    func testOfferKeepsRetainedTargetAndProgressUsesPressSnapshotWithoutOfferContamination() throws {
        let offer = try status()
        XCTAssertEqual(offer.cardMachines.first?.attemptId, "old-attempt")
        XCTAssertTrue(offer.cardMachines.first?.canRetry == true)
        XCTAssertEqual(offer.cardInterruption?.sentence, "Your agents keep running")
        let progress = try status(["state": "updating"])
        XCTAssertEqual(progress.cardInterruption?.sentence, "This update stops your agents")
        XCTAssertEqual(progress.cardInterruption?.agents, ["Earlier worker"])
        let historical = try status(["state": "updating", "attempt": ["attempt_id": "historical", "state": "installing"]])
        XCTAssertNil(historical.cardInterruption, "A historical attempt must not inherit a new offer's impact")
        let finished = try status(["state": "up_to_date", "available": NSNull(), "attempt": ["attempt_id": "new-attempt", "state": "done", "interruption": ["stops_agents": true, "agents": ["Earlier worker"], "machine_ids": []]]])
        XCTAssertNil(finished.cardInterruption, "With nothing offered or running, the card states no impact")
        let failed = try status(["state": "failed", "available": NSNull(), "allowed_actions": ["retry"]])
        XCTAssertEqual(failed.cardInterruption?.sentence, "This update stops your agents", "Try again stops the agents again")
        XCTAssertEqual(failed.cardInterruption?.agents, ["Earlier worker"])
    }
    func testCompletedInstallAndLatestCheckRemainSeparate() throws {
        let attempt = ["attempt_id": "saved", "state": "done", "to_sha": "running-sha"]
        let values: [String: Any] = ["state": "check_unavailable", "attempt": attempt, "machines": []]
        let unchecked = try status(["state": "not_checked", "attempt": NSNull(), "machines": []])
        XCTAssertEqual(unchecked.title, "Not checked for updates")
        XCTAssertFalse(unchecked.needsAttention)
        let recent = try status(["state": "not_checked", "attempt": attempt, "machines": []])
        XCTAssertEqual(recent.title, "Update installed")
        XCTAssertNil(recent.cardDetail)
        let installed = try status(values)
        XCTAssertEqual(installed.title, "Update installed")
        XCTAssertEqual(installed.cardDetail, "Couldn’t confirm the latest version.")
        let historical = try status(values.merging(["app_sha": "another-sha"]) { _, new in new })
        XCTAssertEqual(historical.title, "Couldn’t check for updates")
        XCTAssertNil(historical.cardDetail)
        let unknown = try status(["state": "check_unavailable", "app_sha": "", "attempt": ["attempt_id": "saved", "state": "done", "to_sha": ""]])
        XCTAssertEqual(unknown.title, "Couldn’t check for updates")
        let failedMachine = try status(["state": "check_unavailable", "attempt": attempt])
        XCTAssertEqual(failedMachine.title, "Machine update needs attention")
        XCTAssertEqual(failedMachine.cardDetail, installed.cardDetail)
    }
    func testCandidateCompatibilityIsOptionalAndWarnsForDeclaredClientsOnly() throws {
        XCTAssertEqual(try status().warnings, [])
        let limits: [String: Any] = ["ios": ["minimum_build": 2], "desktop_shell": ["minimum_bridge_version": 3], "machine": ["minimum_protocol_version": 2, "maximum_protocol_version": 4], "outbox_format": 2]
        let candidate = try status([
            "available": ["app_sha": "offered", "compatibility": limits],
            "client_versions": [["kind": "ios", "device_id": "phone", "build": 1], ["kind": "ios", "device_id": "other-phone", "build": 1], ["kind": "desktop", "device_id": "desktop", "bridge_version": 1], ["kind": "machine", "device_id": "machine-uuid", "protocol_version": 9, "outbox_format": 2]]
        ])
        XCTAssertEqual(candidate.warnings, ["Your iPhone needs the TestFlight update first.", "Your desktop app needs an update first.", "Studio needs an update to continue."])
        XCTAssertEqual(try status(["available": NSNull()]).warnings, [])
    }
    func testForegroundStatusReadsContinueAfterUnchangedAnswers() async throws {
        let idle: [String: Any] = ["state": "up_to_date", "allowed_actions": [], "app_sha": "running",
                                  "machines": [], "available_machines": [], "client_versions": [],
                                  "interruption": ["stops_agents": false, "agents": [], "machine_ids": []]]
        let idleData = try JSONSerialization.data(withJSONObject: idle)
        var active = idle
        active["state"] = "updating"
        let activeData = try JSONSerialization.data(withJSONObject: active)
        UpdateProtocol.state.withLock { $0.readData = idleData }
        let resources = ResourceOwner(api: api)
        let handle = resources.handle(UpdateResources.status)
        await handle.refresh()
        var intervals: [TimeInterval] = []
        await UpdateStatusRefresh.follow(current: { handle.value }, refresh: { await handle.refresh() }, wait: { interval in
            intervals.append(interval)
            if intervals.count == 4 { throw CancellationError() }
            if intervals.count == 3 { UpdateProtocol.state.withLock { $0.readData = activeData } }
        })
        XCTAssertEqual(intervals, [30, 30, 30, 3])
        XCTAssertEqual(handle.value?.state, "updating")
        XCTAssertEqual(UpdateProtocol.state.withLock { $0.requests.count }, 4, "Unchanged bytes cannot stop later update reads")
        resources.stop()
    }
    func testExpectedRestartRequiresValidServerDeadlineAndExpires() throws {
        let before = Date(timeIntervalSince1970: 100)
        let active = try status(["state": "updating", "deadline_at": "1970-01-01T00:02:00Z"])
        XCTAssertTrue(active.expectedRestart(now: before))
        XCTAssertFalse(active.expectedRestart(now: Date(timeIntervalSince1970: 120)))
        XCTAssertFalse(try status(["state": "updating"]).expectedRestart(now: before))
        XCTAssertFalse(try status(["state": "updating", "deadline_at": "invalid"]).expectedRestart(now: before))
        XCTAssertFalse(try status(["deadline_at": "1970-01-01T00:02:00Z"]).expectedRestart(now: before))
        XCTAssertTrue(try status(["deadline_at": "1970-01-01T00:02:00Z"]).expectedRestart(now: before, starting: true))
    }
    func testUpdateRoutesUseCapturedOfferAndMachineOwnedAttemptWithIdentityHeaders() async throws {
        let snapshot = try status()
        try await api.performUpdate(.install, status: snapshot)
        try await api.performUpdate(.retryServer, status: snapshot)
        try await api.performUpdate(.retryMachine(try XCTUnwrap(snapshot.machines.first)), status: snapshot)
        let requests = UpdateProtocol.state.withLock { $0.requests }
        XCTAssertEqual(requests[2].url?.absoluteString, "https://updates.invalid/api/updates/attempts/old-attempt/machines/machine-uuid/retry")
        XCTAssertEqual(requests.map { $0.url!.path }, ["/api/updates/install", "/api/updates/attempts/new-attempt/retry", "/api/updates/attempts/old-attempt/machines/machine-uuid/retry"])
        XCTAssertEqual(try JSONSerialization.jsonObject(with: try XCTUnwrap(requests[0].httpBody)) as? [String: String], ["app_sha": "offered-sha"])
        for request in requests {
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-Panels-App-SHA"), "running-sha")
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-Panels-iOS-Build"), NativeBuildGate.build)
            XCTAssertTrue(request.value(forHTTPHeaderField: "X-Panels-Client")?.contains("ios") == true)
        }
        let reserved = try status(["machines": [["machine_id": "machine/a?b", "name": "Studio", "state": "failed", "present": true, "attempt_id": "attempt/a?b", "can_retry": true]]])
        try await api.performUpdate(.retryMachine(try XCTUnwrap(reserved.machines.first)), status: reserved)
        let encoded = try XCTUnwrap(UpdateProtocol.state.withLock { $0.requests.last?.url })
        XCTAssertTrue(encoded.absoluteString.contains("attempt%2Fa%3Fb/machines/machine%2Fa%3Fb/retry"))
        XCTAssertFalse(encoded.absoluteString.contains("%252F"))
    }
    func testDuplicatePressAndUncertainResultNeverReplayWriteAndOldServerCannotChangeNewState() async throws {
        let snapshot = try status()
        let actions = UpdateActionsState()
        UpdateProtocol.state.withLock { $0.hold = true; $0.failure = true; $0.networkFailure = true }
        var refreshes = 0
        let first = Task { await actions.perform(.install, status: snapshot, api: api) { refreshes += 1 } }
        try await awaitRequests(1)
        XCTAssertTrue(actions.inFlight)
        await actions.perform(.install, status: snapshot, api: api) { refreshes += 1 }
        XCTAssertEqual(UpdateProtocol.state.withLock { $0.requests.count }, 1)
        UpdateProtocol.release()
        await first.value
        XCTAssertNotNil(actions.error, "A lost response keeps the action outcome explicit")
        XCTAssertFalse(actions.inFlight)
        XCTAssertEqual(refreshes, 1)
        XCTAssertEqual(UpdateProtocol.state.withLock { $0.requests.count }, 1, "Refresh and return to the route cannot replay an update")

        UpdateProtocol.state.withLock { $0.hold = true; $0.networkFailure = false }
        let oldServer = Task { await actions.perform(.retryServer, status: snapshot, api: api) { refreshes += 1 } }
        try await awaitRequests(2)
        actions.reset() // AppStore calls this before it configures another server.
        UpdateProtocol.release()
        await oldServer.value
        XCTAssertNil(actions.error)
        XCTAssertFalse(actions.inFlight)
        XCTAssertEqual(refreshes, 1, "An old server completion cannot refresh the new server")
    }
}

private struct UpdateFixture {
    var requests: [URLRequest] = []
    var hold = false
    var failure = false
    var networkFailure = false
    var readData = Data("{}".utf8)
    var held: UpdateProtocol?
}
private final class UpdateProtocol: URLProtocol, @unchecked Sendable {
    static let state = OSAllocatedUnfairLock(initialState: UpdateFixture())
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "updates.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var captured = request
        if captured.httpBody == nil, let stream = captured.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var bytes = [UInt8](repeating: 0, count: 1024)
            var body = Data()
            while stream.hasBytesAvailable {
                let count = stream.read(&bytes, maxLength: bytes.count)
                if count <= 0 { break }
                body.append(contentsOf: bytes.prefix(count))
            }
            captured.httpBody = body
        }
        let requestSnapshot = captured
        let hold = Self.state.withLock { state in
            state.requests.append(requestSnapshot)
            if state.hold { state.held = self }
            return state.hold
        }
        if !hold { finish() }
    }
    static func release() {
        let held = state.withLock { state in let held = state.held; state.held = nil; state.hold = false; return held }
        held?.finish()
    }
    private func finish() {
        if Self.state.withLock({ $0.networkFailure }) {
            client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost))
            return
        }
        let failed = Self.state.withLock { $0.failure }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: failed ? 409 : 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        if request.httpMethod == "GET" {
            client?.urlProtocol(self, didLoad: Self.state.withLock { $0.readData })
            client?.urlProtocolDidFinishLoading(self)
            return
        }
        client?.urlProtocol(self, didLoad: Data((failed ? "{\"detail\":\"This update changed. Read the status and try again.\"}" : "{}").utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
