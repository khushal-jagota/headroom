import SwiftUI

struct TicketApprovalLimitSheet: View {
    let presentation: TicketPresentation
    let save: (String?, Principal) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var ceiling: String
    @State private var holder: Principal

    init(presentation: TicketPresentation, save: @escaping (String?, Principal) async -> Void) {
        self.presentation = presentation
        self.save = save
        _ceiling = State(initialValue: presentation.ticket.ceiling)
        _holder = State(initialValue: presentation.ticket.ceilingHolder)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Approved until") {
                    Picker("Approval limit", selection: $ceiling) {
                        Text("No further").tag("none")
                        if !presentation.ceilingOptions.contains(where: { $0.id == presentation.ticket.ceiling }), presentation.ticket.ceiling != "none" {
                            Text(presentation.ceilingLabel).tag(presentation.ticket.ceiling)
                        }
                        ForEach(presentation.ceilingOptions) { stage in Text(stage.label).tag(stage.id) }
                    }.pickerStyle(.inline).labelsHidden().accessibilityIdentifier("ticket-ceiling-picker")
                }
                Section("Then reviews") {
                    Picker("Reviewer", selection: $holder) {
                        ForEach(presentation.holders, id: \.self) { principal in
                            Text(presentation.holderLabel(principal)).tag(principal)
                        }
                    }.pickerStyle(.inline).labelsHidden()
                }
            }.panelsScreen().navigationTitle("Approval limit").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { Haptics.light(); dismiss() }
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Save") {
                            Haptics.medium()
                            let changedCeiling = ceiling == presentation.ticket.ceiling ? nil : ceiling
                            dismiss()
                            Task { await save(changedCeiling, holder) }
                        }.disabled(ceiling == presentation.ticket.ceiling && holder == presentation.ticket.ceilingHolder)
                    }
                }
        }
        .presentationDragIndicator(.visible)
        .onChange(of: ceiling) { _, _ in Haptics.selection() }
        .onChange(of: holder) { _, _ in Haptics.selection() }
    }
}
