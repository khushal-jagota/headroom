import SwiftUI

/// The web's rolling number (animated-counter.tsx): one wheel per place value.
/// Every wheel turns the way the whole number moved, wrapping 9 → 0, on the smooth
/// spring; columns that appear or vanish morph their width on the morph spring.
struct GoalCounter: View {
    let block: GoalJSON
    let value: Double?
    var size: CGFloat = PanelsTheme.Goal.reading
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ScaledMetric(relativeTo: .largeTitle) private var scale: CGFloat = 1
    /// Unbounded wheel positions keyed by place value; position mod 10 is the digit on show.
    @State private var wheels: [String: Double] = [:]
    private var formatted: String { GoalChartPresentation.format(block, value) }
    private var parts: [GoalCounterPart] { GoalChartPresentation.counterParts(block, value: value) }
    var body: some View {
        let font = size * scale
        HStack(alignment: .center, spacing: 0) {
            ForEach(parts) { part in
                Group {
                    if let digit = part.digit {
                        GoalDigitWheel(position: wheels[part.id] ?? Double(digit), size: font)
                    } else {
                        Text(part.text).font(.system(size: font, weight: .medium)).monospacedDigit().fixedSize().frame(height: font)
                    }
                }.transition(reduceMotion ? .identity : .goalWidth)
            }
        }
        .contentShape(Rectangle())
        .foregroundStyle(PanelsTheme.strong)
        .animation(reduceMotion ? nil : PanelsTheme.Goal.morph, value: parts.map(\.id))
        .accessibilityElement(children: .ignore).accessibilityLabel(formatted)
        .onAppear { roll(from: nil, to: value) }
        .onChange(of: value) { old, next in roll(from: old, to: next) }
    }
    private func roll(from old: Double?, to next: Double?) {
        let fresh = GoalChartPresentation.counterParts(block, value: next)
        guard let old, let next, old.isFinite, next.isFinite else {
            wheels = Dictionary(fresh.compactMap { part in part.digit.map { (part.id, Double($0)) } }, uniquingKeysWith: { a, _ in a })
            return
        }
        let direction = next > old ? 1 : -1
        var targets: [String: Double] = [:]
        for part in fresh {
            guard let digit = part.digit else { continue }
            guard let position = wheels[part.id] else { targets[part.id] = Double(digit); continue }
            let shown = ((Int(position.rounded()) % 10) + 10) % 10
            targets[part.id] = position + Double(direction < 0 ? -((shown - digit + 10) % 10) : (digit - shown + 10) % 10)
        }
        if reduceMotion { wheels = targets; return }
        // New columns start on their digit; existing ones roll from where they stand.
        var placed = wheels
        for (key, target) in targets where wheels[key] == nil { placed[key] = target }
        wheels = placed
        withAnimation(PanelsTheme.Goal.smooth) { wheels = targets }
    }
}

/// One digit wheel. Layout is one em tall and one tabular zero wide; the window
/// reaches 0.16 em past the line box and feathers, as `.column` does on the web.
private struct GoalDigitWheel: View, @MainActor Animatable {
    var position: Double
    let size: CGFloat
    var animatableData: Double { get { position } set { position = newValue } }
    private static let feather = 0.16
    var body: some View {
        Text("0").font(.system(size: size, weight: .medium)).monospacedDigit().hidden()
            .frame(height: size)
            .overlay {
                Canvas { context, bounds in
                    let middle = bounds.height / 2
                    for digit in 0..<10 {
                        let raw = (Double(digit) - position).truncatingRemainder(dividingBy: 10)
                        let offset = ((raw + 15).truncatingRemainder(dividingBy: 10) + 10).truncatingRemainder(dividingBy: 10) - 5
                        guard abs(offset) < 1 else { continue }
                        var glyph = context
                        glyph.opacity = max(0, 1 - abs(offset))
                        if abs(offset) >= 0.02 { glyph.addFilter(.blur(radius: abs(offset) * 2)) }
                        glyph.draw(Text(String(digit)).font(.system(size: size, weight: .medium)).monospacedDigit().foregroundStyle(PanelsTheme.strong),
                                   at: CGPoint(x: bounds.width / 2, y: middle + offset * size))
                    }
                }
                .frame(height: size * (1 + 2 * Self.feather))
                .mask(LinearGradient(stops: [.init(color: .clear, location: 0), .init(color: .black, location: 1.5 * Self.feather / (1 + 2 * Self.feather)), .init(color: .black, location: 1 - 1.5 * Self.feather / (1 + 2 * Self.feather)), .init(color: .clear, location: 1)], startPoint: .top, endPoint: .bottom))
                .allowsHitTesting(false).accessibilityHidden(true)
            }
    }
}

