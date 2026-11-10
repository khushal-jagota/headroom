import XCTest
import SwiftUI
@testable import Panels

/// Native layer lifecycle and SwiftUI's refresh-control bridge require UIKit;
/// no network or additional browser/test host is involved.
final class RingSpinnerTests: XCTestCase {
    @MainActor func testRingStopsForReduceMotionAndWhenDetached() throws {
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 320, height: 480))
        window.rootViewController = UIViewController()
        window.makeKeyAndVisible()
        defer { window.isHidden = true }
        let ring = RingSpinnerUIView(frame: CGRect(x: 20, y: 20, width: 14, height: 14))
        ring.tintColor = UIColor(PanelsTheme.faint)
        window.rootViewController!.view.addSubview(ring)
        ring.layoutIfNeeded()
        let spin = try XCTUnwrap(ring.layer.animation(forKey: "panels-ring-spin") as? CABasicAnimation)
        XCTAssertEqual(spin.duration, 0.9)
        XCTAssertEqual(spin.repeatCount, .infinity)
        XCTAssertEqual(spin.timingFunction, CAMediaTimingFunction(name: .linear))
        let shapes = try XCTUnwrap(ring.layer.sublayers as? [CAShapeLayer])
        XCTAssertEqual(shapes.count, 2)
        XCTAssertEqual(shapes[0].lineWidth, 2)
        XCTAssertEqual(try XCTUnwrap(shapes[0].strokeColor).alpha, 0.3, accuracy: 0.001)
        XCTAssertEqual(try XCTUnwrap(shapes[1].strokeColor).alpha, 1)
        XCTAssertEqual(try XCTUnwrap(shapes[0].path).boundingBox.width, 12, accuracy: 0.01)
        ring.reduceMotion = true
        XCTAssertNil(ring.layer.animation(forKey: "panels-ring-spin"))
        XCTAssertNotNil(shapes[1].path, "Reduced motion keeps the arc visible")
        ring.reduceMotion = false
        XCTAssertNotNil(ring.layer.animation(forKey: "panels-ring-spin"))
        ring.removeFromSuperview()
        XCTAssertNil(ring.layer.animation(forKey: "panels-ring-spin"))
    }

    @MainActor func testNativeRefreshUsesRingAndStillCompletesAction() async throws {
        let refreshed = expectation(description: "Native refresh invokes its existing action")
        let content = ScrollView {
            VStack {
                RingSpinner("Loading…")
                RingSpinner(size: .small)
                ConversationSignalMark(signal: ConversationSignal(working: true, messageForOwner: false))
                ConversationSignalMark(signal: ConversationSignal(working: true, messageForOwner: true))
                Text("Pull to refresh").frame(height: 900)
            }
        }.panelsRefreshable {
            // Stub the work; never contact a Panels server.
            refreshed.fulfill()
        }
        let host = UIHostingController(rootView: content)
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        window.rootViewController = host
        window.makeKeyAndVisible()
        defer { window.isHidden = true }
        try await Task.sleep(for: .milliseconds(300))
        host.view.layoutIfNeeded()
        let control = try XCTUnwrap(find(UIRefreshControl.self, in: host.view))
        let ring = try XCTUnwrap(control.subviews.compactMap { $0 as? RingSpinnerUIView }.first)
        XCTAssertEqual(control.tintColor, .clear, "Apple's spokes must be invisible")
        XCTAssertEqual(ring.bounds.size, CGSize(width: 20, height: 20))
        XCTAssertEqual(ring.reduceMotion, UIAccessibility.isReduceMotionEnabled)
        if ring.reduceMotion { XCTAssertNil(ring.layer.animation(forKey: "panels-ring-spin")) }
        else { XCTAssertNotNil(ring.layer.animation(forKey: "panels-ring-spin")) }
        control.beginRefreshing()
        control.sendActions(for: .valueChanged)
        await fulfillment(of: [refreshed], timeout: 2)
        try await Task.sleep(for: .milliseconds(300))
        XCTAssertFalse(control.isRefreshing, "Native async completion still ends refreshing")
        let image = UIGraphicsImageRenderer(bounds: host.view.bounds).image { _ in
            host.view.drawHierarchy(in: host.view.bounds, afterScreenUpdates: true)
        }
        let screenshot = XCTAttachment(image: image)
        screenshot.name = "Panels ring sizes and unread precedence"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    @MainActor private func find<T: UIView>(_ type: T.Type, in view: UIView) -> T? {
        if let match = view as? T { return match }
        for child in view.subviews { if let match = find(type, in: child) { return match } }
        return nil
    }
}
