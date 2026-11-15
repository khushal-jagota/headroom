import SwiftUI

/// Retained unreadable work is removed only by an explicit, origin-scoped action.
struct OutgoingStorageErrors: View {
    let server: String
    @State private var rows: [OutgoingStore.Summary] = []
    @State private var discardID: String?
    @State private var error: String?
    private func refresh() {
        do { rows = try LocalStore.shared.outgoing.corruptSummaries(server: server); error = nil }
        catch { self.error = error.localizedDescription }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(rows) { row in
                HStack(alignment: .top) {
                    Text("Saved work from \(row.created.formatted(date: .abbreviated, time: .shortened)) cannot be read. It remains on this device.")
                    Spacer()
                    Button("Discard") { discardID = row.id }
                }
            }
            if let error { Text(error).foregroundStyle(PanelsTheme.error) }
        }
        .font(.footnote)
        .padding(rows.isEmpty && error == nil ? 0 : 8)
        .onChange(of: ReviewSavedWork.Key(server: server, revision: LocalStore.shared.outgoingRevision(server)), initial: true) { _, _ in refresh() }
        .confirmationDialog("Delete this saved work from this device?", isPresented: Binding(get: { discardID != nil }, set: { if !$0 { discardID = nil } }), titleVisibility: .visible) {
            if let id = discardID {
                Button("Delete saved work", role: .destructive) {
                    do {
                        try LocalStore.shared.outgoing.remove(server: server, ids: [id])
                        error = nil; discardID = nil; refresh()
                    } catch { self.error = "Couldn’t delete saved work: \(error.localizedDescription)" }
                }
            }
            Button("Keep saved work", role: .cancel) { discardID = nil }
        }
    }
}