struct GoalRangeSwitch: View {
    let choices: [(String, String)]
    @Binding var selection: String
    @Namespace private var highlight
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        HStack(spacing: 2) {
            ForEach(choices, id: \.0) { id, label in
                Button {
                    withAnimation(reduceMotion ? nil : PanelsTheme.Goal.snappy) { selection = id }
                } label: {
                    Text(label).panelsScaledFont(size: 11, weight: .medium).frame(maxWidth: .infinity).frame(minHeight: 36)
                        .background { if selection == id { RoundedRectangle(cornerRadius: 6).fill(PanelsTheme.Goal.selectedRange).matchedGeometryEffect(id: "selection", in: highlight) } }
                }.buttonStyle(.plain).foregroundStyle(selection == id ? PanelsTheme.strong : PanelsTheme.Goal.faint)
                    .accessibilityAddTraits(selection == id ? .isSelected : [])
            }
        }.padding(3).background(PanelsTheme.Goal.range, in: RoundedRectangle(cornerRadius: 8))
            .accessibilityElement(children: .contain).accessibilityLabel("Time range")
    }
}

struct GoalSaveFailure: View {
    let session: GoalSession
    var body: some View {
        if let error = session.saveError {
            VStack(alignment: .leading, spacing: 8) {
                Text(error).foregroundStyle(PanelsTheme.error)
                HStack(spacing: 16) {
                    Button("Retry save") { Task { await session.retry() } }
                    Button("Discard edit") { Task { await session.discard() } }
                }.disabled(session.busy)
            }.font(.footnote).accessibilityIdentifier("goal-save-error")
        }
    }
}

struct GoalPanel: ViewModifier {
    var padding: CGFloat = 24
    var radius: CGFloat = PanelsTheme.Goal.cardRadius
    func body(content: Content) -> some View {
        content.padding(padding).frame(maxWidth: .infinity, alignment: .leading)
            .background(PanelsTheme.Goal.surface, in: RoundedRectangle(cornerRadius: radius))
            .overlay(RoundedRectangle(cornerRadius: radius).strokeBorder(PanelsTheme.Goal.border))
    }
}

/// The web clears retained touch inspection when the next tap lands outside the
/// chart. Observe that tap without cancelling the destination control's action.
struct GoalInspectionDismissal: UIViewRepresentable {
    let clear: () -> Void
    func makeCoordinator() -> Coordinator { Coordinator(clear: clear) }
    func makeUIView(context: Context) -> Probe {
        let view = Probe(); view.isUserInteractionEnabled = false
        context.coordinator.probe = view
        view.changedWindow = { [weak coordinator = context.coordinator] in coordinator?.attach() }
        return view
    }
    func updateUIView(_ view: Probe, context: Context) { context.coordinator.clear = clear; context.coordinator.attach() }
    static func dismantleUIView(_ view: Probe, coordinator: Coordinator) { coordinator.detach() }
    final class Probe: UIView {
        var changedWindow: (() -> Void)?
        override func didMoveToWindow() { super.didMoveToWindow(); changedWindow?() }
    }
    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var clear: () -> Void
        weak var probe: Probe?
        weak var window: UIWindow?
        lazy var tap = UITapGestureRecognizer(target: self, action: #selector(outsideTap))
        init(clear: @escaping () -> Void) { self.clear = clear }
        func attach() {
            guard window !== probe?.window else { return }
            detach(); window = probe?.window
            tap.delegate = self; tap.cancelsTouchesInView = false
            window?.addGestureRecognizer(tap)
        }
        func detach() { window?.removeGestureRecognizer(tap); window = nil }
        @objc func outsideTap() { clear() }
        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
            guard let probe, let window else { return false }
            return !probe.convert(probe.bounds, to: window).contains(touch.location(in: window))
        }
        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool { true }
    }
}

/// The server supplies the next calendar boundary; suspension cancels the wait.
struct GoalBoundaryRefresh: ViewModifier {
    let at: Double?
    let refresh: () async -> Void
    @Environment(\.scenePhase) private var scenePhase
    func body(content: Content) -> some View {
        content.task(id: "\(at ?? 0):\(scenePhase == .active)") {
            guard scenePhase == .active, let at else { return }
            do {
                try await Task.sleep(for: .seconds(max(0, at - Date().timeIntervalSince1970) + 0.05))
                try Task.checkCancellation()
                await refresh()
            } catch { }
        }
    }
}
