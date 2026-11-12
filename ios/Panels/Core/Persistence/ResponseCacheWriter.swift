import Foundation
import SwiftData

/// Replaceable cache work is bounded before it enters the actor mailbox. User work
/// never enters this queue. One drain serializes writes with a separate context.
actor ResponseCacheWriter {
    struct Write: Sendable {
        let server: String
        let id: String
        let data: Data
        let updated: Date
        var completion: (@Sendable (String?) -> Void)? = nil
        var key: String { LocalStore.key(server, "cache", id) }
    }

    /// A small lock protects admission only; it never covers disk I/O.
    final class Pending: @unchecked Sendable {
        private let lock = NSLock()
        private var writes: [String: Write] = [:]
        private var order: [String] = []
        private var bytes = 0
        private var draining = false
        let entryLimit: Int
        let byteLimit: Int

        init(entryLimit: Int = LocalStore.cacheLimit, byteLimit: Int = LocalStore.cacheByteLimit) {
            self.entryLimit = entryLimit; self.byteLimit = byteLimit
        }
        /// Returns true only for the producer that must start the drain.
        func append(_ write: Write) -> Bool {
            lock.lock(); defer { lock.unlock() }
            guard write.data.count <= byteLimit else { return false }
            if let old = writes.removeValue(forKey: write.key) { bytes -= old.data.count }
            order.removeAll { $0 == write.key }
            while !order.isEmpty && (order.count >= entryLimit || bytes + write.data.count > byteLimit) {
                let key = order.removeFirst()
                bytes -= writes.removeValue(forKey: key)?.data.count ?? 0
            }
            guard entryLimit > 0 else { return false }
            writes[write.key] = write; order.append(write.key); bytes += write.data.count
            guard !draining else { return false }
            draining = true
            return true
        }
        func next() -> Write? {
            lock.lock(); defer { lock.unlock() }
            guard !order.isEmpty else { draining = false; return nil }
            let key = order.removeFirst()
            let write = writes.removeValue(forKey: key)
            bytes -= write?.data.count ?? 0
            return write
        }
        var count: Int { lock.lock(); defer { lock.unlock() }; return writes.count }
        var byteCount: Int { lock.lock(); defer { lock.unlock() }; return bytes }
    }

    private let container: ModelContainer
    private let beforeWrite: (@Sendable () throws -> Void)?
    private var context: ModelContext?
    nonisolated let pending = Pending()

    init(container: ModelContainer, beforeWrite: (@Sendable () throws -> Void)? = nil) {
        self.container = container; self.beforeWrite = beforeWrite
    }

    nonisolated func enqueue(server: String, id: String, data: Data, updated: Date,
                             completion: @escaping @Sendable (String?) -> Void) {
        guard pending.append(Write(server: server, id: id, data: data, updated: updated, completion: completion)) else { return }
        Task { await drain() }
    }

    /// A barrier for explicit background checkpoints and deterministic disk readback.
    func flush() { drain() }

    private func drain() {
        while let write = pending.next() {
            do {
                try put(server: write.server, id: write.id, data: write.data, updated: write.updated)
                write.completion?(nil)
            } catch { write.completion?(error.localizedDescription) }
        }
    }

    func put(server: String, id: String, data: Data, updated: Date) throws {
        guard data.count <= LocalStore.cacheByteLimit else { return }
        try beforeWrite?()
        let context: ModelContext
        if let existing = self.context { context = existing }
        else {
            context = ModelContext(container)
            context.autosaveEnabled = false
            self.context = context
        }
        let key = LocalStore.key(server, "cache", id)
        do {
            let record = try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.key == key })).first
            if let record {
                if record.payload != data { record.payload = data }
                record.updatedAt = updated
            } else {
                context.insert(LocalRecord(key: key, server: server, kind: "cache", payload: data, updated: updated))
            }
            let rows = try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.kind == "cache" }, sortBy: [SortDescriptor(\.updatedAt, order: .reverse)]))
            var bytes = 0
            for (index, row) in rows.enumerated() {
                bytes += row.payload.count
                if index >= LocalStore.cacheLimit || bytes > LocalStore.cacheByteLimit { context.delete(row) }
            }
            try context.save()
        } catch { context.rollback(); throw error }
    }
}
