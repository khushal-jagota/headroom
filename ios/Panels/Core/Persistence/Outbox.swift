import Foundation
import Network
import Observation

struct PendingWork: Codable, Identifiable, Sendable {
    enum Kind: String, Codable { case message, approval, sendBack, voice }
    enum State: String, Codable { case recording, waiting, sending, uncertain, serverQueued, sent, failed, changed }
    let id: String
    let created: Date
    let kind: Kind
    var state: State = .waiting
    var note: String = "Waiting for connection"
    var owner: ConversationOwner?
    var message: ConversationSend?
    var ticketID: String?
    var proposal: TicketProposal?
    var approval: ReviewApproval?
    var guidance: String?
    var audioFile: String?
    var draftID: String?
    var completed: Date?
    var priorMessageIDs: [String]?
    var ticketTitle: String?
    var proposalFieldLabel: String?
    var displayText: String {
        message?.content.compactMap { $0.text ?? $0.fileName }.joined(separator: "\n") ?? (kind == .approval ? "Approve" : kind == .sendBack ? "Send back" : "Voice recording")
    }
}

/// One foreground drainer. Persist before sending; restart reconciles an in-flight identity.
@MainActor @Observable final class Outbox {
    static let shared = Outbox(store: .shared)
    let store: LocalStore
    private var draining: Set<String> = []
    private var foregroundRunID = UUID()
    private(set) var networkAvailable = true
    init(store: LocalStore) { self.store = store }

