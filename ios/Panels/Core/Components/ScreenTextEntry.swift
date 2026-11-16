import SwiftUI
import UIKit

/// A visible screen owns focus. Draft content remains with its existing model.
struct ScreenTextEntryScope<Content: View>: View {
    var dismissesBackgroundTaps = false
    @ViewBuilder let content: (ScreenTextEntryContext) -> Content
    @FocusState private var focusedInput: UUID?
    @State private var inputs = ScreenTextEntryInputs()

    var body: some View {
        let entry = ScreenTextEntryContext(focus: $focusedInput, inputs: inputs)
        content(entry)
            .environment(\.screenTextEntry, entry)
            .background { ScreenTextEntryTransition(entry: entry, dismissesBackgroundTaps: dismissesBackgroundTaps).allowsHitTesting(false).accessibilityHidden(true) }
    }
}

@MainActor struct ScreenTextEntryContext {
    let focus: FocusState<UUID?>.Binding
    fileprivate let inputs: ScreenTextEntryInputs

    func requestFocus(_ id: UUID) { focus.wrappedValue = id }

    @discardableResult func endEditing() -> Bool {
        focus.wrappedValue = nil
        return inputs.resignFirstResponders()
    }

    func endEditingBeforeNavigation(_ navigate: () -> Void) {
        if endEditing() { navigate() }
    }
}

private struct ScreenTextEntryKey: EnvironmentKey {
    static let defaultValue: ScreenTextEntryContext? = nil
}

extension EnvironmentValues {
    var screenTextEntry: ScreenTextEntryContext? {
        get { self[ScreenTextEntryKey.self] }
        set { self[ScreenTextEntryKey.self] = newValue }
    }
}

extension View {
    func screenTextInput(_ id: UUID) -> some View { modifier(ScreenTextInput(id: id)) }
    func screenTextEntryInteraction() -> some View { modifier(ScreenTextEntryInteraction()) }
}

private struct ScreenTextInput: ViewModifier {
    let id: UUID
    @Environment(\.screenTextEntry) private var entry
    @ViewBuilder func body(content: Content) -> some View {
        if let entry {
            content.focused(entry.focus, equals: id)
                .background { ScreenTextInputProbe(id: id, inputs: entry.inputs).allowsHitTesting(false).accessibilityHidden(true) }
        } else { content }
    }
}

/// Protect selectable prose and SwiftUI controls from the scoped background recognizer.
private struct ScreenTextEntryInteraction: ViewModifier {
    @Environment(\.screenTextEntry) private var entry
    func body(content: Content) -> some View {
        content.background {
            if let entry { ScreenTextEntryProtectedRegion(inputs: entry.inputs).allowsHitTesting(false).accessibilityHidden(true) }
        }
    }
}

private struct ScreenTextEntryProtectedRegion: UIViewRepresentable {
    let inputs: ScreenTextEntryInputs
    func makeUIView(context: Context) -> UIView {
        let view = UIView()
        view.isUserInteractionEnabled = false
        view.isAccessibilityElement = false
        view.accessibilityElementsHidden = true
        inputs.protectedRegions.add(view)
        return view
    }
    func updateUIView(_ view: UIView, context: Context) {}
}

/// A value route whose push waits for the outgoing screen's native resignation.
struct ScreenTextEntryNavigationLink: View {
    let title: String
    let route: AppRoute
    @Environment(\.screenTextEntry) private var entry
    @State private var opened = false

    init(_ title: String, value: AppRoute) { self.title = title; route = value }

    var body: some View {
        Button(title) {
            if let entry { entry.endEditingBeforeNavigation { opened = true } }
            else { opened = true }
        }
        .navigationDestination(isPresented: $opened) { RouteDestination(route: route) }
    }
}

/// Only the native inputs registered by this screen can be resigned here.
@MainActor private final class ScreenTextEntryInputs {
    private final class Reference {
        weak var probe: ScreenTextInputProbeView?
        init(_ probe: ScreenTextInputProbeView) { self.probe = probe }
    }
    private var fields: [UUID: Reference] = [:]
    let protectedRegions = NSHashTable<UIView>.weakObjects()

    func register(_ probe: ScreenTextInputProbeView, id: UUID) { fields[id] = Reference(probe) }
    func remove(_ id: UUID) { fields[id] = nil }
    var hasFirstResponder: Bool { fields.values.contains { $0.probe?.nativeInput?.isFirstResponder == true } }

    func resignFirstResponders() -> Bool {
        for field in fields.values {
            let input = field.probe?.nativeInput
            if let input, input.isFirstResponder {
                input.resignFirstResponder()
            }
        }
        return !hasFirstResponder
    }

    func restore(_ id: UUID) {
        guard let input = fields[id]?.probe?.nativeInput, !input.isFirstResponder else { return }
        input.becomeFirstResponder()
    }
}

