import SwiftUI

extension View {
    /// Keep SwiftUI's native refresh action, gesture, and completion handling;
    /// change only the indicator inside its UIRefreshControl.
    func panelsRefreshable(action: @escaping @Sendable () async -> Void) -> some View {
        refreshable(action: action).background(RingRefreshControl())
    }
}

private struct RingRefreshControl: UIViewRepresentable {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeUIView(context: Context) -> RefreshIndicatorInstaller {
        let view = RefreshIndicatorInstaller()
        view.isUserInteractionEnabled = false
        return view
    }
    func updateUIView(_ view: RefreshIndicatorInstaller, context: Context) {
        view.reduceMotion = reduceMotion
        view.setNeedsLayout()
    }
}

final class RefreshIndicatorInstaller: UIView {
    private weak var installedControl: UIRefreshControl?
    private let ring = RingSpinnerUIView()
    var reduceMotion = false { didSet { ring.reduceMotion = reduceMotion } }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        // SwiftUI attaches its refresh control after assembling the scroll view.
        DispatchQueue.main.async { [weak self] in self?.install() }
    }

    override func layoutSubviews() { super.layoutSubviews(); install() }

    private func install() {
        guard window != nil else { return }
        var ancestor = superview
        while let view = ancestor {
            if let control = refreshControl(in: view) {
                guard installedControl !== control else { return }
                installedControl = control
                control.tintColor = .clear
                ring.tintColor = UIColor(PanelsTheme.accent)
                ring.reduceMotion = reduceMotion
                ring.translatesAutoresizingMaskIntoConstraints = false
                control.addSubview(ring)
                NSLayoutConstraint.activate([
                    ring.centerXAnchor.constraint(equalTo: control.centerXAnchor),
                    ring.centerYAnchor.constraint(equalTo: control.centerYAnchor),
                    ring.widthAnchor.constraint(equalToConstant: PanelsTheme.Spinner.regular),
                    ring.heightAnchor.constraint(equalToConstant: PanelsTheme.Spinner.regular)
                ])
                return
            }
            ancestor = view.superview
        }
    }

    private func refreshControl(in view: UIView) -> UIRefreshControl? {
        if let scroll = view as? UIScrollView, let control = scroll.refreshControl { return control }
        for child in view.subviews {
            if let control = refreshControl(in: child) { return control }
        }
        return nil
    }
}
