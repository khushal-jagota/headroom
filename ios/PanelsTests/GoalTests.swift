import XCTest
@testable import Panels

@MainActor final class GoalTests: XCTestCase {
    private func json(_ text: String) throws -> GoalJSON { try JSONDecoder().decode(GoalJSON.self, from: Data(text.utf8)) }
    private func goal() throws -> GoalRecord {
        try APIClient.decoder().decode(GoalRecord.self, from: Data(Self.goalWire.utf8))
    }
    static let goalWire = #"{"id":"g","project_id":"project_personal","title":"Release","why":"Help people","kind":"binary","active":true,"completed":false,"primary_checklist_block_id":"chosen","deadline":"2026-11-04","primary_chart":null,"page":{"kind":"headroom.page","version":1,"title":"Release","summary":"","blocks":[{"type":"section","id":"section","title":"Steps","views":[{"id":"a","label":"Plan","blocks":[{"type":"checklist","id":"chosen","items":[{"id":"one","label":"First","state":"open"}]}]}]}]},"revision":5,"created_at":1,"updated_at":1,"employee_backend":"codex","employee_launch_model":"gpt-6","employee_launch_reasoning_effort":null,"conversation_id":"c","status":"attention","status_date":"2026-11-04","next_refresh_at":1793836800}"#

