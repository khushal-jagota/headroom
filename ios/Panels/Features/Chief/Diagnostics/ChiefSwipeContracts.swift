import Foundation

/// An allowlist of diagnostic metadata. No view descriptions, labels, URLs or domain records enter this contract.
struct ChiefSwipeRecord: Codable, Sendable {
    enum Phase: String, Codable, Sendable { case start, sample, end, cancel, transitionComplete }
    let timestamp: TimeInterval
    let session: Int
    let phase: Phase
    let displacement: ChiefSwipePoint
    let views: [ChiefSwipeViewSnapshot]
    let recognizers: [ChiefSwipeRecognizerSnapshot]
    let navigation: ChiefSwipeNavigationSnapshot
    let appVersion: String
    let appBuild: String
    let systemVersion: String
}

struct ChiefSwipePoint: Codable, Sendable { let x: Double; let y: Double }
struct ChiefSwipeRect: Codable, Sendable { let x: Double; let y: Double; let width: Double; let height: Double }
struct ChiefSwipeTransform: Codable, Sendable { let a: Double; let b: Double; let c: Double; let d: Double; let tx: Double; let ty: Double }

struct ChiefSwipeViewSnapshot: Codable, Sendable {
    enum Role: String, Codable, Sendable { case page, ancestor, nested, touched }
    let index: Int
    let parentIndex: Int?
    let role: Role
    let className: String
    let frame: ChiefSwipeRect
    let bounds: ChiefSwipeRect
    let windowFrame: ChiefSwipeRect
    let transform: ChiefSwipeTransform
    let presentationFrame: ChiefSwipeRect?
    let presentationTransform: ChiefSwipeTransform?
    let contentOffset: ChiefSwipePoint?
}

struct ChiefSwipeRecognizerSnapshot: Codable, Sendable {
    enum Role: String, Codable, Sendable { case pop, scroll, other }
    enum State: String, Codable, Sendable { case possible, began, changed, ended, cancelled, failed, unknown }
    let className: String
    let viewIndex: Int?
    let role: Role
    let state: State
    let isEnabled: Bool
}

struct ChiefSwipeNavigationSnapshot: Codable, Sendable {
    let stackCount: Int
    let transitionInProgress: Bool
    let isInteractive: Bool
    let percentComplete: Double
    let wasCancelled: Bool?
}
