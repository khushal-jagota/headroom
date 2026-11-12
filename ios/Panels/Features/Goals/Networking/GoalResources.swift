import Foundation

/// Goals declare their read dependencies without teaching the resource core about Goals.
enum GoalResources {
    static let list = ResourceRegistration<GoalsResponse>(key: "/api/goals", nextRefreshAt: { $0.nextRefreshAt })
    static let projects = ResourceRegistration<GoalJSON>(key: "/api/projects?detail=full", versioned: false)
    static let managers = ResourceRegistration<ManagersResponse>(key: "/api/managers?detail=summary&limit=30&offset=0&settled=false", versioned: false)
    static func goal(_ id: String) -> ResourceRegistration<GoalRecord> {
        .init(key: "/api/goals/\(id)", nextRefreshAt: { $0.nextRefreshAt })
    }
    static func readings(_ id: String) -> ResourceRegistration<GoalReadings> {
        .init(key: "/api/goals/\(id)/readings", nextRefreshAt: { $0.nextRefreshAt })
    }
    static func holderConversation(_ holder: GoalHolder) -> ResourceRegistration<GoalJSON> {
        .init(key: holder.kind == .chief ? "/api/chief/conversation" : "/api/managers/\(holder.id!)/workspace", versioned: false)
    }
    static func holder(_ holder: GoalHolder) -> ResourceRegistration<HolderPage> {
        .init(key: holder.pagePath, versioned: holder.kind == .chief)
    }
    static func holderReadings(_ holder: GoalHolder) -> ResourceRegistration<GoalReadings> {
        .init(key: holder.pagePath + "/readings", versioned: false, nextRefreshAt: { $0.nextRefreshAt })
    }
}
