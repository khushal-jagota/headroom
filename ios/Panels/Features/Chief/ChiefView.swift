import SwiftUI

struct ChiefView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var chief: ChiefRosterEntry?
    @State private var active: TicketsResponse?
    @State private var backlogged: TicketsResponse?
    @State private var done: TicketsResponse?
    @State private var error: String?
    @State private var loadingMore = false
    @State private var headerCollapsed = false
    @State private var actions = AgentPageActions()
    @State private var scrolling = false
    @State private var refreshAfterScroll = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                if let error { LoadErrorView(message: error) { await load() } }
                VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                    AgentEyebrow(title: "", signal: chief?.conversationSignal)
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
                    ConversationPlaceholder()
                } else if error == nil {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, PanelsTheme.Space.xxl)
                }
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, PanelsTheme.Space.xl)
        }
        .onScrollPhaseChange { _, phase in
            scrolling = phase != .idle
            if !scrolling && refreshAfterScroll {
                refreshAfterScroll = false
                Task { await load() }
            }
        }
        .onScrollGeometryChange(for: Bool.self) { $0.contentOffset.y + $0.contentInsets.top > 80 } action: { _, collapsed in
            withAnimation(reduceMotion ? nil : PanelsTheme.Motion.fade) { headerCollapsed = collapsed }
        }
        .navigationTitle(headerCollapsed ? chief?.label ?? "Chief of Staff" : "")
        .navigationBarTitleDisplayMode(.inline).panelsScreen()
        .toolbar { ToolbarItem(placement: .topBarTrailing) {
            AgentPageMenu(target: Principal(kind: "chief", id: "chief"), settled: nil, actions: actions) { await store.refresh() }
        } }
        .task(id: store.revision) { await load() }
        .refreshable { await load() }
        .accessibilityIdentifier("chief-screen")
    }

    @ViewBuilder private func loadMoreButton(page: PageFacts, filter: TicketFilter) -> some View {
        if let offset = page.nextOffset {
            Button {
                Task { await loadMore(filter: filter, offset: offset) }
            } label: {
                HStack {
                    Text("Load more")
                    if loadingMore { ProgressView().controlSize(.mini) }
                }.font(PanelsTheme.TypeScale.small).frame(minHeight: 44).frame(maxWidth: .infinity)
            }.disabled(loadingMore)
        }
    }

    private func load() async {
        let api = store.client
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
            if scrolling { refreshAfterScroll = true; return }
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
