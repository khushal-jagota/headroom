import SwiftUI

private struct GoalManualCounter: View {
    let block: GoalJSON
    let value: Double?
    var size: CGFloat = 32
    private var displayBlock: GoalJSON {
        guard block["manual"]["model"].text != "individual_readings" else { return block }
        return block.setting("unit", .string("")).setting("format", (block["format"].isNull ? .object([:]) : block["format"]).setting("suffix", .string("")))
    }
    var body: some View {
        if let literal = GoalChartPresentation.manualLiteral(displayBlock, value: value) {
            Text(literal).font(.system(size: size, weight: .medium)).monospacedDigit()
        } else { GoalCounter(block: GoalChartPresentation.manualDisplayBlock(displayBlock, value: value), value: value, size: size) }
    }
}

struct GoalManualValue: View {
    @Bindable var editor: GoalManualEditor
    let block: GoalJSON
    var centered = false
    @FocusState private var editing: Bool
    private var current: Double? { editor.state?.value(cumulative: editor.cumulative) }
    var body: some View {
        VStack(alignment: centered ? .center : .leading, spacing: 6) {
            if editing || editor.draft != nil {
                TextField("Reading", text: Binding(get: { editor.draft ?? current.map { String($0) } ?? "" }, set: { editor.draft = $0 }))
                    .keyboardType(.numbersAndPunctuation).font(.system(size: 32, weight: .medium)).monospacedDigit()
                    .multilineTextAlignment(centered ? .center : .leading).focused($editing).onSubmit { save() }
                    .accessibilityLabel(block["title"].string ?? "Reading")
                HStack(spacing: 16) {
                    Button("Save", action: save).disabled(Double(editor.draft ?? "")?.isFinite != true || editor.busy || editor.pending != nil)
                    Button("Cancel") { Task { await editor.discard(); editing = false } }
                }.font(.footnote)
            } else {
                Button { editor.beginEditing(); editing = true } label: {
                    GoalManualCounter(block: block, value: current)
                }.buttonStyle(.plain).contentShape(Rectangle()).disabled(editor.busy || editor.pending != nil)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(GoalChartPresentation.manualLiteral(block, value: current) ?? GoalChartPresentation.format(GoalChartPresentation.manualDisplayBlock(block, value: current), current))
                    .accessibilityIdentifier("goal-manual-value-" + block.id).accessibilityHint("Edit reading")
            }
        }.onChange(of: editing) { old, focused in
            if old && !focused && editor.draft != nil && editor.pending == nil { Task { _ = await editor.prepare() } }
        }.toolbar {
            if editing { ToolbarItemGroup(placement: .keyboard) { Spacer(); Button("Done") { save() } } }
        }
    }
    private func save() {
        guard let value = Double(editor.draft?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""), value.isFinite else { return }
        Task { await editor.write(value: value); editing = false }
    }
}

