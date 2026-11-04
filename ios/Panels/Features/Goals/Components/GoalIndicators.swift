import SwiftUI

private struct GaugeArc: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        let radius = min(rect.width / 2 - 8, rect.height / 1.8)
        p.addArc(center: CGPoint(x: rect.midX, y: rect.height * 0.56), radius: radius, startAngle: .degrees(135), endAngle: .degrees(405), clockwise: false)
        return p
    }
}
private struct GaugeTarget: Shape {
    let fraction: Double
    func path(in rect: CGRect) -> Path {
        let radius = min(rect.width / 2 - 8, rect.height / 1.8)
        let angle = (135 + 270 * fraction) * .pi / 180
        let center = CGPoint(x: rect.midX, y: rect.height * 0.56)
        var p = Path()
        p.move(to: .init(x: center.x + (radius - 7) * cos(angle), y: center.y + (radius - 7) * sin(angle)))
        p.addLine(to: .init(x: center.x + (radius + 7) * cos(angle), y: center.y + (radius + 7) * sin(angle)))
        return p
    }
}

/// One interpolated value drives the arc, count and threshold on every frame.
private struct GaugeFrame: View, @MainActor Animatable {
    let block: GoalJSON
    let headline: GoalChartHeadline
    let manual: GoalManualEditor?
    var displayed: Double
    var settled: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var animatableData: Double { get { displayed } set { displayed = newValue } }
    private var limits: (Double, Double) {
        let domain = block["domain"].array.compactMap(\.number)
        return (domain.first ?? (manual == nil ? 0 : min(0, headline.value ?? 0)), manual != nil ? headline.target ?? 100 : domain.count == 2 ? domain[1] : 100)
    }
    private var fraction: Double { max(0, min(1, (displayed - limits.0) / max(0.000001, limits.1 - limits.0))) }
    private var label: String {
        guard let target = headline.target else { return "" }
        if manual != nil { return "of " + GoalChartPresentation.number(target, decimals: 6, trim: true) }
        if GoalChartPresentation.direction(block) == "atmost" { return displayed <= target ? "Within limit" : "Above limit" }
        return displayed >= target ? "On target" : "Below target"
    }
    private var tone: Color {
        guard headline.target != nil else { return PanelsTheme.accent }
        if !block["binding"].isNull, headline.comparison == nil { return PanelsTheme.muted }
        let good = GoalChartPresentation.direction(block) == "atmost" ? displayed <= headline.target! : displayed >= headline.target!
        return good ? PanelsTheme.success : PanelsTheme.error
    }
    var body: some View {
        ZStack {
            GaugeArc().stroke(tone.opacity(0.13), style: .init(lineWidth: 9, lineCap: .round))
            GaugeArc().trim(from: 0, to: fraction).stroke(tone, style: .init(lineWidth: 9, lineCap: .round)).opacity(min(1, fraction / 0.02))
            if manual == nil, let target = headline.target, target >= limits.0, target <= limits.1 {
                GaugeTarget(fraction: (target - limits.0) / max(0.000001, limits.1 - limits.0)).stroke(PanelsTheme.Goal.surface, lineWidth: 5)
                GaugeTarget(fraction: (target - limits.0) / max(0.000001, limits.1 - limits.0)).stroke(PanelsTheme.Goal.reference, lineWidth: 1.5)
            }
            VStack(spacing: 4) {
                if let manual { GoalManualValue(editor: manual, block: block, centered: true) }
                else {
                    Text(GoalChartPresentation.format(block, headline.value, trim: true)).hidden()
                        .overlay(alignment: .trailing) { Text(GoalChartPresentation.format(block, displayed, trim: true)) }
                        .panelsScaledFont(size: 32, relativeTo: .largeTitle, weight: .medium).monospacedDigit().foregroundStyle(PanelsTheme.strong)
                        .animation(reduceMotion ? nil : PanelsTheme.Goal.smooth, value: GoalChartPresentation.format(block, headline.value, trim: true))
                }
                if headline.target != nil {
                    Text(label).id(label).font(.footnote).foregroundStyle(tone).opacity(settled ? 1 : 0)
                        .transition(.offset(y: 5).combined(with: .opacity).combined(with: .blurReplace))
                }
            }.offset(y: 4)
        }
    }
}

