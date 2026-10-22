import SwiftUI

struct WorkspaceView: View {
    @Environment(AppStore.self) private var store
    private var chiefTickets: [TicketSummary] { store.tickets.filter { $0.manager?.kind == "chief" } }
    private var otherTickets: [TicketSummary] {
        let shown = Set(store.managers.map(\.id))
        return store.tickets.filter { $0.manager?.kind != "chief" && !shown.contains($0.managerId ?? "") }
    }
    var body: some View {
        List {
            if let error = store.errors["Workspace"] ?? store.errors["Tickets"] ?? store.errors["Chief"] {
                LoadErrorView(message: error) { await store.refresh() }.listRowBackground(PanelsTheme.raised)
            }
            Section {
                NavigationLink(value: AppRoute.chief) {
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                        Label(store.chief?.label ?? "Chief of Staff", systemImage: "sparkle").font(PanelsTheme.TypeScale.heading)
                        if let recap = store.chief?.recap, !recap.isEmpty {
                            Text(recap).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted).lineLimit(3)
                        }
                    }.padding(.vertical, PanelsTheme.Space.sm)
                }
                ForEach(chiefTickets) { ticket in ticketRow(ticket) }
            }.listRowBackground(PanelsTheme.raised)
            ForEach(store.managers) { manager in
                Section {
                    NavigationLink(value: AppRoute.orchestrator(manager.id)) {
                        VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                            Text(manager.title).font(PanelsTheme.TypeScale.heading)
                            Text(manager.project).font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.muted)
                        }.padding(.vertical, PanelsTheme.Space.sm)
                    }
                    ForEach(store.tickets.filter { $0.managerId == manager.id }) { ticket in ticketRow(ticket) }
                }.listRowBackground(PanelsTheme.raised)
            }
            if !otherTickets.isEmpty {
                Section("Other active tickets") { ForEach(otherTickets) { ticket in ticketRow(ticket) } }.listRowBackground(PanelsTheme.raised)
            }
            if store.managerPage?.nextOffset != nil || store.ticketPage?.nextOffset != nil {
                Button("Load more") { Haptics.light(); Task { await store.loadMore() } }.listRowBackground(PanelsTheme.raised)
            }
        }
        .overlay { if store.isLoading { ProgressView().controlSize(.large) } }
        .navigationTitle("Workspace").panelsScreen()
        .refreshable { await store.refresh() }
    }
    private func ticketRow(_ ticket: TicketSummary) -> some View {
        NavigationLink(value: AppRoute.ticket(ticket.id)) {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                Text(ticket.title).font(PanelsTheme.TypeScale.body)
                Text(ticket.stage.replacingOccurrences(of: "_", with: " ").capitalized)
                    .font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.muted)
            }.padding(.vertical, PanelsTheme.Space.xs)
        }
    }
}
