import Foundation

/// Lossless JSON for the recursive page/chart union in goals/contracts.ts. Keeping
/// the original keys also preserves authored fields during a single-field edit.
indirect enum GoalJSON: Codable, Equatable, Sendable {
    case object([String: GoalJSON]), array([GoalJSON]), string(String), number(Double), bool(Bool), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([GoalJSON].self) { self = .array(v) }
        else { self = .object(try c.decode([String: GoalJSON].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .object(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .null: try c.encodeNil()
        }
    }
    subscript(_ key: String) -> GoalJSON {
        get { if case .object(let v) = self { return v[key] ?? .null }; return .null }
        set { if case .object(var v) = self { v[key] = newValue; self = .object(v) } }
    }
    var array: [GoalJSON] { if case .array(let v) = self { return v }; return [] }
    var string: String? { if case .string(let v) = self { return v }; return nil }
    var text: String { string ?? "" }
    var number: Double? { if case .number(let v) = self, v.isFinite { return v }; return nil }
    var bool: Bool? { if case .bool(let v) = self { return v }; return nil }
    var isNull: Bool { self == .null }
    var id: String { self["id"].text }
    var type: String { self["type"].text }
    var kind: String { self["kind"].text }
    func setting(_ key: String, _ value: GoalJSON) -> GoalJSON { var copy = self; copy[key] = value; return copy }
}

struct GoalRecord: Codable, Identifiable, Sendable, Equatable {
    let id: String
    var projectId: String
    var title: String
    var why: String
    let kind: String
    var active: Bool
    var completed: Bool
    var completionDate: String? = nil
    var completionRule: GoalJSON? = nil
    var outcome: String? = nil
    let primaryChecklistBlockId: String?
    var deadline: String?
    let primaryChart: GoalJSON?
    var page: GoalJSON
    let revision: Int
    let createdAt: Double
    let updatedAt: Double
    let employeeBackend: String
    let employeeLaunchModel: String
    let employeeLaunchReasoningEffort: String?
    let holderKind: GoalHolder.Kind
    let holderId: String?
    let legacyConversationId: String?
    var holder: GoalHolder? { GoalHolder(kind: holderKind, id: holderId) }
    let status: String
    let statusDate: String
    let nextRefreshAt: Double
}
struct GoalsResponse: Decodable, Sendable {
    let goals: [GoalRecord]
    let statusDate: String
    let nextRefreshAt: Double
}
struct GoalReadings: Decodable, Sendable {
    let goalId: String
    let revision: Int
    let status: String
    let statusDate: String
    let nextRefreshAt: Double
    let values: [GoalJSON]
    let sources: [GoalJSON]
    let charts: [String: GoalJSON]
}
struct GoalManualState: Codable, Equatable, Sendable {
    let measureId: String
    let definitionRevision: Int
    let writeRevision: Int
    let timezone: String
    let currentDate: String
    let date: String
    let observation: GoalJSON?
    let latestObservation: GoalJSON?
    func value(cumulative: Bool) -> Double? { (cumulative ? latestObservation : observation)?["value"].number }
}
struct GoalManualResult: Decodable, Sendable {
    let receipt: GoalJSON
    let state: GoalManualState
    let replayed: Bool
}

/// Exact retained request body; an uncertain retry reuses the request ID and date.
struct GoalManualCommand: Codable, Equatable, Sendable {
    let requestId: String
    var date: String
    let expectedDefinitionRevision: Int
    let basis: String
    let operation: String
    var value: Double? = nil
    var delta: Double? = nil
    var expectedWriteRevision: Int? = nil
}

/// The server's goal holder: Chief or a particular Manager, never a goal.
struct GoalHolder: Hashable, Sendable {
    enum Kind: String, Codable, Sendable { case chief, manager }
    let kind: Kind
    let id: String?
    init?(kind: Kind, id: String?) {
        guard kind == .chief ? id == nil : id?.isEmpty == false else { return nil }
        self.kind = kind; self.id = id
    }
    static let chief = GoalHolder(kind: .chief, id: nil)!
    static func manager(_ id: String) -> GoalHolder { GoalHolder(kind: .manager, id: id)! }
    var conversationOwner: ConversationOwner {
        kind == .chief ? .chief : .manager(id!)
    }
    var pagePath: String { kind == .chief ? "/api/chief/page" : "/api/managers/\(id!)/page" }
    func soleGoal(in goals: [GoalRecord]) -> GoalRecord? {
        let held = self.goals(in: goals)
        return held.count == 1 ? held.first : nil
    }
    func goals(in goals: [GoalRecord]) -> [GoalRecord] { goals.filter { $0.holder == self } }
}
struct HolderPage: Codable, Sendable {
    let page: GoalJSON?
    let pageRevision: Int
}

enum PageDocumentPresentation {
    static func hasTickets(_ blocks: [GoalJSON]) -> Bool {
        blocks.contains { block in
            block.type == "tickets" || hasTickets(block["blocks"].array) || block["views"].array.contains { hasTickets($0["blocks"].array) }
        }
    }
}
