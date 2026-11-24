import SwiftUI

struct TicketStepRow: View {
    let field: TicketWorkerType.Field
    let presentation: TicketPresentation
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded = false
    private var complete: Bool { presentation.isComplete(field.id) }
    private var fieldResource: ResourceHandle<TicketFieldBody> { store.resources.handle(TicketResources.field(presentation.ticket.id, field.id)) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                guard complete else { return }
                withAnimation(reduceMotion ? nil : PanelsTheme.Motion.disclosure) { expanded.toggle() }
            } label: {
                HStack(spacing: 10) {
                    TicketStepMark(field: field.id, presentation: presentation)
                    Text(field.label).panelsScaledFont(size: PanelsTheme.Web.bodySize)
                        .foregroundStyle(PanelsTheme.Web.secondary)
                    Spacer(minLength: 0)
                    if let approval = presentation.ticket.fieldApprovals[field.id] {
                        Text(approval.displayLabel).panelsScaledFont(size: PanelsTheme.Web.smallSize)
                            .foregroundStyle(PanelsTheme.Web.tertiary)
                    }
                }.frame(minHeight: 40).padding(.horizontal, 8).contentShape(Rectangle())
            }.buttonStyle(.plain).accessibilityIdentifier("ticket-step-\(field.id)")
                .accessibilityValue(complete ? (expanded ? "Expanded" : "Collapsed") : "")
            if expanded && complete {
                VStack(alignment: .leading, spacing: 8) {
                    if let error = fieldResource.error {
                        LoadErrorView(message: error) { await fieldResource.refresh() }
                    }
                    PanelsMarkdown(source: fieldResource.value?.value ?? presentation.ticket.fieldValues[field.id] ?? "", baseURL: store.baseURL)
                }.padding(.vertical, 12).padding(.horizontal, 16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(PanelsTheme.Web.recessed, in: RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(PanelsTheme.Web.border, lineWidth: 1))
                    .padding(.bottom, 12)
                    .resourceDemand(fieldResource)
            }
        }
    }
}

struct TicketStepMark: View {
    let field: String
    let presentation: TicketPresentation
    private var current: Bool { field == presentation.currentField }
    var body: some View {
        Group {
            if presentation.isComplete(field) {
                Circle().fill(PanelsTheme.Web.success).accessibilityLabel("Complete")
            } else if current {
                Circle().fill(PanelsTheme.Web.accent).accessibilityLabel("Current step")
            } else {
                Circle().strokeBorder(PanelsTheme.Web.tertiary, lineWidth: 2).opacity(0.56)
                    .accessibilityLabel("Not started")
            }
        }.frame(width: 8, height: 8)
    }
}
