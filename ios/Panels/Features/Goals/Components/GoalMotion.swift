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
            ForEach(Array(formatted.reversed().enumerated()).reversed(), id: \.offset) { position, character in
                if let digit = character.wholeNumberValue, !reduceMotion {
                    GoalDigit(digit: digit, increasing: (value ?? 0) >= (previous ?? value ?? 0), size: size * scale)
                        .id("digit-\(position)")
                } else { Text(String(character)).font(.system(size: size * scale, weight: .medium)).monospacedDigit() }
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
    @State private var moving = false
    private var height: CGFloat { size * 1.18 }
    var body: some View {
        Color.clear.frame(width: size * 0.64, height: height)
            .overlay(alignment: .top) {
                GoalDigitWindow(position: position, size: size)
                    .blur(radius: moving ? 0.45 : 0)
                    .allowsHitTesting(false).accessibilityHidden(true)
            }
            .mask(LinearGradient(stops: [.init(color: .clear, location: 0), .init(color: .black, location: 0.12), .init(color: .black, location: 0.88), .init(color: .clear, location: 1)], startPoint: .top, endPoint: .bottom))
            .clipped().contentShape(Rectangle())
        .task(id: digit) {
            var transaction = Transaction(); transaction.disablesAnimations = true
            let old = Int(position.rounded()) % 10
            withTransaction(transaction) { position = Double(old + 10) }
            var destination = digit + 10
            if increasing, digit < old { destination += 10 }
            if !increasing, digit > old { destination -= 10 }
            moving = true
            withAnimation(PanelsTheme.Goal.smooth) { position = Double(destination) }
            try? await Task.sleep(for: .seconds(0.4))
            guard !Task.isCancelled else { return }
            moving = false
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
                guard y > -height, y < bounds.height + height else { continue }
                context.draw(Text(String(index % 10)).font(.system(size: size, weight: .medium)).monospacedDigit().foregroundStyle(PanelsTheme.strong), at: CGPoint(x: bounds.width / 2, y: y))
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
