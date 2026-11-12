import Foundation

enum ConversationRetention {
    static let itemLimit = 300
    static let inlineByteLimit = 512 * 1024
    static let historyScopeLimit = 20

    struct Result {
        let items: [ConversationItem]
        let bodies: [String: ConversationBody]
        let removedOldest: Bool
        let removedNewest: Bool
    }

    /// Keep the visible neighborhood. Older paging trims the opposite edge so that
    /// a long transcript can be read with bounded storage.
    static func retain(_ items: [ConversationItem], bodies: [String: ConversationBody], visible: Set<String>, preferOlder: Bool, bodyBytes: ((String, ConversationBody) -> Int)? = nil) -> Result {
        let encoder = JSONEncoder()
        func count(_ key: String, _ body: ConversationBody) -> Int { bodyBytes?(key, body) ?? ((try? encoder.encode(body).count) ?? 0) }
        var kept = items
        var expanded = bodies.filter { key, _ in items.contains { $0.key == key } }
        var sizes = Dictionary(items.map { item in
            (item.key, item.body?.value.map { count("inline:" + item.key, $0) } ?? 0)
        }, uniquingKeysWith: { _, last in last })
        var bytes = sizes.values.reduce(0, +)
        // Visible expansions keep their space before offscreen rows.
        // Nonvisible expansions use only the remaining budget.
        for key in expanded.keys.sorted(by: { visible.contains($0) && !visible.contains($1) }) {
            let size = expanded[key].map { count("expanded:" + key, $0) } ?? 0
            // A deferred body can exceed the reader budget. Reject that decoded
            // expansion without charging it or evicting its row and neighbors.
            if size > inlineByteLimit || (!visible.contains(key) && bytes + size > inlineByteLimit) { expanded[key] = nil }
            else { bytes += size; sizes[key, default: 0] += size }
        }
        var removedOldest = false, removedNewest = false
        while kept.count > itemLimit || bytes > inlineByteLimit {
            let visibleIndices = kept.indices.filter { visible.contains(kept[$0].id) || visible.contains(kept[$0].key) }
            let removeLast: Bool
            if let first = visibleIndices.first, let last = visibleIndices.last {
                removeLast = first < kept.count - 1 - last
            } else { removeLast = preferOlder }
            let removed = removeLast ? kept.removeLast() : kept.removeFirst()
            if removeLast { removedNewest = true } else { removedOldest = true }
            bytes -= sizes.removeValue(forKey: removed.key) ?? 0
            expanded[removed.key] = nil
        }
        return Result(items: kept, bodies: expanded, removedOldest: removedOldest, removedNewest: removedNewest)
    }
}

/// Display only. Coverage and runtime authority never survive restoration.
struct ConversationDisplaySnapshot: Codable, Sendable {
    let schema: Int
    let conversationID: String
    let lens: String
    let page: ConversationPage
    let status: ConversationStatus
}

/// The server cursor names a history scope. Keep the same scope identity here,
/// and discard only reusable display when an inactive scope leaves the budget.
struct ConversationHistoryScopes {
    private struct Entry { var keys: Set<String>; var use: Int }
    private var entries: [ConversationCursor.Scope: Entry] = [:]
    private var sequence = 0
    var count: Int { entries.count }
    var scopes: Set<ConversationCursor.Scope> { Set(entries.keys) }

    mutating func admit(_ scope: ConversationCursor.Scope, keys: Set<String>, visibleKeys: Set<String>, active: ConversationCursor.Scope) -> Set<String> {
        sequence += 1
        let previous = entries[scope]?.keys ?? []
        entries[scope] = Entry(keys: previous.union(keys), use: sequence)
        var removed: Set<String> = []
        while entries.count > ConversationRetention.historyScopeLimit {
            let victim = entries.filter { key, entry in key != active && entry.keys.isDisjoint(with: visibleKeys) }
                .min { $0.value.use < $1.value.use }?.key
            guard let victim else {
                // All existing scopes are visible. Do not admit another scope.
                removed.formUnion(entries.removeValue(forKey: scope)?.keys ?? [])
                break
            }
            removed.formUnion(entries.removeValue(forKey: victim)?.keys ?? [])
        }
        return removed.subtracting(entries.values.reduce(into: Set<String>()) { $0.formUnion($1.keys) })
    }
    mutating func retain(_ keys: Set<String>) {
        for scope in entries.keys {
            entries[scope]?.keys.formIntersection(keys)
            if entries[scope]?.keys.isEmpty == true { entries[scope] = nil }
        }
    }
}
