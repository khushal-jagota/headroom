import SwiftUI

/// One plotted series: values per datum in data units, with the colours the web derives.
struct GoalLineSeries: Equatable {
    let key: String
    let label: String
    let color: Color
    /// Stroke colour along x (0…1) for compared and dated series; nil strokes in `color`.
    var stops: [Gradient.Stop]? = nil
    var dashed = false
    var area = false
    var smooth = true
    let values: [Double?]
    var breaks: [Bool] = []
}

/// The web's line chart painter (line-chart-integrated.tsx), driven frame by frame:
/// range changes morph every line from the shape on screen while the axis rescales
/// on the same spring; gridlines and labels ride the scale and fade; the crosshair
/// glides and the tooltip follows on its own spring.
@MainActor @Observable final class GoalLineMotion {
    static let top: CGFloat = 12, gap: CGFloat = 14, samples = 240
    private(set) var animating = false
    struct Track { var shape: [Double]; var valid: [Bool]; var presence: GoalSpringValue }
    private struct Move { let from: [Double]; let fromValid: [Bool]; let to: [Double]; let toValid: [Bool] }
    struct Fade { var opacity: GoalTween; var present: Bool; var value: Double; var label: String }
    @ObservationIgnored private var tracks: [String: Track] = [:]
    @ObservationIgnored private var moves: [String: Move] = [:]
    @ObservationIgnored private var progress = GoalSpringValue(1, spec: .init(spring: GoalSpringSpec.settle.spring, restDelta: 0.0005, restSpeed: 0.001))
    @ObservationIgnored private var scaleMin = GoalSpringValue(0, spec: .settle)
    @ObservationIgnored private var scaleMax = GoalSpringValue(100, spec: .settle)
    @ObservationIgnored private var drawn = GoalTween(0)
    @ObservationIgnored private var cursor = GoalSpringValue(1, spec: .glide)
    @ObservationIgnored private var tipX = GoalSpringValue(0, spec: .follow)
    @ObservationIgnored private var tipY = GoalSpringValue(0, spec: .follow)
    @ObservationIgnored private var tipJump = false
    @ObservationIgnored private var scrubbing = false
    @ObservationIgnored private var scrub = GoalTween(0)
    @ObservationIgnored private var tipShown = GoalTween(0)
    @ObservationIgnored private(set) var ticks: [String: Fade] = [:]
    @ObservationIgnored private(set) var picks: [String: Fade] = [:]
    @ObservationIgnored var tipSize = CGSize(width: 140, height: 60)
    @ObservationIgnored private var hasScale = false
    @ObservationIgnored private var settleQueued = false

    static func now() -> Double { Date().timeIntervalSinceReferenceDate }
    private func wake() { if !animating { animating = true } }

