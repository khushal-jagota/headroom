import SwiftUI

struct WorkerSections<T: GroupedWorker>: View {
    let tickets: [T]
    var showHistory = true
    var body: some View {
        VStack(spacing: PanelsTheme.Space.sm) {
            ForEach(WorkerGroup.allCases.filter { showHistory || ($0 != .backlogged && $0 != .done) }) { group in
                let rows = WorkerGroup.ordered(tickets.filter { WorkerGroup.classify($0) == group })
                if !rows.isEmpty || (showHistory && (group == .backlogged || group == .done)) {
                    WorkerDisclosure(title: group.title, count: rows.count, initiallyExpanded: group.initiallyExpanded) {
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
    @ViewBuilder let content: () -> Content
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded: Bool

    init(title: String, count: Int, initiallyExpanded: Bool = false, @ViewBuilder content: @escaping () -> Content) {
        self.title = title; self.count = count; self.content = content
        _expanded = State(initialValue: initiallyExpanded)
    }

    var body: some View {
        VStack(spacing: 0) {
            Button {
                Haptics.selection()
                withAnimation(reduceMotion ? nil : .easeInOut(duration: PanelsTheme.Motion.slow)) { expanded.toggle() }
            } label: {
                HStack(spacing: PanelsTheme.Space.sm) {
                    Text(title).font(PanelsTheme.TypeScale.small.weight(.semibold)).multilineTextAlignment(.leading)
                    Spacer(minLength: PanelsTheme.Space.sm)
                    Text(count.formatted()).font(PanelsTheme.TypeScale.small).monospacedDigit().foregroundStyle(PanelsTheme.faint)
                }
                .foregroundStyle(PanelsTheme.muted)
                .padding(.horizontal, PanelsTheme.Space.md).frame(minHeight: 44)
                .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
                .contentShape(Rectangle())
            }.buttonStyle(.plain)
                .accessibilityLabel("\(title), \(count)")
                .accessibilityValue(expanded ? "Expanded" : "Collapsed")
                .accessibilityIdentifier("worker-group-\(title)")
            if expanded {
                content().transition(.opacity.combined(with: .move(edge: .top)))
                    .padding(.vertical, PanelsTheme.Space.xs)
            }
        }.clipped()
    }
}

struct WorkerRows<T: GroupedWorker>: View {
    let tickets: [T]
    var body: some View {
        VStack(spacing: 0) {
            if tickets.isEmpty {
                Text("No workers").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(PanelsTheme.Space.md)
            }
            ForEach(tickets) { ticket in
                NavigationLink(value: AppRoute.ticket(ticket.id)) {
                    HStack(alignment: .center, spacing: PanelsTheme.Space.md) {
                        Text(ticket.title).font(PanelsTheme.TypeScale.body)
                            .foregroundStyle(ticket.stage == "done" ? PanelsTheme.faint : PanelsTheme.text)
                            .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
                        WorkerSignalMark(signal: ticket.conversationSignal)
                    }.padding(.horizontal, PanelsTheme.Space.md).padding(.vertical, PanelsTheme.Space.md)
                        .frame(minHeight: 44).contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityIdentifier("worker-\(ticket.id)")
            }
        }
    }
}

struct WorkerSignalMark: View {
    let signal: ConversationSignal?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        if signal?.messageForOwner == true {
            Circle().fill(PanelsTheme.accent).frame(width: 9, height: 9)
                .accessibilityLabel("Unread message")
        } else if signal?.working == true {
            if reduceMotion {
                Image(systemName: "circle.dotted").font(PanelsTheme.TypeScale.small)
                    .foregroundStyle(PanelsTheme.accent).accessibilityLabel("Working")
            } else {
                ProgressView().controlSize(.mini).tint(PanelsTheme.accent).accessibilityLabel("Working")
            }
        }
    }
}
