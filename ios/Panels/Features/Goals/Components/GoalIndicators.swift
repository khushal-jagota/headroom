import SwiftUI

/// The web's dial (gauge-geometry.ts): a 270° ring open at the bottom, in a 100 × 90 box.
enum GoalGaugeGeometry {
    static func point(_ fraction: Double, radius: Double, scale: CGFloat) -> CGPoint {
        let angle = (-135 + 270 * fraction) * Double.pi / 180
        return CGPoint(x: (50 + radius * sin(angle)) * scale, y: (50 - radius * cos(angle)) * scale)
    }
    static func arc(_ fraction: Double, scale: CGFloat) -> Path {
        let sweep = min(1, max(0, fraction))
        var path = Path()
        path.addArc(center: CGPoint(x: 50 * scale, y: 50 * scale), radius: 42 * scale, startAngle: .degrees(135), endAngle: .degrees(135 + 270 * sweep), clockwise: false)
        return path
    }
}

private struct GaugeBand: Equatable { let from: Double; let tone: String; let label: String }

/// Drives the read-only gauge as gauge.tsx does: the first fill sweeps the arc and
/// counts the number together on the smooth spring stretched to 0.768 s; later the
/// arc morphs and the count follows on the smooth spring. The state colour and label
/// change as the count on screen crosses a threshold.
@MainActor @Observable private final class GoalGaugeMotion {
    private(set) var animating = false
    private(set) var filled = false
    private(set) var liveBand: GaugeBand?
    @ObservationIgnored private var sweep = GoalSpringValue(0, spec: .visual(PanelsTheme.Goal.gaugeReveal))
    @ObservationIgnored private var count = GoalSpringValue(0, spec: .visual(PanelsTheme.Goal.gaugeReveal))
    @ObservationIgnored private var digitsWidth: GoalSpringValue?
    @ObservationIgnored var bands: [GaugeBand] = []
    @ObservationIgnored private var revealed = false
    /// The ring's colour crossfades over 0.24 s on the standard ease, as the web's CSS transition does.
    @ObservationIgnored private var toneFrom: Color?
    @ObservationIgnored private var toneTo: Color?
    @ObservationIgnored private var toneMix = GoalTween(1)
    func tone(_ next: Color, reduce: Bool) {
        guard next != toneTo else { return }
        let time = GoalLineMotion.now()
        if let current = toneTo, !reduce {
            toneFrom = current.mix(with: toneFrom ?? current, by: 1 - toneMix.value(at: time))
            toneMix.jump(0); toneMix.animate(to: 1, at: time, duration: PanelsTheme.Goal.standardDuration, curve: GoalCurve.standard)
            if !animating { animating = true }
        } else { toneMix.jump(1) }
        toneTo = next
    }
    func color(at time: Double, fallback: Color) -> Color {
        guard let to = toneTo else { return fallback }
        guard let from = toneFrom, !toneMix.resting else { return to }
        return from.mix(with: to, by: toneMix.value(at: time))
    }
    private static let reveal = GoalSpringSpec.visual(PanelsTheme.Goal.gaugeReveal)
    private static let morph = GoalSpringSpec.visual(0.42)
    private static let smooth = GoalSpringSpec.visual(0.4)
    func band(at value: Double) -> GaugeBand? { bands.filter { value >= $0.from - 1e-6 }.last }
    func show(fraction: Double, value: Double, reduce: Bool) {
        let time = GoalLineMotion.now()
        revealed = true
        if reduce { sweep = GoalSpringValue(fraction, spec: Self.morph); count = GoalSpringValue(value, spec: Self.smooth); filled = true; liveBand = band(at: value); return }
        var nextSweep = GoalSpringValue(sweep.value(at: time), spec: filled ? Self.morph : Self.reveal)
        var nextCount = GoalSpringValue(count.value(at: time), spec: filled ? Self.smooth : Self.reveal)
        // Motion drops inherited velocity on time-defined springs.
        nextSweep.animate(to: fraction, at: time, keepVelocity: false); nextCount.animate(to: value, at: time, keepVelocity: false)
        sweep = nextSweep; count = nextCount
        if !animating { animating = true }
    }
    func width(_ target: CGFloat, reduce: Bool) -> Void {
        let time = GoalLineMotion.now()
        guard var current = digitsWidth, !reduce else { digitsWidth = GoalSpringValue(target, spec: Self.smooth); return }
        if current.target != target { current.animate(to: target, at: time, keepVelocity: false); digitsWidth = current; if !animating { animating = true } }
    }
    func frame(at time: Double, value: Double) -> (sweep: Double, count: Double, width: CGFloat?) {
        let current = count.value(at: time)
        let next = band(at: current)
        if filled, next != liveBand { schedule { self.liveBand = next } }
        else if !filled, revealed, abs(current - value) < 1 { schedule { self.filled = true; self.liveBand = next } }
        let resting = sweep.settle(at: time) && count.settle(at: time) && (digitsWidth?.settle(at: time) ?? true) && toneMix.settle(at: time)
        if resting, animating { schedule { if self.sweep.resting && self.count.resting { self.animating = false } } }
        return (sweep.value(at: time), current, digitsWidth.map { CGFloat($0.value(at: time)) })
    }
    private func schedule(_ change: @escaping @MainActor () -> Void) { Task { @MainActor in change() } }
}

