import SwiftUI

struct TicketTextEditor: View {
    let title: String
    var actionTitle = "Save"
    var requiresText = false
    let save: (String) async throws -> Void
    @State var text: String
    @State private var error: String?
    @State private var busy = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading) {
                TextEditor(text: $text).scrollContentBackground(.hidden).padding(PanelsTheme.Space.md)
                    .background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
                if let error { Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error) }
            }.padding(PanelsTheme.Space.lg).panelsScreen()
                .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(busy) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(actionTitle) {
                            guard !busy else { return }
                            busy = true; error = nil; Haptics.light()
                            Task {
                                do { try await save(text); Haptics.success(); dismiss() }
                                catch { self.error = error.localizedDescription; Haptics.error() }
                                busy = false
                            }
                        }.disabled(busy || (requiresText && text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty))
                    }
                }
        }.interactiveDismissDisabled(busy)
    }
}
