import Foundation

struct GoalNumberFilter: Equatable {
    var operation = "any"
    var value = ""
    func matches(_ number: Double?) -> Bool {
        if operation == "any" { return true }
        if operation == "missing" { return number == nil }
        if operation == "present" { return number != nil }
        guard let number, let threshold = Double(value.isEmpty ? "0" : value) else { return false }
        return operation == "gte" ? number >= threshold : operation == "lte" ? number <= threshold : number == threshold
    }
}
struct GoalFilters: Equatable {
    var goal = ""
    var current = GoalNumberFilter()
    var target = GoalNumberFilter()
    var project = "all"
    var dueFrom = ""
    var dueTo = ""
    var state = "all"
    var completion = "all"
    func matches(_ record: GoalRecord, current: Double?, target: Double?) -> Bool {
        (goal.isEmpty || record.title.localizedCaseInsensitiveContains(goal)) && self.current.matches(current) && self.target.matches(target) &&
        (project == "all" || record.projectId == project) && (state == "all" || record.active == (state == "active")) &&
        (completion == "all" || record.completed == (completion == "done")) &&
        (dueFrom.isEmpty || record.deadline.map { $0 >= dueFrom } == true) && (dueTo.isEmpty || record.deadline.map { $0 <= dueTo } == true)
    }
    func active(_ column: Int) -> Bool {
        switch column { case 0: !goal.isEmpty; case 1: current.operation != "any"; case 2: target.operation != "any"; case 3: project != "all"; case 4: !dueFrom.isEmpty || !dueTo.isEmpty; default: state != "all" || completion != "all" }
    }
}
