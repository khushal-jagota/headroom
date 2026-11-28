import Foundation

/// The cards this phone skipped, as web/src/lib/review/skips.ts keeps them. A skip lives on
/// the phone only: the server never hears of it, so a skip here leaves the same card waiting
/// on the desktop. Panels has one owner per server, so a list belongs to a server and to no one else.
///
/// Each entry is one Ticket or one conversation, and it remembers the parts that were waiting
/// when its cards were skipped. The phone skips per card: a card is skipped while the marks
/// cover its own part. A skip ends when the card shows a part that is newer or new. It never
/// ends because a part is gone: an answered message or a decided proposal makes an entry
/// smaller, not new.
struct ReviewSkipMarks: Codable, Equatable, Sendable {
    struct Proposal: Codable, Equatable, Sendable {
        /// The card's identity: its Ticket, field, time and revision.
        let identity: String
        let waitingSince: Double
        let revision: Int
        func later(than other: Proposal) -> Bool {
            waitingSince > other.waitingSince || (waitingSince == other.waitingSince && revision > other.revision)
        }
    }
    /// The newest message to the owner in a conversation; nil when the view gives no sequence.
    struct Message: Codable, Equatable, Sendable { let sequence: Int? }

    var proposal: Proposal?
    var messages: [String: Message] = [:]
    /// When the ask in each conversation was raised.
    var asks: [String: Double] = [:]

    /// The part of one card.
    init(_ item: ReviewItem) {
        switch item.reviewItemType {
        case "message": messages[item.conversationId ?? ""] = Message(sequence: item.latestOwnerMessageSequence)
        case "ask": asks[item.conversationId ?? ""] = item.waitingSince
        default: proposal = Proposal(identity: item.id, waitingSince: item.waitingSince, revision: item.proposalRevision ?? 0)
        }
    }
    init(proposal: Proposal? = nil, messages: [String: Message] = [:], asks: [String: Double] = [:]) {
        self.proposal = proposal; self.messages = messages; self.asks = asks
    }

    private static func later(_ left: Int?, _ right: Int?) -> Bool {
        guard let left else { return false }
        return right.map { left > $0 } ?? true
    }

    /// The marks hold this card's part, and the card shows nothing newer.
    func covers(_ item: ReviewItem) -> Bool {
        let part = ReviewSkipMarks(item)
        if let now = part.proposal {
            guard let held = proposal else { return false }
            return now.identity == held.identity || !now.later(than: held)
        }
        if let (conversation, now) = part.messages.first {
            guard let held = messages[conversation] else { return false }
            return !Self.later(now.sequence, held.sequence)
        }
        if let (conversation, raised) = part.asks.first {
            guard let held = asks[conversation] else { return false }
            return raised <= held
        }
        return false
    }

    /// The newer of each part. A skip made from an older view cannot make a newer one older.
    func merged(_ other: ReviewSkipMarks) -> ReviewSkipMarks {
        var next = self
        for (conversation, message) in other.messages {
            if let held = next.messages[conversation], !Self.later(message.sequence, held.sequence) { continue }
            next.messages[conversation] = message
        }
        for (conversation, raised) in other.asks { next.asks[conversation] = max(raised, next.asks[conversation] ?? raised) }
        if let theirs = other.proposal, next.proposal.map({ theirs.later(than: $0) }) ?? true { next.proposal = theirs }
        return next
    }
}

struct ReviewSkipList: Equatable, Sendable {
    struct Entry: Codable, Equatable, Sendable {
        var marks: ReviewSkipMarks
        /// When the entry was last skipped.
        var at: Double
        /// Every card of the entry was skipped then. It says whether the entry counts as
        /// skipped while the phone has not loaded it.
        var whole: Bool
    }
    /// The most skips the phone keeps.
    static let limit = 500
    static let storedVersion = 1

    private(set) var entries: [RecordKey: Entry] = [:]
    init(_ entries: [RecordKey: Entry] = [:]) { self.entries = entries }
    var isEmpty: Bool { entries.isEmpty }
    var keys: [RecordKey] { entries.keys.sorted { ($0.kind, $0.id) < ($1.kind, $1.id) } }

    func isSkipped(_ item: ReviewItem) -> Bool { entries[item.historyKey]?.marks.covers(item) ?? false }