    func setTargets(_ targets: [String: ([Double], [Bool])], hidden: Set<String>, reduce: Bool) {
        let time = Self.now()
        var moved = false
        for (key, target) in targets {
            guard let track = tracks[key] else {
                tracks[key] = Track(shape: target.0, valid: target.1, presence: GoalSpringValue(hidden.contains(key) ? 0 : 1, spec: .settle)); continue
            }
            let current = shape(key, at: time) ?? (track.shape, track.valid)
            if current.0.isEmpty || reduce { tracks[key]?.shape = target.0; tracks[key]?.valid = target.1; moves[key] = nil; continue }
            moves[key] = Move(from: current.0, fromValid: current.1, to: target.0, toValid: target.1)
            tracks[key]?.shape = target.0; tracks[key]?.valid = target.1
            moved = true
        }
        if moved { progress.jump(0); progress.animate(to: 1, at: time, keepVelocity: false); wake() }
        else if moves.isEmpty == false && reduce { moves = [:]; progress.jump(1) }
    }
    func setHidden(_ hidden: Set<String>, reduce: Bool) {
        let time = Self.now()
        for key in tracks.keys {
            let to: Double = hidden.contains(key) ? 0 : 1
            guard tracks[key]?.presence.target != to else { continue }
            if reduce { tracks[key]?.presence.jump(to) } else { tracks[key]?.presence.animate(to: to, at: time, keepVelocity: false); wake() }
        }
    }
    func setScale(min: Double, max: Double, reduce: Bool) {
        guard hasScale, !reduce else { scaleMin.jump(min); scaleMax.jump(max); hasScale = true; return }
        let time = Self.now()
        if scaleMin.target != min { scaleMin.animate(to: min, at: time); wake() }
        if scaleMax.target != max { scaleMax.animate(to: max, at: time); wake() }
    }
    /// Gridlines and tick labels fade in over 0.24 s and out over 0.12 s; the first set appears at once.
    func setTicks(_ next: [(String, Double, String)], initial: Bool) { fade(&ticks, next, initial: initial) }
    func setPicks(_ next: [(String, Double, String)], initial: Bool) { fade(&picks, next, initial: initial) }
    private func fade(_ store: inout [String: Fade], _ next: [(String, Double, String)], initial: Bool) {
        let time = Self.now(), keys = Set(next.map(\.0))
        for (key, value, label) in next {
            if var item = store[key] {
                item.value = value; item.label = label
                if !item.present { item.present = true; item.opacity.animate(to: 1, at: time, duration: PanelsTheme.Goal.standardDuration, curve: GoalCurve.standard); wake() }
                store[key] = item
            } else {
                var opacity = GoalTween(initial ? 1 : 0)
                if !initial { opacity.animate(to: 1, at: time, duration: PanelsTheme.Goal.standardDuration, curve: GoalCurve.standard); wake() }
                store[key] = Fade(opacity: opacity, present: true, value: value, label: label)
            }
        }
        for key in store.keys where !keys.contains(key) && store[key]!.present {
            store[key]!.present = false
            store[key]!.opacity.animate(to: 0, at: time, duration: PanelsTheme.Goal.instantDuration, curve: GoalCurve.standard); wake()
        }
    }
    /// The lines draw left to right the first time the chart is seen with data.
    func reveal(reduce: Bool) {
        guard drawn.target < 1 else { return }
        if reduce { drawn.jump(1); return }
        drawn.animate(to: 1, at: Self.now(), duration: PanelsTheme.Goal.lineReveal, curve: GoalCurve.inOut); wake()
    }
    /// The crosshair appears on the first inspected point and glides between later ones.
    func inspect(_ position: Double?, reduce: Bool) {
        let time = Self.now()
        guard let position else {
            guard scrubbing else { return }
            scrubbing = false
            scrub.animate(to: 0, at: time, duration: reduce ? 0 : PanelsTheme.Goal.instantDuration, curve: GoalCurve.standard)
            tipShown.animate(to: 0, at: time, duration: reduce ? 0 : 0.16, curve: GoalCurve.standard)
            wake(); return
        }
        if reduce || !scrubbing { cursor.jump(position); tipJump = true } else { cursor.animate(to: position, at: time) }
        scrubbing = true
        scrub.animate(to: 1, at: time, duration: reduce ? 0 : PanelsTheme.Goal.instantDuration, curve: GoalCurve.standard)
        tipShown.animate(to: 1, at: time, duration: reduce ? 0 : 0.16, curve: GoalCurve.standard)
        wake()
    }

