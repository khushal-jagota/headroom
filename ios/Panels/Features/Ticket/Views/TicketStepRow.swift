import SwiftUI

struct TicketStepRow: View {
    let field: TicketWorkerType.Field
    let presentation: TicketPresentation
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded = false
    private var current: Bool { field.id == presentation.currentField }
    private var complete: Bool { presentation.isComplete(field.id) }

    init(field: TicketWorkerType.Field, presentation: TicketPresentation) {
        self.field = field
        self.presentation = presentation
        _expanded = State(initialValue: field.id == presentation.currentField)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                Haptics.selection()
                withAnimation(reduceMotion ? nil : .easeInOut(duration: PanelsTheme.Motion.base)) { expanded.toggle() }
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: PanelsTheme.Space.md) {
                    Image(systemName: complete ? "checkmark.circle.fill" : current ? "circle.inset.filled" : "circle")
                        .foregroundStyle(complete ? PanelsTheme.success : current ? PanelsTheme.accent : PanelsTheme.faint)
                    Text(field.label).foregroundStyle(current ? PanelsTheme.strong : PanelsTheme.muted)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right").font(PanelsTheme.TypeScale.caption)
                        .rotationEffect(.degrees(expanded ? 90 : 0)).foregroundStyle(PanelsTheme.faint)
                }.font(PanelsTheme.TypeScale.small).frame(minHeight: 48).contentShape(Rectangle())
            }.buttonStyle(.plain).accessibilityValue("\(complete ? "Complete" : current ? "Current" : "Upcoming"), \(expanded ? "Expanded" : "Collapsed")")
            if expanded {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                    if let approval = presentation.ticket.fieldApprovals[field.id] {
                        Text(approval.displayLabel).font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.faint)
                    }
                    let body = presentation.ticket.fieldValues[field.id] ?? ""
                    Text(body.isEmpty ? "Nothing here yet." : body)
                        .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted).textSelection(.enabled)
                }.frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.leading, PanelsTheme.Space.xl).padding(.bottom, PanelsTheme.Space.lg)
            }
        }
    }
}
