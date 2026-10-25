import SwiftUI

/// The web spinner: a faint ring and one solid quarter-turn arc.
struct RingSpinner: View {
    enum Size {
        case compact, small, regular
        var diameter: CGFloat {
            switch self {
            case .compact: PanelsTheme.Spinner.compact
            case .small: PanelsTheme.Spinner.small
            case .regular: PanelsTheme.Spinner.regular
            }
        }
    }

    private let label: String?
    let size: Size
    let color: Color
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(_ label: String? = nil, size: Size = .regular, color: Color = PanelsTheme.accent) {
        self.label = label
        self.size = size
        self.color = color
    }

    var body: some View {
        VStack(spacing: PanelsTheme.Space.sm) {
            RingSpinnerRepresentable(color: color, reduceMotion: reduceMotion)
                .frame(width: size.diameter, height: size.diameter)
            if let label { Text(label).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted) }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label ?? "Loading")
    }
}

private struct RingSpinnerRepresentable: UIViewRepresentable {
    let color: Color
    let reduceMotion: Bool

    func makeUIView(context: Context) -> RingSpinnerUIView { RingSpinnerUIView() }
    func updateUIView(_ view: RingSpinnerUIView, context: Context) {
        view.tintColor = UIColor(color)
        view.reduceMotion = reduceMotion
    }
}

/// Also used inside the native refresh control, so pulling and inline loading
/// have exactly the same drawing and animation. No display timer is needed.
final class RingSpinnerUIView: UIView {
    private let track = CAShapeLayer()
    private let arc = CAShapeLayer()
    var reduceMotion = false { didSet { updateAnimation() } }

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        for shape in [track, arc] {
            shape.fillColor = UIColor.clear.cgColor
            shape.lineWidth = PanelsTheme.Spinner.stroke
            layer.addSublayer(shape)
        }
        updateColors()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func layoutSubviews() {
        super.layoutSubviews()
        let center = CGPoint(x: bounds.midX, y: bounds.midY)
        let radius = max(0, (min(bounds.width, bounds.height) - PanelsTheme.Spinner.stroke) / 2)
        track.path = UIBezierPath(arcCenter: center, radius: radius, startAngle: 0, endAngle: .pi * 2, clockwise: true).cgPath
        arc.path = UIBezierPath(arcCenter: center, radius: radius, startAngle: -.pi * 0.75, endAngle: -.pi * 0.25, clockwise: true).cgPath
    }

    override func tintColorDidChange() { super.tintColorDidChange(); updateColors() }
    override func didMoveToWindow() { super.didMoveToWindow(); updateAnimation() }

    private func updateColors() {
        track.strokeColor = tintColor.withAlphaComponent(PanelsTheme.Spinner.trackOpacity).cgColor
        arc.strokeColor = tintColor.cgColor
    }

    private func updateAnimation() {
        if reduceMotion || window == nil {
            layer.removeAnimation(forKey: "panels-ring-spin")
        } else if layer.animation(forKey: "panels-ring-spin") == nil {
            let spin = CABasicAnimation(keyPath: "transform.rotation.z")
            spin.fromValue = 0
            spin.toValue = Double.pi * 2
            spin.duration = PanelsTheme.Motion.loopSpin
            spin.repeatCount = .infinity
            spin.timingFunction = CAMediaTimingFunction(name: .linear)
            layer.add(spin, forKey: "panels-ring-spin")
        }
    }
}
