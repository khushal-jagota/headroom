import SwiftUI

private struct PlotVector: VectorArithmetic {
    var values: [Double]
    static var zero: Self { .init(values: []) }
    static func + (a: Self, b: Self) -> Self { .init(values: (0..<max(a.values.count, b.values.count)).map { (a.values.indices.contains($0) ? a.values[$0] : 0) + (b.values.indices.contains($0) ? b.values[$0] : 0) }) }
    static func - (a: Self, b: Self) -> Self { a + .init(values: b.values.map { -$0 }) }
    mutating func scale(by rhs: Double) { values = values.map { $0 * rhs } }
    var magnitudeSquared: Double { values.reduce(0) { $0 + $1 * $1 } }
}

/// All ranges share sampled x coordinates, so changes morph from the drawn line.
private struct PlotLine: Shape {
    var samples: PlotVector
    var valid: [Bool]
    var fill = false
    var animatableData: PlotVector { get { samples } set { samples = newValue } }
    func path(in rect: CGRect) -> Path {
        var path = Path(); var started = false; var startX: CGFloat = 0; var lastX: CGFloat = 0
        for index in samples.values.indices {
            let x = CGFloat(index) / CGFloat(max(1, samples.values.count - 1)) * rect.width
            guard valid.indices.contains(index), valid[index] else {
                if fill, started { path.addLine(to: .init(x: lastX, y: rect.height)); path.addLine(to: .init(x: startX, y: rect.height)); path.closeSubpath() }
                started = false; continue
            }
            let point = CGPoint(x: x, y: rect.height * (1 - samples.values[index]))
            if !started { path.move(to: point); startX = x; started = true } else { path.addLine(to: point) }
            lastX = x
        }
        if fill, started { path.addLine(to: .init(x: lastX, y: rect.height)); path.addLine(to: .init(x: startX, y: rect.height)); path.closeSubpath() }
        return path
    }
}

/// Touch inspection must coexist with the enclosing page's vertical pan. A
/// zero-duration press observes the finger without claiming the scroll gesture.
struct GoalLineInspection: UIViewRepresentable {
    let changed: (CGPoint) -> Void
    let ended: (Bool) -> Void
    let scrolling: () -> Void
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeUIView(context: Context) -> UIView {
        let view = UIView(); view.backgroundColor = .clear
        let press = UILongPressGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.touch(_:)))
        press.minimumPressDuration = 0; press.allowableMovement = .greatestFiniteMagnitude
        press.cancelsTouchesInView = false; press.delegate = context.coordinator
        view.addGestureRecognizer(press)
        return view
    }
    func updateUIView(_ view: UIView, context: Context) { context.coordinator.owner = self }
    @MainActor final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var owner: GoalLineInspection
        private var origin = CGPoint.zero
        private var isScrolling = false
        init(_ owner: GoalLineInspection) { self.owner = owner }
        @objc func touch(_ press: UILongPressGestureRecognizer) {
            let point = press.location(in: press.view)
            switch press.state {
            case .began:
                origin = point; isScrolling = false; owner.changed(point)
            case .changed:
                if abs(point.y - origin.y) > 12 && abs(point.y - origin.y) > abs(point.x - origin.x) { isScrolling = true }
                if isScrolling { owner.scrolling() } else { owner.changed(point) }
            case .ended: owner.ended(!isScrolling)
            case .cancelled, .failed: owner.ended(false)
            default: break
            }
        }
        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool { true }
    }
}