    /// Adds the card's part to its entry. `whole` says that every card of the entry is now skipped.
    func skipping(_ item: ReviewItem, at: Double, whole: Bool) -> ReviewSkipList {
        var next = self
        let marks = ReviewSkipMarks(item)
        next.entries[item.historyKey] = Entry(marks: entries[item.historyKey]?.marks.merged(marks) ?? marks, at: at, whole: whole)
        return next
    }

    /// The entries the server is told are skipped. An entry whose cards the phone knows is
    /// skipped when every one of them is. An entry it does not know counts as it was skipped.
    func applyingKeys(known: [RecordKey: [ReviewItem]]) -> [RecordKey] {
        keys.filter { key in
            guard let cards = known[key], !cards.isEmpty else { return entries[key]!.whole }
            return cards.allSatisfy { entries[key]!.marks.covers($0) }
        }
    }

    /// Drops the skips of entries that left the queue: those absent from a read of the whole queue.
    func keeping(_ present: Set<RecordKey>) -> ReviewSkipList { ReviewSkipList(entries.filter { present.contains($0.key) }) }

    /// Keeps `limit` skips. The entries the phone has not seen go first, the oldest first, because they may be gone.
    func limited(seen: Set<RecordKey>, to limit: Int = Self.limit) -> ReviewSkipList {
        guard entries.count > limit else { return self }
        let ranked = entries.sorted { left, right in
            let (a, b) = (seen.contains(left.key), seen.contains(right.key))
            return a != b ? a : left.value.at > right.value.at
        }
        return ReviewSkipList(Dictionary(uniqueKeysWithValues: ranked.prefix(limit).map { ($0.key, $0.value) }))
    }

    private struct Stored: Codable {
        struct Row: Codable { let kind: String; let id: String; let entry: Entry }
        let version: Int
        let entries: [Row]
    }
    func encoded() -> Data? {
        try? JSONEncoder().encode(Stored(version: Self.storedVersion, entries: keys.map { Stored.Row(kind: $0.kind, id: $0.id, entry: entries[$0]!) }))
    }
    /// Anything that is not a list this build wrote reads as no skips.
    init(stored data: Data?) {
        guard let data, let stored = try? JSONDecoder().decode(Stored.self, from: data), stored.version == Self.storedVersion else { return }
        var entries: [RecordKey: Entry] = [:]
        for row in stored.entries where (row.kind == "ticket" || row.kind == "conversation") && row.entry.at.isFinite {
            entries[RecordKey(kind: row.kind, id: row.id)] = row.entry
        }
        self = ReviewSkipList(entries).limited(seen: [])
    }
}

/// The one owner of the skip lists on this phone, one per server installation. It reads a
/// list once and writes on every change. A save that fails leaves the skips for this app run.
@MainActor final class ReviewSkipStore {
    /// A list nobody has touched for this long belongs to a server that is gone.
    static let abandonedAfter: TimeInterval = 30 * 24 * 60 * 60
    static let kind = "review_skips"
    private let load: (String) -> Data?
    private let save: (String, Data?) -> Void
    private let forgetAbandoned: (String) -> Void

    init(load: @escaping (String) -> Data?, save: @escaping (String, Data?) -> Void, forgetAbandoned: @escaping (String) -> Void = { _ in }) {
        self.load = load; self.save = save; self.forgetAbandoned = forgetAbandoned
    }

    /// The phone's own store, under the server's address and the installation that the view names.
    static func local(server: String, in store: LocalStore = .shared) -> ReviewSkipStore {
        ReviewSkipStore(load: { installation in
            try? store.record(server: server, kind: kind, id: installation)?.payload
        }, save: { installation, data in
            if let data { try? store.put(server: server, kind: kind, id: installation, data: data) }
            else { try? store.remove(server: server, kind: kind, id: installation) }
        }, forgetAbandoned: { installation in
            let rows = (try? store.records(kind: kind)) ?? []
            for row in rows where row.updated < Date.now.addingTimeInterval(-abandonedAfter) {
                let id = String(row.key.split(separator: "\u{1f}", omittingEmptySubsequences: false).last ?? "")
                if row.server != server || id != installation { try? store.remove(server: row.server, kind: kind, id: id) }
            }
        })
    }

    func list(_ installation: String) -> ReviewSkipList {
        forgetAbandoned(installation)
        return ReviewSkipList(stored: load(installation))
    }
    func write(_ list: ReviewSkipList, for installation: String) {
        save(installation, list.isEmpty ? nil : list.encoded())
    }
}
