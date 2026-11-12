import SwiftUI

struct AllGoalsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @State private var goalsHandle: ResourceHandle<GoalsResponse>?
    @State private var managersHandle: ResourceHandle<ManagersResponse>?
    @State private var projectsHandle: ResourceHandle<GoalJSON>?
    private var goals: [GoalRecord] { goalsHandle?.value?.goals ?? [] }
    private var managers: [ManagerSummary] { managersHandle?.value?.managers ?? [] }
    private var projects: [GoalJSON] { projectsHandle?.value?["projects"].array ?? [] }
    @State private var sessions = PageSessionCollection()
    @State private var filters = GoalFilters()
    @State private var draft = GoalFilters()
    private enum FilterColumn: Int, CaseIterable, Identifiable {
        case goal, current, target, project, due, state
        var id: Int { rawValue }
        var title: String {
            switch self {
            case .goal: "Goal"
            case .current: "Current"
            case .target: "Target"
            case .project: "Project"
            case .due: "Due"
            case .state: "State"
            }
        }
    }
    @State private var selectedFilter: FilterColumn?
    private var loaded: Bool { goalsHandle?.value != nil }
    private var nextRefreshAt: Double? { goalsHandle?.value?.nextRefreshAt }
    private var error: String? { goalsHandle?.error ?? managersHandle?.error ?? projectsHandle?.error }
    private func readings(_ goal: GoalRecord) -> (Double?, Double?) {
        if let checklist = GoalPresentation.primaryChecklist(goal) { return (Double(checklist["items"].array.filter { $0["state"].text == "done" }.count), Double(checklist["items"].array.count)) }
        guard let session = sessions.session(goal.id, server: store.baseURL), let block = GoalPresentation.primaryChart(goal), block.kind != "binary" else { return (nil, nil) }
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
                        ForEach(FilterColumn.allCases) { column in
                            Button { draft = filters; selectedFilter = column } label: {
                                HStack(spacing: 6) { Text(column.title); Image(systemName: "line.3.horizontal.decrease").font(.system(size: 10)) }
                                    .font(.footnote).padding(.horizontal, 12).frame(minHeight: 36)
                                    .background(filters.active(column.rawValue) ? PanelsTheme.accentSurface : PanelsTheme.raised, in: Capsule())
                            }.buttonStyle(.plain).foregroundStyle(filters.active(column.rawValue) ? PanelsTheme.accentText : PanelsTheme.muted)
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
            .task(id: "\(store.baseURL):\(scenePhase == .active)") { if scenePhase == .active { await follow() } }
            .task(id: "\(store.baseURL):\(goals.map(\.id)):\(scenePhase == .active)") { if scenePhase == .active { await followReadings() } }
            .onChange(of: sessions.unfinishedKeys) { _, _ in sessions.prune() }
            .sheet(item: $selectedFilter) { column in filterSheet(column) }
            .accessibilityIdentifier("all-goals-screen")
    }
    private func row(_ goal: GoalRecord) -> some View {
        let values = readings(goal)
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(displayName(goal)).font(.body.weight(.medium)).foregroundStyle(PanelsTheme.strong)
                Spacer(minLength: 8)
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
    private func displayName(_ goal: GoalRecord) -> String {
        guard let holder = goal.holder, holder.kind == .manager, holder.soleGoal(in: goals) != nil else { return goal.title }
        return managers.first { $0.id == holder.id }?.title ?? "Manager"
    }
    private func format(_ goal: GoalRecord, _ value: Double?) -> String {
        if let block = GoalPresentation.primaryChart(goal), block.kind != "binary" { return GoalChartPresentation.format(block, value) }
        return value.map { GoalChartPresentation.number($0) } ?? "—"
    }
    private func filterSheet(_ column: FilterColumn) -> some View {
        NavigationStack {
            Form {
                switch column {
                case .goal: TextField("Goal contains", text: $draft.goal)
                case .current: numberFilter($draft.current)
                case .target: numberFilter($draft.target)
                case .project:
                    Picker("Project", selection: $draft.project) {
                        Text("All projects").tag("all")
                        ForEach(projects, id: \.id) { Text($0["name"].text).tag($0.id) }
                    }.pickerStyle(.inline)
                case .due:
                    filterDate("From", value: $draft.dueFrom)
                    filterDate("To", value: $draft.dueTo)
                case .state:
                    Picker("State", selection: $draft.state) { Text("All").tag("all"); Text("Active").tag("active"); Text("Inactive").tag("inactive") }
                    Picker("Completion", selection: $draft.completion) { Text("All").tag("all"); Text("Done").tag("done"); Text("Not done").tag("open") }
                }
            }.panelsScreen().navigationTitle(column.title).navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Clear") { clear(column) } }
                    ToolbarItem(placement: .confirmationAction) { Button("Apply") { filters = draft; selectedFilter = nil } }
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
    private func clear(_ column: FilterColumn) {
        switch column {
        case .goal: draft.goal = ""
        case .current: draft.current = .init()
        case .target: draft.target = .init()
        case .project: draft.project = "all"
        case .due: draft.dueFrom = ""; draft.dueTo = ""
        case .state: draft.state = "all"; draft.completion = "all"
        }
    }
    private func follow() async {
        let list = store.resources.handle(GoalResources.list), managers = store.resources.handle(GoalResources.managers), projects = store.resources.handle(GoalResources.projects)
        goalsHandle = list; managersHandle = managers; projectsHandle = projects
        list.acquire(); managers.acquire(); projects.acquire()
        defer { list.release(); managers.release(); projects.release() }
        await load()
        do { try await Task.sleep(for: .seconds(365 * 24 * 60 * 60)) } catch { }
    }
    private func followReadings() async {
        let active = sessions.update(goals, api: store.client, resources: store.resources)
        active.forEach { $0.acquire() }; defer { active.forEach { $0.release() } }
        await withTaskGroup(of: Void.self) { group in
            for session in active { group.addTask { await session.refresh() } }
        }
        do { try await Task.sleep(for: .seconds(365 * 24 * 60 * 60)) } catch { }
    }
    private func load() async {
        async let list: () = goalsHandle?.refresh() ?? ()
        async let managers: () = managersHandle?.refresh() ?? ()
        async let projects: () = projectsHandle?.refresh() ?? ()
        _ = await (list, managers, projects)
    }
}