private struct ScreenTextInputProbe: UIViewRepresentable {
    let id: UUID
    let inputs: ScreenTextEntryInputs
    func makeUIView(context: Context) -> ScreenTextInputProbeView {
        let view = ScreenTextInputProbeView()
        view.isUserInteractionEnabled = false
        view.isAccessibilityElement = false
        view.accessibilityElementsHidden = true
        view.inputID = id
        view.inputs = inputs
        inputs.register(view, id: id)
        return view
    }
    func updateUIView(_ view: ScreenTextInputProbeView, context: Context) {}
    static func dismantleUIView(_ view: ScreenTextInputProbeView, coordinator: ()) { view.inputs?.remove(view.inputID) }
}

private final class ScreenTextInputProbeView: UIView {
    var inputID = UUID()
    weak var inputs: ScreenTextEntryInputs?
    private weak var resolvedInput: UIView?

    override func layoutSubviews() { super.layoutSubviews(); _ = nativeInput }

    /// Cache this field while it is visible. A covered parent cannot resolve a child's input.
    var nativeInput: UIView? {
        guard let window, bounds.width > 0, bounds.height > 0 else { return nil }
        if let resolvedInput, resolvedInput.window === window,
           owningController(of: resolvedInput) === owningController(of: self) { return resolvedInput }
        let point = convert(CGPoint(x: bounds.midX, y: bounds.midY), to: window)
        var target = window.hitTest(point, with: nil)
        while let view = target {
            if view is any UITextInput {
                guard let controller = owningController(of: self), controller === owningController(of: view) else { return nil }
                resolvedInput = view
                return view
            }
            target = view.superview
        }
        return nil
    }


    private func owningController(of view: UIView) -> UIViewController? {
        var responder: UIResponder? = view
        while let current = responder {
            if let controller = current as? UIViewController { return controller }
            responder = current.next
        }
        return nil
    }
}

private struct ScreenTextEntryTransition: UIViewControllerRepresentable {
    let entry: ScreenTextEntryContext
    let dismissesBackgroundTaps: Bool
    func makeUIViewController(context: Context) -> Controller { Controller(entry: entry, dismissesBackgroundTaps: dismissesBackgroundTaps) }
    func updateUIViewController(_ controller: Controller, context: Context) { controller.entry = entry }

    final class Controller: UIViewController, UIGestureRecognizerDelegate {
        var entry: ScreenTextEntryContext
        private let dismissesBackgroundTaps: Bool
        private lazy var backgroundTap = UITapGestureRecognizer(target: self, action: #selector(tapBackground))
        init(entry: ScreenTextEntryContext, dismissesBackgroundTaps: Bool) {
            self.entry = entry
            self.dismissesBackgroundTaps = dismissesBackgroundTaps
            super.init(nibName: nil, bundle: nil)
        }
        required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }
        override func loadView() {
            view = UIView()
            view.backgroundColor = .clear
            view.isUserInteractionEnabled = false
            view.isAccessibilityElement = false
            view.accessibilityElementsHidden = true
        }

        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            guard dismissesBackgroundTaps, let host = parent?.view else { return }
            backgroundTap.cancelsTouchesInView = false
            backgroundTap.delegate = self
            host.addGestureRecognizer(backgroundTap)
        }

        override func viewDidDisappear(_ animated: Bool) {
            super.viewDidDisappear(animated)
            backgroundTap.view?.removeGestureRecognizer(backgroundTap)
        }

        @objc private func tapBackground() { entry.endEditing() }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
            guard entry.focus.wrappedValue != nil, let window = view.window,
                  let touchView = touch.view, owningController(of: touchView) === parent,
                  (transitionCoordinator ?? parent?.transitionCoordinator) == nil else { return false }
            let point = touch.location(in: window)
            guard view.convert(view.bounds, to: window).contains(point) else { return false }
            var target: UIView? = touchView
            while let current = target {
                if current is UIControl || current is any UITextInput { return false }
                target = current.superview
            }
            return !entry.inputs.protectedRegions.allObjects.contains { region in
                region.window === window && region.convert(region.bounds, to: window).contains(point)
            }
        }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool { true }

        private func owningController(of view: UIView) -> UIViewController? {
            var responder: UIResponder? = view
            while let current = responder {
                if let controller = current as? UIViewController { return controller }
                responder = current.next
            }
            return nil
        }

        override func viewWillAppear(_ animated: Bool) {
            super.viewWillAppear(animated)
            guard let transition = transitionCoordinator ?? parent?.transitionCoordinator else { return }
            transition.animate(alongsideTransition: nil) { [weak self] context in
                // The parent can receive native focus during Back, after its initial appearance.
                if !context.isCancelled { self?.entry.endEditing() }
            }
        }

        override func viewWillDisappear(_ animated: Bool) {
            super.viewWillDisappear(animated)
            guard let id = entry.focus.wrappedValue, entry.inputs.hasFirstResponder,
                  let transition = transitionCoordinator ?? parent?.transitionCoordinator else { return }
            transition.animate(alongsideTransition: nil) { [weak self] context in
                guard context.isCancelled, let self, !self.entry.inputs.hasFirstResponder else { return }
                self.entry.requestFocus(id)
                self.entry.inputs.restore(id)
            }
        }
    }
}