struct GoalGauge: View {
    let block: GoalJSON
    let headline: GoalChartHeadline
    var manual: GoalManualEditor? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var displayed = 0.0
    @State private var appeared = false
    @State private var settled = false
    @State private var inspecting = false
    var body: some View {
        GaugeFrame(block: block, headline: headline, manual: manual, displayed: displayed, settled: settled)
            .frame(height: 190)
            .contentShape(Rectangle())
            .simultaneousGesture(TapGesture().onEnded { if manual == nil { inspecting.toggle() } })
            .overlay(alignment: .topTrailing) {
                if inspecting {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(block["title"].text).fontWeight(.medium)
                        Text("Current: " + GoalChartPresentation.format(block, headline.value))
                        if let target = headline.target { Text("Target: " + GoalChartPresentation.format(block, target)) }
                    }.font(.caption).padding(12).background(PanelsTheme.Goal.raised, in: RoundedRectangle(cornerRadius: 8)).allowsHitTesting(false)
                        .transition(.opacity.combined(with: .scale(scale: 0.98)))
                }
            }
            .background { if inspecting { GoalInspectionDismissal { inspecting = false } } }
            .animation(reduceMotion ? nil : PanelsTheme.Goal.snappy, value: inspecting)
            .accessibilityElement(children: manual == nil ? .ignore : .contain)
            .accessibilityLabel(GoalChartPresentation.format(block, headline.value) + ", " + GoalChartPresentation.comparison(block, headline: headline))
            .accessibilityAction(named: "Inspect reading") { inspecting.toggle() }
            .onScrollVisibilityChange(threshold: 0.5) { visible in if visible { reveal() } }
            .onAppear { if manual != nil || reduceMotion { reveal() } }
            .onChange(of: headline.value) { _, next in
                guard appeared else { return }
                withAnimation(reduceMotion ? nil : manual == nil ? PanelsTheme.Goal.morph : PanelsTheme.Goal.smooth) { displayed = next ?? 0 }
            }
    }
    private func reveal() {
        guard !appeared else { return }; appeared = true
        if manual != nil || reduceMotion { displayed = headline.value ?? 0; settled = true; return }
        withAnimation(.timingCurve(0.65, 0, 0.35, 1, duration: PanelsTheme.Goal.gaugeReveal), completionCriteria: .logicallyComplete) { displayed = headline.value ?? 0 } completion: {
            withAnimation(PanelsTheme.Goal.standard) { settled = true }
        }
    }
}

struct GoalProgressMeter: View {
    let block: GoalJSON
    let headline: GoalChartHeadline
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private var fraction: Double { guard let value = headline.value, let target = headline.target, target > 0 else { return 0 }; return max(0, min(1, value / target)) }
    var body: some View {
        VStack(spacing: 10) {
            GeometryReader { geo in
                Capsule().fill(PanelsTheme.Goal.meter)
                Capsule().fill(!block["binding"].isNull && headline.comparison == nil ? PanelsTheme.muted : GoalChartPresentation.tone(block, headline: headline) == "good" ? PanelsTheme.success : PanelsTheme.accent)
                    .frame(width: geo.size.width * fraction)
                    .animation(reduceMotion ? nil : PanelsTheme.Goal.smooth, value: fraction)
            }.frame(height: 8)
            HStack { Text("0"); Spacer(); Text(block["scale_label"].string ?? GoalChartPresentation.format(block, headline.target)) }
                .font(.system(size: 11)).foregroundStyle(PanelsTheme.Goal.faint)
        }.accessibilityElement(children: .ignore).accessibilityLabel("\(GoalChartPresentation.format(block, headline.value)) of \(GoalChartPresentation.format(block, headline.target))")
    }
}

