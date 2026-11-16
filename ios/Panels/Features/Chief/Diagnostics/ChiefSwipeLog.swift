import Foundation
import Observation

/// One local owner for the preference, bounded records and shareable snapshot.
@MainActor @Observable final class ChiefSwipeLog {
    static let shared = ChiefSwipeLog()
    static let preferenceKey = "panels.chief-swipe-diagnostics"

    var isEnabled: Bool {
        didSet {
            guard isEnabled != oldValue else { return }
            defaults.set(isEnabled, forKey: Self.preferenceKey)
            if !isEnabled { generation += 1 }
        }
    }
    private(set) var error: String?
    private(set) var recordCount = 0
    private(set) var generation = 0

    private let directory: URL
    private let defaults: UserDefaults
    @ObservationIgnored private var records: [ChiefSwipeRecord] = []
    private var savedURL: URL { directory.appendingPathComponent("chief-swipes.jsonl") }
    private var exportURL: URL { directory.appendingPathComponent("ChiefSwipeLog.jsonl") }

    init(directory: URL? = nil, defaults: UserDefaults = .standard) {
        self.directory = directory ?? LocalStore.directory.appendingPathComponent("ChiefSwipeDiagnostics", isDirectory: true)
        self.defaults = defaults
        isEnabled = defaults.bool(forKey: Self.preferenceKey)
        do {
            if FileManager.default.fileExists(atPath: savedURL.path) {
                let handle = try FileHandle(forReadingFrom: savedURL)
                defer { try? handle.close() }
                let data = try handle.read(upToCount: ChiefSwipeLimits.maximumBytes + 1) ?? Data()
                if data.count <= ChiefSwipeLimits.maximumBytes {
                    records = data.split(separator: 10).compactMap {
                        try? JSONDecoder().decode(ChiefSwipeRecord.self, from: Data($0))
                    }
                }
            }
            refresh()
        } catch {
            self.error = "Could not read the swipe log."
        }
    }

    /// Both the saved log and a gesture's pending buffer use the same bounds.
    static func bounded(_ records: [ChiefSwipeRecord], now: TimeInterval) -> [ChiefSwipeRecord] {
        let recent = records.enumerated().filter {
            $0.element.timestamp.isFinite && $0.element.timestamp >= now - ChiefSwipeLimits.maximumAge
                && $0.element.timestamp <= now
        }.sorted {
            if $0.element.timestamp == $1.element.timestamp { return $0.offset < $1.offset }
            return $0.element.timestamp < $1.element.timestamp
        }
        var retained: [ChiefSwipeRecord] = []
        var bytes = 0
        for (_, record) in recent.reversed() {
            guard let encoded = try? encode(record), encoded.count + 1 <= ChiefSwipeLimits.maximumBytes else { continue }
            guard bytes + encoded.count + 1 <= ChiefSwipeLimits.maximumBytes else { break }
            bytes += encoded.count + 1
            retained.append(record)
        }
        return Array(retained.reversed())
    }

    func append(_ newRecords: [ChiefSwipeRecord], generation token: Int) {
        guard isEnabled, token == generation else { return }
        records = Self.bounded(records + newRecords, now: Date().timeIntervalSince1970)
        recordCount = records.count
        do {
            try save()
            error = nil
        } catch {
            self.error = "Could not save the swipe log."
        }
    }

    /// Reapply retention before the Settings view exposes the retained count.
    func refresh() {
        records = Self.bounded(records, now: Date().timeIntervalSince1970)
        recordCount = records.count
        do {
            try save()
            try removeIfPresent(exportURL)
            error = nil
        } catch {
            self.error = "Could not update the swipe log."
        }
    }

    func export() throws -> URL {
        records = Self.bounded(records, now: Date().timeIntervalSince1970)
        recordCount = records.count
        do {
            try save()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try Self.encode(records).write(to: exportURL, options: .atomic)
            error = nil
            return exportURL
        } catch {
            self.error = "Could not share the swipe log."
            throw error
        }
    }

    func clear() throws {
        generation += 1
        records = []
        recordCount = 0
        do {
            try removeIfPresent(savedURL)
            try removeIfPresent(exportURL)
            error = nil
        } catch {
            self.error = "Could not clear the swipe log."
            throw error
        }
    }

    private func save() throws {
        guard !records.isEmpty else {
            try removeIfPresent(savedURL)
            return
        }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try Self.encode(records).write(to: savedURL, options: .atomic)
    }

    private func removeIfPresent(_ url: URL) throws {
        if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
    }

    private static func encode(_ record: ChiefSwipeRecord) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(record)
    }

    private static func encode(_ records: [ChiefSwipeRecord]) throws -> Data {
        var data = Data()
        for record in records {
            data.append(try encode(record))
            data.append(10)
        }
        return data
    }
}
