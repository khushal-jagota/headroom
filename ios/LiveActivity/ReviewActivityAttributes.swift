import ActivityKit
import Foundation

/// The Review Live Activity. The app and the widget extension both compile this file.
/// The server names this type in a push-to-start (`attributes-type`).
struct ReviewActivityAttributes: ActivityAttributes {
    typealias ContentState = ReviewActivityContentState

    /// A tap on the Lock Screen card or the Island opens Review's summary.
    static let reviewURL = URL(string: "panels://review")!
}
