import Foundation

struct ArtifactOwner: Hashable, Sendable {
    enum Kind: String, Sendable { case ticket, manager }
    let kind: Kind
    let id: String
}
struct ManagedArtifact: Codable, Sendable { let name: String; let opens: String?; let modifiedAt: Double; let children: [ManagedArtifact] }
struct ArtifactPage: Codable, Sendable { let revision: String; let directory: String; let offset: Int; let total: Int; let entries: [ArtifactEntry] }
struct ArtifactEntry: Codable, Identifiable, Sendable {
    let key: String
    let path: String
    let label: String
    let kind: String
    let href: String?
    let directory: String?
    let childCount: Int
    var id: String { key }
}
