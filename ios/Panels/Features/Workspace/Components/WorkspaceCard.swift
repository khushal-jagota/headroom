import SwiftUI

struct WorkspaceCard: View {
    let title: String
    var project: String? = nil
    var chief = false
    let route: AppRoute
    let signal: ConversationSignal?
    let groups: [WorkspaceTicketGroup]
    var goals: [GoalRecord] = []
    var ticketWindow: ViewWindow? = nil
    var onTicketPage: (String, String) -> Void = { _, _ in }
    var goalWindow: ViewWindow? = nil
    var onGoalPage: (String, String) -> Void = { _, _ in }
    var goalRows: [GoalRecord]? = nil
    var goalRowsEnabled: Bool? = nil
    var machines: [WorkspaceMachine] = []
    var machineHealth: [String: WorkspaceMachineHealth] = [:]
    var railNamespace: Namespace.ID? = nil
    var railExitingIDs: Set<String> = []
    var railEnteringIDs: Set<String> = []
    var railEnterVisible = true
    let previewChanged: (Bool) -> Void
    let open: (AppRoute) -> Void
    @AppStorage("panels.goals-sidebar-rows") private var showGoalRows = false
    @Environment(\.dynamicTypeSize) private var typeSize
    private var rowsEnabled: Bool { goalRowsEnabled ?? showGoalRows }
    private var visibleGroups: [WorkspaceTicketGroup] { groups }
    private var ownerID: String {
        if case .orchestrator(let id) = route { return id }
        return "chief"
    }
    private var holder: GoalHolder { chief ? .chief : .manager(ownerID) }
    private var heldGoals: [GoalRecord] { holder.goals(in: goals).filter(\.active) }
    private var tagGoal: GoalRecord? {
        guard !rowsEnabled else { return nil }
        let ranks = ["bad": 0, "attention": 1, "good": 2]
        return heldGoals.filter { ranks[$0.status] != nil }
            .reduce(nil) { current, goal in
                guard let current else { return goal }
                return ranks[goal.status, default: 3] < ranks[current.status, default: 3] ? goal : current
            }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if rowsEnabled {
                WorkspaceGoals(holder: holder, goals: goals, rows: goalRows, window: goalWindow,
                               onPage: onGoalPage, railNamespace: railNamespace, railExitingIDs: railExitingIDs, railEnteringIDs: railEnteringIDs, railEnterVisible: railEnterVisible, previewChanged: previewChanged, open: open)
            }
            ForEach(visibleGroups) { group in
                VStack(alignment: .leading, spacing: 0) {
                    Text(group.section.title.uppercased())
                        .panelsScaledFont(size: PanelsTheme.Web.Rail.headingSize, relativeTo: .caption, weight: .medium)
                        .tracking(0.06 * PanelsTheme.Web.Rail.headingSize)
                        .foregroundStyle(PanelsTheme.Web.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, minHeight: PanelsTheme.Web.Rail.headingLine, alignment: .leading)
                        .padding(.horizontal, PanelsTheme.Web.Rail.textInset)
                        .padding(.bottom, PanelsTheme.Web.Rail.headingGap)
                        .accessibilityAddTraits(.isHeader)
                        .modifier(WorkspaceRailGeometry(id: "heading:\(ownerID):\(group.section.rawValue)", namespace: railNamespace, exiting: railExitingIDs, entering: railEnteringIDs, enterVisible: railEnterVisible))
                    ForEach(group.tickets) { ticket in
                        ticketRow(ticket, section: group.section)
                            .modifier(WorkspaceRailGeometry(id: "ticket:\(ticket.id)", namespace: railNamespace, exiting: railExitingIDs, entering: railEnteringIDs, enterVisible: railEnterVisible))
                    }
                }.padding(.top, PanelsTheme.Web.Rail.groupGap)
            }
            WorkspaceWindowPages(window: ticketWindow, label: "Tickets", onPage: onTicketPage)
                .padding(.horizontal, PanelsTheme.Web.Rail.textInset)
        }
        .padding(.horizontal, PanelsTheme.Web.Rail.padding).padding(.top, PanelsTheme.Web.Rail.padding)
        .padding(.bottom, visibleGroups.isEmpty && (!rowsEnabled || (goalRows ?? heldGoals).isEmpty) ? PanelsTheme.Web.Rail.padding : 10)
        .background(alignment: .top) {
            if let tagGoal {
                LinearGradient(colors: [PanelsTheme.Web.goalTone(tagGoal.status).opacity(tagGoal.status == "good" ? 0.07 : 0.09), .clear], startPoint: .top, endPoint: .bottom)
                    .frame(height: PanelsTheme.Web.Rail.hazeHeight)
            }
        }
        .background(PanelsTheme.Web.rail)
        .clipShape(RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
        .modifier(WorkspaceRailGeometry(id: "card:\(ownerID)", namespace: nil, exiting: railExitingIDs, entering: railEnteringIDs, enterVisible: railEnterVisible))
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !chief || !rowsEnabled {
            HStack(spacing: 8) {
                NavigationLink(value: route) {
                    Text(chief ? "All projects" : project ?? "")
                        .foregroundStyle(PanelsTheme.Web.tertiary)
                        .lineLimit(typeSize.isAccessibilitySize ? nil : 1)
                        .frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                }.buttonStyle(.plain)
                if let tagGoal {
                    NavigationLink(value: AppRoute.goal(tagGoal.id)) {
                        Text(tagLabel(tagGoal.status)).foregroundStyle(PanelsTheme.Web.goalTone(tagGoal.status))
                            .fixedSize(horizontal: true, vertical: false).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                        .accessibilityLabel("Goals: " + tagLabel(tagGoal.status))
                        .accessibilityIdentifier("workspace-goal-status-\(tagGoal.id)")
                }
            }.panelsScaledFont(size: PanelsTheme.Web.smallSize, relativeTo: .caption, weight: .medium)
                .frame(minHeight: 16)
            }
            Button { open(route) } label: {
                HStack(alignment: .top, spacing: 0) {
                    Text(title).panelsScaledFont(size: PanelsTheme.Web.Rail.titleSize, relativeTo: .title3, weight: .semibold)
                        .tracking(-0.014 * PanelsTheme.Web.Rail.titleSize)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, minHeight: PanelsTheme.Web.Rail.titleLine, alignment: .leading)
                    WorkspaceSignalMark(signal: signal).frame(height: PanelsTheme.Web.Rail.titleLine)
                }.offset(y: chief && rowsEnabled ? 0 : 2).contentShape(Rectangle())
            }
            .conversationAccessibility(label: [project, title].compactMap { $0 }.joined(separator: ", "), signal: signal)
            .accessibilityIdentifier(chief ? "workspace-chief" : "workspace-orchestrator")
            .modifier(WorkspaceContextMenu(route: route, title: title, parent: project, ticket: nil,
                signal: signal, goals: heldGoals, machines: machines, machineHealth: machineHealth,
                resting: signal?.messageForOwner == true ? PanelsTheme.Web.strong : PanelsTheme.Web.railTitle, header: true,
                previewChanged: previewChanged, open: open))
        }.padding(.horizontal, PanelsTheme.Web.Rail.textInset)
            .padding(.top, chief && rowsEnabled ? 4 : 7).padding(.bottom, 4).contentShape(Rectangle())
    }

    private func ticketRow(_ ticket: TicketSummary, section: WorkspaceSection) -> some View {
        let machine = WorkspaceMachinePresentation.machine(for: ticket, in: machines)
        let signal = WorkspaceMachineHealth.signal(for: ticket, health: ticket.machineId.flatMap { machineHealth[$0] })
        return Button { open(.ticket(ticket.id)) } label: {
            HStack(alignment: .top, spacing: 0) {
                HStack(spacing: 8) {
                    if machine?.isHome != true && (machine != nil || ticket.machineId != nil || ticket.locale == "macos") {
                        WorkspaceMachineIdentity(machine: machine, locale: ticket.locale, machineID: ticket.machineId, showName: true)
                            .frame(maxWidth: .infinity, alignment: .leading).layoutPriority(-1)
                    }
                    Text(ticket.title).panelsScaledFont(size: PanelsTheme.Web.rowSize)
                        .lineLimit(typeSize.isAccessibilitySize ? nil : 1).truncationMode(.tail)
                        .frame(maxWidth: .infinity, minHeight: PanelsTheme.Web.Rail.rowLine, alignment: .leading)
                        .layoutPriority(1)
                }.frame(maxWidth: .infinity, alignment: .leading)
                WorkspaceSignalMark(signal: signal, approval: ticket.awaitingApproval, agentApproval: ticket.awaitingAgentApproval)
                    .frame(height: PanelsTheme.Web.Rail.rowLine)
            }.padding(.horizontal, PanelsTheme.Web.Rail.textInset).padding(.vertical, 2)
                .frame(minHeight: PanelsTheme.Web.Rail.rowHeight).contentShape(Rectangle())
        }
        .accessibilityIdentifier("workspace-ticket-\(ticket.id)")
        .conversationAccessibility(label: ticket.title + (ticket.awaitingApproval ? ", Needs your approval" : ticket.awaitingAgentApproval && signal?.messageForOwner != true && signal?.working != true ? ", Awaiting an agent's approval" : "") + WorkspaceMachinePresentation.accessibilitySuffix(ticket: ticket, machine: machine, health: ticket.machineId.flatMap { machineHealth[$0] }), signal: signal)
        .modifier(WorkspaceContextMenu(route: .ticket(ticket.id), title: ticket.title, parent: title,
            ticket: ticket, signal: signal, machines: machines, machineHealth: machineHealth,
            previewChanged: previewChanged, open: open))
    }
    private func tagLabel(_ status: String) -> String {
        switch status { case "bad": "Off track"; case "attention": "At risk"; default: "On track" }
    }
}

