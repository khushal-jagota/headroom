import SwiftUI

struct TicketView: View {
    let ticketID: String
    @Environment(AppStore.self) private var store
    @State private var detail: TicketDetail?
    @State private var error: String?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                if let error { LoadErrorView(message: error) { await load() } }
                if let detail {
                    Text(detail.stage.replacingOccurrences(of: "_", with: " ").capitalized).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.accent)
                    Text(detail.recap).textSelection(.enabled)
                } else if error == nil { ProgressView() }
                NavigationLink(value: AppRoute.artifacts(owner: ArtifactOwner(kind: .ticket, id: ticketID), directory: "")) {
                    Label("Artifacts", systemImage: "folder")
                }
                ConversationPlaceholder()
            }.padding(PanelsTheme.Space.lg)
        }.navigationTitle(detail?.title ?? "Ticket").panelsScreen()
            .task(id: store.revision) { await load() }
    }
    private func load() async {
        do { let result = try await store.client.ticket(ticketID); try Task.checkCancellation(); detail = result; error = nil }
        catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}
