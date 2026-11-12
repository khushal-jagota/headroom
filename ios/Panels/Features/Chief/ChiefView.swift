import SwiftUI

struct ChiefView: View {
    var openConversationID: String?
    @Environment(AppStore.self) private var store
    private var resource: ResourceHandle<ChiefRoster> { store.resources.handle(ChiefResources.roster) }
    private var chief: ChiefRosterEntry? { resource.value?.chiefOfStaff }
    private var error: String? { resource.error }
    @State private var actions = AgentPageActions()
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                AgentPageControls {
                    AgentPageMenu(target: Principal(kind: "chief", id: "chief"), settled: nil, actions: actions) { await load(); await store.refresh() }
                }
                VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                    AgentPageTitle(title: chief?.label ?? store.chief?.label ?? "Chief of Staff", signal: store.chiefSignal ?? chief?.conversationSignal)
                    AgentRecap(recap: chief?.recap ?? store.chief?.recap ?? "", target: Principal(kind: "chief", id: "chief"), refresh: { await load(); await store.refresh() }, actions: actions)
                }
                if chief != nil { HolderContent(holder: .chief) }
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, PanelsTheme.Space.xl)
        }
        .toolbar(.hidden, for: .navigationBar)
        .panelsScreen()
        .conversationNavigation(owner: .chief, conversationID: nil, title: chief?.label ?? "Chief of Staff", openConversationID: openConversationID, refreshError: error, retry: { await load() })
        .resourceDemand(resource)
        .navigationDemand("chief", windows: [NavigationScope.children(of: .chief)])
        .panelsRefreshable { await load() }
        .screenLoadState("Chief of Staff", hasContent: chief != nil, error: error, showsRefreshError: false) { await load() }
        .accessibilityIdentifier("chief-screen")
    }

    private func load() async { await resource.refresh() }

}

struct HolderTickets: View {
    let holder: GoalHolder
    @Environment(AppStore.self) private var store
    @State private var through: [String: Int] = [:]
    @State private var loadingMore = false
    private func handles(_ filter: TicketFilter) -> [ResourceHandle<TicketsResponse>] {
        stride(from: 0, through: through[filter.rawValue] ?? 0, by: 30).map {
            store.resources.handle(HolderTicketResources.page(holder, filter: filter, offset: $0))
        }
    }
    private var allHandles: [ResourceHandle<TicketsResponse>] { TicketFilter.allCases.flatMap(handles) }
    private func response(_ filter: TicketFilter) -> TicketsResponse? {
        let pages = handles(filter).compactMap(\.value)
        guard let last = pages.last else { return nil }
        var seen: Set<String> = []
        return TicketsResponse(tickets: pages.flatMap(\.tickets).filter { seen.insert($0.id).inserted }, page: last.page)
    }
    private var active: TicketsResponse? { response(.active) }
    private var backlogged: TicketsResponse? { response(.backlogged) }
    private var done: TicketsResponse? { response(.done) }
    private var error: String? { allHandles.compactMap(\.error).first }

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
        .resourceDemand(allHandles)
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
        await withTaskGroup(of: Void.self) { group in
            for handle in allHandles { group.addTask { await handle.refresh() } }
        }
    }

    private func loadMore(filter: TicketFilter, offset: Int) async {
        guard !loadingMore else { return }
        loadingMore = true; defer { loadingMore = false }
        through[filter.rawValue] = offset
        let handle = store.resources.handle(HolderTicketResources.page(holder, filter: filter, offset: offset))
        await handle.refresh()
    }
}
