import XCTest
import UIKit
@testable import Panels

final class ChiefSwipeLogTests: XCTestCase {
    @MainActor func testTouchObserverCannotPreventOrDelayExistingGestures() {
        let observer = ChiefSwipeTouchObserver()
        let existing = UIPanGestureRecognizer()
        XCTAssertFalse(observer.cancelsTouchesInView)
        XCTAssertFalse(observer.delaysTouchesBegan)
        XCTAssertFalse(observer.delaysTouchesEnded)
        XCTAssertFalse(observer.canPrevent(existing))
        XCTAssertFalse(observer.canBePrevented(by: existing))
        XCTAssertEqual(observer.state, .possible)
    }

    @MainActor func testDefaultOffAndDisableRejectPendingCapture() throws {
        try withLog { log, directory, defaults in
            let record = swipeRecord()
            let firstGeneration = log.generation
            XCTAssertFalse(log.isEnabled)
            log.append([record], generation: firstGeneration)
            XCTAssertEqual(log.recordCount, 0)
            XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appendingPathComponent("chief-swipes.jsonl").path))

            log.isEnabled = true
            log.append([record], generation: firstGeneration)
            XCTAssertEqual(log.recordCount, 1)
            log.isEnabled = false
            XCTAssertGreaterThan(log.generation, firstGeneration)
            log.append([record], generation: firstGeneration)
            XCTAssertEqual(log.recordCount, 1)
            log.isEnabled = true
            log.append([record], generation: firstGeneration)
            XCTAssertEqual(log.recordCount, 1, "A capture from before disable must not return after re-enable")
            log.append([record], generation: log.generation)
            XCTAssertEqual(log.recordCount, 2)
            XCTAssertTrue(ChiefSwipeLog(directory: directory, defaults: defaults).isEnabled)
        }
    }

    @MainActor func testByteCapEvictsOldestAndRejectsOversizeRecord() throws {
        try withLog { log, _, _ in
            log.isEnabled = true
            let now = Date().timeIntervalSince1970 - 10
            let records = (1...6).map {
                swipeRecord(timestamp: now + Double($0), session: $0, className: String(repeating: "X", count: 200_000))
            }
            log.append(records, generation: log.generation)
            let data = try Data(contentsOf: log.export())
            XCTAssertLessThanOrEqual(data.count, ChiefSwipeLimits.maximumBytes)
            let retained = try decodeRecords(data)
            XCTAssertLessThan(retained.count, records.count)
            XCTAssertEqual(retained.last?.session, 6)
            XCTAssertFalse(retained.contains { $0.session == 1 }, "The oldest records leave first")

            let count = log.recordCount
            log.append([swipeRecord(className: String(repeating: "X", count: ChiefSwipeLimits.maximumBytes + 1))], generation: log.generation)
            XCTAssertEqual(log.recordCount, count, "One oversize record cannot defeat the byte bound")
            XCTAssertLessThanOrEqual(try Data(contentsOf: log.export()).count, ChiefSwipeLimits.maximumBytes)
        }
    }

    @MainActor func testAgeCapAppliesToPendingBufferSavedLogAndExport() throws {
        let now: TimeInterval = 100_000
        let bounded = ChiefSwipeLog.bounded([
            swipeRecord(timestamp: now - ChiefSwipeLimits.maximumAge - 1, session: 1),
            swipeRecord(timestamp: now - ChiefSwipeLimits.maximumAge, session: 2),
            swipeRecord(timestamp: now - 1, session: 3)
        ], now: now)
        XCTAssertEqual(bounded.map(\.session), [2, 3])

        try withLog { _, directory, defaults in
            let current = Date().timeIntervalSince1970
            try writeRecords([
                swipeRecord(timestamp: current - ChiefSwipeLimits.maximumAge - 10, session: 1),
                swipeRecord(timestamp: current - 10, session: 2)
            ], directory: directory)
            let restored = ChiefSwipeLog(directory: directory, defaults: defaults)
            XCTAssertEqual(restored.recordCount, 1)
            XCTAssertEqual(try decodeRecords(Data(contentsOf: restored.export())).map(\.session), [2])
            XCTAssertEqual(try decodeRecords(Data(contentsOf: directory.appendingPathComponent("chief-swipes.jsonl"))).map(\.session), [2])
        }
    }

    @MainActor func testTypedExportExcludesUnknownPageAndConversationFields() throws {
        try withLog { _, directory, defaults in
            let marker = "private-page-and-conversation-content"
            var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(swipeRecord())) as? [String: Any])
            object["pageText"] = marker
            object["conversation"] = ["messages": [marker]]
            var recognizers = try XCTUnwrap(object["recognizers"] as? [[String: Any]])
            recognizers[0]["accessibilityLabel"] = marker
            object["recognizers"] = recognizers
            var data = try JSONSerialization.data(withJSONObject: object)
            data.append(10)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try data.write(to: directory.appendingPathComponent("chief-swipes.jsonl"))

            let restored = ChiefSwipeLog(directory: directory, defaults: defaults)
            let exported = try Data(contentsOf: restored.export())
            let text = try XCTUnwrap(String(data: exported, encoding: .utf8))
            XCTAssertFalse(text.contains(marker))
            XCTAssertFalse(text.contains("pageText"))
            XCTAssertFalse(text.contains("accessibilityLabel"))
            XCTAssertEqual(try decodeRecords(exported).first?.recognizers.first?.className, "UIPanGestureRecognizer")
        }
    }

    @MainActor func testClearRemovesSavedAndExportedRecordsAndRejectsPendingCapture() throws {
        try withLog { log, directory, defaults in
            log.isEnabled = true
            let generation = log.generation
            log.append([swipeRecord()], generation: generation)
            let exported = try log.export()
            XCTAssertTrue(FileManager.default.fileExists(atPath: exported.path))
            try log.clear()
            XCTAssertEqual(log.recordCount, 0)
            XCTAssertTrue(log.isEnabled, "Clear does not change the user's switch")
            XCTAssertFalse(FileManager.default.fileExists(atPath: exported.path))
            XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appendingPathComponent("chief-swipes.jsonl").path))
            log.append([swipeRecord()], generation: generation)
            XCTAssertEqual(log.recordCount, 0, "A delayed transition cannot repopulate a cleared log")
            XCTAssertEqual(ChiefSwipeLog(directory: directory, defaults: defaults).recordCount, 0)
        }
    }

    @MainActor private func withLog(_ body: (ChiefSwipeLog, URL, UserDefaults) throws -> Void) throws {
        let name = "ChiefSwipeLogTests-" + UUID().uuidString
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(name, isDirectory: true)
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer {
            defaults.removePersistentDomain(forName: name)
            try? FileManager.default.removeItem(at: directory)
        }
        try body(ChiefSwipeLog(directory: directory, defaults: defaults), directory, defaults)
    }

    private func writeRecords(_ records: [ChiefSwipeRecord], directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var data = Data()
        for record in records {
            data.append(try JSONEncoder().encode(record))
            data.append(10)
        }
        try data.write(to: directory.appendingPathComponent("chief-swipes.jsonl"))
    }

    private func decodeRecords(_ data: Data) throws -> [ChiefSwipeRecord] {
        try data.split(separator: 10).map { try JSONDecoder().decode(ChiefSwipeRecord.self, from: Data($0)) }
    }

    private func swipeRecord(timestamp: TimeInterval = Date().timeIntervalSince1970 - 1, session: Int = 1,
                             className: String = "UIPanGestureRecognizer") -> ChiefSwipeRecord {
        ChiefSwipeRecord(timestamp: timestamp, session: session, phase: .sample,
                         displacement: ChiefSwipePoint(x: 50, y: 2), views: [],
                         recognizers: [ChiefSwipeRecognizerSnapshot(className: className, viewIndex: nil,
                                                                   role: .scroll, state: .changed, isEnabled: true)],
                         navigation: ChiefSwipeNavigationSnapshot(stackCount: 2, transitionInProgress: false,
                                                                  isInteractive: false, percentComplete: 0, wasCancelled: nil),
                         appVersion: "0.1", appBuild: "5", systemVersion: "18.0")
    }
}
