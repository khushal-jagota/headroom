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

    func testAttentionPrecedenceAndUnreadDoesNotCreateASection() throws {
        let overlap = try ticket(["awaiting_approval": true, "awaiting_answer": true, "assigned": true, "agent_state": "working"])
        XCTAssertEqual(WorkspaceGrouping.section(for: overlap), .approval)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_answer": true, "assigned": true])), .answer)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_reply": true, "ticket_status": "agent"])), .agent)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_reply": true])), .other)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["agent_state": "errored", "ticket_status": "agent"])), .errored)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["waiting_to_closeout": true, "awaiting_agent_approval": true])), .consequences)
        XCTAssertEqual(WorkspaceGrouping.section(for: try ticket(["awaiting_agent_approval": true])), .agentApproval)
    }

    func testRailExcludesHiddenWorkEvenWhenItNeedsApproval() throws {
        for override: [String: Any] in [["is_backlogged": true], ["ticket_status": "blocked"], ["stage": "done"]] {
            var changes = override
            changes["awaiting_approval"] = true
            XCTAssertNil(WorkspaceGrouping.section(for: try ticket(changes)))
        }
    }

    func testSurvivorsKeepPositionAndArrivalsAppendWhenSectionsChange() throws {
        let alpha = try ticket(["id": "a", "title": "Alpha", "assigned": true])
        let beta = try ticket(["id": "b", "title": "Beta", "assigned": true])
        let previous = WorkspaceGrouping.groups([beta, alpha])
        let newArrival = try ticket(["id": "c", "title": "A new arrival", "assigned": true])
        let refresh = WorkspaceGrouping.groups([newArrival, beta, alpha], previous: previous)
        XCTAssertEqual(refresh.first?.tickets.map(\.id), ["a", "b", "c"])
        let moved = try ticket(["id": "a", "title": "Alpha", "ticket_status": "agent"])
        let next = WorkspaceGrouping.groups([moved, beta, newArrival], previous: refresh)
        XCTAssertEqual(next.map(\.section), [.yours, .agent])
        XCTAssertEqual(next.first?.tickets.map(\.id), ["b", "c"])
        XCTAssertEqual(next.last?.tickets.map(\.id), ["a"])
    }
}
