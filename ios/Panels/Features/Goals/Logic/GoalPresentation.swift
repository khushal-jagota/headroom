import Foundation

enum GoalPresentation {
    static func deadlineTone(_ deadline: String?, completed: Bool, today: String?) -> String {
        guard let deadline, !deadline.isEmpty, let today else { return "neutral" }
        if completed { return "good" }
        return deadline < today ? "bad" : deadline == today ? "attention" : "neutral"
    }
    static func statusText(_ goal: GoalRecord) -> String {
        if goal.kind != "binary" { return goal.status == "good" ? "On target" : goal.status == "bad" ? "Below target" : "No readings" }
        return goal.completed ? "Done" : goal.status == "attention" ? "Due today" : goal.status == "bad" ? "Overdue" : "Not done"
    }
    static func date(_ value: String?) -> String {
        guard let value, !value.isEmpty else { return "No deadline" }
        guard let day = calendarDate(value) else { return value }
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "en_US"); formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "MMM d"
        return formatter.string(from: day)
    }
    static func calendarDate(_ value: String) -> Date? {
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0); formatter.dateFormat = "yyyy-MM-dd"
        return formatter.date(from: value)
    }
    static func day(_ date: Date) -> String {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(secondsFromGMT: 0); f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }
    static func checklists(_ blocks: [GoalJSON]) -> [GoalJSON] {
        blocks.flatMap { block in
            if block.type == "checklist" { return [block] }
            return checklists(block["blocks"].array) + block["views"].array.flatMap { checklists($0["blocks"].array) }
        }
    }
    static func primaryChecklist(_ goal: GoalRecord) -> GoalJSON? {
        guard goal.kind == "binary", let id = goal.primaryChecklistBlockId else { return nil }
        return checklists(goal.page["blocks"].array).first { $0.id == id }
    }
    static func primaryChart(_ goal: GoalRecord) -> GoalJSON? {
        if primaryChecklist(goal) != nil { return nil }
        if goal.kind == "binary" { return .object(["type": .string("chart"), "id": .string("completion"), "kind": .string("binary"), "placement": .string("primary")]) }
        return goal.primaryChart
    }
    static func updateBlock(_ blocks: [GoalJSON], id: String, update: (GoalJSON) -> GoalJSON) -> [GoalJSON] {
        blocks.map { block in
            if block.id == id { return update(block) }
            var next = block
            if !block["blocks"].isNull { next["blocks"] = .array(updateBlock(block["blocks"].array, id: id, update: update)) }
            if !block["views"].isNull {
                next["views"] = .array(block["views"].array.map { $0.setting("blocks", .array(updateBlock($0["blocks"].array, id: id, update: update))) })
            }
            return next
        }
    }
    static func withoutChecklist(_ blocks: [GoalJSON], id: String?) -> [GoalJSON] {
        blocks.filter { $0.type != "checklist" || $0.id != id }.map { block in
            var next = block
            if !block["blocks"].isNull { next["blocks"] = .array(withoutChecklist(block["blocks"].array, id: id)) }
            if !block["views"].isNull { next["views"] = .array(block["views"].array.map { $0.setting("blocks", .array(withoutChecklist($0["blocks"].array, id: id))) }) }
            return next
        }
    }
}

extension GoalJSON {
    /// Paths are native editor locations, never sent as a new backend contract.
    func replacing(path: [String], with value: GoalJSON) -> GoalJSON {
        guard let first = path.first else { return value }
        let rest = Array(path.dropFirst())
        if case .array(var items) = self, let index = Int(first), items.indices.contains(index) {
            items[index] = items[index].replacing(path: rest, with: value); return .array(items)
        }
        if case .object(var fields) = self, let child = fields[first] {
            fields[first] = child.replacing(path: rest, with: value); return .object(fields)
        }
        return self
    }
}
