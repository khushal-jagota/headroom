import Foundation

struct SettingsWorkers: Decodable, Sendable {
    let workers: [SettingsWorker]
    let chiefOfStaff: ChiefDetail
}
struct SettingsWorker: Decodable, Sendable, Identifiable {
    var id: String { workerType }
    let workerType: String
    let label: String
    let specialistSkillName: String
    let launchDefaults: EmployeeConfiguration
}
struct SettingsSkills: Decodable, Sendable { let skills: [ManagedSkill] }
struct SettingsSchedules: Decodable, Sendable { let schedules: [SettingsSchedule] }
struct SettingsSchedule: Decodable, Sendable, Identifiable {
    let id: String
    let title: String
    let enabled: Bool
    let cadence: String
    let localTime: String
    let workerType: String
    let kickoffNote: String
}
struct SettingsNotifications: Decodable, Sendable {
    struct Subject: Decodable, Sendable {
        struct Preference: Decodable, Sendable, Identifiable {
            let id: String
            let label: String
            let description: String
            let enabled: Bool
        }
        let key: String
        let label: String
        let types: [Preference]
    }
    let subjects: [Subject]
}
extension APIClient {
    func settingsWorkers() async throws -> SettingsWorkers { try await get("/api/workers") }
    func settingsSkills() async throws -> SettingsSkills { try await get("/api/skills") }
    func settingsSchedules() async throws -> SettingsSchedules { try await get("/api/schedules") }
    func settingsNotifications() async throws -> SettingsNotifications { try await get("/api/notifications/settings") }
}
enum SettingsServerAddress {
    static func validated(_ text: String) -> URL? {
        guard let components = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              components.scheme == "https", let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              components.path.isEmpty || components.path == "/" else { return nil }
        return components.url
    }
}
