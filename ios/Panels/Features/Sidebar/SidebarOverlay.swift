import SwiftUI
import Observation
import QuartzCore
import UIKit

/// Where the Sidebar is and how it moves. One offset, from 0 (hidden) to the panel width
/// (open), places the panel, the dim and the shadow. A finger sets it directly. A release,
/// the button, a tap on the dim and a choice animate it here frame by frame, so the offset
/// is always where the panel is on screen and a finger can catch it mid-slide.
@MainActor @Observable final class SidebarPresentation {
    /// The panel's distance out from hidden, in points. Past the width only while stretched.
    private(set) var offset: CGFloat = 0
    /// The Sidebar layer's opacity. Below 1 only while Reduce Motion fades it.
    private(set) var opacity: Double = 1
    /// True while the Sidebar is on screen at all: resting open, under a finger, or sliding.
    private(set) var shown = false
    /// Where the Sidebar rests or is going. A finger does not change it until release.
    private(set) var open = false

    @ObservationIgnored private var motion: Motion?
    @ObservationIgnored private var dragStart: CGFloat = 0
    @ObservationIgnored private lazy var ticker = FrameTicker { [weak self] now in self?.step(now) }

    private enum Motion {
        /// A release: a spring with no bounce that starts at the finger's speed.
        case spring(from: CGFloat, to: CGFloat, velocity: CGFloat, start: CFTimeInterval)
        /// The button, a tap on the dim or a choice: the approved timing curves.
        case curve(from: CGFloat, to: CGFloat, duration: Double, curve: UnitCurve, start: CFTimeInterval)
        /// Reduce Motion: the layer fades in or out where it is.
        case fade(from: Double, to: Double, start: CFTimeInterval)
    }

    private static let settle = Spring(duration: 0.3, bounce: 0)
    private static let openCurve = UnitCurve.bezier(startControlPoint: UnitPoint(x: 0.2, y: 0.7), endControlPoint: UnitPoint(x: 0.5, y: 1))
    private static let closeCurve = UnitCurve.bezier(startControlPoint: UnitPoint(x: 0.4, y: 0), endControlPoint: UnitPoint(x: 1, y: 1))
    private static let openDuration = 0.22
    private static let closeDuration = PanelsTheme.Motion.base

    /// How far out the panel is, from 0 to 1, for the dim and the shadow.
    func progress(width: CGFloat) -> CGFloat { width > 0 ? min(1, offset / width) : 0 }

    // MARK: Finger

    /// A finger takes the panel where it is, even mid-slide.
    func beginDrag() {
        stop()
        dragStart = offset
        opacity = 1
        shown = true
    }

    func drag(_ translation: CGFloat, width: CGFloat) {
        offset = SidebarMotion.position(dragStart + translation, width: width)
    }

    /// Returns true when the release leaves the Sidebar open.
    @discardableResult func endDrag(velocity: CGFloat, width: CGFloat, reduceMotion: Bool) -> Bool {
        let opens = SidebarMotion.opens(offset: offset, velocity: velocity, width: width)
        open = opens
        let target = opens ? width : 0
        if reduceMotion {
            if opens { offset = target; finish() } else { start(.fade(from: 1, to: 0, start: CACurrentMediaTime())) }
            return opens
        }
        // A stretched panel returns without first travelling further out.
        let speed = offset > width ? min(velocity, 0) : velocity
        start(.spring(from: offset, to: target, velocity: speed, start: CACurrentMediaTime()))
        return opens
    }

    // MARK: Button, dim and choice

    func show(width: CGFloat, reduceMotion: Bool) {
        open = true
        shown = true
        if reduceMotion {
            let from = shownOpacity
            opacity = from
            offset = width
            start(.fade(from: from, to: 1, start: CACurrentMediaTime()))
        } else {
            opacity = 1
            start(.curve(from: offset, to: width, duration: Self.openDuration, curve: Self.openCurve, start: CACurrentMediaTime()))
        }
    }

    func hide(reduceMotion: Bool) {
        guard shown else { return }
        open = false
        if reduceMotion {
            start(.fade(from: opacity, to: 0, start: CACurrentMediaTime()))
        } else {
            opacity = 1
            start(.curve(from: offset, to: 0, duration: Self.closeDuration, curve: Self.closeCurve, start: CACurrentMediaTime()))
        }
    }

    /// Hidden at once, with no motion: a server change or a notification's page.
    func reset() {
        stop()
        open = false
        settleClosed()
    }

    // MARK: Frames

    /// A fade from hidden starts clear; a fade that reverses starts where it is.
    private var shownOpacity: Double { motion == nil && offset == 0 ? 0 : opacity }

    private func start(_ next: Motion) {
        motion = next
        ticker.start()
    }

    private func stop() {
        motion = nil
        ticker.stop()
    }