struct GoalRing: View {
    let block: GoalJSON
    let data: [GoalChartDatum]
    let series: [GoalJSON]
    let headline: GoalChartHeadline
    let projection: GoalJSON
    let range: String
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var active: Int?
    private var entries: [(String, Double?, Color)] {
        if block.kind == "time-budget" {
            let remaining = !block["binding"].isNull ? headline.difference.map { -$0 } : headline.target.flatMap { target in headline.value.map { target - $0 } }
            return [("Spent", headline.value, !block["binding"].isNull && headline.comparison == nil ? PanelsTheme.muted : GoalChartPresentation.tone(block, headline: headline) == "bad" ? PanelsTheme.error : PanelsTheme.accent), ((remaining ?? 0) < 0 ? "Over budget" : "Remaining", remaining.map(abs), PanelsTheme.Goal.budget)]
        }
        let category = GoalChartPresentation.categoryRange(projection, range)
        if !block["binding"].isNull {
            return category["series"].array.filter { !$0["summary"].isNull }.enumerated().map { index, item in
                (item["label"].text, GoalChartPresentation.validNumber(item["summary"]), PanelsTheme.Goal.seriesColor(series.indices.contains(index) ? series[index]["color"].string : nil, index: index))
            }
        }
        let reading = data.last
        return series.enumerated().map { index, item in (item["name"].text, reading?.values[GoalChartPresentation.seriesKey(item, index)], PanelsTheme.Goal.seriesColor(series.indices.contains(index) ? series[index]["color"].string : nil, index: index)) }
    }
    private var total: Double? {
        if block.kind == "time-budget" { return headline.target }
        return headline.value
    }
    private var display: Double? {
        let value = active.flatMap { entries.indices.contains($0) ? entries[$0].1 : nil } ?? (block.kind == "time-budget" ? headline.value : total)
        if block["allocation_mode"].text == "percentage", let value, let total, total > 0 { return value / total * 100 }
        return value
    }
    private var displayBlock: GoalJSON {
        guard block["allocation_mode"].text == "percentage" else { return block }
        return block.setting("format", .object(["suffix": .string("%"), "decimals": .number(active == nil ? 0 : 1)]))
    }
    private func fraction(_ index: Int) -> Double {
        guard let total, total > 0, entries.allSatisfy({ $0.1 != nil }) else { return 0 }
        if block.kind == "time-budget", index == 1, (headline.value ?? 0) > total { return 0 }
        return max(0, min(1, (entries[index].1 ?? 0) / total))
    }
    var body: some View {
        HStack(spacing: 16) {
            ZStack {
                Circle().stroke(PanelsTheme.Goal.meter, lineWidth: PanelsTheme.Goal.ringWidth)
                ForEach(entries.indices, id: \.self) { index in
                    let start = entries.indices.filter { $0 < index }.reduce(0.0) { $0 + fraction($1) }
                    Circle().trim(from: min(1, start), to: min(1, start + max(0, fraction(index) - (fraction(index) == 1 ? 0 : 0.005))))
                        .stroke(active == index && block.kind == "allocation" ? PanelsTheme.Goal.seriesColor(series.indices.contains(index) ? series[index]["color"].string : nil, index: index, active: true) : entries[index].2, style: .init(lineWidth: active == index ? PanelsTheme.Goal.activeRingWidth : PanelsTheme.Goal.ringWidth, lineCap: .butt))
                        .rotationEffect(.degrees(-90)).opacity(active == nil || active == index ? 1 : 0.5)
                        .animation(reduceMotion ? nil : PanelsTheme.Goal.smooth, value: fraction(index))
                }
                VStack(spacing: 5) {
                    GoalCounter(block: displayBlock, value: display, size: 32).minimumScaleFactor(0.6).lineLimit(1)
                    Text(active.flatMap { entries.indices.contains($0) ? entries[$0].0 : nil } ?? (block.kind == "time-budget" ? "Spent" : block["total_label"].string ?? "Total"))
                        .font(.footnote).foregroundStyle(PanelsTheme.Goal.faint)
                }
            }.padding(14).frame(width: 144, height: 144)
                .contentShape(Circle())
                .onTapGesture { point in
                    let dx = point.x - 72, dy = point.y - 72
                    guard hypot(dx, dy) > 44 else { active = nil; return }
                    let angle = (atan2(dy, dx) / (2 * .pi) + 1.25).truncatingRemainder(dividingBy: 1)
                    var sum = 0.0
                    for index in entries.indices { sum += fraction(index); if angle <= sum { inspect(index); return } }
                    active = nil
                }
            VStack(spacing: 4) {
                ForEach(entries.indices, id: \.self) { index in
                    Button { inspect(index) } label: {
                        HStack(spacing: 8) {
                            Circle().fill(entries[index].2).frame(width: 6, height: 6)
                            Text(entries[index].0); Spacer()
                            Text(legendValue(index)).monospacedDigit()
                        }.font(.footnote).foregroundStyle(PanelsTheme.muted).frame(minHeight: 32)
                            .opacity(active == nil || active == index ? 1 : 0.5)
                    }.buttonStyle(.plain).accessibilityAddTraits(active == index ? .isSelected : [])
                }
            }.frame(maxWidth: .infinity)
        }.animation(reduceMotion ? nil : PanelsTheme.Goal.smooth, value: active)
            .background { if active != nil { GoalInspectionDismissal { active = nil } } }
    }
    private func legendValue(_ index: Int) -> String {
        if block["allocation_mode"].text == "percentage" {
            guard let amount = entries[index].1, let total, total > 0 else { return "—" }
            return GoalChartPresentation.number(amount / total * 100, decimals: 1, trim: true) + "%"
        }
        return GoalChartPresentation.format(block, entries[index].1, trim: true)
    }
    private func inspect(_ index: Int) { withAnimation(reduceMotion ? nil : PanelsTheme.Goal.smooth) { active = active == index ? nil : index } }
}