    func shape(_ key: String, at time: Double) -> ([Double], [Bool])? {
        guard let track = tracks[key] else { return nil }
        guard let move = moves[key], !progress.resting else { return (track.shape, track.valid) }
        let p = progress.value(at: time)
        var values = move.to
        for index in values.indices where move.toValid[index] && index < move.from.count && move.fromValid[index] {
            values[index] = move.from[index] + (move.to[index] - move.from[index]) * p
        }
        return (values, move.toValid)
    }
    func presence(_ key: String, at time: Double) -> Double { tracks[key]?.presence.value(at: time) ?? 1 }
    func scale(at time: Double) -> (Double, Double) { (scaleMin.value(at: time), scaleMax.value(at: time)) }
    func drawnFraction(at time: Double) -> Double { drawn.value(at: time) }
    func cursorPosition(at time: Double) -> Double { cursor.value(at: time) }
    func scrubOpacity(at time: Double) -> Double { scrub.value(at: time) }
    func tipOpacity(at time: Double) -> Double { tipShown.value(at: time) }
    /// The tooltip sits beside the crosshair, flips before it would leave the plot, and centres on the points it describes.
    func tip(at time: Double, crosshair: CGFloat, middle: CGFloat, width: CGFloat, height: CGFloat) -> CGPoint {
        var x = crosshair + Self.gap
        if x + tipSize.width > width { x = crosshair - Self.gap - tipSize.width }
        x = Swift.min(Swift.max(0, x), Swift.max(0, width - tipSize.width))
        let y = Swift.min(Swift.max(0, middle - tipSize.height / 2), Swift.max(0, height - tipSize.height))
        if tipJump { tipX.jump(x); tipY.jump(y); tipJump = false }
        else {
            if tipX.target != x { tipX.animate(to: x, at: time) }
            if tipY.target != y { tipY.animate(to: y, at: time) }
        }
        return CGPoint(x: tipX.value(at: time), y: tipY.value(at: time))
    }
    /// Called once per painted frame: settles finished motion and pauses the clock when nothing moves.
    func settle(at time: Double) {
        if !progress.resting, progress.settle(at: time) { moves = [:] }
        for key in tracks.keys { _ = tracks[key]?.presence.settle(at: time) }
        _ = scaleMin.settle(at: time); _ = scaleMax.settle(at: time)
        _ = drawn.settle(at: time); _ = cursor.settle(at: time); _ = tipX.settle(at: time); _ = tipY.settle(at: time)
        _ = scrub.settle(at: time); _ = tipShown.settle(at: time)
        for key in ticks.keys { _ = ticks[key]?.opacity.settle(at: time) }
        for key in picks.keys { _ = picks[key]?.opacity.settle(at: time) }
        ticks = ticks.filter { $0.value.present || !$0.value.opacity.resting }
        picks = picks.filter { $0.value.present || !$0.value.opacity.resting }
        let resting = progress.resting && tracks.values.allSatisfy(\.presence.resting) && scaleMin.resting && scaleMax.resting && drawn.resting && cursor.resting && tipX.resting && tipY.resting && scrub.resting && tipShown.resting && ticks.values.allSatisfy(\.opacity.resting) && picks.values.allSatisfy(\.opacity.resting)
        if resting, animating, !settleQueued {
            settleQueued = true
            Task { @MainActor in self.settleQueued = false; if self.isResting { self.animating = false } }
        }
    }
    private var isResting: Bool {
        progress.resting && tracks.values.allSatisfy(\.presence.resting) && scaleMin.resting && scaleMax.resting && drawn.resting && cursor.resting && tipX.resting && tipY.resting && scrub.resting && tipShown.resting && ticks.values.allSatisfy(\.opacity.resting) && picks.values.allSatisfy(\.opacity.resting)
    }
}

struct GoalLineChart: View {
    let data: [GoalChartDatum]
    let series: [GoalLineSeries]
    var hidden: Set<String> = []
    var height: CGFloat = PanelsTheme.Goal.plotHeight
    var domain: [Double]? = nil
    var integerTicks = false
    var title = "Reading"
    let formatTick: (Double) -> String
    let formatValue: (Double?) -> String
    /// Tooltip and dot colour for a series at an inspected datum.
    let pointColor: (GoalLineSeries, GoalChartDatum?) -> Color
    @Binding var active: String?
    var select: ((String) -> Void)? = nil
    var revealAtOnce = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var motion = GoalLineMotion()
    @State private var plotWidth: CGFloat = 0

    private var positions: [Double] { data.enumerated().map { $0.element.position ?? (data.count > 1 ? Double($0.offset) / Double(data.count - 1) : 0.5) } }
    private var visible: [GoalLineSeries] { series.filter { !hidden.contains($0.key) } }
    /// Clean gridlines: the smallest step of 1, 2, 2.5 or 5 covering the shown series in four rows, from zero unless the data dips below it.
    private var steady: (Double, Double, [Double]) {
        if let domain, domain.count == 2 { return (domain[0], domain[1], (0...4).map { clean(domain[0] + (domain[1] - domain[0]) * Double($0) / 4) }) }
        let numbers = visible.flatMap { $0.values.compactMap { $0 } }.filter(\.isFinite)
        guard let low = numbers.min(), let high = numbers.max() else { return GoalLineGeometry.scale(low: 0, high: 100) }
        return GoalLineGeometry.scale(low: low, high: high)
    }
    private func clean(_ value: Double) -> Double { Double(String(format: "%.12g", value)) ?? value }
    private var tickValues: [Double] { steady.2.filter { !integerTicks || $0 == $0.rounded() } }
    private var signature: String {
        series.map { line in "\(line.key):\(line.smooth):" + line.values.map { $0.map { String($0) } ?? "nil" }.joined(separator: ",") + line.breaks.map { $0 ? "1" : "0" }.joined() }.joined(separator: ";") + "|" + positions.map { String($0) }.joined(separator: ",")
    }
    private var targets: [String: ([Double], [Bool])] {
        Dictionary(series.map { line in
            let sampled = GoalLineGeometry.samples(values: line.values, positions: positions, breaks: line.breaks, smooth: line.smooth, count: GoalLineMotion.samples)
            var values = sampled.map { $0 ?? .nan }
            // Gaps hold their neighbour so a line that gains a reading morphs from a nearby height.
            var last = values.first { $0.isFinite } ?? 0
            for index in values.indices { if values[index].isFinite { last = values[index] } else { values[index] = last } }
            return (line.key, (values, sampled.map { $0 != nil }))
        }, uniquingKeysWith: { a, _ in a })
    }
    private var tickEntries: [(String, Double, String)] { tickValues.map { (String($0), $0, formatTick($0)) } }
    /// Axis labels that fit: at most one per 76 points, counted back from the latest so the newest date always shows.
    private var pickEntries: [(String, Double, String)] {
        guard plotWidth > 0, !data.isEmpty else { return [] }
        let room = max(2, Int(floor(plotWidth / 76)))
        let stride = Int(ceil(Double(data.count) / Double(room)))
        let chosen = Array(data.indices.reversed()).enumerated().filter { $0.offset % stride == 0 }.map(\.element).reversed()
        return chosen.map { ("\(data[$0].id):\(data[$0].label)", positions[$0], data[$0].label) }
    }
    private var gutterWidth: CGFloat {
        let font = UIFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        return 10 + (tickValues.map { (formatTick($0) as NSString).size(withAttributes: [.font: font]).width }.max() ?? 0).rounded(.up)
    }
    private var activeIndex: Int? { data.firstIndex { $0.id == active } }

