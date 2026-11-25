import XCTest

/// Moves through the app as the owner does: the Sidebar for destinations, the native bar for Back.
extension XCUIApplication {
    /// UIKit exposes read-only text as a value; SwiftUI exposes it as a label.
    func readableText(_ text: String) -> XCUIElement {
        readableTexts.matching(NSPredicate(
            format: "identifier == %@ OR label == %@ OR value == %@", text, text, text
        )).firstMatch
    }

    var sidebarButton: XCUIElement { buttons["sidebar-button"] }
    /// The system Back button: the first button of a pushed page's bar. Its label is the
    /// previous page's title, or "Back" when that page has none.
    var backButton: XCUIElement { navigationBars.firstMatch.buttons.firstMatch }

    /// Opens a Sidebar destination by identifier: sidebar-review, sidebar-chief or sidebar-settings.
    func openDestination(_ identifier: String, timeout: TimeInterval = 10, file: StaticString = #filePath, line: UInt = #line) {
        openSidebar(timeout: timeout, file: file, line: line)
        let item = buttons[identifier]
        XCTAssertTrue(item.waitForExistence(timeout: timeout), "Sidebar has no \(identifier)", file: file, line: line)
        item.tap()
        XCTAssertTrue(item.waitForNonExistence(timeout: timeout), "The Sidebar did not close", file: file, line: line)
    }

    /// Opens the Manager whose Sidebar card label contains `title`.
    func openManager(titled title: String, timeout: TimeInterval = 10, file: StaticString = #filePath, line: UInt = #line) {
        openSidebar(timeout: timeout, file: file, line: line)
        let card = buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'sidebar-manager-' AND label CONTAINS %@", title)).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: timeout), "Sidebar has no Manager \(title)", file: file, line: line)
        card.tap()
        XCTAssertTrue(card.waitForNonExistence(timeout: timeout), "The Sidebar did not close", file: file, line: line)
    }

    /// Review opens on its summary each time it becomes the destination. Back to review
    /// (or Review skipped) brings its cards.
    func openReviewCards(timeout: TimeInterval = 12, file: StaticString = #filePath, line: UInt = #line) {
        let back = buttons["review.return"]
        XCTAssertTrue(back.waitForExistence(timeout: timeout), "Review has no cards to return to", file: file, line: line)
        back.tap()
        XCTAssertTrue(back.waitForNonExistence(timeout: timeout), "Review stayed on its summary", file: file, line: line)
    }

    func openSidebar(timeout: TimeInterval = 10, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(sidebarButton.waitForExistence(timeout: timeout), "No Sidebar button: not on a root page", file: file, line: line)
        sidebarButton.tap()
        XCTAssertTrue(buttons["sidebar-review"].waitForExistence(timeout: timeout), "The Sidebar did not open", file: file, line: line)
    }
}

/// Read the same visible prose from either native read-only accessibility role.
extension XCUIElement {
    /// Read-only prose can use native selectable text or SwiftUI static text.
    /// Keep one lazy query so a wait can observe either accessibility role.
    var readableTexts: XCUIElementQuery {
        descendants(matching: .any).matching(NSPredicate(
            format: "elementType == %lu OR elementType == %lu",
            XCUIElement.ElementType.staticText.rawValue,
            XCUIElement.ElementType.textView.rawValue
        ))
    }

    var readableContent: String { label.isEmpty ? (value as? String ?? "") : label }
}
