import Foundation

/// Display snapshots are replaceable and do not carry current stream authority.
enum DisplayCacheConfiguration {
    static let checkpointInterval: TimeInterval = 5
    /// A saved view older than this is not shown at launch.
    static let maximumAge: TimeInterval = 7 * 24 * 60 * 60
    /// A view whose last fresh snapshot is older than this no longer stands in for an ask's
    /// read: the turn that waited on it has likely moved on, so the card waits for the
    /// connection instead.
    static let pendingAskMaximumAge: TimeInterval = 60 * 60
}
