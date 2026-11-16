import SwiftUI
import UIKit
import UIKit.UIGestureRecognizerSubclass

/// A background probe locates Chief's own scroll view without a hit-test surface.
struct ChiefSwipeObservation: UIViewRepresentable {
    let enabled: Bool
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> Probe {
        let probe = Probe()
        probe.isUserInteractionEnabled = false
        context.coordinator.probe = probe
        probe.hierarchyChanged = { [weak coordinator = context.coordinator] in coordinator?.attach() }
        return probe
    }
    func updateUIView(_ probe: Probe, context: Context) {
        context.coordinator.enabled = enabled
        context.coordinator.attach()
    }
    static func dismantleUIView(_ probe: Probe, coordinator: Coordinator) { coordinator.detach() }

    final class Probe: UIView {
        var hierarchyChanged: (() -> Void)?
        override func didMoveToWindow() { super.didMoveToWindow(); hierarchyChanged?() }
        override func layoutSubviews() { super.layoutSubviews(); hierarchyChanged?() }
    }

    @MainActor final class Coordinator: NSObject {
        weak var probe: Probe?
        weak var scrollView: UIScrollView?
        var enabled = false
        private var capture: ChiefSwipeCapture?
        private var timer: Timer?
        private let observer = ChiefSwipeTouchObserver()

        override init() {
            super.init()
            observer.started = { [weak self] touch in self?.start(touch) }
            observer.moved = { [weak self] touch in self?.move(touch) }
            observer.finished = { [weak self] cancelled in self?.finish(cancelled) }
        }
        func attach() {
            guard enabled, ChiefSwipeLog.shared.isEnabled, probe?.window != nil else { detach(); return }
            var ancestor = probe?.superview
            while let candidate = ancestor, !(candidate is UIScrollView) { ancestor = candidate.superview }
            guard let page = ancestor as? UIScrollView, scrollView !== page else { return }
            detach()
            scrollView = page
            page.addGestureRecognizer(observer)
        }
        func detach() {
            timer?.invalidate(); timer = nil
            // A transition completion retains its own capture. No observer remains on a hidden page.
            capture?.stopObservingRecognizers()
            capture?.flush()
            capture = nil
            scrollView?.removeGestureRecognizer(observer)
            scrollView = nil
        }
        private func start(_ touch: UITouch) {
            guard enabled, ChiefSwipeLog.shared.isEnabled, let page = scrollView else { return }
            capture = ChiefSwipeCapture(page: page, touched: touch.view, origin: touch.location(in: page.window))
            capture?.snapshot(.start)
            let timer = Timer(timeInterval: ChiefSwipeLimits.sampleInterval, target: self, selector: #selector(sample), userInfo: nil, repeats: true)
            self.timer = timer
            RunLoop.main.add(timer, forMode: .common)
        }
        private func move(_ touch: UITouch) {
            guard let capture, let page = scrollView else { return }
            capture.update(touch.location(in: page.window))
        }
        @objc private func sample() {
            guard enabled, ChiefSwipeLog.shared.isEnabled, let capture,
                  capture.generation == ChiefSwipeLog.shared.generation else { detach(); return }
            capture.sampleIfDue()
        }
        private func finish(_ cancelled: Bool) {
            timer?.invalidate(); timer = nil
            guard let capture else { return }
            capture.snapshot(cancelled ? .cancel : .end)
            self.capture = nil
            // Read once after UIKit finishes delivery too; terminal recognizer states can change during delivery.
            DispatchQueue.main.async {
                capture.stopObservingRecognizers()
                capture.snapshot(cancelled ? .cancel : .end)
                capture.flush()
            }
        }
    }
}

/// Receives raw touches, but never enters a recognized state or prevents another recognizer.
final class ChiefSwipeTouchObserver: UIGestureRecognizer {
    var started: ((UITouch) -> Void)?
    var moved: ((UITouch) -> Void)?
    var finished: ((Bool) -> Void)?
    private weak var trackedTouch: UITouch?

    init() {
        super.init(target: nil, action: nil)
        cancelsTouchesInView = false
        delaysTouchesBegan = false
        delaysTouchesEnded = false
        requiresExclusiveTouchType = false
    }
    override func canPrevent(_ preventedGestureRecognizer: UIGestureRecognizer) -> Bool { false }
    override func canBePrevented(by preventingGestureRecognizer: UIGestureRecognizer) -> Bool { false }
    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        guard trackedTouch == nil, let touch = touches.first else { return }
        trackedTouch = touch
        started?(touch)
    }
    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {
        guard let touch = trackedTouch, touches.contains(touch) else { return }
        moved?(touch)
    }
    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
        guard let touch = trackedTouch, touches.contains(touch) else { return }
        moved?(touch)
        finished?(false)
        state = .failed
    }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) {
        guard let touch = trackedTouch, touches.contains(touch) else { return }
        finished?(true)
        state = .failed
    }
    override func reset() { super.reset(); trackedTouch = nil }
}

