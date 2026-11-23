import Foundation

/// The native projection of the shared update view contract.
struct PanelsUpdateStatus: Decodable, Sendable {
    let state: String
    let allowedActions: [String]
    let available: Available?
    let attempt: Attempt?
    let appSha: String
    let detail: String?
    let deadlineAt: String?
    let machines: [Machine]
    let availableMachines: [Machine]
    let interruption: Interruption
    let clientVersions: [Client]

    struct Interruption: Decodable, Sendable {
        let stopsAgents: Bool
        let agents: [String]
        let machineIds: [String]
        var sentence: String { stopsAgents ? "This update stops your agents" : "Your agents keep running" }
    }
    struct Available: Decodable, Sendable {
        let appSha: String
        let compatibility: Compatibility?
    }
    struct Compatibility: Decodable, Sendable {
        struct IOS: Decodable, Sendable { let minimumBuild: Int }
        struct Desktop: Decodable, Sendable { let minimumBridgeVersion: Int }
        struct Machine: Decodable, Sendable { let minimumProtocolVersion: Int; let maximumProtocolVersion: Int }
        let ios: IOS
        let desktopShell: Desktop
        let machine: Machine
        let outboxFormat: Int
    }
    struct Client: Decodable, Sendable {
        let kind: String
        let deviceId: String
        let build: Int?
        let bridgeVersion: Int?
        let protocolVersion: Int?
        let outboxFormat: Int?
    }
    struct Attempt: Decodable, Sendable {
        let attemptId: String
        let state: String
        let detail: String?
        let interruption: Interruption?
    }
    struct Machine: Decodable, Sendable, Identifiable {
        let machineId: String
        let name: String
        let state: String
        let present: Bool
        let attemptId: String?
        let detail: String?
        let canRetry: Bool
        var id: String { machineId }
        var label: String {
            switch state {
            case "current": return attemptId == nil ? "Up to date" : "Done"
            case "available": return present ? "Will update" : "Updates when it is back"
            case "waiting": return "Updates when it is back"
            case "updating": return "Updating…"
            case "failed": return "Update failed"
            default: return "Needs an update to continue"
            }
        }
    }
    var answerKey: String { "\(state):\(attempt?.attemptId ?? "")" }
    var cardInterruption: Interruption? { state == "available" ? interruption : attempt?.interruption }
    var cardMachines: [Machine] {
        guard state == "available", available != nil else { return machines }
        return machines.map { row in
            row.attemptId != nil ? row : availableMachines.first { $0.machineId == row.machineId } ?? row
        }
    }
    var warnings: [String] {
        guard let limits = available?.compatibility else { return [] }
        var result: [String] = []
        for client in clientVersions {
            var warning: String?
            if client.kind == "ios", let build = client.build, build < limits.ios.minimumBuild {
                warning = "Your iPhone needs the TestFlight update first."
            } else if client.kind == "desktop", let bridge = client.bridgeVersion, bridge < limits.desktopShell.minimumBridgeVersion {
                warning = "Your desktop app needs an update first."
            } else if client.kind == "machine" {
                let protocolMismatch = client.protocolVersion.map { $0 < limits.machine.minimumProtocolVersion || $0 > limits.machine.maximumProtocolVersion } ?? false
                let outboxMismatch = client.outboxFormat.map { $0 != limits.outboxFormat } ?? false
                if protocolMismatch || outboxMismatch {
                    warning = "\(machines.first { $0.machineId == client.deviceId }?.name ?? "A machine") needs an update to continue."
                }
            }
            if let warning, !result.contains(warning) { result.append(warning) }
        }
        return result
    }
    var needsAttention: Bool {
        state == "failed" || state == "mac_failed" || state == "mac_unreachable" || machines.contains { ["failed", "unavailable"].contains($0.state) }
    }
    var title: String {
        switch state {
        case "available": return "Update available"
        case "updating": return "Updating…"
        case "failed": return "Update didn't install"
        case "mac_failed", "mac_unreachable": return "Machine update needs attention"
        default:
            if needsAttention { return "Machine update needs attention" }
            if machines.contains(where: { $0.state == "updating" }) { return "Updating machines…" }
            if machines.contains(where: { $0.state == "waiting" }) { return "Machine updates" }
            return machines.contains { $0.attemptId != nil } ? "Update complete" : "Up to date"
        }
    }
    func expectedRestart(now: Date, starting: Bool = false) -> Bool {
        guard state == "updating" || starting, let deadlineAt else { return false }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let deadline = formatter.date(from: deadlineAt) ?? ISO8601DateFormatter().date(from: deadlineAt)
        return deadline.map { now < $0 } ?? false
    }
}
