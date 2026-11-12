import XCTest
@testable import Panels

@MainActor final class GoalTests: XCTestCase {
    private func json(_ text: String) throws -> GoalJSON { try JSONDecoder().decode(GoalJSON.self, from: Data(text.utf8)) }
    private func goal() throws -> GoalRecord {
        try APIClient.decoder().decode(GoalRecord.self, from: Data(Self.goalWire.utf8))
    }
    static let goalWire = #"{"id":"g","project_id":"project_personal","title":"Release","why":"Help people","kind":"binary","active":true,"completed":false,"completion_date":null,"completion_rule":null,"outcome":"pending","primary_checklist_block_id":"chosen","deadline":"2026-11-04","primary_chart":null,"page":{"kind":"headroom.page","version":1,"title":"Release","summary":"","blocks":[{"type":"section","id":"section","title":"Steps","views":[{"id":"a","label":"Plan","blocks":[{"type":"checklist","id":"chosen","items":[{"id":"one","label":"First","state":"open"}]}]}]}]},"revision":5,"created_at":1,"updated_at":1,"employee_backend":"codex","employee_launch_model":"gpt-6","employee_launch_reasoning_effort":null,"holder_kind":"chief","holder_id":null,"legacy_conversation_id":"c","status":"attention","status_date":"2026-11-04","next_refresh_at":1793836800}"#

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
    func testCounterPlacesSurviveNewFractionDigitsAndGrouping() throws {
        let block = try json(#"{"format":{"prefix":"Q4 ","suffix":" hours2"}}"#)
        let before = GoalChartPresentation.counterParts(block, value: 999)
        let after = GoalChartPresentation.counterParts(GoalChartPresentation.manualDisplayBlock(block, value: 1000.25), value: 1000.25)
        XCTAssertEqual(before.first { $0.id == "i0" }?.digit, 9)
        XCTAssertEqual(after.first { $0.id == "i0" }?.digit, 0)
        XCTAssertEqual(after.first { $0.id == "f0" }?.digit, 2)
        XCTAssertEqual(after.first { $0.id == "f1" }?.digit, 5)
        XCTAssertNil(after.first { $0.id == "prefix" }?.digit)
        XCTAssertNil(after.first { $0.id == "suffix" }?.digit)
        XCTAssertEqual(after.map(\.text).joined(), "Q4 1,000.25 hours2")
    }
    func testServerCalendarAndRecursiveChecklistProjectionKeepTheSavedDocument() throws {
        let original = try goal()
        XCTAssertEqual(original.outcome, "pending")
        XCTAssertNil(original.completionDate)
        XCTAssertNil(original.completionRule)
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
        let order = try json(#"{"items":[{"id":"a","state":"open"},{"id":"b","state":"done"},{"id":"c","state":"blocked"}]}"#)
        XCTAssertEqual(GoalPresentation.nextChecklistItem(order, after: "b"), "c")
        XCTAssertEqual(GoalPresentation.nextChecklistItem(order, after: "c"), "a")
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
        XCTAssertEqual(GoalChartPresentation.format(GoalChartPresentation.manualDisplayBlock(chart, value: 400), 400), "400")
        XCTAssertEqual(GoalChartPresentation.manualLiteral(chart, value: 1e-25), "1e-25")
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
    func testGoalLinksStillOpenTheGoalWhileRetiredOwnersHaveNoConversationDoor() {
        XCTAssertNil(ConversationOwner.goal("g").path)
        XCTAssertEqual(NotificationRouting.destination("#/workspace/goal/g"), .goal("g"))
    }
    /// A route owns unfinished edits after a Goal leaves its visible membership.
    /// Existing refresh tests keep the row mounted and do not prove this boundary.
    func testRouteSessionCollectionRetainsRemovedEditsAndPrunesCleanSessions() async throws {
        GoalProtocol.state.reset(try goal())
        let api = client(), resources = ResourceOwner(api: client()), sessions = PageSessionCollection()
        func record(_ id: String) throws -> GoalRecord {
            try APIClient.decoder().decode(GoalRecord.self, from: Data(Self.goalWire.replacingOccurrences(of: "\"id\":\"g\"", with: "\"id\":\"\(id)\"").utf8))
        }
        let records = try ["text", "manual", "pending", "clean"].map(record)
        let active = sessions.update(records, api: api, resources: resources)
        active[0].textDrafts["title"] = GoalTextDraft(text: "Unfinished title", revision: 5)
        let block = GoalJSON.object(["measure_id": .string("m"), "manual": .object(["model": .string("daily_quota")])])
        active[1].manual(block).draft = "12"
        let command = active[2].manual(block)
        command.accept(try APIClient.decoder().decode(GoalManualState.self, from: Data(GoalProtocolState.manualState.utf8)))
        await command.write(value: 12)
        XCTAssertNotNil(command.pending, "The fixture retains an uncertain manual command")
        sessions.update([], api: api, resources: resources)
        for id in ["text", "manual", "pending"] { XCTAssertNotNil(sessions.session(id, server: api.baseURL)) }
        XCTAssertNil(sessions.session("clean", server: api.baseURL))
        let returned = sessions.update([records[0]], api: api, resources: resources)
        XCTAssertTrue(returned[0] === active[0])
        XCTAssertEqual(returned[0].textDrafts["title"]?.text, "Unfinished title")
        sessions.update([], api: api, resources: resources)
        active[0].textDrafts = [:]; active[1].manual(block).draft = nil
        sessions.prune()
        XCTAssertNil(sessions.session("text", server: api.baseURL))
        XCTAssertNil(sessions.session("manual", server: api.baseURL))
        XCTAssertNotNil(sessions.session("pending", server: api.baseURL), "Membership never discards an unresolved command")
    }
    /// Replaceable child views ask their parent route for the same edit owner.
    /// List membership coverage does not prove document/holder acquisition or server isolation.
    func testRouteOwnerReusesDocumentAndHolderEditsAcrossChildRecreation() async throws {
        GoalProtocol.state.reset(try goal())
        let api = client(), resources = ResourceOwner(api: client()), route = PageSessionRoute()
        var child: PageSession? = route.goal("g", api: api, resources: resources)
        weak var retained = child
        child?.textDrafts["title"] = GoalTextDraft(text: "Unfinished", revision: 5)
        let block = try json(#"{"measure_id":"m","manual":{"model":"daily_quota"}}"#)
        let editor = try XCTUnwrap(child).manual(block)
        editor.draft = "12"
        editor.accept(try APIClient.decoder().decode(GoalManualState.self, from: Data(GoalProtocolState.manualState.utf8)))
        await editor.write(value: 12)
        await child?.save(["title": .string("Unfinished")], revision: 5)
        child = nil // The old document view and its local state disappear.
        let replacement = route.goal("g", api: api, resources: resources)
        XCTAssertTrue(replacement === retained)
        XCTAssertEqual(replacement.textDrafts["title"]?.text, "Unfinished")
        XCTAssertTrue(replacement.hasPendingEdit)
        XCTAssertTrue(replacement.manual(block) === editor)
        XCTAssertNotNil(editor.pending)
        let holder = route.holder(.manager("holder-test"), api: api, resources: resources)
        holder.textDrafts["block"] = GoalTextDraft(text: "Holder draft", revision: 4)
        XCTAssertTrue(route.holder(.manager("holder-test"), api: api, resources: resources) === holder)
        let otherAPI = APIClient(baseURL: URL(string: "https://other.example")!)
        let other = route.goal("g", api: otherAPI, resources: ResourceOwner(api: otherAPI))
        XCTAssertFalse(other === replacement)
        XCTAssertTrue(route.goal("g", api: api, resources: resources) === replacement)
        XCTAssertEqual(route.retainedCount, 2, "Only the two dirty sessions survive selection changes")
        replacement.textDrafts = [:]; await replacement.discard(); await editor.discard()
        holder.textDrafts = [:]
        route.prune()
        XCTAssertEqual(route.retainedCount, 1, "Clean inactive sessions do not accumulate")
    }
    /// An accepted document can advance before its separate readings endpoint succeeds.
    /// Preserve the prior projection through both the revision gap and a failed refresh.
    func testReadingsStayVisibleAndStaleUntilCurrentRevisionArrives() async throws {
        GoalProtocol.state.reset(try goal())
        GoalProtocol.state.setReadings(revision: 5)
        let api = client()
        // Model a supported coherent envelope. The older-server fallback intentionally
        // lacks a revision stamp and remains stale, even after a successful body read.
        let transport = ResourceTransport(versions: { _, _ in throw URLError(.unsupportedURL) }, read: { api, key, _ in
            ResourceRead(data: try await api.responseData(key).0, epoch: "goal-test", revision: "accepted")
        })
        let session = PageSession(id: "g", api: api, goal: try goal(), resources: ResourceOwner(api: api, transport: transport))
        await session.refresh()
        XCTAssertEqual(session.readings?.revision, 5)
        XCTAssertFalse(session.readingsAreStale)
        GoalProtocol.state.conflictGoal()
        await session.refresh()
        XCTAssertEqual(session.goal?.revision, 9)
        XCTAssertEqual(session.readings?.revision, 5)
        XCTAssertEqual(session.readings?.charts["chart"]?["current"].number, 12)
        XCTAssertTrue(session.readingsAreStale)
        GoalProtocol.state.setReadings(revision: 9, fails: true)
        await session.refresh()
        XCTAssertEqual(session.readings?.revision, 5)
        XCTAssertEqual(session.readings?.charts["chart"]?["current"].number, 12)
        XCTAssertTrue(session.readingsAreStale)
        XCTAssertNotNil(session.readError)
        GoalProtocol.state.setReadings(revision: 9)
        await session.refresh()
        XCTAssertEqual(session.readings?.revision, 9)
        XCTAssertFalse(session.readingsAreStale)
        XCTAssertNil(session.readError)
    }
    func testIndependentRefreshCannotClearSubmittedTextAndConfirmedSavePreservesNewerTyping() async throws {
        GoalProtocol.state.reset(try goal()); GoalProtocol.state.acceptGoalWrites()
        let session = PageSession(id: "g", api: client(), goal: try goal())
        let key = "header:title"
        session.textDrafts[key] = GoalTextDraft(text: "Submitted", revision: 5)
        let first = Task {
            await session.save(["title": .string("Submitted")], revision: 5,
                textSubmission: GoalTextSubmission(key: key, text: "Submitted", revision: 5))
        }
        for _ in 0..<100 {
            if !GoalProtocol.state.writes().isEmpty { break }
            try await Task.sleep(for: .milliseconds(1))
        }
        await session.refresh()
        XCTAssertEqual(session.goal?.revision, 6)
        XCTAssertEqual(session.textDrafts[key]?.text, "Submitted", "A new accepted revision is not this save's receipt")
        session.textDrafts[key]?.text = "Newer typing"
        await first.value
        XCTAssertNil(session.saveError)
        XCTAssertEqual(session.textDrafts[key]?.text, "Newer typing")
        XCTAssertEqual(session.textDrafts[key]?.revision, 6)
        await session.save(["title": .string("Newer typing")], revision: 6,
            textSubmission: GoalTextSubmission(key: key, text: "Newer typing", revision: 6))
        XCTAssertNil(session.saveError)
        XCTAssertNil(session.textDrafts[key], "Only confirmed acceptance clears the unchanged submitted text")
    }
    func testConflictingTextRetryKeepsCapturedRevisionAndDiscardUsesAcceptedGoal() async throws {
        GoalProtocol.state.reset(try goal())
        let api = client(); let session = PageSession(id: "g", api: api, goal: try goal())
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
        editor.beginEditing(); editor.draft = "invalid"
        let prepared = await editor.prepare()
        XCTAssertFalse(prepared)
        XCTAssertTrue(GoalProtocol.state.manualWrites().isEmpty)
        editor.beginEditing()
        XCTAssertEqual(editor.draft, "5", "A whole reading opens without a decimal")
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

    func testHolderPlacementAndNestedTicketsUseCurrentServerFields() throws {
        let chiefGoal = try goal()
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        var fields = try JSONSerialization.jsonObject(with: encoder.encode(chiefGoal)) as! [String: Any]
        fields["id"] = "managed"; fields["holder_kind"] = "manager"; fields["holder_id"] = "m"
        let managed = try APIClient.decoder().decode(GoalRecord.self, from: JSONSerialization.data(withJSONObject: fields))
        XCTAssertEqual(GoalHolder.chief.goals(in: [chiefGoal, managed]).map(\.id), ["g"])
        XCTAssertEqual(GoalHolder.manager("m").goals(in: [chiefGoal, managed]).map(\.id), ["managed"])
        XCTAssertTrue(GoalHolder.manager("other").goals(in: [chiefGoal, managed]).isEmpty)
        XCTAssertEqual(GoalHolder.manager("m").soleGoal(in: [chiefGoal, managed])?.id, "managed")
        XCTAssertNil(GoalHolder.manager("m").soleGoal(in: [managed, managed]))
        XCTAssertNil(GoalHolder.manager("other").soleGoal(in: [chiefGoal, managed]))
        XCTAssertEqual(managed.holder?.conversationOwner, .manager("m"))
        XCTAssertEqual(chiefGoal.holder?.conversationOwner, .chief)
        XCTAssertEqual(chiefGoal.legacyConversationId, "c")
        let page = try json(#"[{"type":"section","blocks":[],"views":[{"blocks":[{"type":"tickets","id":"t"}]}]}]"#)
        XCTAssertTrue(PageDocumentPresentation.hasTickets(page.array))
        XCTAssertFalse(PageDocumentPresentation.hasTickets(chiefGoal.page["blocks"].array))
    }

    func testHolderPageEditsUseHolderRevisionAndPreserveTheDocument() async throws {
        GoalProtocol.state.reset(try goal())
        let session = PageSession.holder(.manager("holder-test"), api: client())
        await session.refresh()
        XCTAssertTrue(session.hasContent)
        XCTAssertNil(session.goal)
        XCTAssertEqual(session.revision, 4)
        XCTAssertEqual(session.readings?.goalId, "holder-test")
        await session.updateBlock("chosen", revision: 3) { $0.setting("title", .string("Changed")) }
        XCTAssertNotNil(session.saveError)
        XCTAssertEqual(session.revision, 4)
        XCTAssertEqual(GoalProtocol.state.writes().last?["expected_revision"].number, 3)
        await session.discard()
        await session.updateBlock("chosen", revision: 4) { $0.setting("title", .string("Changed")) }
        XCTAssertNil(session.saveError)
        XCTAssertEqual(session.revision, 5)
        XCTAssertEqual(GoalPresentation.checklists(session.page?["blocks"].array ?? []).first?["title"].text, "Changed")
        XCTAssertEqual(session.page?["blocks"].array.first?["title"].text, "Steps")
        XCTAssertEqual(GoalProtocol.state.writes().count, 2)
    }

    /// The server test replays this same fixture through the repaired transactions.
    /// Existing stubs alone cannot prove that native request shape reaches that door.
    func testNativeDocumentRequestsMatchRepairedTransactionFixtureAndKeepCapturedRevision() async throws {
#if SWIFT_PACKAGE
        let fixtureURL = try XCTUnwrap(Bundle.module.url(forResource: "document-revision-requests", withExtension: "json", subdirectory: "Fixtures"))
#else
        let fixtureURL = try XCTUnwrap(Bundle(for: GoalTests.self).url(forResource: "document-revision-requests", withExtension: "json"))
#endif
        let fixture = try JSONDecoder().decode(GoalJSON.self, from: Data(contentsOf: fixtureURL))
        for item in fixture["cases"].array {
            let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
            var initial = try XCTUnwrap(JSONSerialization.jsonObject(with: encoder.encode(try goal())) as? [String: Any])
            var page = try XCTUnwrap(JSONSerialization.jsonObject(with: encoder.encode(fixture["page"])) as? [String: Any])
            var blocks = try XCTUnwrap(page["blocks"] as? [[String: Any]])
            blocks[0]["text"] = "Before native edit"; page["blocks"] = blocks; initial["page"] = page
            let record = try APIClient.decoder().decode(GoalRecord.self, from: JSONSerialization.data(withJSONObject: initial))
            GoalProtocol.state.reset(record); GoalProtocol.state.acceptGoalWrites()
            let api = client()
            let session: PageSession
            switch item["kind"].text {
            case "goal": session = PageSession(id: "g", api: api, goal: record)
            case "chief": session = PageSession.holder(.chief, api: api)
            default: session = PageSession.holder(.manager("holder-test"), api: api)
            }
            await session.refresh()
            let captured = try XCTUnwrap(item["expected_revision"].number)
            XCTAssertEqual(session.revision, Int(captured))
            await session.updateBlock("native-text", revision: Int(captured)) { $0.setting("text", .string("Native submitted")) }
            XCTAssertNil(session.saveError)
            XCTAssertEqual(session.revision, Int(captured) + 1)
            // Even a no-op body must reject the captured revision after acceptance.
            await session.updateBlock("native-text", revision: Int(captured)) { $0.setting("text", .string("Native submitted")) }
            XCTAssertNotNil(session.saveError)
            await session.retry()
            let expected = GoalJSON.object(["page": fixture["page"], "expected_revision": .number(captured)])
            XCTAssertEqual(GoalProtocol.state.writes(), [expected, expected, expected], "A failed retry never rebases the captured edit")
            for bytes in GoalProtocol.state.wireWrites() {
                let wire = String(decoding: bytes, as: UTF8.self)
                // The repaired server rejects 5.0. This checks emitted protocol bytes,
                // rather than JSON numeric equality, which conflates 5 and 5.0.
                let integerToken = #""expected_revision"\s*:\s*"# + String(Int(captured)) + #"(?=\s*[,}])"#
                XCTAssertNotNil(wire.range(of: integerToken, options: .regularExpression))
            }
            let path = item["path"].text.replacingOccurrences(of: "{goal_id}", with: "g").replacingOccurrences(of: "{manager_id}", with: "holder-test")
            XCTAssertEqual(GoalProtocol.state.patchRequests().map { $0.url?.path }, [path, path, path])
            for request in GoalProtocol.state.patchRequests() {
                XCTAssertEqual(request.httpMethod, "PATCH")
                XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
                XCTAssertEqual(request.value(forHTTPHeaderField: "X-Panels-iOS-Build"), NativeBuildGate.build)
            }
        }
    }

    func testGoalConversationUsesTheHolderCurrentConversationAndSendDoor() async throws {
        GoalProtocol.state.reset(try goal())
        let api = client()
        for holder in [GoalHolder.chief, .manager("holder-test")] {
            let current = try await api.currentConversation(holder.conversationOwner)
            XCTAssertEqual(current, holder.kind == .chief ? "chief-current" : "manager-current")
            let message = ConversationSend(conversationId: current, content: [.init(text: "Hello holder")], senderMessageId: UUID().uuidString, sentAtUnixMilliseconds: 1)
            let delivery = try await api.sendConversation(holder.conversationOwner, message: message)
            XCTAssertEqual(delivery.conversationId, current)
            XCTAssertEqual(delivery.fate, "injected")
        }
        XCTAssertEqual(GoalProtocol.state.requests(), ["/api/chief/conversation", "/api/chief/conversation/send", "/api/managers/holder-test/workspace", "/api/managers/holder-test/conversation/send"])
    }

    func testRetiredGoalLinksAndPersistedGoalSendsHaveNoRequestDoor() async throws {
        GoalProtocol.state.reset(try goal())
        let api = client()
        for link in ["/files/goals/g/plan.md", "/#/preview?source=goal&goal=g&path=plan.md"] {
            let url = URL(string: link, relativeTo: api.baseURL)!.absoluteURL
            XCTAssertNil(ArtifactTarget.from(url, baseURL: api.baseURL))
            XCTAssertTrue(ArtifactTarget.isRetiredGoalURL(url, baseURL: api.baseURL))
        }
        let store = LocalStore(url: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("phone.store"))
        let outbox = Outbox(store: store)
        let legacyOwner = try JSONDecoder().decode(ConversationOwner.self, from: Data(#"{"goal":{"_0":"g"}}"#.utf8))
        let message = ConversationSend(conversationId: nil, content: [.init(text: "Saved words")], senderMessageId: "retired", sentAtUnixMilliseconds: 1)
        try outbox.save(PendingWork(id: "retired", created: .now, kind: .message, owner: legacyOwner, message: message), server: LocalStore.server(api.baseURL))
        await outbox.drain(api: api)
        XCTAssertEqual(outbox.entries(LocalStore.server(api.baseURL)).first?.state, .changed)
        do { _ = try await api.sendConversation(legacyOwner, message: message); XCTFail("Retired owner sent") }
        catch { XCTAssertEqual((error as? APIError)?.status, 410) }
        do { _ = try await api.conversationConfiguration(legacyOwner, id: "old"); XCTFail("Retired owner opened") }
        catch { XCTAssertEqual((error as? APIError)?.status, 410) }
        XCTAssertEqual(GoalProtocol.state.requests(), [])
    }
    func testGoalProjectsReadRequestsTheFullCatalogue() async throws {
        GoalProtocol.state.reset(try goal())
        let response = try await client().goalProjects()
        let request = try XCTUnwrap(GoalProtocol.state.lastRequest())
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.path, "/api/projects")
        XCTAssertEqual(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false)?.queryItems,
                       [URLQueryItem(name: "detail", value: "full")])
        XCTAssertEqual(response["projects"].array.first?["id"].text, "project_personal")
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
            let deliver: @Sendable () -> Void = { [self] in
                client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
                client?.urlProtocol(self, didLoad: data); client?.urlProtocolDidFinishLoading(self)
            }
            if request.httpMethod == "PATCH", Self.state.delaysAcceptedWrites { DispatchQueue.global().asyncAfter(deadline: .now() + 0.05, execute: deliver) }
            else { deliver() }
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() { }
}
private final class GoalProtocolState: @unchecked Sendable {
    let lock = NSLock()
    private var goal: GoalRecord?
    private var goalWrites: [GoalJSON] = []
    private var encodedGoalWrites: [Data] = []
    private var commands: [GoalJSON] = []
    private var recordedRequests: [URLRequest] = []
    private var holderRevision = 4
    private var document: GoalJSON = .null
    private var acceptsGoalWrites = false
    private var readingsRevision = 9
    private var readingsFail = false
    func setReadings(revision: Int, fails: Bool = false) { lock.lock(); defer { lock.unlock() }; readingsRevision = revision; readingsFail = fails }
    var delaysAcceptedWrites: Bool { lock.lock(); defer { lock.unlock() }; return acceptsGoalWrites }
    func acceptGoalWrites() { lock.lock(); defer { lock.unlock() }; acceptsGoalWrites = true }
    func requests() -> [String] { lock.lock(); defer { lock.unlock() }; return recordedRequests.compactMap { $0.url?.path } }
    func lastRequest() -> URLRequest? { lock.lock(); defer { lock.unlock() }; return recordedRequests.last }
    func patchRequests() -> [URLRequest] { lock.lock(); defer { lock.unlock() }; return recordedRequests.filter { $0.httpMethod == "PATCH" } }
    static let manualState = #"{"measure_id":"m","definition_revision":1,"write_revision":1,"timezone":"UTC","current_date":"2026-11-04","date":"2026-11-04","observation":{"value":5},"latest_observation":{"value":5}}"#
    func reset(_ value: GoalRecord) { lock.lock(); defer { lock.unlock() }; goal = value; goalWrites = []; encodedGoalWrites = []; commands = []; recordedRequests = []; acceptsGoalWrites = false; readingsRevision = 9; readingsFail = false; holderRevision = 4; document = value.page }
    func conflictGoal() {
        lock.lock(); defer { lock.unlock() }
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        var fields = try! JSONSerialization.jsonObject(with: encoder.encode(goal)) as! [String: Any]
        fields["title"] = "Other editor"; fields["revision"] = 9
        goal = try! APIClient.decoder().decode(GoalRecord.self, from: JSONSerialization.data(withJSONObject: fields))
    }
    func writes() -> [GoalJSON] { lock.lock(); defer { lock.unlock() }; return goalWrites }
    func wireWrites() -> [Data] { lock.lock(); defer { lock.unlock() }; return encodedGoalWrites }
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
        recordedRequests.append(request)
        // This domain fixture exercises the ordinary endpoints. It does not serve envelopes.
        if path == "/api/resource-body" { return (404, Data(#"{"error":{"message":"Resource endpoint unavailable"}}"#.utf8)) }
        if path == "/api/projects" { return (200, Data(#"{"projects":[{"id":"project_personal","name":"Personal"}]}"#.utf8)) }
        if path == "/api/chief/conversation" { return (200, Data(#"{"conversation_id":"chief-current"}"#.utf8)) }
        if path == "/api/managers/holder-test/workspace" {
#if SWIFT_PACKAGE
            let fixture = Bundle.module.url(forResource: "manager", withExtension: "json", subdirectory: "Fixtures")!
#else
            let fixture = Bundle(for: GoalTests.self).url(forResource: "manager", withExtension: "json")!
#endif
            var value = try JSONSerialization.jsonObject(with: Data(contentsOf: fixture)) as! [String: Any]
            var manager = value["manager"] as! [String: Any]; manager["conversation_id"] = "manager-current"; value["manager"] = manager
            return (200, try JSONSerialization.data(withJSONObject: value))
        }
        if path == "/api/chief/conversation/send" || path == "/api/managers/holder-test/conversation/send" {
            guard request.httpMethod == "POST", request.value(forHTTPHeaderField: "X-Panels-iOS-Build") == NativeBuildGate.build else { throw URLError(.badServerResponse) }
            let body = try JSONDecoder().decode(GoalJSON.self, from: bodyData(request))
            let expected = path == "/api/chief/conversation/send" ? "chief-current" : "manager-current"
            guard body["conversation_id"].text == expected else { throw URLError(.badServerResponse) }
            return (200, Data("{\"conversation_id\":\"\(expected)\",\"fate\":\"injected\"}".utf8))
        }
        if path == "/api/managers/holder-test/page/readings" || path == "/api/chief/page/readings" {
            return (200, Data("{\"goal_id\":\"holder-test\",\"revision\":\(holderRevision),\"status\":\"neutral\",\"status_date\":\"2026-11-05\",\"next_refresh_at\":1794000000,\"values\":[],\"sources\":[],\"charts\":{}}".utf8))
        }
        if path == "/api/managers/holder-test/page" || path == "/api/chief/page" {
            if request.httpMethod == "PATCH" {
                let data = bodyData(request)
                let body = try JSONDecoder().decode(GoalJSON.self, from: data); goalWrites.append(body); encodedGoalWrites.append(data)
                if body["expected_revision"].number != Double(holderRevision) { return (409, Data(#"{"error":{"message":"Holder page changed"}}"#.utf8)) }
                document = body["page"]; holderRevision += 1
            }
            return (200, try encoder.encode(HolderPage(page: document, pageRevision: holderRevision)))
        }
        if path.hasSuffix("/manual-state") { return (200, Data(Self.manualState.utf8)) }
        if path.hasSuffix("/manual-writes") {
            let body = try JSONDecoder().decode(GoalJSON.self, from: bodyData(request))
            commands.append(body)
            if commands.count == 1 { throw URLError(.timedOut) }
            let reply = #"{"receipt":{"date":"2026-11-04"},"replayed":true,"state":{"measure_id":"m","definition_revision":1,"write_revision":2,"timezone":"UTC","current_date":"2026-11-04","date":"2026-11-04","observation":{"value":12},"latest_observation":{"value":12}}}"#
            return (200, Data(reply.utf8))
        }
        if request.httpMethod == "PATCH" {
            let data = bodyData(request)
            let body = try JSONDecoder().decode(GoalJSON.self, from: data); goalWrites.append(body); encodedGoalWrites.append(data)
            if acceptsGoalWrites, let current = goal, body["expected_revision"].number == Double(current.revision) {
                var fields = try JSONSerialization.jsonObject(with: encoder.encode(current)) as! [String: Any]
                if case .object(let patch) = body {
                    if let title = patch["title"] { fields["title"] = title.text }
                    if let page = patch["page"] { fields["page"] = try JSONSerialization.jsonObject(with: encoder.encode(page)) }
                }
                fields["revision"] = current.revision + 1
                goal = try APIClient.decoder().decode(GoalRecord.self, from: JSONSerialization.data(withJSONObject: fields))
                return (200, try encoder.encode(goal))
            }
            return (409, Data(#"{"error":{"message":"Goal changed; refresh before editing"}}"#.utf8))
        }
        if path.hasSuffix("/readings") {
            if readingsFail { throw URLError(.timedOut) }
            return (200, Data("{\"goal_id\":\"g\",\"revision\":\(readingsRevision),\"status\":\"attention\",\"status_date\":\"2026-11-04\",\"next_refresh_at\":1793836800,\"values\":[],\"sources\":[],\"charts\":{\"chart\":{\"current\":12}}}".utf8))
        }
        return (200, try encoder.encode(goal))
    }
}

private extension GoalJSON {
    var objectNumbers: [String: Double] {
        guard case .object(let fields) = self else { return [:] }
        return fields.compactMapValues(\.number)
    }
}
