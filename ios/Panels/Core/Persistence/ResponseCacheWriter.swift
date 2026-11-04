import Foundation
import SwiftData

/// The response cache has one serial writer, separate from the main-actor user-work
/// context. No model objects cross the actor boundary. Every completed GET still
/// saves its freshness timestamp and participates in the same global eviction order.
actor ResponseCacheWriter {
    private let container: ModelContainer
    private var context: ModelContext?

    init(container: ModelContainer) { self.container = container }

    func put(server: String, id: String, data: Data, updated: Date) throws {
        guard data.count <= LocalStore.cacheByteLimit else { return }
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
                record.updated = updated
            } else {
                context.insert(LocalRecord(key: key, server: server, kind: "cache", payload: data, updated: updated))
            }
            let rows = try context.fetch(FetchDescriptor<LocalRecord>(predicate: #Predicate { $0.kind == "cache" }, sortBy: [SortDescriptor(\.updated, order: .reverse)]))
            var bytes = 0
            for (index, row) in rows.enumerated() {
                bytes += row.payload.count
                if index >= LocalStore.cacheLimit || bytes > LocalStore.cacheByteLimit { context.delete(row) }
            }
            try context.save()
        } catch { context.rollback(); throw error }
    }
}
