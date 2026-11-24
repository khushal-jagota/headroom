import XCTest
@testable import Panels

/// The Sidebar's release rule and its stretch past fully open. These numbers decide where
/// every drag lands, and a small edit to them changes the feel without breaking a screen walk.
final class SidebarMotionTests: XCTestCase {
    private let width: CGFloat = 342

    func testFlickGoesTheWayTheFingerMovedAndASlowReleaseGoesToTheNearerSide() {
        XCTAssertFalse(SidebarMotion.opens(offset: width * 0.9, velocity: -301, width: width), "A leftward flick closes from nearly open")
        XCTAssertTrue(SidebarMotion.opens(offset: width * 0.1, velocity: 301, width: width), "A rightward flick opens from nearly hidden")
        XCTAssertTrue(SidebarMotion.opens(offset: width * 0.6, velocity: -300, width: width), "300 pt/s is not a flick")
        XCTAssertFalse(SidebarMotion.opens(offset: width * 0.4, velocity: 0, width: width))
        XCTAssertFalse(SidebarMotion.opens(offset: width / 2, velocity: 0, width: width), "Exactly half closes")
        XCTAssertTrue(SidebarMotion.opens(offset: width * 0.67, velocity: -200, width: width),
                      "A slow drag back of a third stays open; the old rule closed it")
    }

    func testPositionTracksOneToOneThenStretchesWithResistance() {
        XCTAssertEqual(SidebarMotion.position(-40, width: width), 0, "The panel never passes hidden")
        XCTAssertEqual(SidebarMotion.position(120, width: width), 120)
        XCTAssertEqual(SidebarMotion.position(width, width: width), width)
        let small = SidebarMotion.position(width + 10, width: width) - width
        let large = SidebarMotion.position(width + 400, width: width) - width
        XCTAssertGreaterThan(small, 0); XCTAssertLessThan(small, 10, "Past fully open the panel moves less than the finger")
        XCTAssertGreaterThan(large, small)
        XCTAssertLessThan(large, SidebarMotion.stretchLimit, "The stretch never passes its limit")
    }
}
