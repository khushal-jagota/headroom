import SwiftUI

struct GoalChecklist: View {
    let block: GoalJSON
    let session: GoalSession
    var supplementary = false
    var preview = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var opened = true
    @State private var expanded: String?
    @State private var completedOpen = false
    @State private var held: Set<String> = []
    @State private var optimistic: [String: Bool] = [:]
    private var items: [GoalJSON] { block["items"].array }
    private var doneCount: Int { items.filter { $0["state"].text == "done" }.count }
    private var allDone: Bool { !items.isEmpty && doneCount == items.count }
    private var complete: [GoalJSON] { items.filter { $0["state"].text == "done" && !held.contains($0.id) } }
    private var remaining: [GoalJSON] { items.filter { $0["state"].text != "done" || held.contains($0.id) } }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(reduceMotion ? nil : .easeOut(duration: PanelsTheme.Goal.bodyCollapse)) { opened.toggle() }
            } label: {
                HStack(spacing: 12) {
                    ZStack {
                        Circle().stroke(PanelsTheme.Goal.meter, lineWidth: 2)
                        Circle().trim(from: 0, to: items.isEmpty ? 0 : Double(doneCount) / Double(items.count)).stroke(allDone ? PanelsTheme.success : PanelsTheme.muted, style: .init(lineWidth: 2, lineCap: .round)).rotationEffect(.degrees(-90))
                            .animation(reduceMotion ? nil : PanelsTheme.Goal.standard, value: doneCount)
                        ChecklistTick().trim(from: 0, to: allDone ? 1 : 0).stroke(PanelsTheme.success, style: .init(lineWidth: 1.5, lineCap: .round, lineJoin: .round)).frame(width: 24, height: 24)
                            .animation(reduceMotion ? nil : PanelsTheme.Goal.standard, value: allDone)
                    }.frame(width: 32, height: 32)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(allDone ? block["doneTitle"].string ?? "All items done" : block["title"].string ?? "Checklist")
                            .id(allDone).panelsScaledFont(size: 17, weight: .medium).foregroundStyle(PanelsTheme.strong).transition(.asymmetric(insertion: .offset(y: reduceMotion ? 0 : 6).combined(with: .opacity).animation(.easeOut(duration: 0.22)), removal: .offset(y: reduceMotion ? 0 : -6).combined(with: .opacity).animation(.easeOut(duration: 0.15))))
                        Text("\(doneCount) of \(items.count) done").id(doneCount).panelsScaledFont(size: 12).foregroundStyle(PanelsTheme.Goal.faint)
                            .transition(.asymmetric(insertion: .offset(y: reduceMotion ? 0 : 8).combined(with: .opacity).animation(.easeOut(duration: 0.22)), removal: .offset(y: reduceMotion ? 0 : -8).combined(with: .opacity).animation(.easeOut(duration: 0.14))))
                    }
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.up").font(.system(size: 12)).rotationEffect(.degrees(opened ? 0 : 180)).foregroundStyle(PanelsTheme.Goal.faint).animation(reduceMotion ? nil : .easeOut(duration: 0.15), value: opened)
                }.padding(supplementary ? 16 : 24).contentShape(Rectangle())
            }.buttonStyle(.plain).accessibilityValue(opened ? "Expanded" : "Collapsed")
            if opened {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(remaining, id: \.id) { item in row(item, filed: false).transition(.opacity.combined(with: .scale(scale: 0.98, anchor: .top))) }
                    if !complete.isEmpty {
                        Button {
                            withAnimation(reduceMotion ? nil : .easeOut(duration: PanelsTheme.Goal.completedCollapse)) { completedOpen.toggle() }
                        } label: {
                            HStack { Text("\(complete.count) completed").contentTransition(.numericText()); Spacer(); Image(systemName: "chevron.down").rotationEffect(.degrees(completedOpen ? 180 : 0)) }.font(.system(size: 13)).foregroundStyle(PanelsTheme.Goal.faint).frame(minHeight: 44)
                        }.buttonStyle(.plain)
                        if completedOpen { ForEach(complete, id: \.id) { row($0, filed: true) } }
                    }
                }.padding(.horizontal, 16).padding(.bottom, 16).transition(.opacity)
            }
        }.background(PanelsTheme.Goal.surface, in: RoundedRectangle(cornerRadius: 24))
            .overlay(RoundedRectangle(cornerRadius: 24).strokeBorder(PanelsTheme.Goal.border))
            .animation(reduceMotion ? nil : PanelsTheme.Goal.standard, value: doneCount)
            .onAppear { opened = !supplementary; expanded = items.first { $0["state"].text != "done" }?.id }
            .accessibilityElement(children: .contain).accessibilityIdentifier("goal-checklist-\(block.id)")
    }
    private func row(_ item: GoalJSON, filed: Bool) -> some View {
        let done = optimistic[item.id] ?? (item["state"].text == "done")
        let current = !filed && expanded == item.id
        return HStack(alignment: .top, spacing: 8) {
            Button { tick(item, done: !done) } label: {
                ZStack {
                    Circle().stroke(PanelsTheme.faint, lineWidth: 1).frame(width: 20, height: 20)
                    Circle().fill(PanelsTheme.strong).frame(width: 20, height: 20).scaleEffect(done ? 1 : 0.6).opacity(done ? 1 : 0)
                    ChecklistTick().trim(from: 0, to: done ? 1 : 0).stroke(PanelsTheme.surface, style: .init(lineWidth: 1.5, lineCap: .round, lineJoin: .round)).frame(width: 20, height: 20)
                        .animation(reduceMotion ? nil : .easeOut(duration: PanelsTheme.Goal.tickDuration).delay(PanelsTheme.Goal.tickDelay), value: done)
                }.frame(width: 36, height: 44).contentShape(Rectangle())
            }.buttonStyle(ChecklistPressStyle()).disabled(preview || session.busy)
                .accessibilityLabel(item["label"].text).accessibilityValue(done ? "Done" : "Not done")
                .accessibilityIdentifier("goal-check-\(item.id)")
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Button {
                        withAnimation(reduceMotion ? nil : .easeOut(duration: PanelsTheme.Goal.panelCollapse)) { expanded = expanded == item.id ? nil : item.id }
                    } label: {
                        Text(item["label"].text).panelsScaledFont(size: 13, weight: .medium)
                            .foregroundStyle(done ? PanelsTheme.Goal.faint : PanelsTheme.strong)
                            .overlay(alignment: .leading) { Rectangle().fill(PanelsTheme.Goal.faint).frame(height: 1).scaleEffect(x: done ? 1 : 0, y: 1, anchor: .leading).animation(reduceMotion ? nil : PanelsTheme.Goal.standard, value: done) }
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    }.buttonStyle(.plain).disabled(filed)
                    GoalDateControl(value: item["deadline"].string, label: "Item deadline", tone: GoalPresentation.deadlineTone(item["deadline"].string, completed: done, today: session.goal?.statusDate), revision: session.goal?.revision ?? 0) { date, revision in
                        await session.updateBlock(block.id, revision: revision) { old in
                            old.setting("items", .array(old["items"].array.map { $0.id == item.id ? $0.setting("deadline", date.map(GoalJSON.string) ?? .null) : $0 }))
                        }
                    }.font(.system(size: 12)).disabled(preview || session.busy)
                }
                if current {
                    VStack(alignment: .leading, spacing: 12) {
                        if let detail = item["detail"].string, !detail.isEmpty { Text(detail).font(.system(size: 13)).foregroundStyle(PanelsTheme.Goal.faint) }
                        if item["state"].text == "blocked", !done { Text("Blocked").font(.system(size: 12)).foregroundStyle(PanelsTheme.Goal.attention) }
                        if let action = item["action"].string, !action.isEmpty {
                            Button { tick(item, done: true) } label: { Label(action, systemImage: "arrow.right") }.font(.system(size: 13)).disabled(preview || session.busy)
                        }
                    }.padding(.bottom, 16).transition(.opacity)
                }
            }
        }.padding(.horizontal, 8).background(current ? PanelsTheme.Goal.raised.opacity(0.6) : .clear, in: RoundedRectangle(cornerRadius: 8))
            .animation(reduceMotion ? nil : PanelsTheme.Goal.standard, value: done)
    }
    private func tick(_ item: GoalJSON, done: Bool) {
        guard !preview, !session.busy else { return }
        withAnimation(reduceMotion ? nil : PanelsTheme.Goal.standard) { optimistic[item.id] = done; if done { held.insert(item.id) } }
        let started = ContinuousClock.now
        Task {
            await session.updateBlock(block.id) { old in old.setting("items", .array(old["items"].array.map { $0.id == item.id ? $0.setting("state", .string(done ? "done" : "open")) : $0 })) }
            guard session.saveError == nil else { optimistic[item.id] = nil; held.remove(item.id); return }
            if done, !reduceMotion { try? await Task.sleep(until: started.advanced(by: .seconds(PanelsTheme.Goal.checkedHold)), clock: .continuous) }
            guard !Task.isCancelled else { return }
            withAnimation(reduceMotion ? nil : .easeOut(duration: PanelsTheme.Goal.itemCollapse)) {
                optimistic[item.id] = nil; held.remove(item.id)
                if done, expanded == item.id {
                    let accepted = GoalPresentation.checklists(session.goal?.page["blocks"].array ?? []).first { $0.id == block.id }
                    expanded = GoalPresentation.nextChecklistItem(accepted ?? block, after: item.id)
                }
                if !done { expanded = item.id }
            }
        }
    }
}
private struct ChecklistTick: Shape {
    func path(in r: CGRect) -> Path { var p = Path(); p.move(to: .init(x: r.width * 0.3, y: r.height * 0.5)); p.addLine(to: .init(x: r.width * 0.45, y: r.height * 0.65)); p.addLine(to: .init(x: r.width * 0.7, y: r.height * 0.35)); return p }
}
private struct ChecklistPressStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.scaleEffect(configuration.isPressed && !reduceMotion ? 0.92 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: PanelsTheme.Goal.pressDuration), value: configuration.isPressed)
    }
}
