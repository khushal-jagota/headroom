import SwiftUI

/// Place-value keyed wheels keep 9→0 travelling in the direction of the reading.
struct GoalCounter: View {
    let block: GoalJSON
    let value: Double?
    var size: CGFloat = PanelsTheme.Goal.reading
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ScaledMetric(relativeTo: .largeTitle) private var scale: CGFloat = 1
    @State private var previous: Double?
    private var formatted: String { GoalChartPresentation.format(block, value) }
    var body: some View {
        HStack(spacing: 0) {
            ForEach(GoalChartPresentation.counterParts(block, value: value)) { part in
                if let digit = part.digit, !reduceMotion {
                    GoalDigit(digit: digit, increasing: (value ?? 0) >= (previous ?? value ?? 0), size: size * scale)
                        .transition(.opacity.combined(with: .scale(scale: 0.01, anchor: .trailing)))
                } else { Text(part.text).font(.system(size: size * scale, weight: .medium)).monospacedDigit() }
            }
        }
        .contentShape(Rectangle())
        .foregroundStyle(PanelsTheme.strong)
        .animation(reduceMotion ? nil : PanelsTheme.Goal.morph, value: formatted.count)
        .accessibilityElement(children: .ignore).accessibilityLabel(formatted)
        .onChange(of: value) { old, _ in previous = old }
    }
}

private struct GoalDigit: View {
    let digit: Int
    let increasing: Bool
    let size: CGFloat
    @State private var position = 10.0
    init(digit: Int, increasing: Bool, size: CGFloat) {
        self.digit = digit; self.increasing = increasing; self.size = size
        _position = State(initialValue: Double(digit + 10))
    }
    private var height: CGFloat { size * 1.18 }
    var body: some View {
        Color.clear.frame(width: size * 0.64, height: height)
            .overlay(alignment: .top) {
                GoalDigitWindow(position: position, size: size)
                    .allowsHitTesting(false).accessibilityHidden(true)
            }
            .mask(LinearGradient(stops: [.init(color: .clear, location: 0), .init(color: .black, location: 0.12), .init(color: .black, location: 0.88), .init(color: .clear, location: 1)], startPoint: .top, endPoint: .bottom))
            .clipped().contentShape(Rectangle())
        .task(id: digit) {
            var transaction = Transaction(); transaction.disablesAnimations = true
            let old = Int(position.rounded()) % 10
            guard old != digit else { return }
            withTransaction(transaction) { position = Double(old + 10) }
            var destination = digit + 10
            if increasing, digit < old { destination += 10 }
            if !increasing, digit > old { destination -= 10 }
            withAnimation(PanelsTheme.Goal.smooth) { position = Double(destination) }
            try? await Task.sleep(for: .seconds(PanelsTheme.Goal.smoothDuration))
            guard !Task.isCancelled else { return }
            withTransaction(transaction) { position = Double(digit + 10) }
        }
    }
}

/// A bounded drawing surface keeps offscreen wheel digits out of hit testing
/// and accessibility. The counter supplies one formatted spoken reading.
private struct GoalDigitWindow: View, @MainActor Animatable {
    var position: Double
    let size: CGFloat
    var animatableData: Double { get { position } set { position = newValue } }
    var body: some View {
        Canvas { context, bounds in
            let height = size * 1.18
            for index in 0..<30 {
                let y = (Double(index) - position + 0.5) * height
                let offset = abs(Double(index) - position)
                guard offset < 1 else { continue }
                var glyph = context
                glyph.opacity = 1 - offset
                if offset > 0.02 { glyph.addFilter(.blur(radius: offset * 2)) }
                glyph.draw(Text(String(index % 10)).font(.system(size: size, weight: .medium)).monospacedDigit().foregroundStyle(PanelsTheme.strong), at: CGPoint(x: bounds.width / 2, y: y))
            }
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