struct GoalManualChart: View {
    let block: GoalJSON
    let session: GoalSession
    var integrated = false
    var preview = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var active: String?
    private var editor: GoalManualEditor { session.manual(block) }
    private var preference: GoalChartPreference { session.preference(block.id) }
    private var supporting: Bool { block["placement"].text == "supporting" }
    private var current: Double? { editor.state?.value(cumulative: editor.cumulative) }
    private var target: Double? {
        block["reference"].array.isEmpty ? GoalChartPresentation.target(block, range: preference.range) : GoalChartPresentation.reference(block, date: editor.cumulative ? editor.state?.latestObservation?["date"].string ?? editor.state?.date ?? "" : editor.state?.date ?? "")
    }
    private var history: [GoalJSON] {
        let original = (session.readings?.values ?? []).filter { $0["measure_id"].text != editor.measureID || editor.history[$0["date"].text] == nil }
        return original + Array(editor.history.values)
    }
    private var dates: [String] {
        if !block["ranges"].array.isEmpty { return GoalChartPresentation.data(block, values: history, range: preference.range).map(\.id) }
        guard let day = editor.state?.currentDate, let today = GoalPresentation.calendarDate(day) else { return [] }
        return (0..<7).map { GoalPresentation.day(today.addingTimeInterval(Double($0 - 6) * 86400)) }
    }
    private var data: [GoalChartDatum] {
        dates.map { day in
            let observation = history.first { $0["measure_id"].text == editor.measureID && $0["date"].text == day }
            var point = GoalChartDatum(id: day, label: day == editor.state?.currentDate ? "Today" : GoalPresentation.date(day))
            point.values["actual"] = observation?["value"].number
            point.values["target"] = block["reference"].array.isEmpty ? target : GoalChartPresentation.reference(block, date: day)
            return point
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if supporting {
                Button {
                    Task {
                        guard await editor.prepare() else { return }
                        withAnimation(reduceMotion ? nil : PanelsTheme.Goal.disclosure) { var next = preference; next.expanded.toggle(); session.remember(block.id, next) }
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack { title; Spacer(); Image(systemName: "chevron.down").rotationEffect(.degrees(preference.expanded ? 180 : 0)) }
                        GoalManualCounter(block: block, value: current, size: 28)
                    }.contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityValue(preference.expanded ? "Expanded" : "Collapsed")
            } else { title }
            if !supporting || preference.expanded {
                VStack(alignment: .leading, spacing: 16) {
                    if editor.cumulative {
                        GoalGauge(block: block, headline: .init(value: current, target: target), manual: preview ? nil : editor)
                        increments
                    } else {
                        HStack(alignment: .top) {
                            if preview { GoalManualCounter(block: block, value: current) }
                            else { GoalManualValue(editor: editor, block: block) }
                            Spacer(minLength: 8)
                            if block["manual"]["model"].text == "daily_quota" { increments }
                        }
                        if let date = editor.state?.date { Text(GoalPresentation.date(date)).font(.caption).foregroundStyle(PanelsTheme.Goal.faint) }
                        GoalPlot(block: block.setting("kind", .string(block["manual"]["model"].text == "daily_quota" ? "bar" : "line")), data: data, series: [], range: preference.range, active: $active, manual: true, select: preview ? nil : { date in
                            Task { guard await editor.prepare() else { return }; await editor.refresh(date: date) }
                        })
                    }
                    if !block["ranges"].array.isEmpty {
                        GoalRangeSwitch(choices: block["ranges"].array.map { ($0.id, $0["label"].text) }, selection: Binding(get: { preference.range }, set: { range in
                            Task {
                                guard await editor.prepare() else { return }
                                var next = preference; next.range = range; session.remember(block.id, next)
                                if !editor.cumulative, let date = GoalChartPresentation.data(block, values: history, range: range).last?.id { await editor.refresh(date: date) }
                            }
                        }))
                    }
                }.transition(.opacity)
            }
            GoalManualFailure(editor: editor)
        }.modifier(GoalChartContainer(framed: !integrated || supporting))
            .task(id: session.readings?.revision) { await editor.refresh() }
    }
    private var title: some View { Text(block["title"].string ?? "Reading").panelsScaledFont(size: supporting ? 14 : 17, weight: .medium).foregroundStyle(supporting ? PanelsTheme.Goal.faint : PanelsTheme.strong) }
    @ViewBuilder private var increments: some View {
        if !preview {
            HStack(spacing: 8) {
                ForEach(Array(block["manual"]["increments"].array.enumerated()), id: \.offset) { _, entry in
                    if let delta = entry.number {
                        Button((delta > 0 ? "+" : "") + GoalChartPresentation.number(delta, decimals: 6, trim: true)) {
                            Task { guard await editor.prepare() else { return }; await editor.write(delta: delta); await session.refresh() }
                        }.buttonStyle(.bordered).font(.footnote).disabled(editor.state == nil || editor.busy || editor.pending != nil)
                    }
                }
            }
        }
    }
}

struct GoalManualFailure: View {
    let editor: GoalManualEditor
    var body: some View {
        if let error = editor.error {
                VStack(alignment: .leading, spacing: 8) {
                    Text(error).foregroundStyle(PanelsTheme.error)
                    HStack {
                        if editor.pending != nil, !editor.conflicted { Button("Retry save") { Task { await editor.retry() } } }
                        else if editor.pending == nil { Button("Retry") { Task { await editor.refresh() } } }
                        Button("Discard edit") { Task { await editor.discard() } }
                    }.disabled(editor.busy)
                }.font(.footnote)
            }
    }
}