private struct GoalTick: Shape {
    func path(in rect: CGRect) -> Path { var p = Path(); p.move(to: .init(x: rect.width * 0.25, y: rect.height * 0.5)); p.addLine(to: .init(x: rect.width * 0.44, y: rect.height * 0.7)); p.addLine(to: .init(x: rect.width * 0.77, y: rect.height * 0.3)); return p }
}
struct GoalCompletion: View {
    let done: Bool
    let status: String
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8).fill(done ? PanelsTheme.success.opacity(0.1) : PanelsTheme.surface)
                    RoundedRectangle(cornerRadius: 8).stroke(done ? PanelsTheme.success : PanelsTheme.faint, lineWidth: 1.5)
                    GoalTick().trim(from: 0, to: done ? 1 : 0).stroke(PanelsTheme.success, style: .init(lineWidth: 2, lineCap: .round)).opacity(done ? 1 : 0)
                    Text("–").foregroundStyle(PanelsTheme.faint).opacity(done ? 0 : 1)
                }.frame(width: 32, height: 32).animation(reduceMotion ? nil : PanelsTheme.Goal.smooth, value: done)
                Text(done ? "Done" : "Not done").id(done).panelsScaledFont(size: PanelsTheme.Goal.completionReading, relativeTo: .largeTitle, weight: .medium)
                    .transition(.offset(y: 8).combined(with: .opacity).combined(with: .blurReplace))
            }
            Label(done ? "Completed" : status == "bad" ? "Overdue" : status == "attention" ? "Due today" : "Not done", systemImage: "circle.fill")
                .font(.footnote).foregroundStyle(PanelsTheme.Goal.tone(status))
        }.animation(reduceMotion ? nil : PanelsTheme.Goal.standard, value: done)
    }
}
