import SwiftUI
import UIKit
import ObjectiveC

/// Read-only native text, with the system's selection handles, Copy menu, and link actions.
/// Callers supply their existing font so selection does not change the type scale.
struct PanelsSelectableText: UIViewRepresentable {
    let text: AttributedString
    var font: UIFont
    var color: UIColor
    var lineSpacing: CGFloat
    var lineLimit: Int?
    var tracking: CGFloat
    var onSelectionChanged: (Bool) -> Void
    @Environment(\.openURL) private var openURL
    @Environment(\.panelsTextSelectionChanged) private var selectionChanged

    init(_ text: String, font: UIFont = .preferredFont(forTextStyle: .body), color: UIColor = .label,
         lineSpacing: CGFloat = 0, lineLimit: Int? = nil, tracking: CGFloat = 0,
         onSelectionChanged: @escaping (Bool) -> Void = { _ in }) {
        self.init(AttributedString(text), font: font, color: color, lineSpacing: lineSpacing,
                  lineLimit: lineLimit, tracking: tracking, onSelectionChanged: onSelectionChanged)
    }

    init(_ text: AttributedString, font: UIFont = .preferredFont(forTextStyle: .body), color: UIColor = .label,
         lineSpacing: CGFloat = 0, lineLimit: Int? = nil, tracking: CGFloat = 0,
         onSelectionChanged: @escaping (Bool) -> Void = { _ in }) {
        self.text = text; self.font = font; self.color = color; self.lineSpacing = lineSpacing
        self.lineLimit = lineLimit; self.tracking = tracking; self.onSelectionChanged = onSelectionChanged
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> UITextView {
        let view = SelectionTextView()
        view.selectionEnded = { [weak coordinator = context.coordinator] in coordinator?.setSelection(false) }
        view.backgroundColor = .clear
        view.isEditable = false
        view.isSelectable = true
        view.isScrollEnabled = false
        view.textContainerInset = .zero
        view.textContainer.lineFragmentPadding = 0
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        view.delegate = context.coordinator
        return view
    }

    func updateUIView(_ view: UITextView, context: Context) {
        context.coordinator.onSelectionChanged = { active in
            onSelectionChanged(active)
            selectionChanged(active)
        }
        context.coordinator.openURL = { url in openURL(url) }
        let value = attributedText()
        if !view.attributedText.isEqual(to: value) {
            context.coordinator.updating = true
            view.attributedText = value
            context.coordinator.updating = false
            // Replacement can reset UIKit's range without a delegate notification.
            // Read the actual range after this SwiftUI update, not a captured value.
            Task { @MainActor [weak view, weak coordinator = context.coordinator] in
                guard let view, let coordinator else { return }
                coordinator.setSelection(view.isFirstResponder && view.selectedRange.length > 0)
            }
        }
        view.textContainer.maximumNumberOfLines = lineLimit ?? 0
        view.textContainer.lineBreakMode = lineLimit == nil ? .byWordWrapping : .byTruncatingTail
        view.linkTextAttributes = [.foregroundColor: UIColor(PanelsTheme.accent), .underlineStyle: NSUnderlineStyle.single.rawValue]
        view.invalidateIntrinsicContentSize()
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: UITextView, context: Context) -> CGSize? {
        let width = proposal.width ?? uiView.attributedText.size().width
        guard width.isFinite, width > 0 else { return .zero }
        let size = uiView.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude))
        let occupied = uiView.attributedText.boundingRect(
            with: CGSize(width: width, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading], context: nil)
        return CGSize(width: min(width, ceil(occupied.width)), height: ceil(size.height))
    }

    static func dismantleUIView(_ uiView: UITextView, coordinator: Coordinator) {
        uiView.delegate = nil
        uiView.resignFirstResponder()
        coordinator.setSelection(false)
    }

    /// Native navigation bars own UILabel geometry. Add only a transparent selection
    /// layer, so the bar keeps its own glyphs, title layout, and collapse behavior.
    @MainActor static func install(on label: UILabel) {
        let overlay: LabelSelectionTextView
        if let existing = objc_getAssociatedObject(label, &labelSelectionAssociation) as? LabelSelectionTextView {
            overlay = existing
        } else {
            overlay = LabelSelectionTextView()
            overlay.label = label
            overlay.backgroundColor = .clear
            overlay.isEditable = false
            overlay.isSelectable = true
            overlay.isScrollEnabled = false
            overlay.textContainer.lineFragmentPadding = 0
            overlay.autoresizingMask = [.flexibleWidth, .flexibleHeight]
            label.addSubview(overlay)
            label.isUserInteractionEnabled = true
            objc_setAssociatedObject(label, &labelSelectionAssociation, overlay, .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
        }
        overlay.frame = label.bounds
        let value = NSMutableAttributedString(attributedString:
            label.attributedText ?? NSAttributedString(string: label.text ?? ""))
        let range = NSRange(location: 0, length: value.length)
        let baseFont = label.font ?? UIFont.preferredFont(forTextStyle: .headline)
        value.enumerateAttribute(.font, in: range) { font, run, _ in
            if font == nil { value.addAttribute(.font, value: baseFont, range: run) }
        }
        value.addAttribute(.foregroundColor, value: UIColor.clear, range: range)
        if !overlay.attributedText.isEqual(to: value) { overlay.attributedText = value }
        overlay.textAlignment = label.textAlignment
        overlay.textContainer.maximumNumberOfLines = label.numberOfLines
        overlay.textContainer.lineBreakMode = label.lineBreakMode
        // UITextView exposes the title through its native value, without duplicate speech.
        overlay.accessibilityTraits = label.accessibilityTraits
        overlay.isAccessibilityElement = true
        label.isAccessibilityElement = false
        overlay.setNeedsLayout()
    }

    private func attributedText() -> NSAttributedString {
        let result = NSMutableAttributedString(string: "")
        for run in text.runs {
            let part = AttributedString(text[run.range])
            let native = (try? NSAttributedString(part, including: \.uiKit)) ?? NSAttributedString(string: String(part.characters))
            let value = NSMutableAttributedString(attributedString: native)
            let range = NSRange(location: 0, length: value.length)
            let intent = run.inlinePresentationIntent ?? []
            var runFont = run.uiKit.font ?? font
            if run.uiKit.font == nil, intent.contains(.code) {
                runFont = .monospacedSystemFont(ofSize: font.pointSize, weight: .regular)
            }
            var traits = runFont.fontDescriptor.symbolicTraits
            if intent.contains(.stronglyEmphasized) { traits.insert(.traitBold) }
            if intent.contains(.emphasized) { traits.insert(.traitItalic) }
            if let descriptor = runFont.fontDescriptor.withSymbolicTraits(traits) {
                runFont = UIFont(descriptor: descriptor, size: runFont.pointSize)
            }
            value.addAttribute(.font, value: runFont, range: range)
            value.addAttribute(.foregroundColor, value: run.uiKit.foregroundColor ?? run.swiftUI.foregroundColor.map { UIColor($0) } ?? color, range: range)
            if let background = run.swiftUI.backgroundColor { value.addAttribute(.backgroundColor, value: UIColor(background), range: range) }
            if tracking != 0 { value.addAttribute(.kern, value: tracking, range: range) }
            let paragraph = NSMutableParagraphStyle()
            paragraph.lineSpacing = lineSpacing
            value.addAttribute(.paragraphStyle, value: paragraph, range: range)
            result.append(value)
        }
        return result
    }

    @MainActor final class Coordinator: NSObject, UITextViewDelegate {
        var onSelectionChanged: (Bool) -> Void = { _ in }
        var openURL: (URL) -> Void = { _ in }
        var updating = false
        private var selected = false

        func setSelection(_ value: Bool) {
            guard value != selected else { return }
            selected = value
            onSelectionChanged(value)
        }
        func textViewDidChangeSelection(_ textView: UITextView) {
            guard !updating else { return }
            setSelection(textView.selectedRange.length > 0)
        }
        func textViewDidEndEditing(_ textView: UITextView) { setSelection(false) }
        func textView(_ textView: UITextView, primaryActionFor textItem: UITextItem,
                      defaultAction: UIAction) -> UIAction? {
            // Only the primary link tap changes routes. UIKit retains its native
            // long-press menu because no menu configuration is overridden.
            guard case .link(let url) = textItem.content else { return defaultAction }
            return UIAction(title: defaultAction.title, image: defaultAction.image) { [weak self] _ in
                self?.openURL(url)
            }
        }
    }
}