struct WorkspaceWindowPages: View {
    let window: ViewWindow?
    var label = "Rows"
    let onPage: (String, String) -> Void
    var body: some View {
        if let window, window.previous != nil || window.next != nil {
            HStack(spacing: 8) {
                Button("Previous") { if let anchor = window.previous { onPage(anchor, "before") } }.disabled(window.previous == nil)
                Text("\(window.members.count) of \(window.total)").monospacedDigit()
                Button("Next") { if let anchor = window.next { onPage(anchor, "after") } }.disabled(window.next == nil)
            }.panelsScaledFont(size: PanelsTheme.Web.smallSize, relativeTo: .caption)
                .foregroundStyle(PanelsTheme.Web.tertiary).buttonStyle(.bordered)
                .padding(.vertical, 8).accessibilityElement(children: .contain)
                .accessibilityLabel(label + " pages")
        }
    }
}

struct WorkspaceRailFramesPreferenceKey: PreferenceKey {
    static let defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, newest in newest })
    }
}
struct WorkspaceRailGeometry: ViewModifier {
    let id: String
    let namespace: Namespace.ID?
    var exiting: Set<String> = []
    var entering: Set<String> = []
    var enterVisible = true
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func body(content: Content) -> some View {
        Group {
            if let namespace { content.matchedGeometryEffect(id: id, in: namespace) }
            else { content }
        }
        .opacity(!reduceMotion && (exiting.contains(id) || (entering.contains(id) && !enterVisible)) ? 0 : 1)
        .offset(y: !reduceMotion && entering.contains(id) && !enterVisible ? -4 : 0)
        .animation(reduceMotion ? nil : PanelsTheme.Web.Rail.Motion.leave, value: exiting.contains(id))
        .animation(reduceMotion ? nil : PanelsTheme.Web.Rail.Motion.enter.delay((id.hasPrefix("ticket:") || id.hasPrefix("goal:")) ? PanelsTheme.Web.Rail.Motion.fadeDelay : 0), value: enterVisible)
        .background(GeometryReader { geometry in
            Color.clear.preference(key: WorkspaceRailFramesPreferenceKey.self, value: [id: geometry.frame(in: .named("workspace-rail"))])
        })
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
                Image(systemName: "tray").font(.system(size: 14)).foregroundStyle(PanelsTheme.Web.tertiary).accessibilityLabel("Awaiting an agent's approval")
            } else if signal?.messageForOwner == true {
                Circle().fill(PanelsTheme.Web.accent).frame(width: 8, height: 8).accessibilityLabel("Unread message")
            } else if signal?.working == true {
                RingSpinner(size: .compact, color: PanelsTheme.Web.tertiary)
                    .scaleEffect(10.0 / 14.0).frame(width: 10, height: 10).accessibilityLabel("Working")
            }
        }.frame(width: PanelsTheme.Web.Rail.markColumn, height: 14, alignment: .trailing)
    }
}

