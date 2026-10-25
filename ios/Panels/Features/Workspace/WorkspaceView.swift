import SwiftUI

struct WorkspaceView: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var visible = false
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var groups: [String: [WorkspaceTicketGroup]] = [:]
    @State private var managers: [ManagerSummary] = []
    @State private var settled: ManagersResponse?
    @State private var settledOpen = false
    @State private var settledRowsVisible = false
    @State private var settledAnimating = false
    @State private var scrollPosition = ScrollPosition()
    @State private var scrollOffset: CGFloat = 0
    @State private var savedScrollOffset: CGFloat = 0
    @State private var viewportHeight: CGFloat = 0
    @State private var settledLoading = false
    @State private var settledRequestedOffset = 0
    @State private var settledError: String?
    @State private var chiefSignal: ConversationSignal?
    @State private var chiefSignalError: String?
    @State private var loadingMore = false

    private var hasContent: Bool { store.chief != nil || store.managerPage != nil || store.ticketPage != nil }
    private var refreshError: String? {
        store.errors["Workspace"] ?? store.errors["Tickets"] ?? store.errors["Chief"] ?? chiefSignalError
    }

    private var motion: Animation? { reduceMotion ? nil : PanelsTheme.Motion.disclosure }

    @GestureState private var fingerDown = false
    @State private var scrolling = false
    @State private var previewOpen = false
    @State private var selectedRoute: AppRoute?

    var body: some View {
        ScrollViewReader { proxy in
        ScrollView {
            LazyVStack(spacing: 12) {
                if hasContent {
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
                    settledSection(proxy: proxy).padding(.top, 16)
                }
            }
            .padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 24)
        }
        .scrollPosition($scrollPosition)
        .onScrollGeometryChange(for: CGFloat.self) { $0.contentOffset.y + $0.contentInsets.top } action: { _, offset in scrollOffset = offset }
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { viewportHeight = $0 }
        .simultaneousGesture(LongPressGesture(minimumDuration: 60, maximumDistance: 10_000)
            .updating($fingerDown) { _, down, _ in down = true })
        .onChange(of: fingerDown) { _, down in if !down { updateGroups() } }
        .onScrollPhaseChange { _, phase in
            scrolling = phase != .idle
            if !scrolling { updateGroups() }
        }
        .refreshable { await refresh() }
        .screenLoadState("workspace", hasContent: hasContent, error: refreshError) { await refresh() }
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: selectedRoute) { old, new in if old != new { Haptics.light() } }
        .navigationDestination(item: $selectedRoute) { RouteDestination(route: $0) }
        .panelsScreen()
        .onAppear {
            visible = true; store.workspaceVisible = true
            updateGroups()
            Task { await store.refresh() }
        }
        .onDisappear { visible = false; store.workspaceVisible = false }
        .onChange(of: store.revision, initial: true) { _, _ in if visible { updateGroups() } }
        .onChange(of: store.baseURL) { _, _ in
            groups = [:]; managers = []; settled = nil; chiefSignal = nil
            settledError = nil; chiefSignalError = nil; settledOpen = false; settledRequestedOffset = 0
        }
        .task(id: "\(store.baseURL.absoluteString):\(store.revision):\(visible)") { if visible { await refreshSupplementary() } }
        }
    }

    private func open(_ route: AppRoute) { selectedRoute = route }
    private func setPreviewOpen(_ value: Bool) {
        previewOpen = value
        if !value { updateGroups() }
    }

    private func settledSection(proxy: ScrollViewProxy) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                toggleSettled(proxy: proxy)
            } label: {
                HStack(spacing: 8) {
                    Text("Settled").foregroundStyle(PanelsTheme.text)
                    if let settled { Text(String(settled.page.matchCount)).foregroundStyle(PanelsTheme.faint) }
                    Spacer()
                    if settled == nil && settledError == nil { ProgressView().controlSize(.mini) }
                }.panelsScaledFont(size: 17).frame(minHeight: 44).contentShape(Rectangle())
            }.buttonStyle(.plain)
                .id("settledLine")
                .disabled(settledAnimating)
                .accessibilityIdentifier("workspace-settled")
                .accessibilityValue(settledOpen ? "Expanded" : "Collapsed")
                .accessibilityHint("Shows settled orchestrators")
            if settledOpen {
                LazyVStack(alignment: .leading, spacing: 0) {
                if let settledError { LoadErrorView(message: settledError) { await refreshSupplementary() } }
                if let settled {
                    ForEach(settled.managers) { manager in
                        NavigationLink(value: AppRoute.orchestrator(manager.id)) {
                            HStack {
                                Text(manager.title).lineLimit(typeSize.isAccessibilitySize ? nil : 1).fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                                ConversationSignalMark(signal: manager.conversationSignal)
                            }.panelsScaledFont(size: 16).foregroundStyle(manager.conversationSignal?.messageForOwner == true ? PanelsTheme.strong : PanelsTheme.faint).frame(minHeight: 44)
                        }.buttonStyle(.plain)
                            .conversationAccessibility(label: manager.title, signal: manager.conversationSignal)
                            .onScrollVisibilityChange { visible in
                                if visible, settled.managers.suffix(10).contains(where: { $0.id == manager.id }), settled.page.nextOffset != nil {
                                    Task { await loadMoreSettled() }
                                }
                            }
                    }
                    if settled.managers.isEmpty { Text("No settled orchestrators").foregroundStyle(PanelsTheme.faint) }
                }
                if settledLoading && settled != nil { ProgressView().controlSize(.mini).frame(maxWidth: .infinity) }
                }.opacity(settledRowsVisible ? 1 : 0)
            }
        }.padding(.horizontal, 16)
            .frame(minHeight: settledOpen ? max(44, viewportHeight - 24) : 44, alignment: .top)
    }

    private func toggleSettled(proxy: ScrollViewProxy) {
        guard !settledAnimating else { return }
        Haptics.selection()
        settledAnimating = true
        let scrollMotion: Animation? = reduceMotion ? nil : PanelsTheme.Motion.entrance
        if !settledOpen {
            savedScrollOffset = scrollOffset
            settledOpen = true
            Task { @MainActor in
                // Let the inserted height participate in layout before scrolling.
                await Task.yield()
                withAnimation(scrollMotion) { proxy.scrollTo("settledLine", anchor: .top) }
                try? await Task.sleep(for: .seconds(reduceMotion ? 0 : PanelsTheme.Motion.enter))
                withAnimation(.easeOut(duration: reduceMotion ? PanelsTheme.Motion.exit : PanelsTheme.Motion.base)) { settledRowsVisible = true }
                settledAnimating = false
            }
        } else {
            withAnimation(.easeOut(duration: PanelsTheme.Motion.exit)) { settledRowsVisible = false }
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(PanelsTheme.Motion.exit))
                // Restore while the expanded space still exists, avoiding an automatic
                // content-size clamp before the animated return has completed.
                withAnimation(scrollMotion) { scrollPosition.scrollTo(y: savedScrollOffset) }
                try? await Task.sleep(for: .seconds(reduceMotion ? 0 : PanelsTheme.Motion.enter))
                settledOpen = false
                settledAnimating = false
            }
        }
    }

    private func updateGroups() {
        let tickets = store.tickets
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

    private func refresh() async {
        await store.refresh()
        await refreshSupplementary()
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
            chiefSignalError = "Chief activity couldn’t refresh. \(error.localizedDescription)"
        }
    }

    private func loadSettled(_ api: APIClient, through offset: Int) async {
        do {
            let result = try await api.workspaceSettledWindow(through: offset)
            guard !Task.isCancelled, api.baseURL == store.baseURL else { return }
            withAnimation(settled == nil ? nil : motion) { settled = result }
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
