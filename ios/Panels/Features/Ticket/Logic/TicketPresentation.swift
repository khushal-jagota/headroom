import Foundation

struct TicketPresentation {
    let ticket: TicketDetail
    let manifest: TicketWorkerType

    var currentStage: TicketWorkerType.Stage? { manifest.stages.first { $0.id == ticket.stage } }
    var terminal: Bool { currentStage?.isTerminal == true }
    var working: Bool { ticket.conversationSignal?.working == true }
    var currentField: String? { currentStage?.gatingField }
    var heading: String {
        if terminal { return "Done" }
        let label = manifest.fields.first { $0.id == currentField }?.label ?? currentStage?.label ?? ticket.stage
        let prefix: String
        if ticket.blocked || ticket.ticketStatus == "blocked" { prefix = "Blocked on" }
        else if ticket.agentState == "errored" { prefix = "Error working on" }
        else if ticket.assigned == true || currentStage?.ownershipMode == "user" { prefix = "Your step:" }
        else if ticket.stage == "needs_brief" { prefix = "Waiting for" }
        else if ticket.ticketStatus == "agent" || ticket.agentState == "working" { prefix = "Currently working on" }
        else { prefix = "Waiting at" }
        return "\(prefix) \(label)"
    }
    func isComplete(_ field: String) -> Bool {
        guard let current = manifest.stages.firstIndex(where: { $0.id == ticket.stage }),
              let gate = manifest.stages.firstIndex(where: { $0.gatingField == field }) else { return false }
        return current > gate
    }
    var completedCount: Int { manifest.fields.filter { isComplete($0.id) }.count }
    var ceilingOptions: [TicketWorkerType.Stage] {
        let start = manifest.stages.firstIndex { $0.id == ticket.stage } ?? 0
        return Array(manifest.stages.dropFirst(start))
    }
    var ceilingLabel: String {
        ticket.ceiling == "none" ? "No further" : manifest.stages.first { $0.id == ticket.ceiling }?.label ?? ticket.ceiling
    }
    func holderLabel(_ holder: Principal) -> String {
        switch holder.kind {
        case "owner": return "User"
        case "chief": return "Chief"
        case "manager": return holder.id == ticket.managerId ? "Manager" : "Manager · \(holder.id)"
        default: return "Worker · \(holder.id)"
        }
    }
    var holders: [Principal] {
        var result = [Principal(kind: "owner", id: "owner")]
        if let id = ticket.managerId { result.append(Principal(kind: "manager", id: id)) }
        result.append(Principal(kind: "chief", id: "chief"))
        return result
    }
}

extension TicketRecapDelivery {
    var note: String? {
        switch fate {
        case "started", "queued", "injected": return nil
        case "uncertain": return "Delivery uncertain · do not resend"
        case "refused":
            let reasons = [
                "no_such_conversation": "there is no such conversation to send to",
                "model_resolution_failed": "the selected model could not be resolved",
                "backend_did_not_start": "the backend would not start",
                "session_did_not_load": "the backend's session would not load",
                "write_to_backend_failed": "the write to the backend did not succeed",
                "no_running_turn_to_steer_into": "there was no running turn to steer into",
                "backend_cannot_steer": "this backend cannot take text into a running turn",
                "running_turn_changed_before_steer": "the running turn changed before delivery",
                "running_turn_cannot_accept_steer": "the running turn cannot accept steering",
                "message_cannot_be_steered": "this message content cannot be steered",
                "backend_rejected_steer": "the backend rejected steering for this turn",
                "command_cannot_join_running_turn": "a command cannot join a turn that is already running"
            ]
            return "Not delivered · \(reasons[refusalReason ?? ""] ?? "the delivery was impossible")"
        default: return "Delivery uncertain · do not resend"
        }
    }
}
