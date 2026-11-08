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
                    AgentPageTitle(title: chief?.label ?? store.chief?.label ?? "Chief of Staff", signal: store.chiefSignal ?? chief?.conversationSignal)
                    AgentRecap(recap: chief?.recap ?? store.chief?.recap ?? "", target: Principal(kind: "chief", id: "chief"), refresh: { await store.refresh() }, actions: actions)
                }
                if chief != nil { HolderContent(holder: .chief) }
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, PanelsTheme.Space.xl)
        }
        .toolbar(.hidden, for: .navigationBar)
        .panelsScreen()
        .conversationNavigation(owner: .chief, conversationID: nil, title: chief?.label ?? "Chief of Staff", openConversationID: openConversationID, refreshError: error, retry: { await load() })
        .task(id: "\(store.baseURL.absoluteString)|\(store.connectionSession)") { await load() }
        .reload(onChangeOf: store.chiefStamp()) { await load() }
        // Chief's own record is always in the view; its active children key the reloads too.
        .navigationDemand("chief", windows: [NavigationScope.children(of: .chief)])
        .panelsRefreshable { await load() }
        .screenLoadState("Chief of Staff", hasContent: chief != nil, error: error, showsRefreshError: false) { await load() }
        .accessibilityIdentifier("chief-screen")
    }

    private func load() async {
        let api = store.client
        if chief == nil {
            let roster: ChiefRoster? = api.cached("/api/workers")
            chief = roster?.chiefOfStaff
        }
        do {
            let result = try await api.chiefRoster()
            guard !Task.isCancelled, api.baseURL == store.baseURL else { return }
            chief = result.chiefOfStaff; error = nil
        } catch { if !Task.isCancelled, api.baseURL == store.baseURL { self.error = error.localizedDescription } }
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
    private var holderStamp: String? { holder.kind == .chief ? store.chiefStamp() : store.managerStamp(holder.id ?? "") }

    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
            if let active {
                if active.tickets.isEmpty {
                    Text("No active workers").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint).padding(.vertical, PanelsTheme.Space.md)
                } else {
                    WorkerSections(tickets: store.withLiveMarks(active.tickets), showHistory: false)
                }
                loadMoreButton(page: active.page, filter: .active)
            }
            WorkerDisclosure(title: "Backlogged", count: backlogged?.page.matchCount ?? 0, unreadCount: store.withLiveMarks(backlogged?.tickets ?? []).filter { $0.conversationSignal?.messageForOwner == true }.count) {
                WorkerRows(tickets: WorkerGroup.ordered(store.withLiveMarks(backlogged?.tickets ?? [])))
                if let page = backlogged?.page { loadMoreButton(page: page, filter: .backlogged) }
            }
            WorkerDisclosure(title: "Done", count: done?.page.matchCount ?? 0, unreadCount: store.withLiveMarks(done?.tickets ?? []).filter { $0.conversationSignal?.messageForOwner == true }.count) {
                WorkerRows(tickets: WorkerGroup.ordered(store.withLiveMarks(done?.tickets ?? [])))
                if let page = done?.page { loadMoreButton(page: page, filter: .done) }
            }
        }
        // The holder's record and children window in the navigation view key the reloads.
        .task(id: "\(store.baseURL.absoluteString)|\(store.connectionSession)") { await load() }
        .reload(onChangeOf: holderStamp) { await load() }
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
