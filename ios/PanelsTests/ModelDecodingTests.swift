import XCTest
@testable import Panels

final class ModelDecodingTests: XCTestCase {
    private func decode<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: name, withExtension: "json"))
        return try APIClient.decoder().decode(type, from: Data(contentsOf: url))
    }
    func testLiveWorkspaceSummariesPreserveGroupingAndPagination() throws {
        let managers = try decode("managers", as: ManagersResponse.self)
        XCTAssertEqual(managers.managers.first?.id, "si_4chgag3w")
        XCTAssertEqual(managers.managers.first?.project, "Panels")
        XCTAssertTrue(managers.page.complete)
        let tickets = try decode("tickets", as: TicketsResponse.self)
        XCTAssertEqual(tickets.tickets.first?.manager?.id, "si_cgnx4jfw")
        XCTAssertEqual(tickets.tickets.first?.workerType, "exploration")
        XCTAssertEqual(tickets.page.returnCount, tickets.tickets.count)
    }
    func testLiveReviewAndDetailContracts() throws {
        let review = try decode("review", as: ReviewResponse.self)
        XCTAssertEqual(review.items.last?.ticketId, "t_02zd37tz")
        XCTAssertEqual(review.items.last?.proposal?.field, "wireframe")
        let ticket = try decode("ticket-full", as: TicketDetail.self)
        XCTAssertEqual(ticket.id, "t_02zd37tz")
        XCTAssertEqual(ticket.manager.kind, "manager")
        XCTAssertNotNil(ticket.pendingProposal)
        XCTAssertFalse(ticket.fieldValues.isEmpty)
        let manager = try decode("manager", as: ManagerDetail.self)
        XCTAssertEqual(manager.id, "si_4chgag3w")
        XCTAssertFalse(manager.tickets.isEmpty)
        let artifacts = try decode("artifacts", as: ArtifactPage.self)
        XCTAssertEqual(artifacts.entries.first?.directory, "artifacts")
        XCTAssertNil(artifacts.entries.first?.href)
        let deployment = try decode("deployment", as: DeploymentStatus.self)
        XCTAssertEqual(deployment.outcome, "succeeded")
    }
    func testChangeStreamIgnoresHeartbeatsAndRecognizesRefresh() {
        var parser = ChangeStreamParser()
        XCTAssertFalse(parser.consume(": keep-alive"))
        XCTAssertFalse(parser.consume(""))
        XCTAssertTrue(parser.consume("data: change"))
        XCTAssertFalse(parser.consume("data: unrelated"))
    }
}
