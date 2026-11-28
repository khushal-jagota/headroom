import Foundation
import Observation
import SwiftData

@Model final class LocalRecord {
    @Attribute(.unique) var key: String
    var server: String
    var kind: String
    var payload: Data
    // `updated` collides with Core Data's isUpdated KVC getter.
    var updatedAt: Date
    var updated: Date { updatedAt }
    init(key: String, server: String, kind: String, payload: Data, updated: Date) {
        self.key = key; self.server = server; self.kind = kind; self.payload = payload; self.updatedAt = updated
    }
}

/// Cached reads are evictable by count and size. Finished outgoing work is cleared by
/// `Outbox.clearFinished`. Drafts, recordings and unfinished work have no age limit.
@MainActor @Observable final class LocalStore {
    static let shared = LocalStore()
    nonisolated static let cacheLimit = 96
    nonisolated static let cacheByteLimit = 16 * 1024 * 1024
    private var container: ModelContainer?
    private var cacheWriter: ResponseCacheWriter?
    private var outgoingStorage: OutgoingStore?
    private var startupError: Error?
    @ObservationIgnored private var videoStartupSweep: Task<Void, Never>?
    private let beforeSave: (() throws -> Void)?
    private let onPayloadDecode: (() -> Void)?
    // Cached GET responses are handed to their screen by the caller. Only durable
    // user work needs to wake draft restoration and outgoing-work observers.
    private(set) var workRevision = 0
    private var outgoingRevisions: [String: Int] = [:]
    func outgoingRevision(_ server: String) -> Int { outgoingRevisions[server, default: 0] }
    var saveError: String?
    private(set) var outgoingError: String?
    private(set) var cacheErrors: [String: String] = [:]
    private var cacheWriteGenerations: [String: Int] = [:]

    init(url: URL? = nil, cacheWrite: (@Sendable () throws -> Void)? = nil, beforeSave: (() throws -> Void)? = nil, onPayloadDecode: (() -> Void)? = nil) {
        self.beforeSave = beforeSave
        self.onPayloadDecode = onPayloadDecode
        do {
            if url == nil { Self.removeRetiredStore() }
            let location = url ?? Self.directory.appendingPathComponent("phone.store")
            try FileManager.default.createDirectory(at: location.deletingLastPathComponent(), withIntermediateDirectories: true)
            container = try ModelContainer(for: Schema([LocalRecord.self, OutgoingMetadata.self]), configurations: [ModelConfiguration(url: location)])
            container?.mainContext.autosaveEnabled = false
            if let container { cacheWriter = ResponseCacheWriter(container: container, beforeWrite: cacheWrite) }
        } catch { container = nil; startupError = error; saveError = "Couldn’t open saved work: \(error.localizedDescription)" }
        // Cleanup is optional and must never make an already-open store unavailable.
        if url == nil, let container {
            let directory = Self.directory
            videoStartupSweep = Task.detached(priority: .utility) {
                try? Self.sweepVideoFiles(in: directory, container: container)
            }
        }
    }
    /// Imports wait for this launch-only sweep, so an in-flight new copy cannot be swept.
    func waitForVideoStartupSweep() async { await videoStartupSweep?.value }