    var body: some View {
        let gutter = gutterWidth
        TimelineView(.animation(minimumInterval: nil, paused: !motion.animating)) { timeline in
            let time = timeline.date.timeIntervalSinceReferenceDate
            frame(at: time, gutter: gutter)
        }
        .frame(height: height + 26)
        .onGeometryChange(for: CGFloat.self) { max(0, $0.size.width - gutter) } action: { plotWidth = $0 }
        .onAppear {
            motion.setTargets(targets, hidden: hidden, reduce: true)
            motion.setScale(min: steady.0, max: steady.1, reduce: true)
            motion.setTicks(tickEntries, initial: true)
            if reduceMotion || revealAtOnce { motion.reveal(reduce: true) }
        }
        .onChange(of: signature) { motion.setTargets(targets, hidden: hidden, reduce: reduceMotion) }
        .onChange(of: hidden) { _, next in motion.setHidden(next, reduce: reduceMotion) }
        .onChange(of: "\(steady.0):\(steady.1)") { motion.setScale(min: steady.0, max: steady.1, reduce: reduceMotion) }
        .onChange(of: tickEntries.map(\.0)) { motion.setTicks(tickEntries, initial: false) }
        .onChange(of: pickEntries.map(\.0), initial: true) { old, _ in motion.setPicks(pickEntries, initial: old.isEmpty) }
        .onChange(of: activeIndex) { _, index in motion.inspect(index.map { positions[$0] }, reduce: reduceMotion) }
        .onChange(of: data.map(\.id)) { if active != nil { active = nil } }
        .onScrollVisibilityChange(threshold: 0.3) { shown in if shown, !data.isEmpty { motion.reveal(reduce: reduceMotion) } }
        .accessibilityElement(children: .contain).accessibilityLabel(title)
        .accessibilityAdjustableAction { direction in
            guard !data.isEmpty else { return }
            let index = activeIndex ?? data.count - 1
            active = data[max(0, min(data.count - 1, index + (direction == .increment ? 1 : -1)))].id
        }
    }

