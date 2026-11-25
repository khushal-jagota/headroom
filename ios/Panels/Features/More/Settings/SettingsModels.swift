import Foundation
import Observation

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
    var locale: String? = nil
    let id: String
    let title: String
    let enabled: Bool
    let cadence: String
    let localTime: String
    let workerType: String
    let kickoffNote: String
}
struct SettingsNotifications: Codable, Sendable {
    struct Subject: Codable, Sendable {
        struct Preference: Codable, Sendable, Identifiable {
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
    func setNotificationPreference(subjectKey: String, type: String, enabled: Bool) async throws -> SettingsNotifications {
        struct Body: Encodable { let enabled: Bool }
        let endpoint = try url("/api/notifications/preferences").appending(component: subjectKey).appending(component: type)
        var request = try self.request(endpoint)
        request.httpMethod = "PUT"
        request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(Body(enabled: enabled))
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(SettingsNotifications.self, from: data)
    }
}
enum SettingsServerAddress {
    private static let key = "panels.baseURL"

    static func saved(in defaults: UserDefaults = .standard) -> URL? {
        guard let address = defaults.string(forKey: key) else { return nil }
#if DEBUG
        return validated(address) ?? fixtureOrigin(address)
#else
        return validated(address)
#endif
    }

    static func save(_ url: URL, in defaults: UserDefaults = .standard) {
        defaults.set(url.absoluteString, forKey: key)
    }

    static func validated(_ text: String) -> URL? {
        guard let components = originComponents(text), components.scheme == "https" else { return nil }
        return components.url
    }

#if DEBUG
    static func fixtureOrigin(_ text: String) -> URL? {
        guard let components = originComponents(text), components.scheme == "http",
              ["localhost", "127.0.0.1"].contains(components.host) else { return nil }
        return components.url
    }
#endif

    private static func originComponents(_ text: String) -> URLComponents? {
        guard let components = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              components.path.isEmpty || components.path == "/" else { return nil }
        return components
    }
}

enum SettingsResources {
    static let workers = ResourceRegistration<SettingsWorkers>(key: "/api/workers", versioned: false, signalRefreshInterval: ResourceLimits.fallbackSeconds)
    static let skills = ResourceRegistration<SettingsSkills>(key: "/api/skills", versioned: false, signalRefreshInterval: ResourceLimits.fallbackSeconds)
    static let schedules = ResourceRegistration<SettingsSchedules>(key: "/api/schedules", versioned: false, signalRefreshInterval: ResourceLimits.fallbackSeconds)
    static let notifications = ResourceRegistration<SettingsNotifications>(key: "/api/notifications/settings", versioned: false, signalRefreshInterval: ResourceLimits.fallbackSeconds)
}

/// Preference writes retain the confirmed catalogue until the server accepts a change.
@MainActor @Observable final class NotificationPreferenceEditor {
    private struct Change {
        let subjectKey: String
        let type: String
        let enabled: Bool
    }
    private(set) var isSaving = false
    private(set) var error: String?
    private var failedChange: Change?

    func save(subjectKey: String, type: String, enabled: Bool, using client: APIClient, resources: ResourceOwner) async {
        guard !isSaving else { return }
        isSaving = true
        error = nil
        failedChange = nil
        let change = Change(subjectKey: subjectKey, type: type, enabled: enabled)
        let generation = resources.admissionGeneration
        defer { isSaving = false }
        do {
            let result = try await client.setNotificationPreference(subjectKey: subjectKey, type: type, enabled: enabled)
            guard generation == resources.admissionGeneration else { return }
            resources.handle(SettingsResources.notifications).accept(result)
        } catch {
            guard generation == resources.admissionGeneration else { return }
            self.error = "Could not change this notification. \(error.localizedDescription)"
            failedChange = change
        }
    }

    func retry(using client: APIClient, resources: ResourceOwner) async {
        guard let change = failedChange else { return }
        await save(subjectKey: change.subjectKey, type: change.type, enabled: change.enabled, using: client, resources: resources)
    }

    func resetError() { error = nil; failedChange = nil }
}
