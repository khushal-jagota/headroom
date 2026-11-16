import XCTest
@testable import Panels

final class WorkspacePresentationTests: XCTestCase {
    private func ticket(_ changes: [String: Any] = [:]) throws -> TicketSummary {
        let url = try XCTUnwrap(Bundle(for: ModelDecodingTests.self).url(forResource: "tickets", withExtension: "json"))
        let envelope = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        var row = try XCTUnwrap((envelope["tickets"] as? [[String: Any]])?.first)
        row["assigned"] = false
        for (key, value) in changes { row[key] = value }
        return try APIClient.decoder().decode(TicketSummary.self, from: JSONSerialization.data(withJSONObject: row))
    }

    func testActionsUseWireContractAndRejectUncertainDelivery() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [WorkspaceURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "https://workspace-stub.invalid")!, session: session)
        try await client.workspaceAction(.backlog, route: .ticket("t1"))
        try await client.workspaceAction(.resume, route: .ticket("t1"))
        try await client.workspaceAction(.settle, route: .orchestrator("m1"))
        try await client.workspaceAction(.reopen, route: .orchestrator("m2"))
        try await client.workspaceAction(.recap, route: .chief)
        do {
            try await client.workspaceAction(.recap, route: .ticket("uncertain"))
            XCTFail("Uncertain delivery must not show success")
        } catch { XCTAssertTrue(error.localizedDescription.contains("uncertain")) }
    }

    func testAttentionPrecedenceAndUnreadDoesNotCreateASection() throws {
        let overlap = try ticket(["awaiting_approval": true, "awaiting_answer": true, "assigned": true, "agent_state": "working"])
        XCTAssertEqual(WorkspaceGrouping.section(for: overlap), .approval)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_answer": true, "assigned": true])), .answer)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_reply": true, "ticket_status": "agent"])), .agent)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_reply": true])), .other)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["agent_state": "errored", "ticket_status": "agent"])), .errored)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["stage": "needs_consequences", "awaiting_agent_approval": true])), .agentApproval)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_agent_approval": true])), .agentApproval)
    }

    func testRailExcludesHiddenWorkEvenWhenItNeedsApproval() throws {
        for override: [String: Any] in [["is_backlogged": true], ["blocked": true], ["ticket_status": "blocked"], ["stage": "done"]] {
            var changes = override
            changes["awaiting_approval"] = true
            XCTAssertNil(WorkspaceGrouping.section(for: try ticket(changes)))
        }
    }

    func testFocusKeepsOnlyUnreadOwnerMessagesInAgentGroupsWithoutChangingFullLists() throws {
        let quietAgent = try ticket(["id": "quiet", "ticket_status": "agent"])
        let workingAgent = try ticket(["id": "working", "ticket_status": "agent", "conversation_signal": ["working": true, "message_for_owner": false]])
        let unreadAgent = try ticket(["id": "unread", "ticket_status": "agent", "conversation_signal": ["working": false, "message_for_owner": true]])
        let quietApproval = try ticket(["id": "review", "awaiting_agent_approval": true])
        let unreadApproval = try ticket(["id": "review-unread", "awaiting_agent_approval": true, "conversation_signal": ["working": false, "message_for_owner": true]])
        let yours = try ticket(["id": "yours", "assigned": true])
        let full = WorkspaceGrouping.groups([quietAgent, workingAgent, unreadAgent, quietApproval, unreadApproval, yours])
        let focused = WorkspaceGrouping.focused(full)
        XCTAssertEqual(focused.map(\.section), [.yours, .agent, .agentApproval])
        XCTAssertEqual(focused.flatMap(\.tickets).map(\.id), ["yours", "unread", "review-unread"])
        XCTAssertEqual(full.flatMap(\.tickets).count, 6, "Focus does not remove entries from the full lists")
        XCTAssertEqual(WorkspaceGrouping.focused(WorkspaceGrouping.groups([quietAgent, quietApproval])).count, 0,
                       "Groups with no unread owner message disappear")
        XCTAssertEqual(WorkspaceGrouping.focused(WorkspaceGrouping.groups([yours])).first?.tickets.map(\.id), ["yours"])
    }

    func testMachineCatalogueAndScopedHealthKeepUnreadWhenActivityIsUnknown() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [WorkspaceURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "https://workspace-stub.invalid")!, session: session)
        let machines = try await client.workspaceMachines().machines
        let machine = try XCTUnwrap(machines.first)
        XCTAssertEqual(machine.machineId, "present /?&")
        XCTAssertEqual(machine.name, "Office Mac")
        XCTAssertFalse(machine.isHome)
        XCTAssertNil(machine.runningWorkers)
        let running = try ticket(["locale": "macos", "machine_id": machine.machineId, "ticket_status": "agent",
                                  "conversation_signal": ["working": true, "message_for_owner": true]])
        XCTAssertEqual(running.machineId, machine.machineId)
        let present = try await client.workspaceMachineHealth(machineId: machine.machineId)
        XCTAssertTrue(present.activityKnown)
        XCTAssertTrue(try XCTUnwrap(WorkspaceMachineHealth.signal(for: running, health: present)).working)
        let away = try await client.workspaceMachineHealth(machineId: "away")
        let unknown = try XCTUnwrap(WorkspaceMachineHealth.signal(for: running, health: away))
        XCTAssertFalse(unknown.working)
        XCTAssertTrue(unknown.messageForOwner)
        XCTAssertEqual(away.activityLabel(for: running), "Paused: the Mac is away")
        let held = try ticket(["locale": "macos", "ticket_status": "agent", "agent_state": "idle",
                               "conversation_signal": ["working": false, "message_for_owner": false]])
        XCTAssertEqual(away.activityLabel(for: held), "Waiting for the Mac")
        XCTAssertFalse(try XCTUnwrap(WorkspaceMachineHealth.signal(for: running, health: nil)).working)
        let resource = WorkspaceResources.machineHealth(machine.machineId)
        XCTAssertEqual(URLComponents(string: resource.key)?.queryItems?.first?.value, machine.machineId)
    }

    func testSurvivorsKeepPositionAndArrivalsAppendWhenSectionsChange() throws {
        let alpha = try ticket(["id": "a", "title": "Alpha", "assigned": true, "activity_at": 1])
        let beta = try ticket(["id": "b", "title": "Beta", "assigned": true, "activity_at": 2])
        let tied = try ticket(["id": "z", "title": "alpha", "assigned": true, "activity_at": 1])
        XCTAssertEqual(WorkspaceGrouping.groups([tied, alpha, beta]).first?.tickets.map(\.id), ["b", "a", "z"],
                       "Equal activity uses case-insensitive title, then identity")
        let previous = WorkspaceGrouping.groups([beta, alpha])
        XCTAssertEqual(previous.first?.tickets.map(\.id), ["b", "a"], "Initial activity outranks alphabetic order")
        let newArrival = try ticket(["id": "c", "title": "A new arrival", "assigned": true, "activity_at": 3])
        let newerArrival = try ticket(["id": "d", "title": "Z newest arrival", "assigned": true, "activity_at": 4])
        let refresh = WorkspaceGrouping.groups([newArrival, beta, newerArrival, alpha], previous: previous)
        XCTAssertEqual(refresh.first?.tickets.map(\.id), ["b", "a", "d", "c"], "Arrivals append in activity order")
        let moved = try ticket(["id": "a", "title": "Alpha", "ticket_status": "agent"])
        let next = WorkspaceGrouping.groups([moved, beta, newArrival, newerArrival], previous: refresh)
        XCTAssertEqual(next.map(\.section), [.yours, .agent])
        XCTAssertEqual(next.first?.tickets.map(\.id), ["b", "d", "c"])
        XCTAssertEqual(next.last?.tickets.map(\.id), ["a"])
    }
}