    /// One corpus proof covers the cross-language presentation boundary using the
    /// current web's functions, not copied expectations or another browser host.
    func testCurrentWebCatalogueAndCurveParity() throws {
        guard let path = ProcessInfo.processInfo.environment["PANELS_GOAL_PARITY_PATH"] else { throw XCTSkip("Run ios/scripts/test-offline.sh for the web oracle") }
        let corpus = try JSONDecoder().decode(GoalJSON.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
        XCTAssertEqual(corpus["cases"].array.count, 132)
        for item in corpus["cases"].array {
            let block = item["block"], range = item["range"].text, name = item["name"].text
            let data = GoalChartPresentation.data(block, values: [], range: range)
            let headline = GoalChartPresentation.headline(block, data: data, active: item["active"].string, range: range)
            XCTAssertEqual(data.count, item["data"].array.count, name)
            for (actual, expected) in zip(data, item["data"].array) {
                XCTAssertEqual(actual.id, expected["key"].text, name)
                XCTAssertEqual(actual.values, expected["values"].objectNumbers, name)
            }
            XCTAssertEqual(headline.value, item["headline"]["value"].number, name)
            XCTAssertEqual(headline.target, item["headline"]["target"].number, name)
            XCTAssertEqual(GoalChartPresentation.tone(block, headline: headline), item["tone"].text, name)
            XCTAssertEqual(GoalChartPresentation.comparison(block, headline: headline), item["comparison"].text, name)
        }
        for item in corpus["curves"].array {
            let actual = GoalLineGeometry.samples(values: item["values"].array.map(\.number), positions: item["positions"].array.compactMap(\.number), breaks: item["breaks"].array.map { $0.bool == true }, smooth: item["smooth"].bool == true)
            for (point, expected) in zip(actual, item["samples"].array) {
                if let number = expected.number { XCTAssertEqual(try XCTUnwrap(point), number, accuracy: 0.00001) }
                else { XCTAssertNil(point) }
            }
        }
    }
    func testServerCalendarAndRecursiveChecklistProjectionKeepTheSavedDocument() throws {
        let original = try goal()
        XCTAssertEqual(GoalPresentation.deadlineTone(original.deadline, completed: false, today: original.statusDate), "attention")
        XCTAssertEqual(GoalPresentation.deadlineTone("2026-11-03", completed: false, today: original.statusDate), "bad")
        XCTAssertEqual(GoalPresentation.deadlineTone(nil, completed: true, today: original.statusDate), "neutral")
        XCTAssertEqual(GoalPresentation.deadlineTone(original.deadline, completed: true, today: original.statusDate), "good")
        XCTAssertEqual(GoalPresentation.primaryChecklist(original)?.id, "chosen")
        XCTAssertNil(GoalPresentation.primaryChart(original))
        let rendered = GoalPresentation.withoutChecklist(original.page["blocks"].array, id: "chosen")
        XCTAssertTrue(GoalPresentation.checklists(rendered).isEmpty)
        XCTAssertEqual(GoalPresentation.checklists(original.page["blocks"].array).count, 1)
        let changed = GoalPresentation.updateBlock(original.page["blocks"].array, id: "chosen") { $0.setting("title", .string("Changed")) }
        XCTAssertEqual(changed.first?["title"].text, "Steps")
        XCTAssertEqual(changed.first?["views"].array.first?["label"].text, "Plan")
        XCTAssertEqual(GoalPresentation.checklists(changed).first?["title"].text, "Changed")
        let roundTrip = try APIClient.decoder().decode(GoalRecord.self, from: JSONEncoder().encode(original))
        XCTAssertEqual(roundTrip.page, original.page)
    }
    func testLatestMissingReadingAndFutureExpectationNeverBorrowAnotherActual() throws {
        let chart = try json(#"{"type":"chart","id":"line","kind":"line","placement":"primary","measure_id":"m","target":10}"#)
        let observations = try json(#"[{"measure_id":"m","date":"2026-11-01","value":12},{"measure_id":"m","date":"2026-11-02","value":null}]"#).array
        let data = GoalChartPresentation.data(chart, values: observations, range: "month")
        XCTAssertNil(GoalChartPresentation.headline(chart, data: data).value)
        XCTAssertEqual(GoalChartPresentation.headline(chart, data: data, active: "2026-11-01").value, 12)
        let expectation = chart.setting("expectation", .object(["op": .string("constant"), "value": .number(10)]))
        let projection = try json(#"{"kind":"scalar","today":"2026-11-02","summary_date":"2026-11-02","points":[{"date":"2026-11-02","actual":{"status":"value","value":6},"expected":{"status":"value","value":10},"difference":-4,"meets_expectation":false,"break_before":false},{"date":"2026-11-03","actual":{"status":"missing_input","value":null},"expected":{"status":"value","value":11},"difference":null,"meets_expectation":null,"break_before":true}]}"#)
        let all = GoalChartPresentation.data(expectation, values: [], range: "all", projection: projection)
        XCTAssertEqual(all.count, 2)
        XCTAssertTrue(all[1].breakBefore)
        XCTAssertNil(all[1].values["actual"])
        XCTAssertEqual(GoalChartPresentation.headline(expectation, data: all, projection: projection).value, 6)
        XCTAssertEqual(GoalChartPresentation.data(expectation, values: [], range: "week", projection: projection).count, 1)
        XCTAssertEqual(GoalChartPresentation.tone(expectation, headline: .init(value: 20, target: 10)), "neutral")
    }
    func testCategoryHeadlinesUseBackendSummaryAndPartialEvidenceIndependentlyFromColour() throws {
        let block = try json(#"{"type":"chart","id":"a","kind":"bar","placement":"primary","binding":{"kind":"dataset","dataset_id":"d"},"aggregate":"sum","unit":"%","format":{"decimals":1},"difference_unit":"pp"}"#)
        let projection = try json(#"{"kind":"category","ranges":[{"id":"all","label":"All time","axis":"category","series":[{"key":"actual","label":"Activation","points":[{"key":"one","label":"One","date":null,"actual":{"status":"value","value":10}},{"key":"two","label":"Two","date":null,"actual":{"status":"value","value":90}}]}],"summary":{"actual":{"status":"value","value":35,"partial":true,"coverage_status":"partial","missing_dates":["2026-11-01"],"reasons":["Retained history is limited"]},"comparison":{"status":"value","value":40},"difference":-5,"meets_expectation":false,"date":null},"point_summaries":[] }]}"#)
        let data = GoalChartPresentation.data(block, values: [], range: "all", projection: projection)
        let headline = GoalChartPresentation.headline(block, data: data, projection: projection)
        XCTAssertEqual(headline.value, 35)
        XCTAssertEqual(headline.target, 40)
        XCTAssertEqual(GoalChartPresentation.comparison(block, headline: headline), "5.0 pp below target")
        XCTAssertEqual(GoalChartPresentation.tone(block, headline: headline), "bad")
        XCTAssertTrue(GoalChartPresentation.observationDetails(headline).contains("Source coverage: partial."))
        XCTAssertTrue(GoalChartPresentation.observationDetails(headline).contains("Retained history is limited"))
    }
    func testReferenceInterpolationAndCompositionDoNotInventMissingMembers() throws {
        let chart = try json(#"{"type":"chart","id":"x","kind":"allocation","placement":"supporting","series":[{"name":"Build","points":[{"label":"2026-11-01","value":5},{"label":"2026-11-03","value":7}]},{"name":"Read","points":[{"label":"2026-11-01","value":3},{"label":"2026-11-03","value":null}]}],"reference":[{"date":"2026-11-01","value":10},{"date":"2026-11-03","value":20}]}"#)
        XCTAssertEqual(GoalChartPresentation.reference(chart, date: "2026-11-02"), 15)
        XCTAssertNil(GoalChartPresentation.reference(chart, date: "2026-11-04"))
        let data = GoalChartPresentation.data(chart, values: [], range: "all")
        XCTAssertNil(GoalChartPresentation.headline(chart, data: data).value)
        XCTAssertEqual(GoalChartPresentation.headline(chart, data: data, active: "2026-11-01").value, 8)
        XCTAssertEqual(GoalChartPresentation.suffix(try json(#"{"unit":"words"}"#)), " words")
        XCTAssertEqual(GoalChartPresentation.suffix(try json(#"{"unit":"words","format":{"suffix":"x"}}"#)), "x")
        let manual = GoalChartPresentation.manualDisplayBlock(chart, value: 2.125)
        XCTAssertEqual(GoalChartPresentation.format(manual, 2.125), "2.125")
    }
    func testFiltersCombineReadingAvailabilityCompletionAndUndatedGoals() throws {
        var record = try goal(); var filter = GoalFilters()
        filter.current.operation = "missing"; filter.completion = "open"
        XCTAssertTrue(filter.matches(record, current: nil, target: 3))
        XCTAssertFalse(filter.matches(record, current: 0, target: 3))
        filter.dueFrom = "2026-11-01"; record.deadline = nil
        XCTAssertFalse(filter.matches(record, current: nil, target: 3))
        filter.dueFrom = ""; filter.state = "inactive"
        XCTAssertFalse(filter.matches(record, current: nil, target: 3))
    }
    func testGoalOwnerUsesTheRealConversationAndArtifactDoors() throws {
        let item = ReviewItem(reviewItemType: "message", title: "Release", waitingSince: 1, conversationId: "c", ownerKind: "goal", ownerId: "g")
        XCTAssertEqual(item.owner, .goal("g"))
        XCTAssertEqual(item.owner?.path, "/api/goals/g/conversation")
        let base = URL(string: "https://panels.example")!
        let target = try XCTUnwrap(ArtifactTarget.from(URL(string: "https://panels.example/files/goals/g/proof.md")!, baseURL: base))
        XCTAssertEqual(target.owner.conversationOwner, .goal("g"))
        XCTAssertEqual(try APIClient(baseURL: base).artifactURL(target).path, "/files/goals/g/proof.md")
        XCTAssertEqual(ArtifactTarget.from(target.previewURL(baseURL: base), baseURL: base), target)
        XCTAssertEqual(NotificationRouting.destination("#/workspace/goal/g"), .goal("g"))
    }
    func testConflictingTextRetryKeepsCapturedRevisionAndDiscardUsesAcceptedGoal() async throws {
        GoalProtocol.state.reset(try goal())
        let api = client(); let session = GoalSession(id: "g", api: api, goal: try goal())
        GoalProtocol.state.conflictGoal()
        await session.save(["title": .string("Draft")], revision: 5)
        XCTAssertNotNil(session.saveError)
        await session.retry()
        let writes = GoalProtocol.state.writes()
        XCTAssertEqual(writes.count, 2)
        XCTAssertEqual(writes[0]["expected_revision"].number, 5)
        XCTAssertEqual(writes[0], writes[1])
        await session.discard()
        XCTAssertNil(session.saveError)
        XCTAssertEqual(session.goal?.revision, 9)
        XCTAssertEqual(session.goal?.title, "Other editor")
    }
    func testUncertainManualWriteRetriesExactIdentityAndDoesNotRegressState() async throws {
        GoalProtocol.state.reset(try goal())
        let editor = GoalManualEditor(measureID: "m", cumulative: true, api: client())
        await editor.refresh()
        editor.beginEditing()
        let changed = GoalProtocolState.manualState.replacingOccurrences(of: "\"write_revision\":1", with: "\"write_revision\":2")
        editor.accept(try APIClient.decoder().decode(GoalManualState.self, from: Data(changed.utf8)))
        await editor.write(value: 12)
        XCTAssertNotNil(editor.error)
        XCTAssertNotNil(editor.pending)
        XCTAssertEqual(editor.pending?.expectedWriteRevision, 1)
        await editor.retry()
        XCTAssertNil(editor.error)
        XCTAssertEqual(editor.state?.value(cumulative: true), 12)
        let writes = GoalProtocol.state.manualWrites()
        XCTAssertEqual(writes.count, 2)
        XCTAssertEqual(writes[0], writes[1])
        XCTAssertEqual(writes[0]["date"].text, "2026-11-04")
        let old = try APIClient.decoder().decode(GoalManualState.self, from: Data(GoalProtocolState.manualState.utf8))
        editor.accept(old)
        XCTAssertEqual(editor.state?.writeRevision, 2)
    }
    private func client() -> APIClient {
        let config = URLSessionConfiguration.ephemeral; config.protocolClasses = [GoalProtocol.self]
        return APIClient(baseURL: URL(string: "https://goals-tests.example")!, session: URLSession(configuration: config))
    }
}

private final class GoalProtocol: URLProtocol, @unchecked Sendable {
    static let state = GoalProtocolState()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (status, data) = try Self.state.response(request)
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data); client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() { }
}
private final class GoalProtocolState: @unchecked Sendable {
    let lock = NSLock()
    private var goal: GoalRecord?
    private var goalWrites: [GoalJSON] = []
    private var commands: [GoalJSON] = []
    static let manualState = #"{"measure_id":"m","definition_revision":1,"write_revision":1,"timezone":"UTC","current_date":"2026-11-04","date":"2026-11-04","observation":{"value":5},"latest_observation":{"value":5}}"#
    func reset(_ value: GoalRecord) { lock.lock(); defer { lock.unlock() }; goal = value; goalWrites = []; commands = [] }
    func conflictGoal() {
        lock.lock(); defer { lock.unlock() }
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        var fields = try! JSONSerialization.jsonObject(with: encoder.encode(goal)) as! [String: Any]
        fields["title"] = "Other editor"; fields["revision"] = 9
        goal = try! APIClient.decoder().decode(GoalRecord.self, from: JSONSerialization.data(withJSONObject: fields))
    }
    func writes() -> [GoalJSON] { lock.lock(); defer { lock.unlock() }; return goalWrites }
    func manualWrites() -> [GoalJSON] { lock.lock(); defer { lock.unlock() }; return commands }
    private func bodyData(_ request: URLRequest) -> Data {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return Data() }
        stream.open(); defer { stream.close() }
        var result = Data(); var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable { let count = stream.read(&buffer, maxLength: buffer.count); if count <= 0 { break }; result.append(buffer, count: count) }
        return result
    }
    func response(_ request: URLRequest) throws -> (Int, Data) {
        lock.lock(); defer { lock.unlock() }
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        let path = request.url!.path
        if path.hasSuffix("/manual-state") { return (200, Data(Self.manualState.utf8)) }
        if path.hasSuffix("/manual-writes") {
            let body = try JSONDecoder().decode(GoalJSON.self, from: bodyData(request))
            commands.append(body)
            if commands.count == 1 { throw URLError(.timedOut) }
            let reply = #"{"receipt":{"date":"2026-11-04"},"replayed":true,"state":{"measure_id":"m","definition_revision":1,"write_revision":2,"timezone":"UTC","current_date":"2026-11-04","date":"2026-11-04","observation":{"value":12},"latest_observation":{"value":12}}}"#
            return (200, Data(reply.utf8))
        }
        if request.httpMethod == "PATCH" {
            goalWrites.append(try JSONDecoder().decode(GoalJSON.self, from: bodyData(request)))
            return (409, Data(#"{"error":{"message":"Goal changed; refresh before editing"}}"#.utf8))
        }
        if path.hasSuffix("/readings") { return (200, Data(#"{"goal_id":"g","revision":9,"status":"attention","status_date":"2026-11-04","next_refresh_at":1793836800,"values":[],"sources":[],"charts":{}}"#.utf8)) }
        return (200, try encoder.encode(goal))
    }
}

private extension GoalJSON {
    var objectNumbers: [String: Double] {
        guard case .object(let fields) = self else { return [:] }
        return fields.compactMapValues(\.number)
    }
}
