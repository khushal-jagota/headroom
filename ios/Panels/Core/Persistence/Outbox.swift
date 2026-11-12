import Foundation
import Network
import Observation

/// One foreground drainer. Persist before sending; restart reconciles an in-flight identity.
@MainActor @Observable final class Outbox {
    static let shared = Outbox(store: .shared)
    let store: LocalStore
    private var draining: Set<String> = []
    private var foregroundRunID = UUID()
    private(set) var networkAvailable = true
    init(store: LocalStore) { self.store = store }

    // Compatibility/debug detail list. Interactive consumers use scoped queries below.
    func entries(_ server: String) -> [PendingWork] {
        _ = store.outgoingRevision(server)
        return works(server, summaries: (try? store.outgoing.allSummaries(server: server)) ?? [])
    }
    private func works(_ server: String, summaries: [OutgoingStore.Summary]) -> [PendingWork] {
        summaries.compactMap { try? store.outgoing.work(server: server, id: $0.id) }
    }
    func entries(_ server: String, owner: ConversationOwner) -> [PendingWork] {
        works(server, summaries: (try? store.outgoing.summaries(server: server, owner: owner)) ?? [])
    }
    func entries(_ server: String, conversationID: String) -> [PendingWork] {
        works(server, summaries: (try? store.outgoing.summaries(server: server, conversationID: conversationID)) ?? [])
    }
    func entries(_ server: String, draftID: String) -> [PendingWork] {
        works(server, summaries: (try? store.outgoing.summaries(server: server, draftID: draftID)) ?? [])
    }
    func work(_ server: String, id: String) -> PendingWork? { try? store.outgoing.work(server: server, id: id) }
    func decisionSummaries(_ server: String) -> [OutgoingStore.Summary] { (try? store.outgoing.decisionSummaries(server: server)) ?? [] }
    static func encode(_ work: PendingWork) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        return try encoder.encode(work)
    }
    func save(_ work: PendingWork, server: String) throws {
        try store.put(server: server, kind: "outbox", id: work.id, data: Self.encode(work))
    }
    func enqueue(_ work: PendingWork, api: APIClient) throws {
        var frozen = work
        if work.kind == .approval || work.kind == .sendBack {
            guard work.protectedDecisionBody == nil, let proposal = work.proposal, work.ticketID != nil else { throw OutgoingStore.StoreError.invalidTransition }
            let command: DecisionCommand
            if work.kind == .approval, let approval = work.approval {
                guard approval.expectedProposalRevision == proposal.revision else { throw OutgoingStore.StoreError.invalidTransition }
                command = DecisionCommand(decision: "approve", field: proposal.field, expectedProposalRevision: proposal.revision,
                    editedBody: approval.editedBody, nextCeiling: approval.nextCeiling,
                    nextHolder: DecisionHolder(kind: approval.nextHolder.kind, id: approval.nextHolder.id))
            } else if work.kind == .sendBack {
                command = DecisionCommand(decision: "send_back", field: proposal.field, expectedProposalRevision: proposal.revision, message: work.guidance)
            } else { throw OutgoingStore.StoreError.invalidTransition }
            frozen.protectedDecisionBody = try APIClient.encodeReviewDecision(command)
            frozen.protectedProposalCreatedAt = proposal.createdAt
            frozen.proposal = nil; frozen.approval = nil; frozen.guidance = nil
        }
        try store.outgoing.enqueue(frozen, server: LocalStore.server(api.baseURL))
        Task { await drain(api: api) }
    }
    func run(api: APIClient) async {
        let runID = UUID(); foregroundRunID = runID
        let monitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak self] path in
            let available = path.status == .satisfied
            Task { @MainActor in
                guard let self, self.foregroundRunID == runID else { return }
                self.networkAvailable = available
                if available { await self.drain(api: api) }
            }
        }
        monitor.start(queue: DispatchQueue(label: "app.panels.connection"))
        defer { monitor.cancel(); if foregroundRunID == runID { foregroundRunID = UUID() } }
        while !Task.isCancelled {
            await drain(api: api)
            do { try await Task.sleep(for: .seconds(3)) } catch { return }
        }
    }
    nonisolated static let finishedLifetime: TimeInterval = 24 * 60 * 60
    nonisolated static let uncertainLifetime: TimeInterval = 7 * 24 * 60 * 60
    private func expire(_ server: String, now: Date = .now) throws {
        let files = try store.outgoing.expire(server: server, now: now)
        for file in files where try store.outgoing.audioIsUnowned(file) { try? FileManager.default.removeItem(at: LocalStore.directory.appendingPathComponent(file)) }
    }
    @discardableResult
    func clearFinished(_ server: String, now: Date = .now) -> [PendingWork] {
        do { try expire(server, now: now) }
        catch { store.saveError = error.localizedDescription }
        return entries(server)
    }
    @discardableResult
    func retry(_ id: String, api: APIClient, content: [ConversationSend.Piece]? = nil, destination: OutgoingStore.Destination? = nil) -> String? {
        let server = LocalStore.server(api.baseURL)
        guard let work = work(server, id: id) else { return nil }
        do {
            if work.kind == .voice {
                try store.outgoing.retryVoice(server: server, id: id)
                Task { await drain(api: api) }
                return id
            }
            guard let message = work.message else { return nil }
            let nextID = UUID().uuidString
            var next = message
            next = ConversationSend(conversationId: message.conversationId, content: content ?? message.content, mode: message.mode,
                backendKey: message.backendKey, model: message.model, reasoningEffort: message.reasoningEffort, reasoningEffortIsSet: message.reasoningEffortIsSet,
                senderMessageId: nextID, sentAtUnixMilliseconds: Int64(Date().timeIntervalSince1970 * 1000), ownerReadThroughSequence: message.ownerReadThroughSequence)
            let aliases = work.state == .failed ? (work.priorMessageIDs ?? []) + [id] : nil
            let replacement = PendingWork(id: nextID, created: .now, kind: .message, owner: work.owner, message: next, priorMessageIDs: aliases)
            try store.outgoing.replace(server: server, id: id, with: replacement)
            if message.conversationId == nil, let destination { try store.outgoing.releaseHold(server: server, id: nextID, destination: destination) }
            Task { await drain(api: api) }
            return nextID
        } catch { store.saveError = error.localizedDescription; return nil }
    }
    func sendHeld(_ id: String, api: APIClient, destination: OutgoingStore.Destination) {
        do {
            try store.outgoing.releaseHold(server: LocalStore.server(api.baseURL), id: id, destination: destination)
            Task { await drain(api: api) }
        } catch { store.saveError = error.localizedDescription }
    }
    /// A task owns one lane until its request settles. Other lanes can use the second slot.
    func drain(api: APIClient) async {
        let server = LocalStore.server(api.baseURL)
        guard !draining.contains(server) else { return }
        do { try expire(server) } catch { return }
        guard !NativeBuildGate.requiresUpdate(api.baseURL), networkAvailable else { return }
        draining.insert(server)
        defer { draining.remove(server) }
        var visited: Set<String> = []
        var activeLanes: Set<String> = []
        await withTaskGroup(of: String.self) { group in
            while !Task.isCancelled, !NativeBuildGate.requiresUpdate(api.baseURL) {
                let rows = (try? store.outgoing.schedulingSummaries(server: server)) ?? []
                // A queued receipt must not hold up a later submission in its lane.
                let candidates = rows.filter { !visited.contains($0.id) && !activeLanes.contains($0.lane) }
                let ready = candidates.filter { (try? store.outgoing.canSubmit(server: server, id: $0.id)) == true }
                let polls = candidates.filter { ["sending", "uncertain", "serverQueued"].contains($0.state) }
                for row in ready + polls where activeLanes.count < 2 && !activeLanes.contains(row.lane) && !visited.contains(row.id) {
                    visited.insert(row.id); activeLanes.insert(row.lane)
                    group.addTask {
                        await self.process(row.id, api: api, server: server)
                        return row.lane
                    }
                }
                guard !activeLanes.isEmpty, let lane = await group.next() else { break }
                activeLanes.remove(lane)
            }
            group.cancelAll()
        }
    }
    private func process(_ id: String, api: APIClient, server: String) async {
        guard let work = work(server, id: id) else { return }
        if work.kind == .approval || work.kind == .sendBack {
            await processDecision(work, api: api, server: server)
            return
        }
        var writeStarted = false
        do {
            if case .goal = work.owner {
                try store.outgoing.settle(server: server, id: id, state: .changed, note: "Not sent · goal conversations are retired; open the goal to talk to its holder")
                return
            }
            if work.state != .waiting {
                // An absent receipt never proves that an attempted or legacy write is safe to replay.
                if work.kind == .message, let conversationID = work.message?.conversationId {
                    if let receipt = try await api.conversationReceipts(conversationID, messageIDs: [id]).first(where: { $0.senderMessageId == id }) {
                        let state: PendingWork.State
                        let note: String
                        switch receipt.fate {
                        case "prompt": state = .sent; note = "Sent"
                        case "prompt_refused": state = .failed; note = "Not sent · tap to retry"
                        case "prompt_discarded": state = .changed; note = "Not sent · no longer applicable"
                        default: state = .uncertain; note = "Delivery uncertain"
                        }
                        try store.outgoing.settle(server: server, id: id, state: state, note: note)
                    } else if work.state != .serverQueued {
                        try store.outgoing.settle(server: server, id: id, state: .uncertain, note: "Delivery uncertain · check conversation")
                    }
                } else if work.state != .uncertain {
                    try store.outgoing.settle(server: server, id: id, state: .uncertain, note: "Delivery uncertain · check before another attempt")
                }
                return
            }
            try store.outgoing.beginAttempt(server: server, id: id)
            if let message = work.message, let owner = work.owner {
                writeStarted = true
                let result = try await api.sendConversation(owner, message: message)
                let state: PendingWork.State, note: String
                switch result.fate {
                case "started", "injected": state = .sent; note = "Sent"
                case "queued": state = .serverQueued; note = "Queued on server"
                case "refused": state = .failed; note = "Not sent · tap to retry"
                case "suppressed": state = .changed; note = "Not sent · no longer applicable"
                default: state = .uncertain; note = "Delivery uncertain"
                }
                try store.outgoing.settle(server: server, id: id, state: state, note: note, conversationID: result.conversationId)
            } else if work.kind == .voice, let file = work.audioFile {
                let audio = try Data(contentsOf: LocalStore.directory.appendingPathComponent(file))
                writeStarted = true
                let transcript = try await api.transcribeReviewAudio(audio)
                // The local transaction prevents a late duplicate response from appending twice.
                guard self.work(server, id: id) != nil else { return }
                try store.outgoing.finishVoice(server: server, id: id, transcript: transcript)
                if (try? store.outgoing.audioIsUnowned(file)) == true { try? FileManager.default.removeItem(at: LocalStore.directory.appendingPathComponent(file)) }

            }
        } catch {
            if let error = error as? APIError, error.isUpdateRequired, writeStarted {
                try? store.outgoing.rejectAdmission(server: server, id: id, note: NativeBuildGate.message)
                return
            }
            if let error = error as? APIError, error.status > 0, error.status < 500, error.status != 408, writeStarted {
                let changed = error.status == 409 || error.isChangedProposal
                try? store.outgoing.settle(server: server, id: id, state: changed ? .changed : .failed,
                    note: changed ? "This changed, review again" : "Not sent · \(error.message)")
            } else if writeStarted {
                try? store.outgoing.settle(server: server, id: id, state: .uncertain, note: "Delivery uncertain · check before another attempt")
            }
        }
    }
    private func processDecision(_ work: PendingWork, api: APIClient, server: String) async {
        guard let body = work.protectedDecisionBody, let ticketID = work.ticketID,
              let command = try? APIClient.decoder().decode(DecisionCommand.self, from: body) else {
            // Bodyless legacy rows never gain protected-write authority on read.
            try? store.outgoing.settle(server: server, id: work.id, state: .uncertain, note: "Delivery uncertain · check the ticket before another decision")
            return
        }
        let fact: OutgoingStore.Summary
        do {
            guard let saved = try store.outgoing.summary(server: server, id: work.id) else { return }
            fact = saved
        } catch { store.saveError = error.localizedDescription; return }
        var attempted = fact.provenance != "unattempted"
        let existing: DecisionReceipt?
        do {
            existing = try await api.lookupReviewDecision(ticketID: ticketID, operationID: work.id, command: command)
        } catch {
            recordDecisionTransportFailure(error, work: work, server: server, attempted: attempted)
            return
        }
        if let existing {
            // Local save errors already expose the storage failure. They cannot prove
            // that the server lacks support or turn its applied outcome into refusal.
            do { try settleDecision(existing, work: work, server: server) } catch { return }
            return
        }
        // Only an explicit supported null receipt permits this exact operation to POST.
        if !attempted {
            do { try store.outgoing.beginAttempt(server: server, id: work.id) } catch { return }
            attempted = true
        }
        let result: DecisionReceipt
        do {
            result = try await api.submitReviewDecision(ticketID: ticketID, operationID: work.id, command: command, frozenBody: body)
        } catch {
            recordDecisionTransportFailure(error, work: work, server: server, attempted: attempted)
            return
        }
        do { try settleDecision(result, work: work, server: server) } catch { return }
    }
    private func recordDecisionTransportFailure(_ error: Error, work: PendingWork, server: String, attempted: Bool) {
        if attempted {
            try? store.outgoing.settle(server: server, id: work.id, state: .uncertain,
                note: "Decision outcome uncertain · waiting for its server receipt")
        } else if let error = error as? APIError, error.isUpdateRequired {
            try? store.outgoing.settle(server: server, id: work.id, state: .waiting, note: NativeBuildGate.message)
        } else if error is URLError || error is CancellationError || (error as? APIError).map({ $0.status >= 500 || $0.status == 408 || $0.status == 429 }) == true {
            // A transient lookup failure did not attempt the write or disprove support.
            try? store.outgoing.settle(server: server, id: work.id, state: .waiting, note: "Waiting for connection")
        } else {
            try? store.outgoing.settle(server: server, id: work.id, state: .changed,
                note: "Not sent · this server cannot confirm protected decisions. Use a compatible server and review again.")
        }
    }
    private func settleDecision(_ receipt: DecisionReceipt, work: PendingWork, server: String) throws {
        if receipt.outcome == "applied" {
            try store.outgoing.settle(server: server, id: work.id, state: .sent, note: work.kind == .approval ? "Approved" : "Sent back")
        } else {
            try store.outgoing.settle(server: server, id: work.id, state: .changed,
                note: "Not sent · " + (receipt.error?.message ?? "This decision was refused. Review again."))
        }
    }

}
