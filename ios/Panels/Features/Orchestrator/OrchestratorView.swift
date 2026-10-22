import SwiftUI

struct OrchestratorView: View {
    let managerID: String
    @Environment(AppStore.self) private var store
    @State private var detail: ManagerDetail?
    @State private var error: String?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                if let error { LoadErrorView(message: error) { await load() } }
                if let detail {
                    Text(detail.recap).textSelection(.enabled)
                    if !detail.goal.isEmpty { Text(detail.goal).foregroundStyle(PanelsTheme.muted) }
                    ForEach(detail.tickets) { ticket in NavigationLink(ticket.title, value: AppRoute.ticket(ticket.id)) }
                } else if error == nil { ProgressView() }
                NavigationLink(value: AppRoute.artifacts(owner: ArtifactOwner(kind: .manager, id: managerID), directory: "")) {
                    Label("Artifacts", systemImage: "folder")
                }
                ConversationPlaceholder()
            }.padding(PanelsTheme.Space.lg)
        }.navigationTitle(detail?.title ?? "Orchestrator").panelsScreen()
            .task(id: store.revision) { await load() }
    }
    private func load() async {
        do { let result = try await store.client.manager(managerID); try Task.checkCancellation(); detail = result; error = nil }
        catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}
