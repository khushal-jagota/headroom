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
    @State private var wheels = GoalWheelPositions()
    private var formatted: String { GoalChartPresentation.format(block, value) }
    var body: some View {
        let font = size * scale
        let parts = GoalChartPresentation.counterParts(block, value: value)
        // Targets are worked out in the same update that shows the new value, so the
        // wheels start turning on the frame the number changes.
        let positions = wheels.roll(to: value, parts: parts)
        HStack(alignment: .center, spacing: 0) {
            ForEach(parts) { part in
                Group {
                    if let digit = part.digit {
                        GoalDigitWheel(position: positions[part.id] ?? Double(digit), size: font)
                            .animation(reduceMotion ? nil : PanelsTheme.Goal.smooth, value: positions[part.id])
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
    }
}

/// Unbounded wheel positions keyed by place value; position mod 10 is the digit on show.
/// Every wheel turns the way the whole number moved, wrapping 9 → 0 (animated-counter.tsx).
final class GoalWheelPositions {
    private var value: Double?
    private var positions: [String: Double] = [:]
    func roll(to next: Double?, parts: [GoalCounterPart]) -> [String: Double] {
        guard next != value || positions.isEmpty else { return positions }
        defer { value = next }
        let direction = (next ?? 0) > (value ?? 0) ? 1 : -1
        let fresh = value == nil || next == nil || value?.isFinite != true || next?.isFinite != true
        var targets: [String: Double] = [:]
        for part in parts {
            guard let digit = part.digit else { continue }
            guard !fresh, let position = positions[part.id] else { targets[part.id] = Double(digit); continue }
            let shown = ((Int(position.rounded()) % 10) + 10) % 10
            targets[part.id] = position + Double(direction < 0 ? -((shown - digit + 10) % 10) : (digit - shown + 10) % 10)
        }
        positions = targets
        return targets
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

/// The web's range control: one highlight that slides between equal segments on the
/// snappy spring (Motion's shared `layoutId`), with no press dimming.
struct GoalRangeSwitch: View {
    let choices: [(String, String)]
    @Binding var selection: String
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private var index: Int { choices.firstIndex { $0.0 == selection } ?? 0 }
    var body: some View {
        HStack(spacing: 2) {
            ForEach(choices, id: \.0) { id, label in
                Button { selection = id } label: {
                    Text(label).panelsScaledFont(size: 12, weight: .regular).frame(maxWidth: .infinity).frame(minHeight: 36).contentShape(Rectangle())
                }.buttonStyle(GoalPlainPress()).foregroundStyle(selection == id ? PanelsTheme.strong : PanelsTheme.faint)
                    .accessibilityAddTraits(selection == id ? .isSelected : [])
            }
        }
        .background(alignment: .leading) {
            GeometryReader { geometry in
                let width = (geometry.size.width - CGFloat(max(0, choices.count - 1)) * 2) / CGFloat(max(1, choices.count))
                RoundedRectangle(cornerRadius: 12).fill(PanelsTheme.Goal.selectedRange)
                    .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(PanelsTheme.Goal.grid))
                    .shadow(color: .black.opacity(0.2), radius: 1.5, y: 1)
                    .frame(width: width, height: geometry.size.height)
                    .offset(x: CGFloat(index) * (width + 2))
                    .animation(reduceMotion ? nil : PanelsTheme.Goal.snappy, value: index)
            }
        }
        .padding(4).background(PanelsTheme.Goal.range, in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(Color.white.opacity(0.024)))
        .accessibilityElement(children: .contain).accessibilityLabel("Time range")
    }
}

/// A button that shows no pressed state, like the web's plain buttons.
struct GoalPlainPress: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View { configuration.label }
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
