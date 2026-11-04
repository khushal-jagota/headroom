import SwiftUI

struct AllGoalsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @State private var goals: [GoalRecord] = []
    @State private var projects: [GoalJSON] = []
    @State private var sessions: [String: GoalSession] = [:]
    @State private var filters = GoalFilters()
    @State private var draft = GoalFilters()
    @State private var selectedFilter: Int?
    @State private var sheet = false
    @State private var loaded = false
    @State private var nextRefreshAt: Double?
    @State private var error: String?
    private let columns = ["Goal", "Current", "Target", "Project", "Due", "State"]
    private func readings(_ goal: GoalRecord) -> (Double?, Double?) {
        if let checklist = GoalPresentation.primaryChecklist(goal) { return (Double(checklist["items"].array.filter { $0["state"].text == "done" }.count), Double(checklist["items"].array.count)) }
        guard let session = sessions[goal.id], let block = GoalPresentation.primaryChart(goal), block.kind != "binary" else { return (nil, nil) }
        let projection = session.readings?.charts[block.id] ?? .null
        let data = GoalChartPresentation.data(block, values: session.readings?.values ?? [], range: "all", projection: projection)
        let headline = GoalChartPresentation.headline(block, data: data, projection: projection)
        return (headline.value, headline.target)
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(columns.indices, id: \.self) { column in
                            Button { draft = filters; selectedFilter = column; sheet = true } label: {
                                HStack(spacing: 6) { Text(columns[column]); Image(systemName: "line.3.horizontal.decrease").font(.system(size: 10)) }
                                    .font(.footnote).padding(.horizontal, 12).frame(minHeight: 36)
                                    .background(filters.active(column) ? PanelsTheme.accentSurface : PanelsTheme.raised, in: Capsule())
                            }.buttonStyle(.plain).foregroundStyle(filters.active(column) ? PanelsTheme.accentText : PanelsTheme.muted)
                        }
                    }
                }
                VStack(spacing: 0) {
                    ForEach(goals.filter { let values = readings($0); return filters.matches($0, current: values.0, target: values.1) }) { goal in
                        NavigationLink(value: AppRoute.goal(goal.id)) { row(goal) }.buttonStyle(.plain)
                        Divider()
                    }
                }
                if loaded, goals.isEmpty { Text("No goals").foregroundStyle(PanelsTheme.Goal.faint) }
                if loaded, !goals.isEmpty, !goals.contains(where: { let values = readings($0); return filters.matches($0, current: values.0, target: values.1) }) { Text("No goals match these filters").foregroundStyle(PanelsTheme.Goal.faint) }
            }.padding(24)
        }.panelsScreen().panelsNavigationTitle("All goals").navigationBarTitleDisplayMode(.inline).toolbar(.visible, for: .navigationBar)
            .screenLoadState("goals", hasContent: loaded, error: error) { await load() }
            .panelsRefreshable { await load() }
            .task(id: "\(store.baseURL):\(store.revision):\(scenePhase == .active)") { if scenePhase == .active { await load() } }
            .modifier(GoalBoundaryRefresh(at: nextRefreshAt, refresh: load))
            .sheet(isPresented: $sheet) { filterSheet }
            .accessibilityIdentifier("all-goals-screen")
    }
    private func row(_ goal: GoalRecord) -> some View {
        let values = readings(goal)
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(goal.title).font(.body.weight(.medium)).foregroundStyle(PanelsTheme.strong)
                Spacer(minLength: 8)
                Text(goal.completed ? "Done" : "Not done").font(.caption).foregroundStyle(goal.completed ? PanelsTheme.success : PanelsTheme.Goal.faint)
            }
            HStack(spacing: 8) {
                Text(projects.first { $0.id == goal.projectId }?["name"].string ?? "…")
                Text("·")
                Text(goal.deadline.map { "By " + GoalPresentation.date($0) } ?? "No deadline").foregroundStyle(PanelsTheme.Goal.tone(GoalPresentation.deadlineTone(goal.deadline, completed: goal.completed, today: goal.statusDate)))
                Spacer(minLength: 0)
                Text(goal.active ? "Active" : "Inactive")
            }.font(.caption).foregroundStyle(PanelsTheme.Goal.faint)
            HStack(spacing: 16) {
                Text("Current  " + format(goal, values.0)).foregroundStyle(PanelsTheme.Goal.tone(goal.status))
                Text("Target  " + format(goal, values.1)).foregroundStyle(PanelsTheme.Goal.faint)
            }.font(.footnote).monospacedDigit()
        }.padding(.vertical, 20).contentShape(Rectangle())
    }
    private func format(_ goal: GoalRecord, _ value: Double?) -> String {
        if let block = GoalPresentation.primaryChart(goal), block.kind != "binary" { return GoalChartPresentation.format(block, value) }
        return value.map { GoalChartPresentation.number($0) } ?? "—"
    }
    private var filterSheet: some View {
        NavigationStack {
            Form {
                switch selectedFilter {
                case 0: TextField("Goal contains", text: $draft.goal)
                case 1: numberFilter($draft.current)
                case 2: numberFilter($draft.target)
                case 3:
                    Picker("Project", selection: $draft.project) {
                        Text("All projects").tag("all")
                        ForEach(projects, id: \.id) { Text($0["name"].text).tag($0.id) }
                    }.pickerStyle(.inline)
                case 4:
                    filterDate("From", value: $draft.dueFrom)
                    filterDate("To", value: $draft.dueTo)
                default:
                    Picker("State", selection: $draft.state) { Text("All").tag("all"); Text("Active").tag("active"); Text("Inactive").tag("inactive") }
                    Picker("Completion", selection: $draft.completion) { Text("All").tag("all"); Text("Done").tag("done"); Text("Not done").tag("open") }
                }
            }.panelsScreen().navigationTitle(columns[selectedFilter ?? 0]).navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Clear") { clear() } }
                    ToolbarItem(placement: .confirmationAction) { Button("Apply") { filters = draft; sheet = false } }
                }
        }.presentationDetents([.medium, .large]).preferredColorScheme(.dark)
    }
    private func numberFilter(_ binding: Binding<GoalNumberFilter>) -> some View {
        Group {
            Picker("Match", selection: binding.operation) {
                Text("Any").tag("any"); Text("At least").tag("gte"); Text("At most").tag("lte"); Text("Equal to").tag("eq"); Text("No reading").tag("missing"); Text("Has reading").tag("present")
            }
            if ["gte", "lte", "eq"].contains(binding.wrappedValue.operation) { TextField("Value", text: binding.value).keyboardType(.numbersAndPunctuation) }
        }
    }
    private func filterDate(_ title: String, value: Binding<String>) -> some View {
        VStack(alignment: .leading) {
            Toggle(title, isOn: Binding(get: { !value.wrappedValue.isEmpty }, set: { value.wrappedValue = $0 ? GoalPresentation.day(Date()) : "" }))
            if !value.wrappedValue.isEmpty {
                DatePicker(title, selection: Binding(get: { GoalPresentation.calendarDate(value.wrappedValue) ?? Date() }, set: { value.wrappedValue = GoalPresentation.day($0) }), displayedComponents: .date)
                    .environment(\.timeZone, TimeZone(secondsFromGMT: 0)!)
            }
        }
    }
    private func clear() {
        switch selectedFilter {
        case 0: draft.goal = ""
        case 1: draft.current = .init()
        case 2: draft.target = .init()
        case 3: draft.project = "all"
        case 4: draft.dueFrom = ""; draft.dueTo = ""
        default: draft.state = "all"; draft.completion = "all"
        }
    }
    private func load() async {
        let api = store.client
        do {
            async let response = api.goals()
            async let projectResponse: GoalJSON = api.get("/api/projects")
            let (list, projects) = try await (response, projectResponse); try Task.checkCancellation()
            guard api.baseURL == store.baseURL else { return }
            goals = list.goals; nextRefreshAt = list.nextRefreshAt; self.projects = projects["projects"].array; loaded = true; error = nil
            for goal in goals { sessions[goal.id] = GoalSession.session(id: goal.id, api: api, initial: goal) }
            await withTaskGroup(of: Void.self) { group in
                for session in sessions.values { group.addTask { await session.refresh() } }
            }
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}
