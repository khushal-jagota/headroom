import SwiftUI
import UIKit

/// UIScrollView supplies native pinch, pan, accessibility zoom and motion cancellation.
struct ArtifactImageView: UIViewRepresentable {
    let image: UIImage
    func makeUIView(context: Context) -> ZoomImageScrollView { ZoomImageScrollView(image: image) }
    func updateUIView(_ view: ZoomImageScrollView, context: Context) {}
}

final class ZoomImageScrollView: UIScrollView, UIScrollViewDelegate {
    private let picture: UIImageView
    private var previousSize = CGSize.zero
    init(image: UIImage) {
        picture = UIImageView(image: image)
        super.init(frame: .zero)
        backgroundColor = UIColor(white: 0.043, alpha: 1)
        delegate = self
        maximumZoomScale = 5
        minimumZoomScale = 1
        showsVerticalScrollIndicator = false; showsHorizontalScrollIndicator = false
        picture.contentMode = .scaleAspectFit
        picture.isAccessibilityElement = true
        picture.accessibilityLabel = "Artifact image"
        picture.accessibilityTraits = .image
        addSubview(picture)
        let tap = UITapGestureRecognizer(target: self, action: #selector(doubleTap(_:)))
        tap.numberOfTapsRequired = 2
        addGestureRecognizer(tap)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func layoutSubviews() {
        super.layoutSubviews()
        if bounds.size != previousSize {
            previousSize = bounds.size
            setZoomScale(1, animated: false)
            picture.frame = CGRect(origin: .zero, size: bounds.size)
            contentSize = bounds.size
        }
    }
    func viewForZooming(in scrollView: UIScrollView) -> UIView? { picture }
    @objc private func doubleTap(_ recognizer: UITapGestureRecognizer) {
        let animated = !UIAccessibility.isReduceMotionEnabled
        if zoomScale > 1 { setZoomScale(1, animated: animated) }
        else {
            let point = recognizer.location(in: picture)
            let size = CGSize(width: bounds.width / 2.5, height: bounds.height / 2.5)
            zoom(to: CGRect(x: point.x - size.width / 2, y: point.y - size.height / 2, width: size.width, height: size.height), animated: animated)
        }
    }
}