struct GoalGauge: View {
    let block: GoalJSON
    let headline: GoalChartHeadline
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var motion = GoalGaugeMotion()
    @State private var seen = false
    @State private var inspecting = false
    @State private var direction = 1.0
    private var limits: (Double, Double) {
        let domain = block["domain"].array.compactMap(\.number)
        let low = domain.count == 2 ? domain[0] : 0, high = domain.count == 2 ? domain[1] : 100
        return (low, high > low ? high : low + 1)
    }
    private var value: Double { headline.value ?? 0 }
    private var fraction: Double { min(1, max(0, (value - limits.0) / (limits.1 - limits.0))) }
    private var suffix: String { GoalChartPresentation.suffix(block) }
    private func digits(_ number: Double) -> String {
        let text = GoalChartPresentation.format(block, number, trim: true)
        return !suffix.isEmpty && text.hasSuffix(suffix) ? String(text.dropLast(suffix.count)) : text
    }
    private var bands: [GaugeBand] {
        guard let target = headline.target, block["binding"].isNull || headline.comparison != nil else { return [] }
        let atmost = GoalChartPresentation.direction(block) == "atmost"
        return [GaugeBand(from: min(0, value), tone: atmost ? "good" : "bad", label: atmost ? "Within limit" : "Below target"),
                GaugeBand(from: target + (atmost ? 0.00001 : 0), tone: atmost ? "bad" : "good", label: atmost ? "Above limit" : "On target")].sorted { $0.from < $1.from }
    }
    private func color(_ band: GaugeBand?) -> (Color, Color) {
        switch band?.tone { case "good": (PanelsTheme.success, PanelsTheme.success); case "bad": (PanelsTheme.error, PanelsTheme.error); default: (PanelsTheme.accent, PanelsTheme.accentText) }
    }
    var body: some View {
        let shownBand = reduceMotion || !motion.filled ? motion.band(at: value) : motion.liveBand
        let (tone, text) = color(shownBand)
        VStack(spacing: 10) {
            TimelineView(.animation(minimumInterval: nil, paused: !motion.animating)) { timeline in
                let frame = motion.frame(at: timeline.date.timeIntervalSinceReferenceDate, value: value)
                let ring = motion.color(at: timeline.date.timeIntervalSinceReferenceDate, fallback: tone)
                GeometryReader { geometry in
                    let scale = geometry.size.width / 100
                    ZStack(alignment: .top) {
                        Canvas { context, _ in
                            let style = StrokeStyle(lineWidth: 9 * scale, lineCap: .round)
                            context.stroke(GoalGaugeGeometry.arc(1, scale: scale), with: .color(ring.opacity(0.13)), style: style)
                            var fill = context; fill.opacity = min(1, max(0, frame.sweep / 0.02))
                            if frame.sweep > 0 { fill.stroke(GoalGaugeGeometry.arc(frame.sweep, scale: scale), with: .color(ring), style: style) }
                            if let target = headline.target {
                                let at = (target - limits.0) / (limits.1 - limits.0)
                                if at >= 0, at <= 1 {
                                    let tick = Path { $0.move(to: GoalGaugeGeometry.point(at, radius: 35, scale: scale)); $0.addLine(to: GoalGaugeGeometry.point(at, radius: 49, scale: scale)) }
                                    context.stroke(tick, with: .color(PanelsTheme.Goal.surface), style: StrokeStyle(lineWidth: 3 * scale, lineCap: .round))
                                    context.stroke(tick, with: .color(PanelsTheme.muted), style: StrokeStyle(lineWidth: 1.1 * scale, lineCap: .round))
                                }
                            }
                        }
                        VStack(spacing: 4) {
                            HStack(alignment: .firstTextBaseline, spacing: 1) {
                                Text(digits(frame.count)).lineLimit(1).fixedSize()
                                    .frame(width: frame.width, alignment: .trailing)
                                    .font(.system(size: 32, weight: .medium)).monospacedDigit().foregroundStyle(PanelsTheme.strong)
                                if !suffix.isEmpty { Text(suffix).font(.system(size: 16)).foregroundStyle(PanelsTheme.muted) }
                            }
                            .background { Text(digits(value)).font(.system(size: 32, weight: .medium)).monospacedDigit().fixedSize().hidden()
                                .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { motion.width($0, reduce: reduceMotion) } }
                            if !bands.isEmpty {
                                ZStack {
                                    let label = motion.filled ? shownBand?.label ?? "" : ""
                                    Text(label).id(label)
                                        .transition(.asymmetric(
                                            insertion: .modifier(active: GoalSwapState(y: 0.3 * 11 * direction, blur: 4, opacity: 0), identity: GoalSwapState(y: 0, blur: 0, opacity: 1)).animation(reduceMotion ? .linear(duration: PanelsTheme.Goal.instantDuration) : PanelsTheme.Goal.enter(PanelsTheme.Goal.standardDuration)),
                                            removal: .modifier(active: GoalSwapState(y: -0.3 * 11 * direction, blur: 2, opacity: 0), identity: GoalSwapState(y: 0, blur: 0, opacity: 1)).animation(reduceMotion ? .linear(duration: PanelsTheme.Goal.instantDuration) : PanelsTheme.Goal.standardEase(0.16))))
                                }
                                .font(.system(size: 11, weight: .medium)).foregroundStyle(text)
                                .frame(minWidth: 100, minHeight: 15.4)
                                .animation(reduceMotion ? nil : PanelsTheme.Goal.standardEase(PanelsTheme.Goal.standardDuration), value: text)
                            }
                        }.frame(width: geometry.size.width, height: geometry.size.width)
                    }
                }
                .aspectRatio(100 / 90, contentMode: .fit)
                .frame(maxWidth: 176)
            }
            .contentShape(Rectangle())
            .simultaneousGesture(TapGesture().onEnded { inspecting.toggle() })
            .overlay(alignment: .topTrailing) {
                if inspecting {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(block["title"].text).font(.system(size: 11)).foregroundStyle(PanelsTheme.muted)
                        Text("Current  " + GoalChartPresentation.format(block, headline.value, trim: true)).font(.system(size: 13))
                        if let target = headline.target { Text("Target  " + GoalChartPresentation.format(block, target, trim: true)).font(.system(size: 13)) }
                    }.padding(.vertical, 10).padding(.horizontal, 12).background(PanelsTheme.Goal.raised, in: RoundedRectangle(cornerRadius: 14)).allowsHitTesting(false)
                        .transition(.opacity.combined(with: .scale(scale: 0.96)))
                }
            }
            .background { if inspecting { GoalInspectionDismissal { inspecting = false } } }
            .animation(reduceMotion ? nil : PanelsTheme.Goal.standardEase(0.16), value: inspecting)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(GoalChartPresentation.format(block, headline.value) + (shownBand.map { ", " + $0.label } ?? ""))
        .accessibilityAction(named: "Inspect reading") { inspecting.toggle() }
        .onAppear { motion.bands = bands; if reduceMotion { motion.show(fraction: fraction, value: value, reduce: true) } }
        .goalInView(amount: 0.5) { if !seen { seen = true; motion.show(fraction: fraction, value: value, reduce: reduceMotion) } }
        .onChange(of: value) { old, next in direction = next < old ? -1 : 1; motion.bands = bands; if seen || reduceMotion { motion.show(fraction: fraction, value: next, reduce: reduceMotion) } }
        .onChange(of: bands) { motion.bands = bands }
        .onChange(of: tone, initial: true) { _, next in motion.tone(next, reduce: reduceMotion) }
    }
}

