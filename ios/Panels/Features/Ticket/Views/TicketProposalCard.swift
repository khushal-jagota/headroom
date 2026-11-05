import SwiftUI

struct TicketProposalCard: View {
    let presentation: TicketPresentation
    let proposal: TicketProposal
    let refresh: () async -> Void
    @Environment(AppStore.self) private var store
    @State private var approveOpen = false
    @State private var returnOpen = false
    @State private var scopeOpen = false
    @State private var editing = false
    @State private var draft = ""
    private var label: String { presentation.manifest.fields.first { $0.id == proposal.field }?.label ?? proposal.field }

    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            Button { scopeOpen = true } label: {
                Text("\(label) — for \(presentation.holderLabel(presentation.ticket.ceilingHolder)) to approve")
                    .font(PanelsTheme.TypeScale.heading).foregroundStyle(PanelsTheme.strong).multilineTextAlignment(.leading)
            }.buttonStyle(.plain)
            if editing {
                TextEditor(text: $draft).frame(minHeight: 160).scrollContentBackground(.hidden)
                    .padding(PanelsTheme.Space.sm).background(PanelsTheme.recessed)
                Button("Done editing") { editing = false }
            } else {
                TicketExpandableText(text: draft, emptyText: "")
                    .contextMenu { Button("Edit proposal", systemImage: "pencil") { editing = true } }
            }
            HStack(spacing: PanelsTheme.Space.lg) {
                if proposal.field != "brief" {
                    Button("Send it back") { returnOpen = true }
                        .buttonStyle(ReviewActionStyle())
                        .disabled(presentation.ticket.conversationId == nil)
                }
                Button("Approve") { approveOpen = true }
                    .buttonStyle(ReviewActionStyle(approval: true))
                    .disabled(presentation.manifest.advance?[presentation.ticket.stage] == nil)
            }
            if proposal.field != "brief" && presentation.ticket.conversationId == nil {
                Text("No existing Worker conversation to send a revision to.").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
            }
        }
        .onAppear { draft = proposal.body }
        .sheet(isPresented: $approveOpen) {
            TicketApprovalLimitSheet(presentation: presentation, approval: true) { ceiling, holder in
                guard let ceiling else { return }
                try await store.client.acceptTicketProposal(presentation.ticket, proposal: proposal, ceiling: ceiling, holder: holder, editedBody: draft)
                await refresh()
            }
        }
        .sheet(isPresented: $scopeOpen) {
            TicketApprovalLimitSheet(presentation: presentation) { ceiling, holder in
                try await store.client.setTicketApprovalLimit(presentation.ticket.id, ceiling: ceiling, holder: holder)
                await refresh()
            }
        }
        .sheet(isPresented: $returnOpen) {
            TicketTextEditor(title: "What needs changing", actionTitle: "Send it back", requiresText: true, save: { text in
                try await store.client.returnTicketProposal(presentation.ticket.id, message: text.trimmingCharacters(in: .whitespacesAndNewlines), expectedProposalRevision: proposal.revision)
                await refresh()
            }, text: "")
        }
    }
}
