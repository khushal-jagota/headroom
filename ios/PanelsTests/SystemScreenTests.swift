import XCTest
@testable import Panels

final class SystemScreenTests: XCTestCase {
    func testRecordedUsageKeepsUnpricedRecordsAndPartialSources() throws {
        let wire = try APIClient.decoder().decode(RecordedUsageSummary.self, from: Data(currentUsageJSON.utf8))
        let summary = UsageSummary(wire, days: 7)
        XCTAssertEqual(summary.totals.costUsd, 1.5)
        XCTAssertEqual(summary.totals.estimatedCostUsd, 1.5)
        XCTAssertEqual(summary.totals.unknownCostRecords, 1)
        XCTAssertEqual(summary.totals.totalTokens, 45, "Reasoning tokens are included in output already")
        XCTAssertEqual(summary.sources.first?.state, "partial")
        XCTAssertEqual(summary.sources.first?.host, "vps")
        XCTAssertNotNil(summary.pricingNote)
        XCTAssertEqual(UsagePresentation.cost(nil), "—")
        XCTAssertNotEqual(UsagePresentation.number(0), "—")
    }
    // Gap: the usage API test does not exercise cached resource identity or day rollover.
    @MainActor func testUsageRegistrationReusesSelectionAndRollsAtLocalMidnightAcrossDST() throws {
        let format = ISO8601DateFormatter()
        func date(_ value: String) throws -> Date { try XCTUnwrap(format.date(from: value)) }
        let london = try XCTUnwrap(TimeZone(identifier: "Europe/London"))
        for (start, next, hours) in [("2026-03-29T00:00:00Z", "2026-03-29T23:00:00Z", 23.0),
                                      ("2026-10-24T23:00:00Z", "2026-10-26T00:00:00Z", 25.0)] {
            let start = try date(start), next = try date(next)
            let registration = UsageResources.summary(days: 7, now: start, timeZone: london)
            let cached = try XCTUnwrap(UsageResources.cachedSummary)
            XCTAssertEqual(cached.day.duration, hours * 3600)
            for offset in [1.0, hours * 3600 - 1] {
                XCTAssertEqual(UsageResources.summary(days: 7, now: start.addingTimeInterval(offset), timeZone: london).key, registration.key)
                XCTAssertTrue(UsageResources.cachedSummary === cached, "Repeated view reads must reuse the prepared request")
            }
            let rolled = UsageResources.summary(days: 7, now: next, timeZone: london)
            XCTAssertFalse(UsageResources.cachedSummary === cached)
            XCTAssertNotEqual(rolled.key, registration.key, "The exact end of a day belongs to the next request")
            let query = try XCTUnwrap(URLComponents(string: rolled.key)?.queryItems)
            let until = hours == 23 ? "2026-03-30" : "2026-10-26"
            let since = hours == 23 ? "2026-03-24" : "2026-10-20"
            XCTAssertEqual(query.first { $0.name == "untilDay" }?.value, until)
            XCTAssertEqual(query.first { $0.name == "sinceDay" }?.value, since)
            let current = try XCTUnwrap(UsageResources.cachedSummary)
            let oneDay = UsageResources.summary(days: 1, now: next, timeZone: london)
            XCTAssertEqual(URLComponents(string: oneDay.key)?.queryItems?.first { $0.name == "sinceDay" }?.value, until)
            XCTAssertFalse(UsageResources.cachedSummary === current, "Range changes replace the single retained request")
            let changedRange = try XCTUnwrap(UsageResources.cachedSummary)
            let utc = TimeZone(secondsFromGMT: 0)!
            let changedZone = UsageResources.summary(days: 1, now: next, timeZone: utc)
            XCTAssertFalse(UsageResources.cachedSummary === changedRange)
            XCTAssertEqual(URLComponents(string: changedZone.key)?.queryItems?.first { $0.name == "timeZone" }?.value, utc.identifier)
            XCTAssertEqual(URLComponents(string: changedZone.key)?.queryItems?.first { $0.name == "untilDay" }?.value, hours == 23 ? "2026-03-29" : "2026-10-26")
        }
    }
    // New boundary: model catalogues must follow the Ticket, never the phone or VPS.
    func testTicketCatalogueAndUsageUseCurrentServerContracts() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MachineContractStub.self]
        let client = APIClient(baseURL: URL(string: "https://machine.invalid")!, session: URLSession(configuration: config))
        let remote = try await client.conversationBackends(owner: .ticket("t_mac"))
        let local = try await client.conversationBackends(owner: .chief)
        XCTAssertEqual(remote.backends.first?.defaultModelId, "mac-model")
        XCTAssertEqual(local.backends.first?.defaultModelId, "vps-model")
        // Risk: the new card fields break decoding, a backend with no effort shows one, or
        // the phone's own guess beats the server's model name. The remote card is the old shape.
        XCTAssertNil(remote.backends.first?.reportsAllowanceUsage)
        XCTAssertEqual(local.backends.map(\.reportsAllowanceUsage), [false, true])
        let plain = try XCTUnwrap(local.backends.first), reasoning = try XCTUnwrap(local.backends.last)
        XCTAssertFalse(plain.offersReasoning, "No card or model effort list: no effort")
        XCTAssertTrue(reasoning.offersReasoning)
        XCTAssertEqual(reasoning.modelName("alias"), "Family 4.7", "The server's name beats the raw resolved id")
        XCTAssertEqual(reasoning.modelName("unlisted"), "unlisted")
        XCTAssertEqual(plain.modelName("plain-model"), "Plain")
        let older = try APIClient.decoder().decode(TicketBackendCatalog.self, from: Data(currentBackendsJSON.replacingOccurrences(of: #","resolved_display_name":"Family 4.7""#, with: "").utf8))
        XCTAssertEqual(older.backends.last?.modelName("alias"), "family-4-7-20260101", "Without the server's name, the resolved id")
        let now = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-10-26T00:30:00Z"))
        let usage = try await client.usage(days: 7, now: now, timeZone: TimeZone(identifier: "Europe/London")!)
        XCTAssertEqual(usage.days, 7)
        XCTAssertEqual(usage.periods.first?.label, "2026-10-20")
        XCTAssertEqual(usage.periods.last?.label, "2026-10-26")
        XCTAssertEqual(usage.periods.count, 7)
        XCTAssertEqual(usage.periods.first?.totalTokens, 0)
    }
    // Risk: an unknown backend draws nothing, a bundled full-colour mark is replaced or
    // tinted by the server's icon, or an SVG the phone cannot draw leaves a blank.
    func testDriverMarksPreferBundledThenPNGThenInitials() throws {
        let server = URL(string: "https://panels.invalid")!
        let catalog = try APIClient.decoder().decode(DriverCatalog.self, from: Data(driverCatalogJSON.utf8))
        let bundled: (String) -> Bool = { $0 == "conversation-picture" }
        func mark(_ key: String, _ catalog: DriverCatalog?) -> DriverMark { DriverMark.resolve(key: key, catalog: catalog, bundled: bundled, server: server) }
        XCTAssertEqual(mark("picture", catalog), .bundled("conversation-picture"), "A bundled mark comes before the server's icon")
        XCTAssertEqual(mark("picture", nil), .bundled("conversation-picture"))
        XCTAssertEqual(mark("glyph", catalog), .remote(URL(string: "https://panels.invalid/api/drivers/glyph/icon?v=0123456789ab")!, template: true))
        XCTAssertEqual(mark("photo", catalog), .remote(URL(string: "https://panels.invalid/api/drivers/photo/icon?v=ba9876543210")!, template: false))
        XCTAssertEqual(mark("vector", catalog), .initials("V"), "An SVG-only icon gives initials")
        XCTAssertEqual(mark("elsewhere", catalog), .initials("E"), "An icon off this server is not fetched")
        XCTAssertEqual(mark("open-code", catalog), .initials("OC"), "An unknown key gives initials")
        XCTAssertEqual(mark("glyph", nil), .initials("G"))
        XCTAssertEqual(DriverCatalog.label("glyph", in: catalog), "Glyph Agent")
        XCTAssertEqual(DriverCatalog.label("glyph", in: nil), "Glyph", "The label falls back to the capitalised key")
        XCTAssertEqual(DriverCatalog.label("unknown", in: catalog), "Unknown")
        XCTAssertEqual(DriverCatalog.composerBackend(nil, in: catalog), "picture")
        XCTAssertEqual(DriverCatalog.composerBackend("glyph", in: catalog), "glyph")
        XCTAssertNil(DriverCatalog.composerBackend(nil, in: nil), "With neither, the composer mark stays clear")
        let minimal = try APIClient.decoder().decode(DriverCatalog.self, from: Data(#"{"drivers":[]}"#.utf8))
        XCTAssertNil(minimal.defaultBackendInstanceId)
        // Usage lists the backends the server says report allowance usage, in card order.
        let usage = try APIClient.decoder().decode(UsageBackends.self, from: Data(#"{"backends":[{"backend_key":"plain","installed":true,"version":null,"cached_usage":null,"diagnoses":[],"reports_allowance_usage":false},{"backend_key":"second","installed":true,"version":null,"cached_usage":null,"diagnoses":[],"reports_allowance_usage":true},{"backend_key":"first","installed":false,"version":null,"cached_usage":null,"diagnoses":[],"reports_allowance_usage":true}]}"#.utf8))
        XCTAssertEqual(usage.reporting.map(\.backendKey), ["second", "first"])
    }
    func testMacActivityRequiresReadyIdentityAndNeverClearsUnread() throws {
        let signal = ConversationSignal(working: true, messageForOwner: true)
        let ready = ExecutorHealth(mode: "remote", identity: .init(executorIncarnation: "current", connectionGeneration: 2), connectionState: "ready", lastContactAtMs: 1000)
        let disconnected = ExecutorHealth(mode: "remote", identity: ready.identity, connectionState: "disconnected", lastContactAtMs: 1000)
        let reconciling = ExecutorHealth(mode: "remote", identity: ready.identity, connectionState: "reconciling", lastContactAtMs: 1000)
        let unbound = ExecutorHealth(mode: "remote", identity: nil, connectionState: "ready", lastContactAtMs: nil)
        for health in [nil, disconnected, reconciling, unbound] {
            let shown = TicketMachine.signal(signal, locale: "macos", health: health)
            XCTAssertFalse(try XCTUnwrap(shown).working)
            XCTAssertTrue(try XCTUnwrap(shown).messageForOwner)
        }
        XCTAssertTrue(TicketMachine.signal(signal, locale: "macos", health: ready)?.working == true)
        XCTAssertFalse(TicketMachine.signal(signal, locale: "macos", health: ready, completed: true)?.working == true)
        XCTAssertTrue(TicketMachine.signal(signal, locale: "vps", health: nil)?.working == true)
        XCTAssertEqual(TicketMachine.name("macos"), "Remote machine")
    }
    func testBrokenTicketIsDiagnosticWithoutHidingTheRestOfTheList() throws {
        let json = #"{"id":"broken","title":"Needs repair","worker_type":"removed","stage":"old","ticket_status":"errored","integrity_error":{"reason":"Worker type is missing"},"recap":"Saved recap","conversation_id":"c_saved","is_backlogged":false}"#
        let data = Data(json.utf8)
        let summary = try APIClient.decoder().decode(TicketSummary.self, from: data)
        let child = try APIClient.decoder().decode(ManagerTicket.self, from: data)
        XCTAssertEqual(summary.integrityError?.reason, "Worker type is missing")
        XCTAssertEqual(child.integrityError?.reason, summary.integrityError?.reason)
        XCTAssertEqual(WorkerGroup.classify(summary), .errored)
        XCTAssertThrowsError(try APIClient.decoder().decode(TicketRead.self, from: data)) { error in
            XCTAssertEqual(error.localizedDescription, "Broken Ticket: Worker type is missing")
        }
        XCTAssertThrowsError(try APIClient.decoder().decode(TicketSummary.self, from: Data(#"{"id":"healthy-but-incomplete"}"#.utf8)))
    }
    // Gap: GET-only tests cannot prove explicit refresh reaches the server's provider
    // read door, or that a partial HTTP-200 failure remains visible. In-memory transport.
    func testExplicitRefreshUsesPostAndPreservesPartialFailure() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [UsageRefreshStub.self]
        let client = APIClient(baseURL: URL(string: "https://usage.invalid")!, session: URLSession(configuration: config))
        let result = try await client.refreshUsageBackends()
        XCTAssertEqual(result.backends.first?.cachedUsage?.observedAt, "2026-10-22T19:21:07Z")
        XCTAssertEqual(result.failure(nil), "Claude read failed; previous limits retained.")
        // An older server does not say which backends report usage; a recorded allowance counts.
        XCTAssertEqual(UsageBackends(backends: result.backends).reporting.map(\.backendKey), ["codex"])
        try await client.refreshRecordedUsage()
    }
    func testCapacityReadingsDoNotConfusePerCoreCPUOrMissingMemoryWithZero() {
        XCTAssertEqual(VPSPresentation.cpuShare(180, cores: 12), 15)
        XCTAssertNil(VPSPresentation.cpuShare(20, cores: 0))
        XCTAssertNil(VPSPresentation.used(total: 100, available: nil))
        XCTAssertEqual(VPSPresentation.used(total: 100, available: 40), 60)
    }
    func testAllowanceDatesAndUnavailableFeed() throws {
        XCTAssertNotNil(UsagePresentation.date("2026-10-22T19:21:07.464940Z"))
        XCTAssertNotNil(UsagePresentation.date("2026-10-22T19:21:07Z"))
        XCTAssertEqual(UsagePresentation.fraction(120), 1)
        let data = Data(#"{"sequence":null,"sample":null,"collection":{"availability":"unavailable","reason":"Not collected","cadence_seconds":15}}"#.utf8)
        let latest = try APIClient.decoder().decode(VPSLatest.self, from: data)
        XCTAssertTrue(VPSPresentation.stale(latest, now: 0))
    }
    // Gap: read-only settings checks do not prove preference writes or confirmed-value recovery.
    // One in-memory transport also holds a write to prove overlapping edits cannot race.
    @MainActor func testNotificationPreferenceSaveRetainsConfirmedValueAndRetries() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [NotificationPreferenceStub.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "https://preferences.invalid")!, session: session)
        let resources = ResourceOwner(api: client)
        defer { resources.stop() }
        let handle = resources.handle(SettingsResources.notifications)
        handle.accept(try APIClient.decoder().decode(SettingsNotifications.self, from: Data(notificationPreferencesJSON.utf8)))
        let editor = NotificationPreferenceEditor()
        NotificationPreferenceStub.state.reset()
        await editor.save(subjectKey: "chief", type: "message", enabled: false, using: client, resources: resources)
        XCTAssertEqual(handle.value?.subjects.first?.types.first?.enabled, true)
        XCTAssertNotNil(editor.error)
        XCTAssertFalse(editor.isSaving)

        let started = expectation(description: "Retry reaches held transport")
        NotificationPreferenceStub.state.hold(started)
        let task = Task { await editor.retry(using: client, resources: resources) }
        await fulfillment(of: [started], timeout: 2)
        XCTAssertTrue(editor.isSaving)
        await editor.save(subjectKey: "chief", type: "message", enabled: true, using: client, resources: resources)
        XCTAssertEqual(NotificationPreferenceStub.state.count, 2, "A second edit cannot overwrite the in-flight catalogue")
        NotificationPreferenceStub.state.finish()
        await task.value
        XCTAssertNil(editor.error)
        XCTAssertFalse(editor.isSaving)
        XCTAssertEqual(handle.value?.subjects.first?.types.first?.enabled, false)
        XCTAssertEqual(handle.value?.subjects.first?.types.last?.enabled, false, "The complete returned catalogue is authoritative")
    }

    func testServerAddressRequiresAnExplicitSavedOrigin() throws {
        let suite = "PanelsTests.ServerAddress." + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        XCTAssertNil(SettingsServerAddress.saved(in: defaults))

        defaults.set("http://other.example", forKey: "panels.baseURL")
        XCTAssertNil(SettingsServerAddress.saved(in: defaults))

        let previous = try XCTUnwrap(URL(string: "https://my-home.example:9443/"))
        defaults.set(previous.absoluteString, forKey: "panels.baseURL")
        XCTAssertEqual(SettingsServerAddress.saved(in: defaults), previous)

        let next = try XCTUnwrap(URL(string: "https://new-home.example"))
        SettingsServerAddress.save(next, in: defaults)
        XCTAssertEqual(SettingsServerAddress.saved(in: defaults), next)
    }

    func testServerAddressRejectsCredentialAndPathInjection() {
        XCTAssertEqual(SettingsServerAddress.validated(" https://panels.example.com/ ")?.host, "panels.example.com")
        for address in ["http://panels.example.com", "https://user:secret@panels.example.com", "https://panels.example.com/api", "https://panels.example.com?secret=x", "https://panels.example.com#fragment", "https://"] {
            XCTAssertNil(SettingsServerAddress.validated(address), address)
        }
    }
}

private final class UsageRefreshStub: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertNil(request.url?.query)
        let body: String
        switch request.url!.path {
        case "/api/conversation/backends/refresh":
            body = #"{"backends":[{"backend_key":"codex","installed":true,"version":null,"cached_usage":{"observed_at":"2026-10-22T19:21:07Z","windows":[]},"diagnoses":[]}],"usage_outcomes":[{"backend_key":"codex","outcome":"succeeded","detail":null},{"backend_key":"claude","outcome":"failed","detail":"Claude read failed; previous limits retained."}]}"#
        case "/api/conversation/usage/rates/refresh": body = #"{"sources":[]}"#
        default: XCTFail("Unexpected refresh path"); body = "{}"
        }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

// Current backend cards: sign-in and usage flags, option descriptors and the server's model name.
private let currentBackendsJSON = #"{"backends":[{"backend_key":"plain","installed":true,"can_sign_in":false,"reports_allowance_usage":false,"available_models":[{"model_id":"plain-model","display_name":"Plain","enabled":true,"reasoning_effort_options":null,"option_descriptors":[],"resolved_display_name":null}],"reasoning_effort_options":[],"default_model_id":"vps-model"},{"backend_key":"reasoning","installed":true,"can_sign_in":true,"reports_allowance_usage":true,"available_models":[{"model_id":"alias","display_name":"Alias","resolved_model_id":"family-4-7-20260101","selected_variant":"alias","enabled":true,"reasoning_effort_options":["low","high"],"option_descriptors":[{"type":"select","id":"reasoning_effort","label":"Reasoning","role":"reasoning","options":[{"id":"low","label":"low"},{"id":"high","label":"high"}]}],"resolved_display_name":"Family 4.7"}],"reasoning_effort_options":["low","high"],"default_model_id":"alias","default_reasoning_effort":"high"}]}"#

// Current server-shaped data; retains unpriced and partial-source evidence.
private let currentUsageJSON = #"{"contractVersion":5,"readAt":"2026-10-26T00:30:00Z","timeZone":"Europe/London","sinceDay":"2026-10-20","untilDay":"2026-10-26","buckets":[{"day":"2026-10-26","provider":"codex","model":"test","totals":{"uncachedInputTokens":10,"cachedInputTokens":20,"cacheCreationTokens":5,"outputTokens":10,"reasoningTokens":4},"costUsd":1.5,"costSource":"modelPriced","records":2,"unpricedRecords":1}],"sources":[{"fingerprint":{"hostId":"vps","provider":"codex","resolvedHomePath":"/example","volumeId":"one"},"status":"partial","message":"Read in progress"}],"pricing":{"status":"cached","source":"rates","fetchedAt":null},"scanDurationMs":1}"#

private final class MachineContractStub: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "machine.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let body: String
        switch request.url!.path {
        case "/api/conversation/backends":
            let remote = request.httpMethod == "POST"
            if remote {
                let data = request.httpBody ?? request.httpBodyStream.map { stream in
                    stream.open(); defer { stream.close() }
                    var bytes = [UInt8](repeating: 0, count: 2048)
                    let count = stream.read(&bytes, maxLength: bytes.count)
                    return Data(bytes.prefix(max(0, count)))
                } ?? Data()
                let value = try? JSONSerialization.jsonObject(with: data) as? [String: String]
                XCTAssertEqual(value, ["ticket_id": "t_mac"])
            } else { XCTAssertEqual(request.httpMethod, "GET") }
            body = remote ? #"{"backends":[{"backend_key":"codex","installed":true,"available_models":[],"reasoning_effort_options":[],"default_model_id":"mac-model"}]}"# : currentBackendsJSON
        case "/api/conversation/usage/summary":
            XCTAssertEqual(request.httpMethod, "GET")
            let items = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
            XCTAssertEqual(Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value!) }),
                ["sinceDay":"2026-10-20", "untilDay":"2026-10-26", "timeZone":"Europe/London", "resolution":"day"])
            body = currentUsageJSON
        default: XCTFail("Unexpected contract request"); body = "{}"
        }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8)); client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private let notificationPreferencesJSON = #"{"subjects":[{"key":"chief","label":"Chief","types":[{"id":"message","label":"Messages","description":"New messages","enabled":true},{"id":"stalled","label":"Stalled work","description":"Work stops","enabled":true}]}]}"#

private final class NotificationPreferenceStub: URLProtocol, @unchecked Sendable {
    final class State: @unchecked Sendable {
        private let lock = NSLock()
        private var requests = 0
        private var held: NotificationPreferenceStub?
        private var started: XCTestExpectation?
        var count: Int { lock.lock(); defer { lock.unlock() }; return requests }
        func reset() { lock.lock(); defer { lock.unlock() }; requests = 0; held = nil; started = nil }
        func hold(_ expectation: XCTestExpectation) { lock.lock(); defer { lock.unlock() }; started = expectation }
        func accept(_ stub: NotificationPreferenceStub) {
            lock.lock(); requests += 1
            if requests == 1 { lock.unlock(); stub.respond(status: 503); return }
            held = stub; let expectation = started; lock.unlock()
            expectation?.fulfill()
        }
        func finish() { lock.lock(); let stub = held; held = nil; lock.unlock(); stub?.respond(status: 200) }
    }
    static let state = State()
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "preferences.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        XCTAssertEqual(request.httpMethod, "PUT")
        XCTAssertEqual(request.url?.path, "/api/notifications/preferences/chief/message")
        let data = request.httpBody ?? request.httpBodyStream.map { stream in
            stream.open(); defer { stream.close() }
            var bytes = [UInt8](repeating: 0, count: 1024)
            let count = stream.read(&bytes, maxLength: bytes.count)
            return Data(bytes.prefix(max(0, count)))
        } ?? Data()
        let body = try? JSONSerialization.jsonObject(with: data) as? [String: Bool]
        XCTAssertEqual(body, ["enabled": false])
        Self.state.accept(self)
    }
    func respond(status: Int) {
        let body = status == 200 ? notificationPreferencesJSON.replacingOccurrences(of: "true", with: "false")
            : #"{"error":{"message":"Preference service unavailable"}}"#
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8)); client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private let driverCatalogJSON = ##"{"drivers":[{"driver_kind":"picture","label":"Picture","icon":{"url":"/api/drivers/picture/icon?v=000000000000","media_type":"image/png","monochrome":false},"accent_color":"#123456","checks_for_updates":false},{"driver_kind":"glyph","label":"Glyph Agent","icon":{"url":"/api/drivers/glyph/icon?v=0123456789ab","media_type":"image/png","monochrome":true},"accent_color":"#abcdef","checks_for_updates":true},{"driver_kind":"photo","label":"Photo","icon":{"url":"/api/drivers/photo/icon?v=ba9876543210","media_type":"image/png","monochrome":false},"accent_color":"#000000","checks_for_updates":false},{"driver_kind":"vector","label":"Vector","icon":{"url":"/api/drivers/vector/icon?v=111111111111","media_type":"image/svg+xml","monochrome":true},"accent_color":"#ffffff","checks_for_updates":true},{"driver_kind":"elsewhere","label":"Elsewhere","icon":{"url":"//other.invalid/icon.png","media_type":"image/png","monochrome":true},"accent_color":"#ffffff","checks_for_updates":false}],"default_backend_instance_id":"picture"}"##
