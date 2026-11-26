import Foundation

/// What the Review Live Activity shows. The server pushes this as the activity's
/// `content-state`, so every key here must match the server's payload exactly.
struct ReviewActivityContentState: Codable, Hashable {
    /// Cards waiting, as the Review summary counts them.
    var count: Int
    /// The card that just arrived. Only the expanded Island shows it.
    var arrivedTitle: String?

    var isClear: Bool { count == 0 }
    /// The second line, in the Review summary's voice.
    var detail: String { isClear ? "Nothing to review." : "Ready to review." }
}
