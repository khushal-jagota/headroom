import SwiftUI

/// Changes the approval limit and reviewer of a Ticket with no proposal waiting.
struct TicketApprovalLimitSheet: View {
    let presentation: TicketPresentation
    let save: (String?, Principal) async throws -> Void
    @State private var action = SheetAction()
    @State private var ceiling: String
    @State private var holder: Principal
    @Environment(\.dismiss) private var dismiss

    init(presentation: TicketPresentation, save: @escaping (String?, Principal) async throws -> Void) {
        self.presentation = presentation
        self.save = save
        let current = presentation.ticket.ceiling
        _ceiling = State(initialValue: presentation.ceilingOptions.contains { $0.id == current } || current == "none" ? current : presentation.ceilingOptions.first?.id ?? "done")
        _holder = State(initialValue: presentation.holders.contains(presentation.ticket.ceilingHolder) ? presentation.ticket.ceilingHolder : Principal(kind: "owner", id: "owner"))
    }

    private var choices: [CeilingChoice] {
        [CeilingChoice(id: "none", label: "No further")] + presentation.ceilingOptions.map { CeilingChoice(id: $0.id, label: $0.label) }
    }
    private var unchanged: Bool { ceiling == presentation.ticket.ceiling && holder == presentation.ticket.ceilingHolder }

    var body: some View {
        SheetForm(title: "Approval limit", confirmTitle: "Save", canConfirm: !unchanged, action: action, confirm: {
            let changedCeiling = ceiling == presentation.ticket.ceiling ? nil : ceiling
            Task { if await action.run({ try await save(changedCeiling, holder) }) { dismiss() } }
        }) {
            ApprovalSections(ticket: presentation.ticket, choices: choices, ceiling: $ceiling, holder: $holder)
        }.presentationDetents([.medium, .large])
    }
}
