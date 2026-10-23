import SwiftUI

struct ChiefView: View {
    @Environment(AppStore.self) private var store
    @State private var chief: ChiefRosterEntry?
    @State private var active: TicketsResponse?
    @State private var backlogged: TicketsResponse?
    @State private var done: TicketsResponse?
    @State private var error: String?
    @State private var loadingMore = false
    @State private var actions = AgentPageActions()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                    AgentPageTitle(title: chief?.label ?? store.chief?.label ?? "Chief of Staff")
                    AgentRecap(recap: chief?.recap ?? store.chief?.recap ?? "", target: Principal(kind: "chief", id: "chief"), refresh: { await store.refresh() }, actions: actions)
                }
                if chief != nil {
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                        if let active {
                            if active.tickets.isEmpty {
                                Text("No active workers").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint).padding(.vertical, PanelsTheme.Space.md)
                            } else {
                                WorkerSections(tickets: active.tickets, showHistory: false)
                            }
                            loadMoreButton(page: active.page, filter: .active)
                        }
                        WorkerDisclosure(title: "Backlogged", count: backlogged?.page.matchCount ?? 0, unreadCount: backlogged?.tickets.filter { $0.conversationSignal?.messageForOwner == true }.count ?? 0) {
                            WorkerRows(tickets: WorkerGroup.ordered(backlogged?.tickets ?? []))
                            if let page = backlogged?.page { loadMoreButton(page: page, filter: .backlogged) }
                        }
                        WorkerDisclosure(title: "Done", count: done?.page.matchCount ?? 0, unreadCount: done?.tickets.filter { $0.conversationSignal?.messageForOwner == true }.count ?? 0) {
                            WorkerRows(tickets: WorkerGroup.ordered(done?.tickets ?? []))
                            if let page = done?.page { loadMoreButton(page: page, filter: .done) }
                        }
                    }
                }
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, PanelsTheme.Space.xl)
        }
        .navigationTitle(chief?.label ?? store.chief?.label ?? "Chief of Staff")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.bar, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .panelsScreen()
        .conversationOverlay(owner: .chief, conversationID: nil, title: chief?.label ?? "Chief of Staff", refreshError: error, retry: { await load() })
        .toolbar {
            ToolbarItem(placement: .principal) {
                AgentNavigationHeader(title: chief?.label ?? store.chief?.label ?? "Chief of Staff", signal: chief?.conversationSignal)
            }
            ToolbarItem(placement: .topBarTrailing) {
                AgentPageMenu(target: Principal(kind: "chief", id: "chief"), settled: nil, actions: actions) { await store.refresh() }
            }
        }
        .task(id: store.revision) { await load() }
        .refreshable { await load() }
        .screenLoadState("Chief of Staff", hasContent: chief != nil, error: error, showsRefreshError: false) { await load() }
        .accessibilityIdentifier("chief-screen")
    }

    @ViewBuilder private func loadMoreButton(page: PageFacts, filter: TicketFilter) -> some View {
        if let offset = page.nextOffset {
            Button {
                Task { await loadMore(filter: filter, offset: offset) }
            } label: {
                HStack {
                    Text("Load more")
                    if loadingMore { ProgressView().controlSize(.mini).accessibilityHidden(true) }
                }.font(PanelsTheme.TypeScale.small).frame(minHeight: 44).frame(maxWidth: .infinity)
            }.disabled(loadingMore)
                .accessibilityValue(loadingMore ? "Loading more workers" : "")
        }
    }

    private func load() async {
        let api = store.client
        if chief == nil {
            let roster: ChiefRoster? = api.cached("/api/workers")
            chief = roster?.chiefOfStaff
            func saved(_ filter: TicketFilter) -> TicketsResponse? {
                var query: [URLQueryItem] = [.init(name: "detail", value: "summary"), .init(name: "limit", value: "30"), .init(name: "offset", value: "0")]
                switch filter {
                case .active: query.append(.init(name: "is_backlogged", value: "false"))
                case .backlogged: query.append(.init(name: "is_backlogged", value: "true"))
                case .done: query += [.init(name: "include_terminal", value: "true"), .init(name: "stage", value: "done")]
                }
                query.append(.init(name: "manager_kind", value: "chief"))
                return api.cached("/api/tickets", query: query)
            }
            active = saved(.active); backlogged = saved(.backlogged); done = saved(.done)
        }
        // Keep a single local writer while paging so a refresh cannot shrink its window.
        while loadingMore {
            do { try await Task.sleep(for: .milliseconds(50)) } catch { return }
        }
        loadingMore = true
        defer { loadingMore = false }
        do {
            async let roster = api.chiefRoster()
            async let activeRead = api.chiefWorkers(filter: .active, through: active?.page.offset ?? 0)
            async let backlogRead = api.chiefWorkers(filter: .backlogged, through: backlogged?.page.offset ?? 0)
            async let doneRead = api.chiefWorkers(filter: .done, through: done?.page.offset ?? 0)
            let result = try await (roster, activeRead, backlogRead, doneRead)
            try Task.checkCancellation()
            guard api.baseURL == store.baseURL else { return }
            chief = result.0.chiefOfStaff; active = result.1; backlogged = result.2; done = result.3; error = nil
        } catch { if !Task.isCancelled, api.baseURL == store.baseURL { self.error = error.localizedDescription } }
    }

    private func loadMore(filter: TicketFilter, offset: Int) async {
        guard !loadingMore else { return }
        loadingMore = true
        defer { loadingMore = false }
        let api = store.client
        do {
            let page = try await api.chiefWorkers(filter: filter, through: offset)
            try Task.checkCancellation()
            guard api.baseURL == store.baseURL else { return }
            switch filter {
            case .active: active = page
            case .backlogged: backlogged = page
            case .done: done = page
            }
            error = nil
        } catch { if !Task.isCancelled, api.baseURL == store.baseURL { self.error = error.localizedDescription } }
    }
}
