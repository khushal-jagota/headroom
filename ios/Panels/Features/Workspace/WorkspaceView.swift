import SwiftUI

struct WorkspaceView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var groups: [String: [WorkspaceTicketGroup]] = [:]
    @State private var managers: [ManagerSummary] = []
    @State private var settled: ManagersResponse?
    @State private var settledOpen = false
    @State private var settledLoading = false
    @State private var settledRequestedOffset = 0
    @State private var settledError: String?
    @State private var chiefSignal: ConversationSignal?
    @State private var chiefSignalError: String?
    @State private var loadingMore = false

    private var motion: Animation? { reduceMotion ? nil : PanelsTheme.Motion.disclosure }

    @GestureState private var fingerDown = false
    @State private var pendingSettled: ManagersResponse?
    @State private var scrolling = false
    @State private var previewOpen = false
    @State private var selectedRoute: AppRoute?

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                if let error = store.errors["Workspace"] ?? store.errors["Tickets"] ?? store.errors["Chief"] {
                    LoadErrorView(message: error) { await store.refresh() }
                }
                if let chiefSignalError {
                    LoadErrorView(message: chiefSignalError) { await refreshSupplementary() }
                }
                WorkspaceCard(title: store.chief?.label ?? "Chief of Staff", chief: true,
                              route: .chief, signal: chiefSignal, groups: groups["chief"] ?? [],
                              previewChanged: setPreviewOpen, open: open)
                ForEach(managers) { manager in
                    WorkspaceCard(title: manager.title, project: manager.project, route: .orchestrator(manager.id),
                                  signal: manager.conversationSignal, groups: groups[manager.id] ?? [],
                                  previewChanged: setPreviewOpen, open: open)
                }
                if store.managerPage?.nextOffset != nil || store.ticketPage?.nextOffset != nil {
                    Button {
                        Task {
                            loadingMore = true
                            await store.loadMore()
                            updateGroups()
                            loadingMore = false
                        }
                    } label: {
                        HStack { if loadingMore { ProgressView() }; Text("Load more") }.frame(minHeight: 44)
                    }.disabled(loadingMore)
                }
                settledSection.padding(.top, 16)
            }
            .padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 24)
        }
        .simultaneousGesture(LongPressGesture(minimumDuration: 60, maximumDistance: 10_000)
            .updating($fingerDown) { _, down, _ in down = true })
        .onChange(of: fingerDown) { _, down in if !down { updateGroups() } }
        .onScrollPhaseChange { _, phase in
            scrolling = phase != .idle
            if !scrolling { updateGroups() }
        }
        .refreshable { await store.refresh() }
        .overlay { if store.isLoading && store.chief == nil { ProgressView().controlSize(.large) } }
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: selectedRoute) { old, new in if old != new { Haptics.light() } }
        .navigationDestination(item: $selectedRoute) { RouteDestination(route: $0) }
        .panelsScreen()
        .onChange(of: store.revision, initial: true) { _, _ in updateGroups() }
        .onChange(of: store.baseURL) { _, _ in
            groups = [:]; managers = []; settled = nil; chiefSignal = nil
            settledError = nil; chiefSignalError = nil; settledOpen = false; settledRequestedOffset = 0; pendingSettled = nil
        }
        .task(id: "\(store.baseURL.absoluteString):\(store.revision)") { await refreshSupplementary() }
    }

    private func open(_ route: AppRoute) { selectedRoute = route }
    private func setPreviewOpen(_ value: Bool) {
        previewOpen = value
        if !value { updateGroups() }
    }

    private var settledSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(motion) { settledOpen.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Text("Settled").foregroundStyle(PanelsTheme.text)
                    if let settled { Text(String(settled.page.matchCount)).foregroundStyle(PanelsTheme.faint) }
                    Spacer()
                }.font(.system(size: 17)).frame(minHeight: 44).contentShape(Rectangle())
            }.buttonStyle(.plain)
                .accessibilityIdentifier("workspace-settled")
                .accessibilityValue(settledOpen ? "Expanded" : "Collapsed")
                .accessibilityHint("Shows settled orchestrators")
            if settledOpen {
                if let settledError { LoadErrorView(message: settledError) { await refreshSupplementary() } }
                if let settled {
                    ForEach(settled.managers) { manager in
                        NavigationLink(value: AppRoute.orchestrator(manager.id)) {
                            HStack {
                                Text(manager.title).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
                                WorkspaceSignalMark(signal: manager.conversationSignal)
                            }.font(.system(size: 16)).foregroundStyle(PanelsTheme.faint).frame(minHeight: 44)
                        }.buttonStyle(.plain)
                    }
                    if settled.managers.isEmpty { Text("No settled orchestrators").foregroundStyle(PanelsTheme.faint) }
                    if settled.page.nextOffset != nil {
                        Button("Load more settled") { Task { await loadMoreSettled() } }
                            .frame(minHeight: 44).disabled(settledLoading)
                    }
                } else if settledError == nil { ProgressView() }
                if settledLoading { ProgressView() }
            }
        }.padding(.horizontal, 16)
    }

    private func updateGroups() {
        let tickets = store.tickets
        if fingerDown || scrolling || previewOpen {
            // Keep content fresh without moving a target beneath the finger.
            let latest = Dictionary(tickets.map { ($0.id, $0) }, uniquingKeysWith: { _, new in new })
            groups = groups.mapValues { old in
                old.map { WorkspaceTicketGroup(section: $0.section, tickets: $0.tickets.map { latest[$0.id] ?? $0 }) }
            }
            let latestManagers = Dictionary(store.managers.map { ($0.id, $0) }, uniquingKeysWith: { _, new in new })
            managers = managers.map { latestManagers[$0.id] ?? $0 }
            return
        }
        if let pendingSettled {
            withAnimation(motion) { settled = pendingSettled }
            self.pendingSettled = nil
        }
        let nextManagers = store.managers.sorted {
            $0.createdAt == $1.createdAt ? $0.id < $1.id : $0.createdAt < $1.createdAt
        }
        let byOwner = Dictionary(grouping: tickets) { ticket in
            ticket.manager?.kind == "chief" ? "chief" : ticket.managerId ?? "chief"
        }
        var next: [String: [WorkspaceTicketGroup]] = [:]
        for (owner, tickets) in byOwner { next[owner] = WorkspaceGrouping.groups(tickets, previous: groups[owner] ?? []) }
        withAnimation(groups.isEmpty ? nil : motion) {
            groups = next; managers = nextManagers
        }
    }

    private func refreshSupplementary() async {
        let api = store.client
        async let signalRead: () = loadChiefSignal(api)
        async let settledRead: () = loadSettled(api, through: settledRequestedOffset)
        _ = await (signalRead, settledRead)
    }

    private func loadChiefSignal(_ api: APIClient) async {
        do {
            let signal = try await api.workspaceChiefSignal()
            guard !Task.isCancelled, api.baseURL == store.baseURL else { return }
            chiefSignal = signal; chiefSignalError = nil
        } catch {
            guard !Task.isCancelled, api.baseURL == store.baseURL else { return }
            chiefSignal = nil; chiefSignalError = "Chief activity couldn’t refresh. \(error.localizedDescription)"
        }
    }

    private func loadSettled(_ api: APIClient, through offset: Int) async {
        do {
            let result = try await api.workspaceSettledWindow(through: offset)
            guard !Task.isCancelled, api.baseURL == store.baseURL else { return }
            if settled != nil && (fingerDown || scrolling || previewOpen) {
                pendingSettled = result
            } else {
                withAnimation(settled == nil ? nil : motion) { settled = result }
            }
            settledError = nil
        } catch {
            guard !Task.isCancelled, api.baseURL == store.baseURL else { return }
            settledError = error.localizedDescription
        }
    }

    private func loadMoreSettled() async {
        guard !settledLoading, let offset = settled?.page.nextOffset else { return }
        settledRequestedOffset = offset
        settledLoading = true
        defer { settledLoading = false }
        await loadSettled(store.client, through: offset)
    }
}