    private func step(_ now: CFTimeInterval) {
        guard let motion else { return ticker.stop() }
        switch motion {
        case let .spring(from, to, velocity, start):
            let time = now - start
            let value = from + Self.settle.value(target: to - from, initialVelocity: velocity, time: time)
            // The panel stops at its rest the moment it reaches it, so it never bounces.
            if (value - to) * (from - to) <= 0 || time >= Self.settle.settlingDuration(target: to - from, initialVelocity: velocity, epsilon: 0.5) {
                offset = to
                finish()
            } else {
                offset = value
            }
        case let .curve(from, to, duration, curve, start):
            let fraction = min(1, (now - start) / duration)
            offset = from + (to - from) * CGFloat(curve.value(at: fraction))
            if fraction >= 1 { finish() }
        case let .fade(from, to, start):
            let fraction = min(1, (now - start) / Self.closeDuration)
            opacity = from + (to - from) * fraction
            if fraction >= 1 { finish() }
        }
    }

    private func finish() {
        stop()
        if !open { settleClosed() }
    }

    private func settleClosed() {
        offset = 0
        opacity = 1
        shown = false
    }
}

/// Calls back once per screen frame while started.
@MainActor private final class FrameTicker: NSObject {
    private var link: CADisplayLink?
    private let tick: @MainActor (CFTimeInterval) -> Void

    init(tick: @escaping @MainActor (CFTimeInterval) -> Void) { self.tick = tick }

    func start() {
        guard link == nil else { return }
        let link = CADisplayLink(target: self, selector: #selector(frame(_:)))
        link.add(to: .main, forMode: .common)
        self.link = link
    }

    func stop() {
        link?.invalidate()
        link = nil
    }

    @objc private func frame(_ link: CADisplayLink) { tick(link.targetTimestamp) }
}

/// The dim and the sliding panel. It alone reads the offset, so a moving finger redraws
/// the Sidebar, not the shell.
struct SidebarOverlay: View {
    let presentation: SidebarPresentation
    let width: CGFloat
    let current: AppDestination
    let reviewBadge: Int
    let select: (AppDestination) -> Void
    let close: () -> Void
    let beginDrag: () -> Void
    let drag: (CGFloat) -> Void
    let endDrag: (CGFloat) -> Void

    var body: some View {
        let shown = presentation.progress(width: width)
        ZStack(alignment: .leading) {
            Color.black.opacity(0.55 * shown).ignoresSafeArea()
                .onTapGesture { close() }
                .accessibilityAddTraits(.isButton)
                .accessibilityLabel("Close sidebar")
            SidebarView(current: current, reviewBadge: reviewBadge, select: select)
                .frame(width: width)
                // The colour reaches past the leading edge, so a stretched panel leaves no gap.
                .background(PanelsTheme.Web.surface.padding(.leading, -SidebarMotion.stretchLimit).ignoresSafeArea())
                .shadow(color: .black.opacity(0.65 * shown), radius: 21, x: 18)
                .offset(x: presentation.offset - width)
                .accessibilityAddTraits(.isModal)
        }
        .opacity(presentation.opacity)
        .gesture(SidebarPan(began: beginDrag, changed: drag, ended: endDrag))
        .accessibilityAction(.escape) { close() }
    }
}

/// The drag over the whole Sidebar layer, the panel and the dim. It claims only a drag
/// that starts mostly sideways, so the card lists keep vertical drags. Its claim cancels
/// the touch for the view under the finger, so a card under a drag does not open.
private struct SidebarPan: UIGestureRecognizerRepresentable {
    let began: () -> Void
    let changed: (CGFloat) -> Void
    /// The release speed, in points per second.
    let ended: (CGFloat) -> Void

    func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator { Coordinator() }

    func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
        let recognizer = UIPanGestureRecognizer()
        recognizer.maximumNumberOfTouches = 1
        recognizer.delegate = context.coordinator
        return recognizer
    }

    func handleUIGestureRecognizerAction(_ recognizer: UIPanGestureRecognizer, context: Context) {
        let translation = recognizer.translation(in: recognizer.view).x
        switch recognizer.state {
        case .began: began(); changed(translation)
        case .changed: changed(translation)
        case .ended: ended(recognizer.velocity(in: recognizer.view).x)
        case .cancelled, .failed: ended(0)
        default: break
        }
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        func gestureRecognizerShouldBegin(_ recognizer: UIGestureRecognizer) -> Bool {
            guard let pan = recognizer as? UIPanGestureRecognizer else { return false }
            let velocity = pan.velocity(in: pan.view)
            return abs(velocity.x) > abs(velocity.y)
        }

        /// A scroll view under the finger waits until this drag has declined.
        func gestureRecognizer(_ recognizer: UIGestureRecognizer, shouldBeRequiredToFailBy other: UIGestureRecognizer) -> Bool {
            other.view is UIScrollView
        }
    }
}