    func sweepUnownedVideoFiles(in directory: URL) async throws {
        guard let container else { throw startupError ?? CocoaError(.fileReadUnknown) }
        try await Task.detached(priority: .utility) {
            try Self.sweepVideoFiles(in: directory, container: container)
        }.value
    }
    nonisolated private static func sweepVideoFiles(in directory: URL, container: ModelContainer) throws {
        let videos = directory.appendingPathComponent("videos", isDirectory: true)
        guard FileManager.default.fileExists(atPath: videos.path) else { return }
        let context = ModelContext(container)
        context.autosaveEnabled = false
        let payloads = try OutgoingStore.videoOwnershipPayloads(in: context)
        // An unreadable record may own any file. Preserve everything until it is readable.
        guard let owned = OutgoingStore.ownedVideoFiles(payloads) else { return }
        for file in try FileManager.default.contentsOfDirectory(at: videos, includingPropertiesForKeys: nil) {
            if !owned.contains("videos/" + file.lastPathComponent) {
                try FileManager.default.removeItem(at: file)
            }
        }
    }
    nonisolated static var directory: URL {
        if let path = ProcessInfo.processInfo.environment["PANELS_STORE_DIRECTORY"] { return URL(fileURLWithPath: path) }
        return URL.applicationSupportDirectory.appendingPathComponent("PanelsPhoneStore", isDirectory: true)
    }
    /// Earlier builds kept a migrated store and its recordings here. This build starts
    /// fresh: their drafts, unsent work and recordings are dropped once.
    nonisolated static let retiredDirectory = URL.applicationSupportDirectory.appendingPathComponent("PanelsPhone", isDirectory: true)
    private static func removeRetiredStore() {
        guard ProcessInfo.processInfo.environment["PANELS_STORE_DIRECTORY"] == nil,
              FileManager.default.fileExists(atPath: retiredDirectory.path) else { return }
        try? FileManager.default.removeItem(at: retiredDirectory)
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
    var outgoing: OutgoingStore {
        get throws {
            if let outgoingStorage { return outgoingStorage }
            let storage = OutgoingStore(context: try context, beforeSave: beforeSave, onPayloadDecode: onPayloadDecode) { [weak self] server in
                self?.outgoingRevisions[server, default: 0] += 1
                self?.workRevision += 1
                guard let self else { return }
                // Successful durable work clears a transient outgoing fault.
                if self.saveError == self.outgoingError { self.saveError = nil }
                self.outgoingError = nil
            } failed: { [weak self] error in
                self?.outgoingError = "Couldn’t save outgoing work: \(error.localizedDescription)"
                self?.saveError = self?.outgoingError
            }
            outgoingStorage = storage
            return storage
        }
    }
    func records(server: String, kind: String) throws -> [LocalRecord] {
        _ = workRevision
        return try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.server == server && $0.kind == kind }, sortBy: [SortDescriptor(\.updatedAt)]))
    }
    /// Every server's rows of one kind.
    func records(kind: String) throws -> [LocalRecord] {
        try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.kind == kind }))
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
        cacheWriteGenerations[server, default: 0] += 1
        let generation = cacheWriteGenerations[server]
        do {
            try await cacheWriter.put(server: server, id: id, data: data, updated: updated)
            if cacheWriteGenerations[server] == generation { cacheErrors[server] = nil }
        } catch {
            if cacheWriteGenerations[server] == generation { cacheErrors[server] = "Couldn’t cache this response: \(error.localizedDescription)" }
            throw error
        }
    }
    /// Admission is synchronous and bounded. A cache save never delays live content.
    func enqueueCacheResponse(server: String, id: String, data: Data, updated: Date = .now) {
        guard let cacheWriter else {
            cacheErrors[server] = "Couldn’t open the response cache."
            return
        }
        cacheWriteGenerations[server, default: 0] += 1
        let generation = cacheWriteGenerations[server]
        cacheWriter.enqueue(server: server, id: id, data: data, updated: updated) { [weak self] error in
            Task { @MainActor in
                guard let self, self.cacheWriteGenerations[server] == generation else { return }
                self.cacheErrors[server] = error.map { "Couldn’t cache this response: \($0)" }
            }
        }
    }
    func cachedResponse(server: String, id: String) async -> Data? { try? await cacheWriter?.read(server: server, id: id) }
    func flushCache() async { await cacheWriter?.flush() }

    func put(server: String, kind: String, id: String, data: Data, draft: (String, String)? = nil) throws {
        precondition(kind != "outbox", "Outgoing work is saved through LocalStore.outgoing.")
        guard kind != "cache" || data.count <= Self.cacheByteLimit else { return }
        let context = try context
        do {
            let key = Self.key(server, kind, id)
            let existing = try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.key == key })).first
            if let record = existing { record.payload = data; record.updatedAt = .now }
            else { context.insert(LocalRecord(key: Self.key(server, kind, id), server: server, kind: kind, payload: data, updated: .now)) }
            if let (id, text) = draft {
                if let row = try record(server: server, kind: "draft", id: id) { row.payload = Data(text.utf8); row.updatedAt = .now }
                else { context.insert(LocalRecord(key: Self.key(server, "draft", id), server: server, kind: "draft", payload: Data(text.utf8), updated: .now)) }
            }
            if kind == "cache" {
                let rows = try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.kind == "cache" }, sortBy: [SortDescriptor(\.updatedAt, order: .reverse)]))
                var bytes = 0
                for (index, row) in rows.enumerated() {
                    bytes += row.payload.count
                    if index >= Self.cacheLimit || bytes > Self.cacheByteLimit { context.delete(row) }
                }
            }
            try context.save()
            // A plain draft save is read back only by its own composer. Waking every
            // saved-work observer on each key press made typing lag.
            if (kind != "cache" && kind != "draft") || draft != nil { workRevision += 1 }
            saveError = nil
        } catch { context.rollback(); saveError = "Couldn’t save on this phone: \(error.localizedDescription)"; throw error }
    }
    func remove(server: String, kind: String, id: String) throws {
        try remove(server: server, kind: kind, ids: [id])
    }
    /// One save and one observer wake for the whole batch.
    func remove(server: String, kind: String, ids: [String]) throws {
        guard !ids.isEmpty else { return }
        if kind == "outbox" { try outgoing.remove(server: server, ids: ids); return }
        let context = try context
        do {
            let keys = ids.map { Self.key(server, kind, $0) }
            let rows = try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { keys.contains($0.key) }))
            for row in rows { context.delete(row) }
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
