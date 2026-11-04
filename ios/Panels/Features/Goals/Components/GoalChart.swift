import SwiftUI

struct GoalChart: View {
    let block: GoalJSON
    let session: GoalSession
    var integrated = false
    var preview = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var active: String?
    @State private var bodyVisible = false
    private var preference: GoalChartPreference { session.preference(block.id) }
    private var supporting: Bool { block["placement"].text == "supporting" }
    private var projection: GoalJSON { session.readings?.charts[block.id] ?? .null }
    private var data: [GoalChartDatum] { GoalChartPresentation.data(block, values: session.readings?.values ?? [], range: preference.range, projection: projection) }
    private var headline: GoalChartHeadline { GoalChartPresentation.headline(block, data: data, active: supporting && !preference.expanded ? nil : active, projection: projection, range: preference.range) }
    private var choices: [(String, String)] {
        let ranges = !block["binding"].isNull && projection.kind == "category" ? projection["ranges"].array : block["ranges"].array
        return ranges.isEmpty ? [("week", "Week"), ("month", "Month"), ("all", "All time")] : ranges.map { ($0.id, $0["label"].text) }
    }
    private var hasRange: Bool { !block["binding"].isNull || !block["ranges"].array.isEmpty || ["line", "bar"].contains(block.kind) && block["stacked"].bool != true }
    private var color: Color {
        if block.kind == "progress", block["binding"].isNull, let value = headline.value, let target = headline.target, value < target { return PanelsTheme.accent }
        return PanelsTheme.Goal.tone(GoalChartPresentation.tone(block, headline: headline))
    }
    var body: some View {
        Group {
            if block.kind == "binary" {
                VStack(alignment: .leading, spacing: 20) {
                    GoalCompletion(done: session.goal?.completed ?? false, status: session.goal?.status ?? "neutral")
                    if !preview {
                        Button(session.goal?.completed == true ? "Mark not done" : "Mark done") { Task { await session.save(["completed": .bool(!(session.goal?.completed ?? false))]) } }
                            .font(.footnote).buttonStyle(.bordered).frame(maxWidth: .infinity, alignment: .trailing).disabled(session.busy)
                    }
                }
            } else if !block["manual"].isNull, !block["measure_id"].isNull {
                GoalManualChart(block: block, session: session, integrated: integrated, preview: preview)
            } else {
                chart
            }
        }.accessibilityElement(children: .contain).accessibilityIdentifier("goal-chart-\(block.id)")
    }
    private var chart: some View {
        VStack(alignment: .leading, spacing: 20) {
            if supporting {
                Button { toggle() } label: {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            title; Spacer()
                            Image(systemName: "chevron.down").font(.system(size: 12)).rotationEffect(.degrees(preference.expanded ? 180 : 0))
                                .animation(reduceMotion ? nil : PanelsTheme.Goal.standard, value: preference.expanded)
                        }
                        GoalCounter(block: block, value: headline.value, size: 28)
                        comparison
                    }.contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityValue(preference.expanded ? "Expanded" : "Collapsed")
            } else {
                title
                if !["gauge", "time-budget", "allocation"].contains(block.kind) {
                    VStack(alignment: .leading, spacing: 8) {
                        if block.kind == "progress" { Text("Completed").font(.caption).foregroundStyle(PanelsTheme.Goal.faint) }
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            GoalCounter(block: block, value: headline.value, size: PanelsTheme.Goal.compactReading)
                            if block.kind == "progress", let target = headline.target, target > 0 { Text("of " + GoalChartPresentation.format(block, target)).font(.body).foregroundStyle(PanelsTheme.Goal.faint) }
                        }
                        comparison
                    }
                }
            }
            if !supporting || preference.expanded {
                VStack(alignment: .leading, spacing: 20) {
                    plot
                    if hasRange {
                        GoalRangeSwitch(choices: choices, selection: Binding(get: { preference.range }, set: { value in active = nil; var next = preference; next.range = value; session.remember(block.id, next) }))
                    }
                    let details = GoalChartPresentation.observationDetails(headline)
                    if !details.isEmpty {
                        DisclosureGroup("Observation details") {
                            VStack(alignment: .leading, spacing: 6) { ForEach(details, id: \.self) { Text($0) } }.font(.caption).foregroundStyle(PanelsTheme.Goal.faint)
                        }.font(.caption).tint(PanelsTheme.Goal.faint)
                    }
                    if let caption = block["caption"].string { Text(caption).font(.caption).foregroundStyle(PanelsTheme.Goal.faint) }
                }.opacity(!supporting || bodyVisible || reduceMotion ? 1 : 0)
                    .transition(.opacity)
            }
        }
        .modifier(GoalChartContainer(framed: !integrated || supporting))
        .onAppear {
            bodyVisible = preference.expanded
            if !choices.contains(where: { $0.0 == preference.range }), let first = choices.first {
                var next = preference; next.range = first.0; session.remember(block.id, next)
            }
        }
        .task(id: preference.expanded) {
            let open = preference.expanded
            if open { try? await Task.sleep(for: .seconds(reduceMotion ? 0 : PanelsTheme.Goal.delay)) }
            guard !Task.isCancelled else { return }
            withAnimation(reduceMotion ? nil : PanelsTheme.Goal.easeInOut(open ? PanelsTheme.Goal.standardDuration : PanelsTheme.Goal.instantDuration)) { bodyVisible = open }
        }
    }
    private var title: some View { Text(block["title"].string ?? "Reading").panelsScaledFont(size: supporting ? 14 : 17, weight: .medium).foregroundStyle(supporting ? PanelsTheme.Goal.faint : PanelsTheme.strong) }
    private var comparison: some View {
        let split = block.kind == "allocation" ? GoalChartPresentation.allocationSummary(block, data: data, projection: projection, range: preference.range) : nil
        let text = block["stacked"].bool == true ? "\(GoalChartPresentation.selectedSeries(block, range: preference.range, projection: projection).count) " + (block["total_label"].string ?? "categories") : split ?? (headline.value == nil ? (supporting ? "Current reading unavailable" : data.contains { !$0.values.filter { $0.key != "target" }.isEmpty } ? "No reading for \(headline.label ?? "this range")" : "No readings yet") : !block["expectation"].isNull && headline.target == nil ? "Expected reading unavailable" : GoalChartPresentation.comparison(block, headline: headline))
        return Text(text + (block["aggregate"].text == "sum" && headline.label == nil && headline.value != nil && headline.missingCount > 0 ? " · \(headline.missingCount) missing readings" : ""))
            .font(supporting ? .caption : .footnote).foregroundStyle(color).fixedSize(horizontal: false, vertical: true)
    }
    @ViewBuilder private var plot: some View {
        switch block.kind {
        case "gauge":
            if headline.value != nil { GoalGauge(block: block.setting("domain", .array(GoalChartPresentation.domain(block, range: preference.range).map(GoalJSON.number))), headline: headline) }
            else { Text("No readings yet").foregroundStyle(PanelsTheme.Goal.faint) }
        case "progress": GoalProgressMeter(block: block, headline: headline)
        case "time-budget", "allocation": GoalRing(block: block, data: data, series: GoalChartPresentation.selectedSeries(block, range: preference.range, projection: projection), headline: headline, projection: projection, range: preference.range)
        case "number": EmptyView()
        default: GoalPlot(block: block, data: data, series: GoalChartPresentation.selectedSeries(block, range: preference.range, projection: projection), range: preference.range, active: $active, integerTicks: GoalChartPresentation.hasCountAxis(block, projection: projection, range: preference.range))
        }
    }
    private func toggle() {
        active = nil
        withAnimation(reduceMotion ? nil : PanelsTheme.Goal.disclosure) {
            var next = preference; next.expanded.toggle(); session.remember(block.id, next)
        }
    }
}

struct GoalChartContainer: ViewModifier {
    let framed: Bool
    func body(content: Content) -> some View {
        if framed { content.modifier(GoalPanel()) } else { content }
    }
}
