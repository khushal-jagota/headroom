import Foundation
import Observation
import SwiftData

@Model final class LocalRecord {
    @Attribute(.unique) var key: String
    var server: String
    var kind: String
    var payload: Data
    var updated: Date
    init(key: String, server: String, kind: String, payload: Data, updated: Date) {
        self.key = key; self.server = server; self.kind = kind; self.payload = payload; self.updated = updated
    }
}

/// Only cached reads are evictable. Drafts, recordings and outgoing work have no age limit.
@MainActor @Observable final class LocalStore {
    static let shared = LocalStore()
    static let cacheLimit = 96
    static let cacheByteLimit = 16 * 1024 * 1024
    private var container: ModelContainer?
    private var startupError: Error?
    // Cached GET responses are handed to their screen by the caller. Only durable
    // user work needs to wake draft restoration and outgoing-work observers.
    private(set) var workRevision = 0
    var saveError: String?

    init(url: URL? = nil) {
        do {
            let location = url ?? Self.directory.appendingPathComponent("phone.store")
            try FileManager.default.createDirectory(at: location.deletingLastPathComponent(), withIntermediateDirectories: true)
            container = try ModelContainer(for: LocalRecord.self, configurations: ModelConfiguration(url: location))
            container?.mainContext.autosaveEnabled = false
        } catch { startupError = error; saveError = "Couldn’t open saved work: \(error.localizedDescription)" }
    }
    static var directory: URL {
        if let path = ProcessInfo.processInfo.environment["PANELS_STORE_DIRECTORY"] { return URL(fileURLWithPath: path) }
        return URL.applicationSupportDirectory.appendingPathComponent("PanelsPhone", isDirectory: true)
    }
    static func server(_ url: URL) -> String {
        var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        parts.scheme = parts.scheme?.lowercased(); parts.host = parts.host?.lowercased()
        parts.path = ""; parts.query = nil; parts.fragment = nil
        if (parts.scheme == "https" && parts.port == 443) || (parts.scheme == "http" && parts.port == 80) { parts.port = nil }
        return parts.string!
    }
    private var context: ModelContext {
        get throws {
            guard let container else { throw startupError ?? CocoaError(.fileWriteUnknown) }
            return container.mainContext
        }
    }
    func records(server: String, kind: String) throws -> [LocalRecord] {
        _ = workRevision
        return try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.server == server && $0.kind == kind }, sortBy: [SortDescriptor(\.updated)]))
    }
    func record(server: String, kind: String, id: String) throws -> LocalRecord? {
        let key = Self.key(server, kind, id)
        return try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.key == key })).first
    }
    private static func key(_ server: String, _ kind: String, _ id: String) -> String { [server, kind, id].joined(separator: "\u{1f}") }
    func put(server: String, kind: String, id: String, data: Data, draft: (String, String)? = nil) throws {
        guard kind != "cache" || data.count <= Self.cacheByteLimit else { return }
        let context = try context
        do {
            if let record = try record(server: server, kind: kind, id: id) { record.payload = data; record.updated = .now }
            else { context.insert(LocalRecord(key: Self.key(server, kind, id), server: server, kind: kind, payload: data, updated: .now)) }
            if let (id, text) = draft {
                if let row = try record(server: server, kind: "draft", id: id) { row.payload = Data(text.utf8); row.updated = .now }
                else { context.insert(LocalRecord(key: Self.key(server, "draft", id), server: server, kind: "draft", payload: Data(text.utf8), updated: .now)) }
            }
            if kind == "cache" {
                let rows = try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.kind == "cache" }, sortBy: [SortDescriptor(\.updated, order: .reverse)]))
                var bytes = 0
                for (index, row) in rows.enumerated() {
                    bytes += row.payload.count
                    if index >= Self.cacheLimit || bytes > Self.cacheByteLimit { context.delete(row) }
                }
            }
            try context.save()
            if kind != "cache" || draft != nil { workRevision += 1 }
            saveError = nil
        } catch { context.rollback(); saveError = "Couldn’t save on this phone: \(error.localizedDescription)"; throw error }
    }
    func remove(server: String, kind: String, id: String) throws {
        let context = try context
        do {
            if let row = try record(server: server, kind: kind, id: id) { context.delete(row) }
            try context.save()
            if kind != "cache" { workRevision += 1 }
        } catch { context.rollback(); throw error }
    }
    func draft(server: String, id: String) -> String? {
        guard let data = try? record(server: server, kind: "draft", id: id)?.payload else { return nil }
        return String(data: data, encoding: .utf8)
    }
    func saveDraft(_ value: String, server: String, id: String) {
        do { try put(server: server, kind: "draft", id: id, data: Data(value.utf8)) }
        catch { saveError = "Draft could not be saved: \(error.localizedDescription)" }
    }
}
