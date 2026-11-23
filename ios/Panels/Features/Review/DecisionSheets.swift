import SwiftUI

struct CeilingChoice: Identifiable, Hashable { let id: String; let label: String }

/// "Approved until" as a checkmark list and "Then reviews" as a segmented control.
struct ApprovalSections: View {
    let ticket: TicketDetail
    let choices: [CeilingChoice]
    @Binding var ceiling: String
    @Binding var holder: Principal

    var body: some View {
        Section("Approved until") {
            ForEach(choices) { choice in
                CheckRow(label: choice.label, selected: choice.id == ceiling) { ceiling = choice.id; Haptics.selection() }
                    .accessibilityIdentifier("decision.stage.\(choice.id)")
            }
        }
        Section(ceiling == "done" ? "Ceiling holder" : "Then reviews") {
            Picker("Reviewer", selection: $holder) {
                ForEach(ticket.holders, id: \.self) { Text(ticket.holderLabel($0)).tag($0) }
            }.pickerStyle(.segmented).labelsHidden().accessibilityIdentifier("decision.holder")
        }
    }
}

struct DecisionApproveSheet: View {
    let ticket: TicketDetail
    let choices: [CeilingChoice]
    let note: String?
    let confirm: (String, Principal) async throws -> Void
    @State private var ceiling: String
    @State private var holder: Principal
    @State private var action = SheetAction()
    @Environment(\.dismiss) private var dismiss

    init(ticket: TicketDetail, choices: [CeilingChoice], note: String?, confirm: @escaping (String, Principal) async throws -> Void) {
        self.ticket = ticket; self.choices = choices; self.note = note; self.confirm = confirm
        _ceiling = State(initialValue: choices.first?.id ?? "done")
        _holder = State(initialValue: ticket.holders.contains(ticket.ceilingHolder) ? ticket.ceilingHolder : Principal(kind: "owner", id: "owner"))
    }

    var body: some View {
        SheetForm(title: "Approve", confirmTitle: "Approve", action: action, confirm: {
            Task { if await action.run({ try await confirm(ceiling, holder) }) { dismiss() } }
        }) {
            ApprovalSections(ticket: ticket, choices: choices, ceiling: $ceiling, holder: $holder)
            if let note, !note.isEmpty {
                Section("Note to Worker") { Text(note).accessibilityIdentifier("decision.note") }
            }
        }
    }
}

struct DecisionReviewerSheet: View {
    let ticket: TicketDetail
    let save: (Principal) async throws -> Void
    @State private var chosen: Principal?
    @State private var action = SheetAction()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        SheetForm(title: "Reviewer", action: action) {
            Section("Who reviews this stage") {
                ForEach(ticket.holders, id: \.self) { principal in
                    CheckRow(label: ticket.holderLabel(principal), selected: principal == ticket.ceilingHolder,
                             busy: action.busy && chosen == principal) { choose(principal) }
                        .accessibilityIdentifier("decision.reviewer.\(principal.kind)")
                }
            }
        }
    }

    private func choose(_ principal: Principal) {
        guard principal != ticket.ceilingHolder else { dismiss(); return }
        chosen = principal
        Task { if await action.run({ try await save(principal) }) { dismiss() } }
    }
}

enum DecisionSheet: String, Identifiable { case approve, reviewer; var id: String { rawValue } }

extension View {
    /// The one decision control: Review and the Ticket page present the same two sheets.
    func decisionSheets(_ sheet: Binding<DecisionSheet?>, ticket: TicketDetail?, manifest: ReviewWorkerManifest?, note: String?,
                        onDismiss: @escaping () -> Void,
                        approve: @escaping (String, Principal) async throws -> Void,
                        setReviewer: @escaping (Principal) async throws -> Void) -> some View {
        self.sheet(item: sheet, onDismiss: onDismiss) { kind in
            if let ticket, let manifest {
                switch kind {
                case .approve:
                    DecisionApproveSheet(ticket: ticket,
                        choices: manifest.approvalStages(after: ticket.stage).map { CeilingChoice(id: $0.id, label: $0.label) },
                        note: note, confirm: approve).presentationDetents([.medium, .large])
                case .reviewer:
                    DecisionReviewerSheet(ticket: ticket, save: setReviewer).presentationDetents([.medium])
                }
            }
        }
    }
}