    func entries(_ server: String) -> [PendingWork] {
        ((try? store.records(server: server, kind: "outbox")) ?? []).compactMap { try? JSONDecoder().decode(PendingWork.self, from: $0.payload) }.sorted { $0.created < $1.created }
    }
    func save(_ work: PendingWork, server: String) throws {
        try store.put(server: server, kind: "outbox", id: work.id, data: JSONEncoder().encode(work))
    }
    func enqueue(_ work: PendingWork, api: APIClient) throws {
        try save(work, server: LocalStore.server(api.baseURL))
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
    func retry(_ id: String, api: APIClient) {
        let server = LocalStore.server(api.baseURL)
        guard var work = entries(server).first(where: { $0.id == id }), work.state == .failed else { return }
        // A terminal server refusal needs a new message identity, only on an explicit tap.
        if let message = work.message {
            let nextID = UUID().uuidString
            let next = ConversationSend(conversationId: message.conversationId, content: message.content, mode: message.mode,
                                    backendKey: message.backendKey, model: message.model, reasoningEffort: message.reasoningEffort,
                                    senderMessageId: nextID, sentAtUnixMilliseconds: Int64(Date().timeIntervalSince1970 * 1000), ownerReadThroughSequence: message.ownerReadThroughSequence)
            let replacement = PendingWork(id: nextID, created: .now, kind: .message, owner: work.owner, message: next, priorMessageIDs: (work.priorMessageIDs ?? []) + [id])
            do { try save(replacement, server: server); try store.remove(server: server, kind: "outbox", id: id) }
            catch { store.saveError = error.localizedDescription; return }
        } else {
            work.state = .waiting; work.note = "Waiting for connection"
            do { try save(work, server: server) } catch { store.saveError = error.localizedDescription; return }
        }
        Task { await drain(api: api) }
    }
    func drain(api: APIClient) async {
        let server = LocalStore.server(api.baseURL)
        guard networkAvailable, !draining.contains(server) else { return }
        draining.insert(server)
        defer { draining.remove(server) }
        for var work in entries(server) {
            guard !Task.isCancelled else { return }
            guard [.waiting, .sending, .uncertain, .serverQueued].contains(work.state) else { continue }
            // Decisions have no server receipt. Never silently replay an ambiguous write.
            if work.kind != .message && work.kind != .voice && [.sending, .uncertain].contains(work.state) {
                work.state = .uncertain; work.note = "Delivery uncertain · check the ticket"
                try? save(work, server: server); continue
            }
            let priorState = work.state
            var writeStarted = false
            do {
                if work.kind == .message, let message = work.message, let owner = work.owner {
                    if let id = message.conversationId {
                        if let receipt = try await api.conversationReceipts(id, messageIDs: [work.id]).first(where: { $0.senderMessageId == work.id }) {
                            switch receipt.fate {
                            case "prompt": work.state = .sent; work.note = "Sent"
                            case "prompt_refused": work.state = .failed; work.note = "Not sent · tap to retry"
                            case "prompt_discarded": work.state = .changed; work.note = "Not sent · no longer applicable"
                            default: work.state = .uncertain; work.note = "Delivery uncertain"
                            }
                            if work.state == .sent { work.completed = .now }
                            try save(work, server: server); continue
                        }
                        if work.state == .serverQueued { continue }
                    } else if work.state != .waiting {
                        work.state = .uncertain; work.note = "Delivery uncertain · check conversation"
                        try save(work, server: server); continue
                    }
                    work.state = .sending; work.note = "Sending…"; try save(work, server: server)
                    writeStarted = true
                    let result = try await api.sendConversation(owner, message: message)
                    if let id = result.conversationId {
                        work.message = ConversationSend(conversationId: id, content: message.content, mode: message.mode,
                            backendKey: message.backendKey, model: message.model, reasoningEffort: message.reasoningEffort,
                            senderMessageId: message.senderMessageId, sentAtUnixMilliseconds: message.sentAtUnixMilliseconds, ownerReadThroughSequence: message.ownerReadThroughSequence)
                    }
                    switch result.fate {
                    case "started", "injected": work.state = .sent; work.note = "Sent"; work.completed = .now
                    case "queued":
                        work.state = .serverQueued; work.note = "Queued on server"
                        if let id = result.conversationId {
                            work.message = ConversationSend(conversationId: id, content: message.content, mode: message.mode,
                                backendKey: message.backendKey, model: message.model, reasoningEffort: message.reasoningEffort,
                                senderMessageId: message.senderMessageId, sentAtUnixMilliseconds: message.sentAtUnixMilliseconds, ownerReadThroughSequence: message.ownerReadThroughSequence)
                        }
                    case "refused": work.state = .failed; work.note = "Not sent · tap to retry"
                    case "suppressed": work.state = .changed; work.note = "Not sent · no longer applicable"
                    default: work.state = .uncertain; work.note = "Delivery uncertain"
                    }
                } else if work.kind == .voice, let file = work.audioFile, let draftID = work.draftID {
                    let audio = try Data(contentsOf: LocalStore.directory.appendingPathComponent(file))
                    let transcript = try await api.transcribeReviewAudio(audio)
                    guard entries(server).contains(where: { $0.id == work.id }) else { continue }
                    let draft = store.draft(server: server, id: draftID) ?? ""
                    let result = draft.isEmpty ? transcript : draft + "\n\n" + transcript
                    // Transcript and completion share the transaction, so relaunch cannot append twice.
                    work.state = .sent; work.note = "Transcribed"; work.completed = .now
                    try store.put(server: server, kind: "outbox", id: work.id, data: JSONEncoder().encode(work), draft: (draftID, result))
                    try? FileManager.default.removeItem(at: LocalStore.directory.appendingPathComponent(file))
                    continue
                } else if let ticketID = work.ticketID, let expected = work.proposal {
                    let latest = try await api.ticket(ticketID)
                    guard let current = latest.pendingProposal, ReviewDecision.matches(expected, current) else {
                        work.state = .changed; work.note = "This changed, review again"
                        try save(work, server: server); continue
                    }
                    work.state = .sending; work.note = "Sending…"; try save(work, server: server)
                    writeStarted = true
                    if let approval = work.approval { try await api.approveReview(ticketID, field: expected.field, approval: approval) }
                    else { try await api.returnReview(ticketID, message: work.guidance ?? "", expectedProposalRevision: expected.revision) }
                    work.state = .sent; work.note = work.kind == .approval ? "Approved" : "Sent back"; work.completed = .now
                }
                try save(work, server: server)
            } catch {
                if let error = error as? APIError, error.status >= 500, writeStarted {
                    work.state = .uncertain; work.note = "Delivery uncertain"
                } else if let error = error as? APIError {
                    let changed = error.status == 409 || error.isChangedProposal
                    work.state = changed ? .changed : .failed
                    work.note = changed ? "This changed, review again" : "Not sent · \(error.message)"
                } else if let error = error as? URLError, [.notConnectedToInternet, .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed].contains(error.code) {
                    work.state = writeStarted ? .waiting : priorState; work.note = "Waiting for connection"
                } else if work.state == .sending {
                    work.state = .uncertain; work.note = "Delivery uncertain"
                } else { work.note = "Waiting for connection" }
                do { try save(work, server: server) } catch { store.saveError = error.localizedDescription }
                // A transport failure blocks later work; preserve ordering across reconnects.
                if !(error is APIError) { return }
            }
        }
    }
}
