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
    /// Active tickets beyond the rows this card loaded; the page shows them all.
    var hiddenCount = 0
    var goals: [GoalRecord] = []
    let previewChanged: (Bool) -> Void
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var typeSize
    private var visibleGroups: [WorkspaceTicketGroup] { WorkspaceGrouping.focused(groups) }
    private var holder: GoalHolder? {
        if chief { return .chief }
        if case .orchestrator(let id) = route { return .manager(id) }
        return nil
    }
    private var tagGoal: GoalRecord? {
        guard let holder else { return nil }
        let ranks = ["bad": 0, "attention": 1, "good": 2]
        return holder.goals(in: goals).filter { $0.active && ranks[$0.status] != nil }
            .min { ranks[$0.status, default: 3] < ranks[$1.status, default: 3] }
    }
    private var rows: [WorkspaceCardRow] {
        visibleGroups.flatMap { group in
            [.heading(group.section)] + group.tickets.map { .ticket($0, group.section) }
        }
    }
    private func tone(_ status: String) -> Color {
        switch status {
        case "bad": PanelsTheme.Web.error
        case "attention": PanelsTheme.Web.attention
        default: PanelsTheme.Web.success
        }
    }
    private func tagLabel(_ status: String) -> String {
        switch status {
        case "bad": "Off track"
        case "attention": "At risk"
        default: "On track"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            NavigationLink(value: route) {
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 8) {
                        Text(chief ? "All projects" : project ?? "")
                            .foregroundStyle(PanelsTheme.Web.tertiary)
                            .lineLimit(typeSize.isAccessibilitySize ? nil : 1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if let tagGoal {
                            Text(tagLabel(tagGoal.status)).foregroundStyle(tone(tagGoal.status))
                                .fixedSize(horizontal: true, vertical: false)
                        }
                    }.panelsScaledFont(size: PanelsTheme.Web.smallSize, relativeTo: .caption, weight: .medium)
                        .frame(minHeight: 16)
                    HStack(alignment: .top, spacing: 8) {
                        Text(title).panelsScaledFont(size: PanelsTheme.Web.Rail.titleSize, relativeTo: .title3, weight: .semibold)
                            .tracking(-0.014 * PanelsTheme.Web.Rail.titleSize)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, minHeight: PanelsTheme.Web.Rail.titleLine, alignment: .leading)
                        WorkspaceSignalMark(signal: signal).frame(height: PanelsTheme.Web.Rail.titleLine)
                    }
                }.padding(.horizontal, PanelsTheme.Web.Rail.textInset)
                    .padding(.top, 7).padding(.bottom, 4).contentShape(Rectangle())
            }
            .buttonStyle(WorkspaceRailButtonStyle(resting: signal?.messageForOwner == true ? PanelsTheme.Web.strong : PanelsTheme.Web.railTitle, header: true))
            .conversationAccessibility(label: [project, title].compactMap { $0 }.joined(separator: ", "), signal: signal)
            .accessibilityIdentifier(chief ? "workspace-chief" : "workspace-orchestrator")
            .modifier(WorkspaceContextMenu(route: route, title: title, parent: project, ticket: nil,
                                           signal: signal, previewChanged: previewChanged, open: open))
            if let holder, holder.goals(in: goals).contains(where: \.active) {
                WorkspaceGoals(holder: holder, goals: goals, open: open)
                    .padding(.horizontal, PanelsTheme.Web.Rail.textInset).padding(.top, 8)
            }
            ForEach(rows) { row in
                switch row {
                case .heading(let section):
                    Text(section.title.uppercased())
                        .panelsScaledFont(size: PanelsTheme.Web.Rail.headingSize, relativeTo: .caption, weight: .medium)
                        .tracking(0.06 * PanelsTheme.Web.Rail.headingSize)
                        .foregroundStyle(PanelsTheme.Web.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, minHeight: PanelsTheme.Web.Rail.headingLine, alignment: .leading)
                        .padding(.horizontal, PanelsTheme.Web.Rail.textInset)
                        .padding(.top, PanelsTheme.Web.Rail.groupGap)
                        .padding(.bottom, PanelsTheme.Web.Rail.headingGap)
                        .accessibilityAddTraits(.isHeader)
                        .transition(.opacity)
                case .ticket(let ticket, _):
                    NavigationLink(value: AppRoute.ticket(ticket.id)) {
                        HStack(spacing: 8) {
                            Text(ticket.title).panelsScaledFont(size: PanelsTheme.Web.rowSize)
                                .lineLimit(1).truncationMode(.tail)
                                .frame(maxWidth: .infinity, minHeight: PanelsTheme.Web.Rail.rowLine, alignment: .leading)
                            WorkspaceSignalMark(signal: TicketMachine.signal(ticket.conversationSignal, locale: ticket.locale,
                                health: store.executorHealth, completed: ticket.stage == "done" || ticket.stage == "cancelled"),
                                approval: ticket.awaitingApproval, agentApproval: ticket.awaitingAgentApproval)
                        }.padding(.horizontal, PanelsTheme.Web.Rail.textInset).padding(.vertical, 2)
                            .frame(minHeight: PanelsTheme.Web.Rail.rowHeight).contentShape(Rectangle())
                    }
                    .buttonStyle(WorkspaceRailButtonStyle())
                    .accessibilityIdentifier("workspace-ticket-\(ticket.id)")
                    .conversationAccessibility(label: ticket.title, signal: TicketMachine.signal(ticket.conversationSignal, locale: ticket.locale, health: store.executorHealth, completed: ticket.stage == "done" || ticket.stage == "cancelled"))
                    .modifier(WorkspaceContextMenu(route: .ticket(ticket.id), title: ticket.title,
                                                   parent: title, ticket: ticket, signal: ticket.conversationSignal,
                                                   previewChanged: previewChanged, open: open))
                    .transition(.opacity)
                }
            }
            if hiddenCount > 0 {
                NavigationLink(value: route) {
                    Text("+\(hiddenCount) more").panelsScaledFont(size: 16).foregroundStyle(PanelsTheme.faint)
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("workspace-more")
            }
        }
        .padding(.horizontal, PanelsTheme.Web.Rail.padding).padding(.top, PanelsTheme.Web.Rail.padding)
        .padding(.bottom, 10)
        .background(alignment: .top) {
            if let tagGoal {
                LinearGradient(colors: [tone(tagGoal.status).opacity(tagGoal.status == "good" ? 0.07 : 0.09), .clear], startPoint: .top, endPoint: .bottom)
                    .frame(height: PanelsTheme.Web.Rail.hazeHeight)
            }
        }
        .background(PanelsTheme.Web.rail)
        .clipShape(RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
    }
}