/// A read-only text view can retain its range after the native menu resigns. Clear
/// that range with its responder so an ancestor's swipe guard cannot stay latched.
@MainActor private class SelectionTextView: UITextView {
    var selectionEnded: (() -> Void)?
    override func resignFirstResponder() -> Bool {
        let resigned = super.resignFirstResponder()
        if resigned {
            selectedRange = NSRange(location: 0, length: 0)
            selectionEnded?()
        }
        return resigned
    }
}

@MainActor private var labelSelectionAssociation: UInt8 = 0

@MainActor private final class LabelSelectionTextView: SelectionTextView {
    weak var label: UILabel?
    override func layoutSubviews() {
        if let label {
            let rect = label.textRect(forBounds: label.bounds, limitedToNumberOfLines: label.numberOfLines)
            let inset = UIEdgeInsets(top: max(0, rect.minY), left: 0,
                                    bottom: max(0, bounds.height - rect.maxY), right: 0)
            if textContainerInset != inset { textContainerInset = inset }
        }
        super.layoutSubviews()
    }
}

private struct PanelsTextSelectionKey: EnvironmentKey {
    static let defaultValue: @MainActor @Sendable (Bool) -> Void = { _ in }
}

extension EnvironmentValues {
    var panelsTextSelectionChanged: @MainActor @Sendable (Bool) -> Void {
        get { self[PanelsTextSelectionKey.self] }
        set { self[PanelsTextSelectionKey.self] = newValue }
    }
}

