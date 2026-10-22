import SwiftUI

private enum WorkspaceCardRow: Identifiable {
    case heading(WorkspaceSection)
    case ticket(TicketSummary, WorkspaceSection)
    var id: String {
        switch self {
        case .heading(let section): "heading:\(section.rawValue)"
        case .ticket(let ticket, let section): "ticket:\(section.rawValue):\(ticket.id)"
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
    let previewChanged: (Bool) -> Void
    let open: (AppRoute) -> Void
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
                VStack(alignment: .leading, spacing: 2) {
                    if !chief {
                        HStack {
                            Text(project ?? "").panelsScaledFont(size: 13).foregroundStyle(PanelsTheme.faint)
                                .lineLimit(typeSize.isAccessibilitySize ? nil : 1)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 8)
                            WorkspaceSignalMark(signal: signal)
                        }
                    }
                    HStack(spacing: 10) {
                        if chief, let portrait = Self.chiefPortrait {
                            Image(uiImage: portrait).renderingMode(.original).resizable().scaledToFill()
                                .frame(width: 28, height: 28).clipShape(RoundedRectangle(cornerRadius: 6))
                                .accessibilityHidden(true)
                        }
                        Text(title).panelsScaledFont(size: 20, relativeTo: .title3, weight: .semibold)
                            .foregroundStyle(signal?.messageForOwner == true ? PanelsTheme.strong : PanelsTheme.faint)
                            .lineLimit(typeSize.isAccessibilitySize ? nil : 2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if chief { WorkspaceSignalMark(signal: signal) }
                    }
                }.frame(minHeight: 44, alignment: .top).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .conversationAccessibility(label: [project, title].compactMap { $0 }.joined(separator: ", "), signal: signal)
            .accessibilityIdentifier(chief ? "workspace-chief" : "workspace-orchestrator")
            .modifier(WorkspaceContextMenu(route: route, title: title, parent: project, ticket: nil,
                                           signal: signal, previewChanged: previewChanged, open: open))
            ForEach(rows) { row in
                switch row {
                case .heading(let section):
                    Text(section.title)
                        .panelsScaledFont(size: 11, relativeTo: .caption2, weight: .semibold)
                        .foregroundStyle(PanelsTheme.faint)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 8)
                        .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: 4))
                        .padding(.top, section == groups.first?.section ? 16 : 8)
                        .accessibilityAddTraits(.isHeader)
                        .transition(.opacity)
                case .ticket(let ticket, let section):
                    NavigationLink(value: AppRoute.ticket(ticket.id)) {
                        HStack(spacing: 12) {
                            Text(ticket.title).panelsScaledFont(size: 16)
                                .foregroundStyle(section.needsAttention || ticket.conversationSignal?.messageForOwner == true ? PanelsTheme.strong : PanelsTheme.faint)
                                .lineLimit(typeSize.isAccessibilitySize ? nil : 1)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            WorkspaceSignalMark(signal: ticket.conversationSignal)
                        }.frame(minHeight: 44).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("workspace-ticket-\(ticket.id)")
                    .conversationAccessibility(label: ticket.title, signal: ticket.conversationSignal)
                    .modifier(WorkspaceContextMenu(route: .ticket(ticket.id), title: ticket.title,
                                                   parent: title, ticket: ticket, signal: ticket.conversationSignal,
                                                   previewChanged: previewChanged, open: open))
                    .transition(.opacity)
                }
            }
        }
        .padding(.horizontal, 16).padding(.top, 16).padding(.bottom, groups.isEmpty ? 16 : 2)
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(PanelsTheme.border))
    }
}

struct WorkspaceSignalMark: View {
    let signal: ConversationSignal?
    var body: some View {
        HStack(spacing: 4) {
            if signal?.working == true {
                ProgressView().controlSize(.mini).tint(PanelsTheme.faint)
                    .accessibilityLabel("Working")
            }
            if signal?.messageForOwner == true {
                Circle().fill(PanelsTheme.accent).frame(width: 8, height: 8)
                    .accessibilityLabel("Unread message")
            }
        }.frame(minWidth: 20, alignment: .trailing)
            .animation(.easeInOut(duration: 0.15), value: signal?.messageForOwner)
            .animation(.easeInOut(duration: 0.15), value: signal?.working)
    }
}
