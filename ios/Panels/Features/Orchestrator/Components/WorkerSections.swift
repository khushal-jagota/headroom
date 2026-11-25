import SwiftUI
import UIKit

struct WorkerSections<T: GroupedWorker>: View {
    let tickets: [T]
    var showHistory = true
    var body: some View {
        VStack(spacing: PanelsTheme.Space.sm) {
            ForEach(WorkerGroup.allCases.filter { showHistory || ($0 != .backlogged && $0 != .done) }) { group in
                let rows = WorkerGroup.ordered(tickets.filter { WorkerGroup.classify($0) == group })
                if !rows.isEmpty || (showHistory && (group == .backlogged || group == .done)) {
                    WorkerDisclosure(title: group.title, count: rows.count, unreadCount: rows.filter { $0.conversationSignal?.messageForOwner == true }.count, initiallyExpanded: group.initiallyExpanded) {
                        WorkerRows(tickets: rows)
                    }
                }
            }
        }
    }
}

struct WorkerDisclosure<Content: View>: View {
    let title: String
    let count: Int
    let unreadCount: Int
    @ViewBuilder let content: () -> Content
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded: Bool

    init(title: String, count: Int, unreadCount: Int = 0, initiallyExpanded: Bool = false, @ViewBuilder content: @escaping () -> Content) {
        self.title = title; self.count = count; self.unreadCount = unreadCount; self.content = content
        _expanded = State(initialValue: initiallyExpanded)
    }

    var body: some View {
        VStack(spacing: 0) {
            Button {
                withAnimation(reduceMotion ? nil : PanelsTheme.Motion.disclosure) { expanded.toggle() }
            } label: {
                (typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: PanelsTheme.Space.sm)) : AnyLayout(HStackLayout(spacing: PanelsTheme.Space.sm))) {
                    Text(title).font(PanelsTheme.TypeScale.sectionLabel).multilineTextAlignment(.leading)
                    if !typeSize.isAccessibilitySize { Spacer(minLength: PanelsTheme.Space.sm) }
                    Text(!expanded && unreadCount > 0 ? "\(unreadCount) unread" : count.formatted()).font(PanelsTheme.TypeScale.small).monospacedDigit().foregroundStyle(PanelsTheme.faint)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .foregroundStyle(PanelsTheme.muted)
                .padding(.horizontal, AgentPageLayout.rowInset).frame(minHeight: 44)
                .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
                .contentShape(Rectangle())
            }.buttonStyle(.plain)
                .accessibilityLabel("\(title), \(count) workers, \(unreadCount) unread")
                .accessibilityHint(expanded ? "Hides workers" : "Shows workers")
                .accessibilityValue(expanded ? "Expanded" : "Collapsed")
                .accessibilityIdentifier("worker-group-\(title)")
            if expanded {
                content().transition(.opacity)
                    .padding(.vertical, PanelsTheme.Space.xs)
            }
        }.clipped()
    }
}

struct WorkerRows<T: GroupedWorker>: View {
    let tickets: [T]
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(AppStore.self) private var store
    var body: some View {
        VStack(spacing: 0) {
            if tickets.isEmpty {
                Text("No workers").textSelection(.enabled).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(PanelsTheme.Space.md)
            }
            ForEach(tickets) { ticket in
                NavigationLink(value: AppRoute.ticket(ticket.id)) {
                    HStack(alignment: .center, spacing: PanelsTheme.Space.md) {
                        TicketMachineLabel(locale: ticket.locale)
                        Text(ticket.title).font(PanelsTheme.TypeScale.body)
                            .foregroundStyle((ticket.awaitingApproval || ticket.awaitingAnswer || ticket.assigned || ticket.agentState == "errored" || ticket.conversationSignal?.messageForOwner == true) ? PanelsTheme.strong : PanelsTheme.faint)
                            .lineLimit(typeSize.isAccessibilitySize ? nil : 2).fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                        TicketActivityMark(locale: ticket.locale, signal: ticket.conversationSignal, completed: ticket.stage == "done" || ticket.stage == "cancelled")
                    }.padding(.horizontal, AgentPageLayout.rowInset).padding(.vertical, PanelsTheme.Space.sm)
                        .frame(minHeight: 44).contentShape(Rectangle())
                }.buttonStyle(.plain).systemCopyText(ticket.title).accessibilityIdentifier("worker-\(ticket.id)")
                    .conversationAccessibility(label: TicketMachine.accessibilityLabel("\(ticket.title), \(WorkerGroup.classify(ticket).title)", locale: ticket.locale, health: store.executorHealth), signal: TicketMachine.signal(ticket.conversationSignal, locale: ticket.locale, health: store.executorHealth, completed: ticket.stage == "done" || ticket.stage == "cancelled"))
            }
        }
    }
}