/// Standard edit menu for a useful value that belongs to a control. Copy does not
/// invoke the control, and a normal tap does not enter this menu.
@MainActor final class SystemTextCopyInteraction: NSObject {
    private static var association: UInt8 = 0
    private let anchor = CopyMenuAnchorView()
    private var press: UILongPressGestureRecognizer?
    private var onPresent: (() -> Void)?

    static func install(on view: UIView, text: String, onPresent: (() -> Void)? = nil) {
        if let existing = objc_getAssociatedObject(view, &association) as? SystemTextCopyInteraction {
            existing.anchor.text = text
            existing.onPresent = onPresent
            return
        }
        let interaction = SystemTextCopyInteraction()
        interaction.anchor.text = text
        interaction.onPresent = onPresent
        interaction.anchor.frame = view.bounds
        interaction.anchor.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        interaction.anchor.isUserInteractionEnabled = false
        view.addSubview(interaction.anchor)
        view.isUserInteractionEnabled = true
        let press = UILongPressGestureRecognizer(target: interaction, action: #selector(interaction.longPress(_:)))
        view.addGestureRecognizer(press)
        interaction.press = press
        objc_setAssociatedObject(view, &association, interaction, .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
    }

    @objc private func longPress(_ recognizer: UILongPressGestureRecognizer) {
        guard recognizer.state == .began else { return }
        anchor.present(at: recognizer.location(in: anchor), onPresent: onPresent)
    }
}

@MainActor private final class CopyMenuAnchorView: UIView {
    var text = ""
    private var menuText = ""
    private let editMenu = UIEditMenuInteraction(delegate: nil)
    override var canBecomeFirstResponder: Bool { true }

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .clear
        addInteraction(editMenu)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    func present(at point: CGPoint? = nil, onPresent: (() -> Void)? = nil) {
        guard !text.isEmpty, window != nil else { return }
        // A live value can change while the native menu remains on screen.
        // Copy the value for which the person opened that menu.
        menuText = text
        guard becomeFirstResponder() else { return }
        onPresent?()
        editMenu.presentEditMenu(with: UIEditMenuConfiguration(identifier: nil,
            sourcePoint: point ?? CGPoint(x: bounds.midX, y: bounds.midY)))
    }
    override func canPerformAction(_ action: Selector, withSender sender: Any?) -> Bool {
        action == #selector(copy(_:)) && !menuText.isEmpty
    }
    override func copy(_ sender: Any?) { UIPasteboard.general.string = menuText }
}

private struct SystemCopyText: ViewModifier {
    let text: String
    @State private var anchor = CopyMenuAnchorView()
    func body(content: Content) -> some View {
        content.background(CopyMenuAnchor(text: text, view: anchor).allowsHitTesting(false))
            .highPriorityGesture(LongPressGesture().onEnded { _ in anchor.present() })
    }
}

private struct CopyMenuAnchor: UIViewRepresentable {
    let text: String
    let view: CopyMenuAnchorView
    func makeUIView(context: Context) -> CopyMenuAnchorView { view }
    func updateUIView(_ uiView: CopyMenuAnchorView, context: Context) { uiView.text = text }
}

extension View {
    func systemCopyText(_ text: String) -> some View { modifier(SystemCopyText(text: text)) }
}