struct GoalPlot: View {
    let block: GoalJSON
    let data: [GoalChartDatum]
    let series: [GoalJSON]
    let range: String
    @Binding var active: String?
    var integerTicks = false
    var manual = false
    var select: ((String) -> Void)? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var revealed = false
    @State private var hidden: Set<String> = []
    private var keys: [String] {
        var result = series.enumerated().map { GoalChartPresentation.seriesKey($0.element, $0.offset) }
        if !block["measure_id"].isNull || result.isEmpty { result = ["actual"] }
        if data.contains(where: { $0.values["target"] != nil }) { result.append("target") }
        return result
    }
    private var bounds: (Double, Double) {
        let explicit = GoalChartPresentation.domain(block, range: range)
        if explicit.count == 2, explicit[1] > explicit[0] { return (explicit[0], explicit[1]) }
        var values = data.flatMap { $0.values.values }
        if block["stacked"].bool == true { values += data.map { $0.values.filter { $0.key != "target" }.values.reduce(0, +) } }
        let scale = GoalLineGeometry.scale(low: values.min() ?? 0, high: values.max() ?? 1)
        return (scale.0, scale.1)
    }
    private var ticks: [Double] { allTicks.filter { !integerTicks || abs($0.rounded() - $0) < 1e-8 } }
    private var allTicks: [Double] {
        let explicit = GoalChartPresentation.domain(block, range: range)
        if explicit.count == 2, explicit[1] > explicit[0] { return (0...4).map { explicit[0] + (explicit[1] - explicit[0]) * Double($0) / 4 } }
        return GoalLineGeometry.scale(low: bounds.0, high: bounds.1).2
    }
    private func color(_ key: String, index: Int, datum: GoalChartDatum? = nil) -> Color {
        if key == "target" { return PanelsTheme.Goal.reference }
        if block["stacked"].bool == true || index > 0 || !block["binding"].isNull && block.kind != "bar" {
            return PanelsTheme.Goal.seriesColor(series.indices.contains(index) ? series[index]["color"].string : nil, index: index, composed: block.kind == "bar")
        }
        if !block["expectation"].isNull || !block["binding"].isNull {
            return datum?.comparison.map { $0 ? PanelsTheme.success : PanelsTheme.Goal.tone(block["below_target_tone"].string ?? "bad") } ?? PanelsTheme.muted
        }
        let headline = GoalChartPresentation.headline(block, data: data, active: datum?.id, range: range)
        return headline.target == nil ? PanelsTheme.accent : PanelsTheme.Goal.tone(GoalChartPresentation.tone(block, headline: headline))
    }
    private func lineGradient(_ key: String, index: Int) -> LinearGradient {
        let fallback = color(key, index: index)
        guard key != "target", index == 0, block["binding"].isNull, data.contains(where: { $0.values["target"] != nil }) else { return LinearGradient(colors: [fallback, fallback], startPoint: .leading, endPoint: .trailing) }
        if block["measure_id"].isNull && block["expectation"].isNull {
            let actual = samples(key), target = samples("target")
            let stops = actual.0.values.indices.map { at -> Gradient.Stop in
                let comparable = actual.1[at] && target.1[at]
                let good = GoalChartPresentation.direction(block) == "atmost" ? actual.0.values[at] <= target.0.values[at] : actual.0.values[at] >= target.0.values[at]
                let tone = comparable ? good ? PanelsTheme.success : PanelsTheme.Goal.tone(block["below_target_tone"].string ?? "bad") : PanelsTheme.muted
                return .init(color: tone, location: Double(at) / Double(max(1, actual.0.values.count - 1)))
            }
            return LinearGradient(stops: stops, startPoint: .leading, endPoint: .trailing)
        }
        let stops = data.enumerated().map { index, datum in
            Gradient.Stop(color: color(key, index: 0, datum: datum), location: datum.position ?? Double(index) / Double(max(1, data.count - 1)))
        }
        return LinearGradient(stops: stops, startPoint: .leading, endPoint: .trailing)
    }
    private func samples(_ key: String) -> (PlotVector, [Bool]) {
        let limits = bounds
        let positions = data.enumerated().map { $0.element.position ?? Double($0.offset) / Double(max(1, data.count - 1)) }
        let samples = GoalLineGeometry.samples(values: data.map { $0.values[key] }, positions: positions, breaks: data.map { key == "target" && $0.breakBefore }, smooth: key != "target" || block["expectation"].isNull)
        return (.init(values: samples.map { hidden.contains(key) ? 0 : (($0 ?? limits.0) - limits.0) / (limits.1 - limits.0) }), samples.map { $0 != nil })
    }
    private var lineSeries: [GoalLineSeries] {
        keys.enumerated().map { index, key in
            let label = key == "target" ? (!block["expectation"].isNull ? "Expected" : "Target") : series.indices.contains(index) ? series[index]["name"].text : (block["series"].array.first?["name"].string ?? (manual ? "Reading" : "Current"))
            // A manual reading line keeps the accent as its own colour; comparisons colour only the stroke.
            return GoalLineSeries(key: key, label: label, color: manual && key != "target" ? PanelsTheme.accent : key == "target" && manual ? PanelsTheme.faint : color(key, index: index), stops: lineStops(key, index: index), dashed: key == "target", area: index == 0 && key != "target",
                                  smooth: key != "target" || block["expectation"].isNull, values: data.map { $0.values[key] }, breaks: data.map { key == "target" && $0.breakBefore })
        }
    }
    /// Stroke colours along x: compared against the target sample by sample, or by each dated comparison.
    private func lineStops(_ key: String, index: Int) -> [Gradient.Stop]? {
        guard key != "target", index == 0, block["binding"].isNull, data.contains(where: { $0.values["target"] != nil }) || manual else { return nil }
        let positions = data.enumerated().map { $0.element.position ?? Double($0.offset) / Double(max(1, data.count - 1)) }
        if block["measure_id"].isNull && block["expectation"].isNull {
            let actual = GoalLineGeometry.samples(values: data.map { $0.values[key] }, positions: positions, breaks: data.map { _ in false }, smooth: true)
            let target = GoalLineGeometry.samples(values: data.map { $0.values["target"] }, positions: positions, breaks: data.map(\.breakBefore), smooth: block["expectation"].isNull)
            return actual.indices.compactMap { at -> Gradient.Stop? in
                guard let value = actual[at] else { return nil }
                let good = target[at].map { GoalChartPresentation.direction(block) == "atmost" ? value <= $0 : value >= $0 } ?? true
                return .init(color: good ? PanelsTheme.success : PanelsTheme.Goal.tone(block["below_target_tone"].string ?? "bad"), location: Double(at) / Double(max(1, actual.count - 1)))
            }
        }
        return data.enumerated().map { index, datum in Gradient.Stop(color: color(key, index: 0, datum: datum), location: positions[index]) }
    }
    private var manualDomain: [Double]? {
        guard manual else { return nil }
        let readings = data.compactMap { $0.values["actual"] }, targets = data.compactMap { $0.values["target"] }
        return [min(0, (targets + readings).min() ?? 0), max(0, (targets + readings).max() ?? 0)]
    }
    var body: some View {
        VStack(spacing: 12) {
            if block.kind == "bar" { barPlot } else {
                GoalLineChart(data: data, series: lineSeries, hidden: hidden, height: manual ? 196 : CGFloat(block["plot_height"].number ?? Double(PanelsTheme.Goal.plotHeight)),
                              domain: manualDomain ?? { let explicit = GoalChartPresentation.domain(block, range: range); return explicit.count == 2 && explicit[1] > explicit[0] ? explicit : nil }(),
                              integerTicks: integerTicks, title: block["title"].string ?? "Reading",
                              formatTick: { GoalChartPresentation.axisTick(block, $0) }, formatValue: { GoalChartPresentation.format(block, $0) },
                              pointColor: { line, datum in color(line.key, index: keys.firstIndex(of: line.key) ?? 0, datum: datum) },
                              active: $active, select: select, revealAtOnce: manual)
                    .accessibilityIdentifier("goal-plot-\(block.id)")
            }
            if !manual { legend }
        }
    }
    private var barPlot: some View {
        GeometryReader { geometry in
            ZStack(alignment: .topLeading) {
                grid
                bars
                if keys.contains("target") {
                    let target = samples("target")
                    PlotLine(samples: target.0, valid: target.1).stroke(PanelsTheme.Goal.reference, style: .init(lineWidth: 1.3, dash: [4, 5])).allowsHitTesting(false)
                        .animation(reduceMotion ? nil : manual ? PanelsTheme.Goal.easeInOut(PanelsTheme.Goal.standardDuration) : PanelsTheme.Goal.bars, value: data)
                }
                if let datum = data.first(where: { $0.id == active }) {
                    inspection(datum).padding(8).background(PanelsTheme.Goal.raised, in: RoundedRectangle(cornerRadius: 8))
                        .frame(maxWidth: min(geometry.size.width - 32, 230), alignment: .leading)
                        .offset(x: tooltipX(width: geometry.size.width - 32), y: 4)
                        .animation(reduceMotion ? nil : PanelsTheme.Goal.snappy, value: active)
                        .allowsHitTesting(false)
                }
            }
            .padding(.trailing, 32).padding(.bottom, 22)
            .contentShape(Rectangle())
            .overlay(alignment: .bottom) { axisLabels.padding(.trailing, 32) }
        }.frame(height: CGFloat(block["plot_height"].number ?? Double(PanelsTheme.Goal.plotHeight)))
            .accessibilityElement(children: .contain).accessibilityLabel(block["title"].string ?? "Reading chart")
            .accessibilityIdentifier("goal-plot-\(block.id)")
            .background { if active != nil { GoalInspectionDismissal { active = nil } } }
            .onAppear { reveal() }
    }
    private func reveal() {
        guard !revealed else { return }
        withAnimation(reduceMotion ? nil : block.kind == "bar" ? PanelsTheme.Goal.bars : PanelsTheme.Goal.reveal) { revealed = true }
    }
    private var grid: some View {
        GeometryReader { geometry in
            ForEach(ticks, id: \.self) { tick in
                let fraction = (bounds.1 - tick) / (bounds.1 - bounds.0)
                Group {
                    Rectangle().fill(PanelsTheme.Goal.grid).frame(height: 1).offset(y: geometry.size.height * fraction)
                    Text(GoalChartPresentation.axisTick(block, tick))
                        .font(.system(size: 9)).foregroundStyle(PanelsTheme.faint).frame(width: 30, alignment: .leading)
                        .offset(x: geometry.size.width + 6, y: geometry.size.height * fraction - 6)
                }
            }
        }.transaction { $0.animation = nil } // Chart.js redraws its scale at once; only the bars move.
    }
    private var axisLabels: some View {
        HStack {
            Text(data.first?.label ?? "")
            Spacer()
            if data.count > 2 { Text(data[data.count / 2].label); Spacer() }
            if data.count > 1 { Text(data.last?.label ?? "") }
        }.font(.system(size: 9)).foregroundStyle(PanelsTheme.faint).lineLimit(1)
    }
    private var bars: some View {
        GeometryReader { geometry in
            HStack(alignment: .bottom, spacing: 8) {
                ForEach(data) { datum in
                    Button {
                        active = active == datum.id ? nil : datum.id
                        select?(datum.id)
                    } label: {
                        ZStack(alignment: .bottom) {
                            let barKeys = keys.filter { $0 != "target" }
                            if block["stacked"].bool == true {
                                VStack(spacing: 1) {
                                    ForEach(Array(barKeys.enumerated()).reversed(), id: \.element) { index, key in
                                        Rectangle().fill(color(key, index: index, datum: datum))
                                            .frame(height: height(datum.values[key], in: geometry.size.height))
                                    }
                                }.clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, topTrailingRadius: 4))
                            } else {
                                HStack(alignment: .bottom, spacing: 2) {
                                    ForEach(Array(barKeys.enumerated()), id: \.element) { index, key in
                                        let value = datum.values[key]
                                        RoundedRectangle(cornerRadius: 4).fill(value == nil ? PanelsTheme.faint : color(key, index: index, datum: datum))
                                            .frame(height: value == nil || value == 0 ? 2 : height(value, in: geometry.size.height))
                                            .offset(y: -geometry.size.height * (min(0, value ?? 0) - bounds.0) / (bounds.1 - bounds.0))
                                            .opacity(hidden.contains(key) ? 0 : 1)
                                    }
                                }
                            }
                        }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                            .opacity(active == nil || active == datum.id ? 1 : 0.45)
                            .contentShape(Rectangle())
                    }.buttonStyle(.plain)
                        .transition(.asymmetric(insertion: .modifier(active: GoalBarGrowth(fraction: 0), identity: GoalBarGrowth(fraction: 1)), removal: .identity))
                        .accessibilityLabel(datum.label + ", " + GoalChartPresentation.format(block, datum.values[keys.first ?? "actual"]))
                        .accessibilityAddTraits(active == datum.id ? .isSelected : [])
                }
            }.scaleEffect(x: 1, y: revealed || reduceMotion || manual ? 1 : 0, anchor: .bottom)
                .animation(reduceMotion ? nil : manual ? PanelsTheme.Goal.easeInOut(PanelsTheme.Goal.standardDuration) : PanelsTheme.Goal.bars, value: data)
        }
    }
    private func height(_ value: Double?, in height: CGFloat) -> CGFloat { max(0, abs(value ?? 0) / (bounds.1 - bounds.0) * height) }
    private func tooltipX(width: CGFloat) -> CGFloat {
        guard let index = data.firstIndex(where: { $0.id == active }) else { return 0 }
        return max(0, min(width - min(width, 230), Double(index) / Double(max(1, data.count - 1)) * width - 90))
    }
    private func inspection(_ datum: GoalChartDatum) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(datum.label).fontWeight(.medium)
            ForEach(Array(keys.enumerated()), id: \.element) { index, key in
                HStack(spacing: 6) {
                    Circle().fill(color(key, index: index, datum: datum)).frame(width: 5, height: 5)
                    Text(key == "target" ? (!block["expectation"].isNull ? "Expected" : "Target") : series.indices.contains(index) ? series[index]["name"].text : "Current")
                    Spacer(minLength: 8)
                    Text(GoalChartPresentation.format(block, datum.values[key])).monospacedDigit()
                }
            }
        }.font(.system(size: 10)).foregroundStyle(PanelsTheme.text)
    }
    private var legend: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 14) { legendItems }
            VStack(alignment: .leading, spacing: 4) { legendItems }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    @ViewBuilder private var legendItems: some View {
        ForEach(Array(keys.enumerated()), id: \.element) { index, key in
            Button {
                withAnimation(reduceMotion ? nil : PanelsTheme.Goal.smooth) {
                    if hidden.contains(key) { hidden.remove(key) } else { hidden.insert(key) }
                }
            } label: {
                HStack(spacing: 5) {
                    Capsule().fill(color(key, index: index)).frame(width: 14, height: 2)
                    Text(key == "target" ? (!block["expectation"].isNull ? "Expected" : "Target") : series.indices.contains(index) ? series[index]["name"].text : "Current")
                }.font(.system(size: 10)).foregroundStyle(PanelsTheme.Goal.faint).opacity(hidden.contains(key) ? 0.4 : 1)
            }.buttonStyle(.plain).accessibilityValue(hidden.contains(key) ? "Hidden" : "Visible")
        }
    }
}

/// Chart.js starts a bar it has not drawn before at the baseline.
private struct GoalBarGrowth: ViewModifier {
    let fraction: Double
    func body(content: Content) -> some View { content.scaleEffect(x: 1, y: fraction, anchor: .bottom) }
}
