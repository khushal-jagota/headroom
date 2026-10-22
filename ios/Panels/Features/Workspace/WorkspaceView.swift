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

    private var motion: Animation? { reduceMotion ? nil : .easeInOut(duration: PanelsTheme.Motion.slow) }

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                ScrollView {
                    VStack(spacing: PanelsTheme.Space.md) {
                        if let error = store.errors["Workspace"] ?? store.errors["Tickets"] ?? store.errors["Chief"] {
                            LoadErrorView(message: error) { await store.refresh() }
                        }
                        if let chiefSignalError {
                            LoadErrorView(message: chiefSignalError) { await refreshSupplementary() }
                        }
                        WorkspaceCard(title: store.chief?.label ?? "Chief of Staff", chief: true,
                                      route: .chief, signal: chiefSignal, groups: groups["chief"] ?? [])
                        ForEach(managers) { manager in card(manager) }
                        if store.managerPage?.nextOffset != nil || store.ticketPage?.nextOffset != nil {
                            Button {
                                Haptics.selection()
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
                    }
                    .padding(.horizontal, PanelsTheme.Space.lg)
                    .padding(.vertical, PanelsTheme.Space.sm)
                }
                .refreshable { Haptics.selection(); await store.refresh() }
                .overlay { if store.isLoading && store.chief == nil { ProgressView().controlSize(.large) } }
                settledSection(maxHeight: geometry.size.height * 0.42)
                    .frame(height: settledOpen ? geometry.size.height * 0.42 + 48 : 48)
                    .clipped()
            }
        }
        .navigationTitle("Workspace")
        .navigationBarTitleDisplayMode(.inline)
        .panelsScreen()
        .onChange(of: store.revision, initial: true) { _, _ in updateGroups() }
        .onChange(of: store.baseURL) { _, _ in
            groups = [:]; managers = []; settled = nil; chiefSignal = nil
            settledError = nil; chiefSignalError = nil; settledOpen = false; settledRequestedOffset = 0
        }
        .task(id: "\(store.baseURL.absoluteString):\(store.revision)") { await refreshSupplementary() }
    }

    private func card(_ manager: ManagerSummary) -> some View {
        WorkspaceCard(title: manager.title, project: manager.project, route: .orchestrator(manager.id),
                      signal: manager.conversationSignal, groups: groups[manager.id] ?? [])
    }

    private func settledSection(maxHeight: CGFloat) -> some View {
        VStack(spacing: 0) {
            Button {
                Haptics.selection()
                withAnimation(motion) { settledOpen.toggle() }
            } label: {
                HStack(spacing: PanelsTheme.Space.md) {
                    Text(settled.map { "Settled (\($0.page.matchCount))" } ?? "Settled")
                        .font(PanelsTheme.TypeScale.small)
                    Rectangle().fill(PanelsTheme.border).frame(height: 1)
                    Image(systemName: "chevron.up").font(PanelsTheme.TypeScale.caption.weight(.semibold))
                        .rotationEffect(.degrees(settledOpen ? 180 : 0))
                }
                .foregroundStyle(PanelsTheme.muted)
                .frame(height: 48).contentShape(Rectangle())
                .padding(.horizontal, PanelsTheme.Space.lg)
            }.buttonStyle(.plain)
                .accessibilityIdentifier("workspace-settled")
                .accessibilityValue(settledOpen ? "Expanded" : "Collapsed")
                .accessibilityHint("Shows settled orchestrators")
            if settledOpen {
                ScrollView {
                    VStack(spacing: PanelsTheme.Space.md) {
                        if let settledError { LoadErrorView(message: settledError) { await refreshSupplementary() } }
                        if let settled {
                            ForEach(settled.managers) { manager in card(manager) }
                            if settled.managers.isEmpty { Text("No settled orchestrators").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint).padding() }
                            if settled.page.nextOffset != nil {
                                Button("Load more settled") {
                                    Haptics.selection()
                                    Task { await loadMoreSettled() }
                                }.frame(minHeight: 44).disabled(settledLoading)
                            }
                        } else if settledError == nil { ProgressView().padding() }
                        if settledLoading { ProgressView() }
                    }.padding(.horizontal, PanelsTheme.Space.lg).padding(.bottom, PanelsTheme.Space.md)
                }.frame(maxHeight: maxHeight)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            } else if settledError != nil {
                Button("Couldn’t load Settled · Retry") {
                    Haptics.selection(); Task { await refreshSupplementary() }
                }.font(PanelsTheme.TypeScale.small).padding(.bottom, PanelsTheme.Space.sm)
            }
        }.background(PanelsTheme.surface)
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
