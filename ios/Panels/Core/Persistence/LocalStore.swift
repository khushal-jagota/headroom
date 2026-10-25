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
    nonisolated static let cacheLimit = 96
    nonisolated static let cacheByteLimit = 16 * 1024 * 1024
    private var container: ModelContainer?
    private var cacheWriter: ResponseCacheWriter?
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
            if let container { cacheWriter = ResponseCacheWriter(container: container) }
        } catch { startupError = error; saveError = "Couldn’t open saved work: \(error.localizedDescription)" }
    }
    static var directory: URL {
        if let path = ProcessInfo.processInfo.environment["PANELS_STORE_DIRECTORY"] { return URL(fileURLWithPath: path) }
        return URL.applicationSupportDirectory.appendingPathComponent("PanelsPhone", isDirectory: true)
    }
    nonisolated static func server(_ url: URL) -> String {
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
        // The cache writer uses another context. A fresh read must not reuse a
        // previously registered model with an old payload or freshness date.
        let context = kind == "cache" ? try cacheReadContext() : try self.context
        return try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.key == key })).first
    }
    nonisolated static func key(_ server: String, _ kind: String, _ id: String) -> String { [server, kind, id].joined(separator: "\u{1f}") }
    private func cacheReadContext() throws -> ModelContext {
        guard let container else { throw startupError ?? CocoaError(.fileReadUnknown) }
        let context = ModelContext(container)
        context.autosaveEnabled = false
        return context
    }
    func cacheResponse(server: String, id: String, data: Data, updated: Date = .now) async throws {
        guard let cacheWriter else { throw startupError ?? CocoaError(.fileWriteUnknown) }
        do {
            try await cacheWriter.put(server: server, id: id, data: data, updated: updated)
            saveError = nil
        } catch {
            saveError = "Couldn’t save on this phone: \(error.localizedDescription)"
            throw error
        }
    }
    func put(server: String, kind: String, id: String, data: Data, draft: (String, String)? = nil) throws {
        guard kind != "cache" || data.count <= Self.cacheByteLimit else { return }
        let context = try context
        do {
            let key = Self.key(server, kind, id)
            if let record = try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.key == key })).first { record.payload = data; record.updated = .now }
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
            let key = Self.key(server, kind, id)
            if let row = try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.key == key })).first { context.delete(row) }
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
