import SwiftUI

/// Chart.js `easeInOutCubic`: the read-only bar chart's update curve.
struct GoalCubicInOut: CustomAnimation {
    let duration: TimeInterval
    static func progress(_ t: Double) -> Double { t < 0.5 ? 4 * t * t * t : 1 - pow(-2 * t + 2, 3) / 2 }
    func animate<V: VectorArithmetic>(value: V, time: TimeInterval, context: inout AnimationContext<V>) -> V? {
        guard time < duration else { return nil }
        return value.scaled(by: Self.progress(time / duration))
    }
}

/// Svelte's `cubicOut`: 1 − (1 − t)³.
struct GoalCubicOut: CustomAnimation {
    let duration: TimeInterval
    func animate<V: VectorArithmetic>(value: V, time: TimeInterval, context: inout AnimationContext<V>) -> V? {
        guard time < duration else { return nil }
        let t = time / duration
        return value.scaled(by: 1 - pow(1 - t, 3))
    }
}

/// A spring written the way the web's line chart writes it (`physical()` in
/// line-chart-integrated.tsx): Motion's visual duration as stiffness and damping,
/// with explicit rest thresholds.
struct GoalSpringSpec {
    let spring: Spring
    let restDelta: Double
    let restSpeed: Double
    static func visual(_ visualDuration: Double, bounce: Double = 0, restDelta: Double = 0.001) -> Self {
        let root = 2 * Double.pi / (visualDuration * 1.2)
        return .init(spring: Spring(mass: 1, stiffness: root * root, damping: 2 * (1 - bounce) * root), restDelta: restDelta, restSpeed: restDelta * 2)
    }
    static let settle = visual(0.4)
    static let glide = visual(0.26, restDelta: 0.0001)
    static let follow = GoalSpringSpec(spring: glide.spring, restDelta: 0.01, restSpeed: 0.02)
}

/// One spring-driven number, evaluated analytically at any time. Retargeting can
/// carry the current velocity, as Motion does for physical springs.
struct GoalSpringValue {
    let spec: GoalSpringSpec
    private(set) var target: Double
    private var origin: Double
    private var velocity0 = 0.0
    private var start = 0.0
    private(set) var resting = true
    init(_ value: Double, spec: GoalSpringSpec) { self.spec = spec; target = value; origin = value }
    func value(at time: Double) -> Double {
        resting ? target : origin + spec.spring.value(target: target - origin, initialVelocity: velocity0, time: max(0, time - start))
    }
    func velocity(at time: Double) -> Double {
        resting ? 0 : spec.spring.velocity(target: target - origin, initialVelocity: velocity0, time: max(0, time - start))
    }
    mutating func animate(to next: Double, at time: Double, keepVelocity: Bool = true) {
        let current = value(at: time), speed = keepVelocity ? velocity(at: time) : 0
        origin = current; velocity0 = speed; target = next; start = time
        resting = abs(next - current) < spec.restDelta && abs(speed) < spec.restSpeed
        if resting { origin = next }
    }
    mutating func jump(_ next: Double) { target = next; origin = next; velocity0 = 0; resting = true }
    /// Settles once both the distance and the speed fall under Motion's rest thresholds.
    mutating func settle(at time: Double) -> Bool {
        if !resting, abs(value(at: time) - target) < spec.restDelta, abs(velocity(at: time)) < spec.restSpeed { jump(target) }
        return resting
    }
}

/// A cubic-bezier tween from the current value, as Motion and CSS transitions run.
struct GoalTween {
    private(set) var target: Double
    private var origin: Double
    private var start = 0.0
    private var duration = 0.0
    private var delay = 0.0
    private var curve = UnitCurve.linear
    init(_ value: Double) { target = value; origin = value }
    var resting: Bool { duration == 0 }
    func value(at time: Double) -> Double {
        guard duration > 0 else { return target }
        let progress = min(1, max(0, (time - start - delay) / duration))
        return origin + (target - origin) * curve.value(at: progress)
    }
    mutating func animate(to next: Double, at time: Double, duration: Double, curve: UnitCurve, delay: Double = 0) {
        origin = value(at: time); target = next; start = time; self.duration = duration; self.curve = curve; self.delay = delay
        if duration <= 0 || origin == next { jump(next) }
    }
    mutating func jump(_ next: Double) { target = next; origin = next; duration = 0 }
    mutating func settle(at time: Double) -> Bool {
        if duration > 0, time >= start + delay + duration { jump(target) }
        return resting
    }
}

enum GoalCurve {
    static let standard = UnitCurve.bezier(startControlPoint: .init(x: 0.22, y: 1), endControlPoint: .init(x: 0.36, y: 1))
    static let inOut = UnitCurve.bezier(startControlPoint: .init(x: 0.65, y: 0), endControlPoint: .init(x: 0.35, y: 1))
}

/// Animates a view's laid-out width between zero and its natural width, so
/// neighbours slide as content arrives or leaves (Motion's `width: 0 ↔ auto`).
struct GoalWidthReveal: Layout, Animatable {
    var fraction: Double
    var animatableData: Double { get { fraction } set { fraction = newValue } }
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let size = subviews.first?.sizeThatFits(.unspecified) ?? .zero
        return CGSize(width: size.width * max(0, fraction), height: size.height)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard let subview = subviews.first else { return }
        let size = subview.sizeThatFits(.unspecified)
        subview.place(at: CGPoint(x: bounds.minX, y: bounds.midY), anchor: .leading, proposal: ProposedViewSize(size))
    }
}

struct GoalWidthPresence: ViewModifier {
    let fraction: Double
    func body(content: Content) -> some View {
        GoalWidthReveal(fraction: fraction) { content }.clipShape(GoalHorizontalClip()).opacity(fraction)
    }
}

/// Clips sideways only, so a wheel's feathered window can reach past its line box.
struct GoalHorizontalClip: Shape {
    func path(in rect: CGRect) -> Path { Path(rect.insetBy(dx: 0, dy: -rect.height)) }
}

extension AnyTransition {
    /// Motion's presence for counter columns: width and opacity from zero.
    static var goalWidth: AnyTransition { .modifier(active: GoalWidthPresence(fraction: 0), identity: GoalWidthPresence(fraction: 1)) }
}

extension View {
    /// Motion's `useInView({ once: true, amount })`: fires once the given share of this
    /// view lies inside the visible part of its scroll view.
    func goalInView(amount: Double, _ action: @escaping () -> Void) -> some View {
        onGeometryChange(for: Bool.self) { proxy in
            let frame = proxy.frame(in: .local)
            guard frame.height > 0, let visible = proxy.bounds(of: .scrollView) else { return true }
            return frame.intersection(visible).height / frame.height >= amount - 0.0001
        } action: { shown in if shown { action() } }
    }
}
