import XCTest

/// Moves through the app as Khushal does: the Sidebar for destinations, the native bar for Back.
extension XCUIApplication {
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

    func openSidebar(timeout: TimeInterval = 10, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(sidebarButton.waitForExistence(timeout: timeout), "No Sidebar button: not on a root page", file: file, line: line)
        sidebarButton.tap()
        XCTAssertTrue(buttons["sidebar-review"].waitForExistence(timeout: timeout), "The Sidebar did not open", file: file, line: line)
    }
}
