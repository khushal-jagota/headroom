import Foundation

enum ConversationRetention {
    static let itemLimit = 300
    static let inlineByteLimit = 512 * 1024

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
            if !visible.contains(key), bytes + size > inlineByteLimit { expanded[key] = nil }
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
