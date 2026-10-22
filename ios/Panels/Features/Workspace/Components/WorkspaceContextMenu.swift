import SwiftUI

struct WorkspaceContextMenu: ViewModifier {
    let route: AppRoute
    let title: String
    let parent: String?
    let ticket: TicketSummary?
    let signal: ConversationSignal?
    let previewChanged: (Bool) -> Void
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    @State private var error: String?
    @State private var sending = false

    func body(content: Content) -> some View {
        content.contextMenu {
            Button("Open", systemImage: "arrow.up.right") { open(route) }
            Button("Ask for a recap", systemImage: "text.alignleft") { perform(.recap) }
                .disabled(sending)
            if let ticket {
                Button(ticket.isBacklogged ? "Resume ticket" : "Backlog ticket", systemImage: "tray") {
                    perform(ticket.isBacklogged ? .resume : .backlog)
                }.disabled(sending)
            } else if case .orchestrator = route {
                Button("Settle", systemImage: "checkmark") { perform(.settle) }.disabled(sending)
            }
        } preview: {
            WorkspacePreview(route: route, title: title, parent: parent, ticket: ticket, signal: signal)
                .environment(store)
                .onAppear { previewChanged(true) }
                .onDisappear { previewChanged(false) }
                .onTapGesture { open(route) }
        }
        .alert("Couldn’t complete action", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("OK", role: .cancel) { error = nil }
        } message: { Text(error ?? "") }
    }

    private func perform(_ action: WorkspaceAction) {
        guard !sending else { return }
        sending = true
        Task {
            defer { sending = false }
            do {
                try await store.client.workspaceAction(action, route: route)
                Haptics.success()
                await store.refresh()
            } catch {
                Haptics.error()
                self.error = error.localizedDescription
            }
        }
    }
}

private struct WorkspacePreview: View {
    let route: AppRoute
    let title: String
    let parent: String?
    let ticket: TicketSummary?
    let signal: ConversationSignal?
    @Environment(AppStore.self) private var store
    @State private var recap: String?
    @State private var stageLine = ""
    @State private var loadError: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let parent { Text(parent).font(.system(size: 13)).foregroundStyle(PanelsTheme.faint) }
            HStack(alignment: .top) {
                Text(title).font(.system(size: 20, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                WorkspaceSignalMark(signal: signal)
            }
            if !stageLine.isEmpty { Text(stageLine).font(.system(size: 15)).foregroundStyle(PanelsTheme.muted) }
            if let recap, !recap.isEmpty {
                Text(recap).font(.system(size: 16)).lineLimit(5)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(12)
                    .background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: 8))
            } else if recap == nil && loadError == nil { ProgressView() }
            if let loadError { Text(loadError).font(.system(size: 13)).foregroundStyle(PanelsTheme.faint) }
        }
        .foregroundStyle(PanelsTheme.strong).padding(20).frame(width: 330, alignment: .leading)
        .background(PanelsTheme.raised)
        .task {
            recap = ticket?.recapPreview
            do {
                switch route {
                case .chief: recap = store.chief?.recap ?? ""
                case .orchestrator(let id): recap = try await store.client.manager(id).recap
                case .ticket(let id):
                    if let ticket {
                        stageLine = try await store.client.workspaceStageSentence(ticket)
                    }
                    recap = try await store.client.ticket(id).recap
                default: break
                }
            } catch { loadError = "Preview couldn’t refresh. \(error.localizedDescription)" }
        }
    }
}
