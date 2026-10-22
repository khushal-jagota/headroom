import SwiftUI

struct TicketApprovalLimitSheet: View {
    let presentation: TicketPresentation
    let approval: Bool
    let save: (String?, Principal) async throws -> Void
    @State private var busy = false
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss
    @State private var ceiling: String
    @State private var holder: Principal

    init(presentation: TicketPresentation, approval: Bool = false, save: @escaping (String?, Principal) async throws -> Void) {
        self.presentation = presentation
        self.save = save
        self.approval = approval
        _ceiling = State(initialValue: approval ? (presentation.manifest.advance?[presentation.ticket.stage] ?? presentation.ticket.ceiling) : presentation.ticket.ceiling)
        _holder = State(initialValue: presentation.ticket.ceilingHolder)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Approved until") {
                    Picker("Approval limit", selection: $ceiling) {
                        if !approval { Text("No further").tag("none") }
                        if !presentation.ceilingOptions.contains(where: { $0.id == presentation.ticket.ceiling }), presentation.ticket.ceiling != "none" {
                            Text(presentation.ceilingLabel).tag(presentation.ticket.ceiling)
                        }
                        ForEach(approval ? Array(presentation.ceilingOptions.dropFirst()) : presentation.ceilingOptions) { stage in Text(stage.label).tag(stage.id) }
                    }.pickerStyle(.inline).labelsHidden().accessibilityIdentifier("ticket-ceiling-picker")
                }
                Section("Then reviews") {
                    Picker("Reviewer", selection: $holder) {
                        ForEach(presentation.holders, id: \.self) { principal in
                            Text(presentation.holderLabel(principal)).tag(principal)
                        }
                    }.pickerStyle(.segmented).labelsHidden()
                }
                if let error { Text(error).foregroundStyle(PanelsTheme.error) }
            }.disabled(busy).panelsScreen().navigationTitle(approval ? "Approve" : "Approval limit").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { dismiss() }.disabled(busy)
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(approval ? "Approve" : "Save") {
                            guard !busy else { return }
                            busy = true; error = nil; Haptics.light()
                            let changedCeiling = !approval && ceiling == presentation.ticket.ceiling ? nil : ceiling
                            Task {
                                do { try await save(changedCeiling, holder); Haptics.success(); dismiss() }
                                catch { self.error = error.localizedDescription; Haptics.error() }
                                busy = false
                            }
                        }
                        .foregroundStyle(approval ? PanelsTheme.ink : PanelsTheme.accent)
                        .padding(approval ? PanelsTheme.Space.sm : 0)
                        .background(approval ? PanelsTheme.accent : Color.clear, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
                        .disabled(busy || (!approval && ceiling == presentation.ticket.ceiling && holder == presentation.ticket.ceilingHolder))
                    }
                }
        }
        .presentationDragIndicator(.visible)
        .interactiveDismissDisabled(busy)
        .onChange(of: ceiling) { _, _ in Haptics.selection() }
        .onChange(of: holder) { _, _ in Haptics.selection() }
    }
}
