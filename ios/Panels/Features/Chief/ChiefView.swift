import SwiftUI

struct ChiefView: View {
    var openConversationID: String?
    @Environment(AppStore.self) private var store
    @State private var chief: ChiefRosterEntry?
    @State private var error: String?
    @State private var actions = AgentPageActions()
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                AgentPageControls {
                    AgentPageMenu(target: Principal(kind: "chief", id: "chief"), settled: nil, actions: actions) { await store.refresh() }
                }
                VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                    AgentPageTitle(title: chief?.label ?? store.chief?.label ?? "Chief of Staff", signal: chief?.conversationSignal)
                    AgentRecap(recap: chief?.recap ?? store.chief?.recap ?? "", target: Principal(kind: "chief", id: "chief"), refresh: { await store.refresh() }, actions: actions)
                }
                HolderContent(holder: .chief)
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, PanelsTheme.Space.xl)
        }.toolbar(.hidden, for: .navigationBar).panelsScreen()
            .conversationNavigation(owner: .chief, conversationID: nil, title: chief?.label ?? "Chief of Staff", openConversationID: openConversationID, refreshError: error, retry: { await load() })
            .task(id: store.revision) { await load() }.panelsRefreshable { await store.refresh(); await load() }
            .screenLoadState("Chief of Staff", hasContent: chief != nil, error: error, showsRefreshError: false) { await load() }
            .accessibilityIdentifier("chief-screen")
    }
    private func load() async {
        let api = store.client
        do {
            let result = try await api.chiefRoster()
            guard !Task.isCancelled, api.baseURL == store.baseURL else { return }
            chief = result.chiefOfStaff; error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

struct HolderTickets: View {
    let holder: GoalHolder
    @Environment(AppStore.self) private var store
    @State private var error: String?
    @State private var active: TicketsResponse?
    @State private var backlogged: TicketsResponse?
    @State private var done: TicketsResponse?
    @State private var loadingMore = false

    var body: some View {
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
        .task(id: "\(store.baseURL):\(store.revision)") { await load() }
        if let error { LoadErrorView(message: error) { await load() } }
    }

    @ViewBuilder private func loadMoreButton(page: PageFacts, filter: TicketFilter) -> some View {
        if let offset = page.nextOffset {
            Button {
                Task { await loadMore(filter: filter, offset: offset) }
            } label: {
                HStack {
                    Text("Load more")
                    if loadingMore { RingSpinner(size: .compact).accessibilityHidden(true) }
                }.font(PanelsTheme.TypeScale.small).frame(minHeight: 44).frame(maxWidth: .infinity)
            }.disabled(loadingMore)
                .accessibilityValue(loadingMore ? "Loading more workers" : "")
        }
    }

    private func load() async {
        let api = store.client
        while loadingMore { do { try await Task.sleep(for: .milliseconds(50)) } catch { return } }
        loadingMore = true; defer { loadingMore = false }
        do {
            async let activeRead = api.holderTickets(holder, filter: .active, through: active?.page.offset ?? 0)
            async let backlogRead = api.holderTickets(holder, filter: .backlogged, through: backlogged?.page.offset ?? 0)
            async let doneRead = api.holderTickets(holder, filter: .done, through: done?.page.offset ?? 0)
            let result = try await (activeRead, backlogRead, doneRead)
            try Task.checkCancellation()
            guard api.baseURL == store.baseURL else { return }
            active = result.0; backlogged = result.1; done = result.2; error = nil
        } catch { if !Task.isCancelled, api.baseURL == store.baseURL { self.error = error.localizedDescription } }
    }

    private func loadMore(filter: TicketFilter, offset: Int) async {
        guard !loadingMore else { return }
        loadingMore = true
        defer { loadingMore = false }
        let api = store.client
        do {
            let page = try await api.holderTickets(holder, filter: filter, through: offset)
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
