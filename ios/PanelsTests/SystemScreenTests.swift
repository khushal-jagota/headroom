import XCTest
@testable import Panels

final class SystemScreenTests: XCTestCase {
    func testRecordedUsageKeepsMissingCostsAndPartialSources() throws {
        let data = Data(#"{"days":7,"timezone":"Europe/London","observed_at":null,"totals":{"total_tokens":0,"input_tokens":0,"cached_input_tokens":null,"cache_creation_tokens":null,"output_tokens":0,"cost_usd":null,"estimated_cost_usd":null,"provider_reported_cost_usd":null,"unknown_cost_records":3,"record_count":3},"providers":[],"periods":[],"sources":[{"provider":"codex","state":"partial","issues":["Read in progress"]}]}"#.utf8)
        let summary = try APIClient.decoder().decode(UsageSummary.self, from: data)
        XCTAssertNil(summary.totals.costUsd)
        XCTAssertEqual(summary.totals.totalTokens, 0)
        XCTAssertEqual(summary.sources.first?.state, "partial")
        XCTAssertEqual(UsagePresentation.cost(nil), "—")
        XCTAssertNotEqual(UsagePresentation.number(0), "—")
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
    func testServerAddressRejectsCredentialAndPathInjection() {
        XCTAssertEqual(SettingsServerAddress.validated(" https://panels.example.com/ ")?.host, "panels.example.com")
        for address in ["http://panels.example.com", "https://user:secret@panels.example.com", "https://panels.example.com/api", "https://panels.example.com?secret=x", "https://panels.example.com#fragment", "https://"] {
            XCTAssertNil(SettingsServerAddress.validated(address), address)
        }
    }
}