    @ViewBuilder private func frame(at time: Double, gutter: CGFloat) -> some View {
        let (lo, hi) = motion.scale(at: time)
        let span = hi - lo == 0 ? 1 : hi - lo
        let h = height, w = plotWidth
        let top = GoalLineMotion.top
        let rowY = { (value: Double) -> CGFloat in (top + (1 - (value - lo) / span) * (h - top)).rounded() + 0.5 }
        let at = motion.cursorPosition(at: time)
        let single = data.count == 1
        let cursorX = CGFloat((single ? 0.5 : at)) * w
        let dots: [(GoalLineSeries, CGFloat)] = visible.compactMap { line in
            guard let shape = motion.shape(line.key, at: time), motion.presence(line.key, at: time) > 0.5 else { return nil }
            let value = Self.value(shape, at: single ? 0.5 : at)
            guard let value else { return nil }
            let p = motion.presence(line.key, at: time)
            return (line, top + (1 - ((lo + (value - lo) * p) - lo) / span) * (h - top))
        }
        let middle = dots.isEmpty ? h / 2 : dots.map(\.1).reduce(0, +) / CGFloat(dots.count)
        let tip = motion.tip(at: time, crosshair: (at * w).rounded() + 0.5, middle: middle, width: w, height: h)
        let scrub = motion.scrubOpacity(at: time), tipOpacity = motion.tipOpacity(at: time)
        let inspected = activeIndex.map { data[$0] } ?? data.last
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                Canvas { context, size in
                    // Gridlines ride the scale, so a taller range slides them while new rows fade in.
                    for fade in motion.ticks.values where fade.value != tickValues.first || !fade.present {
                        let y = rowY(fade.value)
                        context.stroke(Path { $0.move(to: .init(x: 0, y: y)); $0.addLine(to: .init(x: size.width, y: y)) }, with: .color(PanelsTheme.Goal.grid.opacity(fade.opacity.value(at: time))), lineWidth: 1)
                    }
                    context.stroke(Path { $0.move(to: .init(x: 0, y: h - 0.5)); $0.addLine(to: .init(x: size.width, y: h - 0.5)) }, with: .color(PanelsTheme.border), lineWidth: 1)
                    let cx = (at * size.width).rounded() + 0.5
                    if scrub > 0 { context.stroke(Path { $0.move(to: .init(x: cx, y: top - 4)); $0.addLine(to: .init(x: cx, y: h)) }, with: .color(PanelsTheme.border.opacity(scrub)), lineWidth: 1) }
                    var series = context
                    series.clip(to: Path(CGRect(x: -8, y: -16, width: motion.drawnFraction(at: time) * (size.width + 16), height: h + 32)))
                    for line in self.series {
                        guard let shape = motion.shape(line.key, at: time) else { continue }
                        let p = motion.presence(line.key, at: time)
                        guard p > 0 else { continue }
                        var layer = series; layer.opacity = p
                        let y = { (value: Double) -> CGFloat in top + (1 - ((lo + (value - lo) * p) - lo) / span) * (h - top) }
                        let runs = Self.runs(shape, width: size.width, y: y)
                        if line.area && !single {
                            var area = Path()
                            for run in runs where !run.isEmpty {
                                area.addLines(run); area.addLine(to: .init(x: run.last!.x, y: h)); area.addLine(to: .init(x: run[0].x, y: h)); area.closeSubpath()
                            }
                            layer.fill(area, with: .color(line.color.opacity(0.09)))
                        }
                        guard !single || line.dashed else { continue }
                        var stroke = Path()
                        for run in runs where !run.isEmpty { stroke.addLines(run.count == 1 ? [run[0], CGPoint(x: run[0].x + 0.01, y: run[0].y)] : run) }
                        let shading: GraphicsContext.Shading = line.stops.map { .linearGradient(Gradient(stops: $0), startPoint: .zero, endPoint: CGPoint(x: size.width, y: 0)) } ?? .color(line.color)
                        layer.stroke(stroke, with: shading, style: line.dashed ? StrokeStyle(lineWidth: 1.75, lineCap: .round, lineJoin: .round, dash: [4, 5]) : StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                    }
                    for (line, cy) in dots {
                        let opacity = single && !line.dashed ? 1 : scrub
                        guard opacity > 0 else { continue }
                        let dot = Path(ellipseIn: CGRect(x: cursorX - 4, y: cy - 4, width: 8, height: 8))
                        var layer = context; layer.opacity = opacity
                        layer.fill(dot, with: .color(pointColor(line, inspected)))
                        layer.stroke(dot, with: .color(PanelsTheme.Goal.surface), lineWidth: 2)
                    }
                }
                .frame(height: h)
                .overlay(alignment: .topLeading) {
                    tooltip(inspected)
                        .onGeometryChange(for: CGSize.self) { $0.size } action: { motion.tipSize = $0 }
                        .scaleEffect(0.96 + 0.04 * tipOpacity).opacity(tipOpacity)
                        .offset(x: tip.x, y: tip.y)
                        .allowsHitTesting(false)
                }
                .overlay { inspection }
                Canvas { context, size in
                    for fade in motion.ticks.values {
                        let text = context.resolve(Text(fade.label).font(.system(size: 11)).monospacedDigit().foregroundStyle(PanelsTheme.faint))
                        var layer = context; layer.opacity = fade.opacity.value(at: time)
                        layer.draw(text, at: CGPoint(x: 10, y: rowY(fade.value) + 8), anchor: .leading)
                    }
                }
                .frame(width: gutter, height: h + 16)
                .padding(.vertical, -8)
            }
            Canvas { context, size in
                for fade in motion.picks.values {
                    let text = context.resolve(Text(fade.label).font(.system(size: 11)).monospacedDigit().foregroundStyle(PanelsTheme.faint))
                    var layer = context; layer.opacity = fade.opacity.value(at: time)
                    layer.draw(text, at: CGPoint(x: fade.value * w, y: 8), anchor: UnitPoint(x: fade.value, y: 0))
                }
                motion.settle(at: time)
            }
            .frame(height: 26)
            .padding(.trailing, gutter)
        }
        .overlay { if data.isEmpty { Text("No readings yet").font(.system(size: 13)).foregroundStyle(PanelsTheme.faint).padding(.trailing, gutter).padding(.bottom, 26) } }
    }

    private var inspection: some View {
        GoalLineInspection(changed: { point in
            guard !data.isEmpty, plotWidth > 0 else { return }
            let fraction = max(0, min(1, point.x / plotWidth))
            let index = positions.indices.min { abs(positions[$0] - fraction) < abs(positions[$1] - fraction) } ?? data.count - 1
            if active != data[index].id { active = data[index].id }
        }, ended: { inspecting in
            if inspecting, let active, let select { select(active) }
            active = nil
        }, scrolling: { active = nil })
        .accessibilityHidden(true)
    }

    private func tooltip(_ datum: GoalChartDatum?) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(datum?.label ?? "").font(.system(size: 11)).foregroundStyle(PanelsTheme.muted).lineLimit(1).monospacedDigit().padding(.bottom, 2)
            ForEach(visible, id: \.key) { line in
                HStack(spacing: 8) {
                    Group {
                        if line.dashed {
                            Canvas { context, size in
                                var x: CGFloat = 0
                                while x < size.width { context.fill(Path(roundedRect: CGRect(x: x, y: 0, width: min(3, size.width - x), height: size.height), cornerRadius: 1), with: .color(pointColor(line, datum))); x += 5 }
                            }
                        } else { RoundedRectangle(cornerRadius: 2).fill(pointColor(line, datum)) }
                    }.frame(width: 10, height: 2.5)
                    Text(line.label).foregroundStyle(PanelsTheme.muted).lineLimit(1).truncationMode(.tail)
                    Spacer(minLength: 8)
                    Text(datum.flatMap { d in data.firstIndex(of: d).map { line.values[$0] } }.map(formatValue) ?? "No reading").fontWeight(.medium).foregroundStyle(PanelsTheme.strong).monospacedDigit().lineLimit(1)
                }.font(.system(size: 13))
            }
        }
        .padding(.vertical, 10).padding(.horizontal, 12)
        .frame(minWidth: 140, maxWidth: min(220, max(140, plotWidth)), alignment: .leading)
        .background(PanelsTheme.Goal.raised, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(PanelsTheme.Goal.grid))
        .shadow(color: Color(red: 10 / 255, green: 9 / 255, blue: 8 / 255).opacity(0.45), radius: 10, y: 8)
        .fixedSize()
    }

    private static func runs(_ shape: ([Double], [Bool]), width: CGFloat, y: (Double) -> CGFloat) -> [[CGPoint]] {
        var runs: [[CGPoint]] = [], run: [CGPoint] = []
        let count = max(1, shape.0.count - 1)
        for index in shape.0.indices {
            if shape.1[index] { run.append(CGPoint(x: CGFloat(index) / CGFloat(count) * width, y: y(shape.0[index]))) }
            else if !run.isEmpty { runs.append(run); run = [] }
        }
        if !run.isEmpty { runs.append(run) }
        return runs
    }
    /// A sampled line's value at x, straight between samples; a gap has no value.
    static func value(_ shape: ([Double], [Bool]), at x: Double) -> Double? {
        let count = shape.0.count
        guard count > 1 else { return shape.0.first }
        let at = max(0, min(1, x)) * Double(count - 1)
        let low = Int(floor(at)), high = min(count - 1, low + 1)
        guard shape.1[low], shape.1[high] else { return shape.1[low] && at - Double(low) < 0.5 ? shape.0[low] : shape.1[high] ? shape.0[high] : nil }
        return shape.0[low] + (shape.0[high] - shape.0[low]) * (at - Double(low))
    }
}
