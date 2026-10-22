import SwiftUI

private enum WorkspaceCardRow: Identifiable {
    case heading(WorkspaceSection)
    case ticket(TicketSummary, WorkspaceSection)
    var id: String {
        switch self {
        case .heading(let section): "heading:\(section.rawValue)"
        case .ticket(let ticket, _): "ticket:\(ticket.id)"
        }
    }
}

struct WorkspaceCard: View {
    let title: String
    var project: String? = nil
    var chief = false
    let route: AppRoute
    let signal: ConversationSignal?
    let groups: [WorkspaceTicketGroup]
    @Environment(\.dynamicTypeSize) private var typeSize

    private static let chiefPortrait: UIImage? = Bundle.main.url(forResource: "workspace-chief", withExtension: "png")
        .flatMap { UIImage(contentsOfFile: $0.path) }

    private var rows: [WorkspaceCardRow] {
        groups.flatMap { group in
            [.heading(group.section)] + group.tickets.map { .ticket($0, group.section) }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            NavigationLink(value: route) {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                    HStack(spacing: PanelsTheme.Space.md) {
                        if chief, let portrait = Self.chiefPortrait {
                            Image(uiImage: portrait).renderingMode(.original).resizable().scaledToFill()
                                .frame(width: 28, height: 28).clipShape(RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
                                .accessibilityHidden(true)
                        } else if let project, !project.isEmpty {
                            Text(project).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                                .lineLimit(1)
                        }
                        Spacer(minLength: PanelsTheme.Space.sm)
                        WorkspaceSignalMark(signal: signal)
                    }
                    Text(title).font(PanelsTheme.TypeScale.heading).foregroundStyle(PanelsTheme.strong)
                        .lineLimit(typeSize.isAccessibilitySize ? nil : 2)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.vertical, PanelsTheme.Space.md)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier(chief ? "workspace-chief" : "workspace-orchestrator")
            ForEach(rows) { row in
                switch row {
                case .heading(let section):
                    Text(section.title.uppercased())
                        .font(PanelsTheme.TypeScale.caption.weight(.semibold))
                        .tracking(0.8).foregroundStyle(PanelsTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, PanelsTheme.Space.sm)
                        .padding(.vertical, PanelsTheme.Space.sm)
                        .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.sm))
                        .padding(.top, PanelsTheme.Space.md)
                        .padding(.bottom, PanelsTheme.Space.xs)
                    .accessibilityAddTraits(.isHeader)
                    .transition(.opacity)
                case .ticket(let ticket, let section):
                    NavigationLink(value: AppRoute.ticket(ticket.id)) {
                        HStack(spacing: PanelsTheme.Space.md) {
                            Text(ticket.title).font(PanelsTheme.TypeScale.body)
                                .foregroundStyle(section.needsAttention ? PanelsTheme.text : PanelsTheme.faint)
                                .lineLimit(typeSize.isAccessibilitySize ? nil : 1)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            WorkspaceSignalMark(signal: ticket.conversationSignal)
                        }
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("workspace-ticket-\(ticket.id)")
                    .accessibilityLabel(ticket.title)
                    .accessibilityValue(ticket.conversationSignal?.messageForOwner == true ? "Unread message" : ticket.conversationSignal?.working == true ? "Working" : "")
                    .transition(.opacity)
                }
            }
        }
        .padding(.horizontal, PanelsTheme.Space.lg)
        .padding(.bottom, groups.isEmpty ? 0 : PanelsTheme.Space.sm)
        .background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
        .overlay(RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg).strokeBorder(PanelsTheme.border))
    }
}

struct WorkspaceSignalMark: View {
    let signal: ConversationSignal?
    var body: some View {
        Group {
            if signal?.messageForOwner == true {
                Circle().fill(PanelsTheme.accent).frame(width: 10, height: 10)
                    .accessibilityLabel("Unread message")
            } else if signal?.working == true {
                ProgressView().controlSize(.mini).tint(PanelsTheme.faint)
                    .accessibilityLabel("Working")
            }
        }.frame(width: 16)
    }
}