/// Copy that swaps in place: it rises from the side the value moved toward, out of a blur.
struct GoalSwapState: ViewModifier {
    let y: CGFloat
    let blur: CGFloat
    let opacity: Double
    func body(content: Content) -> some View { content.offset(y: y).blur(radius: blur).opacity(opacity) }
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
                        .transaction { $0.animation = nil } // The web swaps this label at once.
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
                // completion-reading.tsx: the new word rises 8 pt out of a 2 pt blur over 0.24 s; the old one leaves upward.
                ZStack(alignment: .leading) {
                    Text(done ? "Done" : "Not done").id(done).panelsScaledFont(size: PanelsTheme.Goal.completionReading, relativeTo: .largeTitle, weight: .medium)
                        .transition(.asymmetric(insertion: .modifier(active: GoalSwapState(y: reduceMotion ? 0 : 8, blur: reduceMotion ? 0 : 2, opacity: 0), identity: GoalSwapState(y: 0, blur: 0, opacity: 1)),
                                                removal: .modifier(active: GoalSwapState(y: reduceMotion ? 0 : -8, blur: reduceMotion ? 0 : 2, opacity: 0), identity: GoalSwapState(y: 0, blur: 0, opacity: 1))))
                }.animation(reduceMotion ? nil : PanelsTheme.Goal.standard, value: done)
            }
            Label(done ? "Completed" : status == "bad" ? "Overdue" : status == "attention" ? "Due today" : "Not done", systemImage: "circle.fill")
                .font(.footnote).foregroundStyle(PanelsTheme.Goal.tone(status))
        }
    }
}