@MainActor private final class ChiefSwipeCapture: NSObject {
    private static var nextSession = Int(Date().timeIntervalSince1970 * 1_000)
    let generation = ChiefSwipeLog.shared.generation
    private let session: Int
    private weak var page: UIScrollView?
    private weak var touched: UIView?
    private weak var navigation: UINavigationController?
    private weak var followedTransition: AnyObject?
    private let origin: CGPoint
    private var displacement = ChiefSwipePoint(x: 0, y: 0)
    private var horizontal = false
    private var entries: [(ChiefSwipeRecord, Int)] = []
    private var byteCount = 2
    private let indices = NSMapTable<UIView, NSNumber>(keyOptions: .weakMemory, valueOptions: .strongMemory)
    private var nextIndex = 0
    private var lastSample: TimeInterval = 0
    private var wasCancelled: Bool?
    private var observesRecognizers = true
    private var observedRecognizers: [WeakRecognizer] = []
    private final class WeakRecognizer {
        weak var value: UIGestureRecognizer?
        init(_ value: UIGestureRecognizer) { self.value = value }
    }

    init(page: UIScrollView, touched: UIView?, origin: CGPoint) {
        Self.nextSession += 1
        session = Self.nextSession
        self.page = page; self.touched = touched; self.origin = origin
        super.init()
        var responder: UIResponder? = page
        while let current = responder {
            if let controller = current as? UIViewController, let navigation = controller.navigationController {
                self.navigation = navigation; break
            }
            responder = current.next
        }
    }
    func update(_ point: CGPoint) {
        displacement = ChiefSwipePoint(x: Double(point.x - origin.x), y: Double(point.y - origin.y))
        if abs(displacement.x) >= ChiefSwipeLimits.horizontalThreshold && abs(displacement.x) > abs(displacement.y) { horizontal = true }
        let now = Date().timeIntervalSince1970
        if horizontal && now - lastSample >= ChiefSwipeLimits.sampleInterval { snapshot(.sample) }
    }
    private func index(_ view: UIView) -> Int {
        if let value = indices.object(forKey: view) { return value.intValue }
        let value = nextIndex; nextIndex += 1
        indices.setObject(NSNumber(value: value), forKey: view)
        return value
    }
    func sampleIfDue() {
        guard horizontal, Date().timeIntervalSince1970 - lastSample >= ChiefSwipeLimits.sampleInterval else { return }
        snapshot(.sample)
    }
    func snapshot(_ phase: ChiefSwipeRecord.Phase, allowBeforeHorizontal: Bool = false) {
        guard ChiefSwipeLog.shared.isEnabled, generation == ChiefSwipeLog.shared.generation else { return }
        // Initial and recognizer-action snapshots remain transient until a horizontal attempt qualifies.
        if phase == .sample && !horizontal && !allowBeforeHorizontal { return }
        let now = Date().timeIntervalSince1970
        lastSample = now
        var views: [(UIView, ChiefSwipeViewSnapshot.Role)] = []
        var seen = Set<ObjectIdentifier>()
        func include(_ view: UIView, _ role: ChiefSwipeViewSnapshot.Role) {
            if seen.insert(ObjectIdentifier(view)).inserted { views.append((view, role)) }
        }
        if let page {
            include(page, .page)
            var ancestor = page.superview
            while let view = ancestor { include(view, .ancestor); ancestor = view.superview }
            var target = touched
            while let view = target, view !== page {
                include(view, view is UIScrollView ? .nested : .touched)
                target = view.superview
            }
        }
        var recognizers: [UIGestureRecognizer] = []
        var recognizerSeen = Set<ObjectIdentifier>()
        func includeRecognizer(_ gesture: UIGestureRecognizer) {
            guard !(gesture is ChiefSwipeTouchObserver), recognizerSeen.insert(ObjectIdentifier(gesture)).inserted else { return }
            recognizers.append(gesture)
            if observesRecognizers, !observedRecognizers.contains(where: { $0.value === gesture }) {
                gesture.addTarget(self, action: #selector(recognizerChanged(_:)))
                observedRecognizers.append(WeakRecognizer(gesture))
            }
        }
        for (view, _) in views { for gesture in view.gestureRecognizers ?? [] { includeRecognizer(gesture) } }
        var popRecognizers: [UIGestureRecognizer] = []
        if let pop = navigation?.interactivePopGestureRecognizer { popRecognizers.append(pop) }
        if #available(iOS 26.0, *), let pop = navigation?.interactiveContentPopGestureRecognizer { popRecognizers.append(pop) }
        for gesture in popRecognizers {
            includeRecognizer(gesture)
            if let view = gesture.view { include(view, .ancestor) }
        }
        let viewSnapshots = views.map { view, role in
            let presentation = view.layer.presentation()
            return ChiefSwipeViewSnapshot(index: index(view), parentIndex: view.superview.map(index), role: role,
                className: NSStringFromClass(type(of: view)), frame: rect(view.frame), bounds: rect(view.bounds),
                windowFrame: rect(view.convert(view.bounds, to: view.window)), transform: transform(view.transform),
                presentationFrame: presentation.map { rect($0.frame) },
                presentationTransform: presentation.map { transform(CATransform3DGetAffineTransform($0.transform)) },
                contentOffset: (view as? UIScrollView).map { point($0.contentOffset) })
        }
        let gestureSnapshots = recognizers.map { gesture in
            ChiefSwipeRecognizerSnapshot(className: NSStringFromClass(type(of: gesture)), viewIndex: gesture.view.map(index),
                role: popRecognizers.contains(where: { $0 === gesture }) ? .pop : (gesture.view as? UIScrollView)?.panGestureRecognizer === gesture ? .scroll : .other,
                state: state(gesture.state), isEnabled: gesture.isEnabled)
        }
        let transition = navigation?.transitionCoordinator
        let record = ChiefSwipeRecord(timestamp: now, session: session, phase: phase, displacement: displacement,
            views: viewSnapshots, recognizers: gestureSnapshots,
            navigation: ChiefSwipeNavigationSnapshot(stackCount: navigation?.viewControllers.count ?? 0,
                transitionInProgress: transition != nil, isInteractive: transition?.isInteractive ?? false,
                percentComplete: Double(transition?.percentComplete ?? 0), wasCancelled: wasCancelled),
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "",
            appBuild: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "",
            systemVersion: UIDevice.current.systemVersion)
        if let data = try? JSONEncoder().encode(record), data.count + 2 <= ChiefSwipeLimits.maximumBytes {
            let cost = data.count + 1
            while byteCount + cost > ChiefSwipeLimits.maximumBytes, !entries.isEmpty { byteCount -= entries.removeFirst().1 }
            entries.append((record, cost)); byteCount += cost
        }
        if horizontal, let transition, followedTransition !== transition as AnyObject {
            followedTransition = transition as AnyObject
            // The existing coordinator retains this capture through Chief's disappearance during a successful pop.
            transition.animate(alongsideTransition: nil) { [self] context in
                let cancelled = context.isCancelled
                Task { @MainActor [self] in
                    wasCancelled = cancelled
                    snapshot(.transitionComplete)
                    flush()
                }
            }
        }
    }
    /// Target actions expose recognized state changes that periodic snapshots can miss.
    /// They add no recognition policy, touch delay or dependency to the observed gesture.
    @objc private func recognizerChanged(_ gesture: UIGestureRecognizer) {
        guard observesRecognizers, ChiefSwipeLog.shared.isEnabled,
              generation == ChiefSwipeLog.shared.generation else { return }
        if let page, gesture is UIPanGestureRecognizer {
            let location = gesture.location(in: page.window)
            displacement = ChiefSwipePoint(x: Double(location.x - origin.x), y: Double(location.y - origin.y))
            if abs(displacement.x) >= ChiefSwipeLimits.horizontalThreshold && abs(displacement.x) > abs(displacement.y) {
                horizontal = true
            }
        }
        switch gesture.state {
        case .began, .ended, .cancelled:
            snapshot(.sample, allowBeforeHorizontal: true)
        case .changed:
            if Date().timeIntervalSince1970 - lastSample >= ChiefSwipeLimits.sampleInterval {
                snapshot(.sample, allowBeforeHorizontal: true)
            }
        default:
            break
        }
    }
    func stopObservingRecognizers() {
        observesRecognizers = false
        for observed in observedRecognizers {
            observed.value?.removeTarget(self, action: #selector(recognizerChanged(_:)))
        }
        observedRecognizers.removeAll()
    }
    func flush() {
        guard horizontal, !entries.isEmpty else { entries.removeAll(); byteCount = 2; return }
        let records = entries.map(\.0)
        entries.removeAll(); byteCount = 2
        let generation = generation
        // Defer storage until touch delivery ends. The storage owner checks generation again.
        DispatchQueue.main.async { ChiefSwipeLog.shared.append(records, generation: generation) }
    }
    private func point(_ value: CGPoint) -> ChiefSwipePoint { ChiefSwipePoint(x: Double(value.x), y: Double(value.y)) }
    private func rect(_ value: CGRect) -> ChiefSwipeRect {
        ChiefSwipeRect(x: Double(value.origin.x), y: Double(value.origin.y), width: Double(value.width), height: Double(value.height))
    }
    private func transform(_ value: CGAffineTransform) -> ChiefSwipeTransform {
        ChiefSwipeTransform(a: Double(value.a), b: Double(value.b), c: Double(value.c), d: Double(value.d), tx: Double(value.tx), ty: Double(value.ty))
    }
    private func state(_ value: UIGestureRecognizer.State) -> ChiefSwipeRecognizerSnapshot.State {
        switch value {
        case .possible: .possible
        case .began: .began
        case .changed: .changed
        case .ended: .ended
        case .cancelled: .cancelled
        case .failed: .failed
        @unknown default: .unknown
        }
    }
}