private final class WorkspaceURLProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var data = request.httpBody ?? Data()
        if data.isEmpty, let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(buffer, count: count)
            }
        }
        let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        let path = request.url!.path
        var reply = "{}"
        let valid: Bool
        switch path {
        case "/api/tickets/t1/backlog", "/api/tickets/t1/resume":
            valid = request.httpMethod == "POST" && body?.isEmpty == true
        case "/api/managers/m1/settled", "/api/managers/m2/settled":
            valid = request.httpMethod == "PATCH" && body?["settled"] as? Bool == path.contains("/m1/")
        case "/api/machines":
            valid = request.httpMethod == "GET"
            reply = #"{"machines":[{"machine_id":"present /?&","name":"Office Mac","icon":"laptop","color":"orchid","is_home":false,"available":true,"running_workers":null,"waiting_tickets":2}]}"#
        case "/api/executor/health":
            let machine = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "machine_id" })?.value
            valid = request.httpMethod == "GET" && ["present /?&", "away"].contains(machine ?? "")
            reply = machine == "away" ? #"{"mode":"remote","presence":"asleep","since_ms":1000,"last_contact_at_ms":1000,"refusal":null}"# : #"{"mode":"remote","presence":"present","since_ms":1000,"last_contact_at_ms":1000,"refusal":null}"#
        case "/api/recap-requests":
            let target = body?["target"] as? [String: String]
            valid = request.httpMethod == "POST" && (target == ["kind": "chief", "id": "chief"] || target == ["kind": "ticket", "id": "uncertain"])
            reply = target?["id"] == "uncertain" ? #"{"fate":"uncertain"}"# : #"{"fate":"queued"}"#
        default: valid = false
        }
        guard valid else { client?.urlProtocol(self, didFailWithError: URLError(.badURL)); return }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(reply.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