enum WorkspaceMachinePresentation {
    static func machine(for ticket: TicketSummary, in machines: [WorkspaceMachine]) -> WorkspaceMachine? {
        machines.first { machine in ticket.machineId.map { $0 == machine.machineId } ?? (machine.isHome == (ticket.locale != "macos")) }
    }
    static func accessibilitySuffix(ticket: TicketSummary, machine: WorkspaceMachine?, health: WorkspaceMachineHealth?) -> String {
        let name = machine?.name ?? (ticket.machineId == nil ? (TicketMachine.name(ticket.locale) ?? "Machine unavailable") : "Machine unavailable")
        let activity = health?.activityLabel(for: ticket)?.replacingOccurrences(of: "the Mac", with: name)
        return ", Runs on " + name + (activity.map { ", " + $0 } ?? "")
    }
    static func symbol(_ icon: String?, locale: String?) -> String {
        switch icon ?? (locale == "macos" ? "laptop" : "server") {
        case "laptop": "laptopcomputer"
        case "desktop": "desktopcomputer"
        case "cloud": "cloud"
        default: "server.rack"
        }
    }
    static func color(_ name: String?) -> Color {
        switch name {
        case "jade": PanelsTheme.Web.Machine.jade
        case "orchid": PanelsTheme.Web.Machine.orchid
        case "sand": PanelsTheme.Web.Machine.sand
        case "cyan": PanelsTheme.Web.Machine.cyan
        case "rose": PanelsTheme.Web.Machine.rose
        case "plum": PanelsTheme.Web.Machine.plum
        case "lime": PanelsTheme.Web.Machine.lime
        case "stone": PanelsTheme.Web.Machine.stone
        default: PanelsTheme.Web.tertiary
        }
    }
}
struct WorkspaceMachineIdentity: View {
    let machine: WorkspaceMachine?
    let locale: String?
    var machineID: String? = nil
    var showName = false
    private var name: String { machine?.name ?? (machineID != nil ? "Machine unavailable" : TicketMachine.name(locale) ?? "VPS") }
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: WorkspaceMachinePresentation.symbol(machine?.icon, locale: locale))
                .font(.system(size: 14)).foregroundStyle(WorkspaceMachinePresentation.color(machine?.color))
            if showName { Text(name)
                .panelsScaledFont(size: PanelsTheme.Web.rowSize).foregroundStyle(PanelsTheme.Web.secondary).lineLimit(1) }
        }.accessibilityLabel("Runs on " + name)
    }
}