struct WorkspaceRailButtonStyle: ButtonStyle {
    var resting = PanelsTheme.Web.railRow
    var header = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(configuration.isPressed ? PanelsTheme.Web.strong : resting)
            .background(configuration.isPressed ? (header ? PanelsTheme.Web.hover : PanelsTheme.Web.selected) : .clear,
                        in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
    }
}

private struct WorkspaceSignalMark: View {
    let signal: ConversationSignal?
    var approval = false
    var agentApproval = false
    var body: some View {
        Group {
            if approval && signal?.messageForOwner != true {
                Image(systemName: "tray").font(.system(size: 14)).foregroundStyle(PanelsTheme.Web.accent)
                    .accessibilityLabel("Needs your approval")
            } else if agentApproval && signal?.messageForOwner != true && signal?.working != true {
                Image(systemName: "tray").font(.system(size: 14)).foregroundStyle(PanelsTheme.Web.tertiary)
                    .accessibilityLabel("Awaiting an agent's approval")
            } else if signal?.messageForOwner == true {
                Circle().fill(PanelsTheme.Web.accent).frame(width: 8, height: 8).accessibilityLabel("Unread message")
            } else if signal?.working == true {
                RingSpinner(size: .compact, color: PanelsTheme.Web.accent)
                    .scaleEffect(10.0 / 14.0).frame(width: 10, height: 10).accessibilityLabel("Working")
            }
        }.frame(width: PanelsTheme.Web.Rail.markColumn, height: 14, alignment: .trailing)
    }
}
