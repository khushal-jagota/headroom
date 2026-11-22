import CryptoKit
import Foundation
import Network
import Observation

/// One foreground drainer. Every action is saved before it is sent, keeps one identity,
/// and is sent again by itself until the server answers:
/// - a message resends with the same sender message ID; the server drops a repeat;
/// - a decision checks its receipt before every submit;
/// - a mark read only moves the read point forward;
/// - an ask answer checks its receipt after a refusal or a lost response.
@MainActor @Observable final class Outbox {
    static let shared = Outbox(store: .shared)
    let store: LocalStore
    private var draining: Set<String> = []
    private enum DrainWake { case changed, finished(String) }
    private var drainWakes: [String: AsyncStream<DrainWake>.Continuation] = [:]
    private var foregroundRunID = UUID()
    private var foregroundWakes: [String: (id: UUID, continuation: AsyncStream<Void>.Continuation)] = [:]
    private(set) var networkAvailable = true
    @ObservationIgnored private let now: () -> Date
    private struct Backoff { let deadline: Date; let delay: TimeInterval }
    nonisolated static let initialRetryDelay: TimeInterval = 5
    nonisolated static let maximumRetryDelay: TimeInterval = 30
    /// Foreground memory only: a relaunch or a regained network path tries again at once.
    @ObservationIgnored private var backoff: [String: [String: Backoff]] = [:]
    init(store: LocalStore, now: @escaping () -> Date = { .now }) { self.store = store; self.now = now }

    // MARK: Reads

    func entries(_ server: String) -> [PendingWork] {
        _ = store.outgoingRevision(server)
        return works(server, (try? store.outgoing.allSummaries(server: server)) ?? [])
    }
    private func works(_ server: String, _ summaries: [OutgoingStore.Summary]) -> [PendingWork] {
        summaries.compactMap { try? store.outgoing.work(server: server, id: $0.id) }
    }
    func entries(_ server: String, conversationID: String) -> [PendingWork] {
        works(server, (try? store.outgoing.summaries(server: server, conversationID: conversationID)) ?? [])
    }
    func entries(_ server: String, draftID: String) -> [PendingWork] {
        works(server, (try? store.outgoing.summaries(server: server, draftID: draftID)) ?? [])
    }
    func work(_ server: String, id: String) -> PendingWork? { try? store.outgoing.work(server: server, id: id) }
    func reviewSummaries(_ server: String) -> [OutgoingStore.Summary] { (try? store.outgoing.reviewSummaries(server: server)) ?? [] }

    // MARK: Saving

    func save(_ work: PendingWork, server: String) throws { try store.outgoing.save(work, server: server) }
    func enqueue(_ work: PendingWork, api: APIClient) throws {
        var frozen = work
        if work.kind == .approval || work.kind == .sendBack {
            guard work.protectedDecisionBody == nil, let proposal = work.proposal, work.ticketID != nil else { throw OutgoingStore.StoreError.invalidTransition }
            let command: DecisionCommand
            if work.kind == .approval, let approval = work.approval {
                guard approval.expectedProposalRevision == proposal.revision else { throw OutgoingStore.StoreError.invalidTransition }
                command = DecisionCommand(decision: "approve", field: proposal.field, expectedProposalRevision: proposal.revision,
                    editedBody: approval.editedBody, nextCeiling: approval.nextCeiling,
                    nextHolder: approval.nextHolder.map { DecisionHolder(kind: $0.kind, id: $0.id) }, message: work.guidance)
            } else if work.kind == .sendBack {
                command = DecisionCommand(decision: "send_back", field: proposal.field, expectedProposalRevision: proposal.revision, message: work.guidance)
            } else { throw OutgoingStore.StoreError.invalidTransition }
            frozen.protectedDecisionBody = try APIClient.encodeReviewDecision(command)
            if work.kind == .sendBack, let draftID = work.draftID, let note = command.message,
               let captured = try store.outgoing.captureDraft(server: LocalStore.server(api.baseURL), id: draftID).text,
               captured.payload == Data(note.utf8) {
                frozen.decisionDraftUpdatedAt = captured.updated
            }
            frozen.protectedProposalCreatedAt = proposal.createdAt
            frozen.proposal = nil; frozen.approval = nil; frozen.guidance = nil
        }
        try store.outgoing.enqueue(frozen, server: LocalStore.server(api.baseURL))
        Task { await drain(api: api) }
    }
    /// Save through the ordinary outgoing transaction, but let the notification
    /// response own its bounded delivery window. Never modify an open composer.
    @discardableResult
    func enqueueNotificationReply(_ reply: NotificationReply, text: String, notificationID: String, api: APIClient) throws -> String {
        let work = try NotificationReplies.work(reply: reply, text: text, notificationID: notificationID, selectedServer: api.baseURL)
        do { try store.outgoing.enqueue(work, server: LocalStore.server(api.baseURL)) }
        catch OutgoingStore.StoreError.duplicateIdentity { /* The original durable intent remains authoritative. */ }
        return work.id
    }
    func enqueueMarkRead(owner: ConversationOwner?, conversationID: String, through: Int, api: APIClient) throws {
        try store.outgoing.enqueueMarkRead(server: LocalStore.server(api.baseURL), owner: owner, conversationID: conversationID, through: through)
        Task { await drain(api: api) }
    }
    /// The outbox entry point for an answer to an agent's ask. Returns the answer's operation ID.
    @discardableResult
    func enqueueAskAnswer(_ answer: AskAnswer, owner: ConversationOwner, api: APIClient) throws -> String {
        let work = PendingWork(id: UUID().uuidString, created: now(), kind: .askAnswer, owner: owner, askAnswer: answer)
        try store.outgoing.enqueue(work, server: LocalStore.server(api.baseURL))
        Task { await drain(api: api) }
        return work.id
    }
    /// The same answer always becomes the same late message, so a crash cannot send it twice.
    nonisolated static func lateAnswerMessageID(operationID: String) -> String {
        let hex = SHA256.hash(data: Data("late-ask-answer:\(operationID)".utf8)).prefix(16).map { String(format: "%02x", $0) }.joined()
        let offsets = [0, 8, 12, 16, 20, 32]
        return zip(offsets, offsets.dropFirst()).map { lower, upper in
            String(hex[hex.index(hex.startIndex, offsetBy: lower)..<hex.index(hex.startIndex, offsetBy: upper)])
        }.joined(separator: "-")
    }

    // MARK: Foreground loop

    func run(api: APIClient) async {
        let runID = UUID(); foregroundRunID = runID
        let server = LocalStore.server(api.baseURL)
        let (wakes, continuation) = AsyncStream<Void>.makeStream(bufferingPolicy: .bufferingNewest(1))
        foregroundWakes[server] = (runID, continuation)
        var wake = wakes.makeAsyncIterator()
        let monitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak self] path in
            let available = path.status == .satisfied
            Task { @MainActor in
                guard let self, self.foregroundRunID == runID else { return }
                if available && !self.networkAvailable { self.backoff[server] = nil }
                self.networkAvailable = available
                continuation.yield(())
            }
        }
        monitor.start(queue: DispatchQueue(label: "app.panels.connection"))
        defer {
            monitor.cancel(); continuation.finish()
            if foregroundRunID == runID { foregroundRunID = UUID() }
            if foregroundWakes[server]?.id == runID { foregroundWakes[server] = nil }
        }
        while !Task.isCancelled {
            await drain(api: api, wakeForeground: false)
            let timer: Task<Void, Never>?
            if networkAvailable && !NativeBuildGate.requiresUpdate(api.baseURL), let delay = retryDelay(server: server) {
                timer = Task {
                    do { try await Task.sleep(for: .seconds(delay)); continuation.yield(()) } catch { }
                }
            } else { timer = nil }
            let event: Void? = await wake.next()
            timer?.cancel()
            if event == nil { return }
        }
    }
    /// When the earliest item that waits after a failed attempt may try again. Nil: nothing waits.
    func retryDelay(server: String) -> TimeInterval? {
        guard let rows = try? store.outgoing.runnable(server: server), !rows.isEmpty else { return nil }
        return rows.map { max(1, backoff[server]?[$0.id]?.deadline.timeIntervalSince(now()) ?? 0) }.min()
    }
    private func deferAttempt(server: String, id: String) {
        let delay = backoff[server]?[id].map { min(Self.maximumRetryDelay, $0.delay * 2) } ?? Self.initialRetryDelay
        backoff[server, default: [:]][id] = Backoff(deadline: now().addingTimeInterval(delay), delay: delay)
    }
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

    // MARK: Retry by tap

    /// A refused message gets a new identity in its old place. A failed recording is transcribed again.
    @discardableResult
    func retry(_ id: String, api: APIClient, content: [ConversationSend.Piece]? = nil,
               imageWorkStarted: @escaping @Sendable () -> Void = {}) async -> String? {
        let server = LocalStore.server(api.baseURL)
        guard let work = work(server, id: id) else { return nil }
        do {
            if work.kind == .voice {
                try store.outgoing.retryVoice(server: server, id: id)
                Task { await drain(api: api) }
                return id
            }
            guard work.state == .failed, let message = work.message else { return nil }
            let original = try store.outgoing.summary(server: server, id: id)
            let replacementContent: [ConversationSend.Piece]
            if let content { replacementContent = content }
            else {
                replacementContent = try await ConversationImagePreparation.prepare(message.content, imageWorkStarted: imageWorkStarted)
                guard try store.outgoing.summary(server: server, id: id) == original else { return nil }
            }
            let nextID = UUID().uuidString
            let next = ConversationSend(conversationId: message.conversationId, content: replacementContent, mode: message.mode,
                backendKey: message.backendKey, model: message.model, reasoningEffort: message.reasoningEffort, reasoningEffortIsSet: message.reasoningEffortIsSet,
                senderMessageId: nextID, sentAtUnixMilliseconds: Int64(Date().timeIntervalSince1970 * 1000), ownerReadThroughSequence: message.ownerReadThroughSequence)
            let replacement = PendingWork(id: nextID, created: .now, kind: .message, owner: work.owner, message: next, draftID: work.draftID,
                priorMessageIDs: (work.priorMessageIDs ?? []) + [id])
            var capturedDraft: OutgoingStore.DraftCapture?
            if let recovery = work.rejectedPhotoDraft {
                let capture = try store.outgoing.captureDraft(server: server, id: recovery.id)
                let expectedText = recovery.originalText ?? message.content.compactMap(\.text).joined(separator: "\n")
                let savedPieces = capture.attachments.flatMap { try? JSONDecoder().decode([ConversationSend.Piece].self, from: $0.payload) }
                if let completed = work.completed,
                   capture.text?.updated == completed, capture.attachments?.updated == completed,
                   capture.text?.payload == Data(expectedText.utf8), savedPieces == message.content.filter({ $0.piece != "text" }) {
                    capturedDraft = capture
                }
            }
            try store.outgoing.replace(server: server, id: id, with: replacement, capturedDraft: capturedDraft)
            Task { await drain(api: api) }
            return nextID
        } catch is OutgoingStore.StoreError { return nil }
        catch { if store.outgoingError == nil { store.saveError = error.localizedDescription }; return nil }
    }

    // MARK: Drain

    /// A task owns one lane until its request settles. Other lanes can use the second slot.
    func drain(api: APIClient, wakeForeground: Bool = true) async {
        let server = LocalStore.server(api.baseURL)
        if wakeForeground { foregroundWakes[server]?.continuation.yield(()) }
        guard !draining.contains(server) else { drainWakes[server]?.yield(.changed); return }
        do { try expire(server) } catch { return }
        guard !NativeBuildGate.requiresUpdate(api.baseURL), networkAvailable else { return }
        draining.insert(server)
        let (wakes, continuation) = AsyncStream<DrainWake>.makeStream()
        drainWakes[server] = continuation
        defer { continuation.finish(); drainWakes[server] = nil; draining.remove(server) }
        var visited: Set<String> = []
        var activeLanes: Set<String> = []
        await withTaskGroup(of: Void.self) { group in
            var wake = wakes.makeAsyncIterator()
            while !Task.isCancelled, !NativeBuildGate.requiresUpdate(api.baseURL) {
                let rows = (try? store.outgoing.runnable(server: server)) ?? []
                let open = Set(rows.map(\.id))
                backoff[server] = backoff[server]?.filter { open.contains($0.key) }
                let ready = rows.filter {
                    !visited.contains($0.id) && !activeLanes.contains($0.lane)
                        && (backoff[server]?[$0.id]?.deadline ?? .distantPast) <= now()
                }
                for row in ready where activeLanes.count < 2 && !activeLanes.contains(row.lane) {
                    visited.insert(row.id); activeLanes.insert(row.lane)
                    group.addTask {
                        await self.process(row, api: api, server: server)
                        continuation.yield(.finished(row.lane))
                    }
                }
                guard !activeLanes.isEmpty, let event = await wake.next() else { break }
                if case .finished(let lane) = event { activeLanes.remove(lane) }
            }
            group.cancelAll()
        }
    }

    /// How one failed request leaves its item.
    private enum Failure {
        /// No answer that settles it: try again later with the same identity.
        case later
        /// The server refused it. The note shows on the item.
        case refused(String)
        /// This build is too old for the server. The update screen covers the app.
        case updateRequired
    }
    private static func classify(_ error: Error) -> Failure {
        if let error = error as? APIError {
            if error.isUpdateRequired { return .updateRequired }
            if error.status >= 400 && error.status < 500 && ![408, 429].contains(error.status) { return .refused("Not sent · " + error.message) }
            return error.status == 0 ? .refused(error.message) : .later
        }
        if error is DecisionResponseError { return .refused("The server returned an invalid decision response. Refresh and review again.") }
        return .later
    }
    private func settleFailure(_ failure: Failure, id: String, server: String, restorePhotoDraft: Bool = false) {
        switch failure {
        case .later: deferAttempt(server: server, id: id)
        case .updateRequired: break
        case .refused(let note):
            try? store.outgoing.settle(server: server, id: id, state: .failed, note: note, restoreRejectedPhotoDraft: restorePhotoDraft)
        }
    }

    private func process(_ row: OutgoingStore.Summary, api: APIClient, server: String) async {
        let work: PendingWork
        do {
            guard let saved = try store.outgoing.work(server: server, id: row.id) else { return }
            work = saved
        } catch {
            try? store.outgoing.settle(server: server, id: row.id, state: .failed, note: "This saved item could not be read.")
            return
        }
        switch work.kind {
        case .message: await processMessage(work, api: api, server: server)
        case .approval, .sendBack: await processDecision(row, work: work, api: api, server: server)
        case .voice: await processVoice(work, api: api, server: server)
        case .markRead: await processMarkRead(work, api: api, server: server)
        case .askAnswer: await processAskAnswer(row, work: work, api: api, server: server)
        }
    }

    private func processMessage(_ work: PendingWork, api: APIClient, server: String) async {
        guard let message = work.message, let owner = work.owner else { return }
        if case .goal = owner {
            try? store.outgoing.settle(server: server, id: work.id, state: .failed, note: "Goal conversations are retired. Open the goal to talk to its holder.")
            return
        }
        do {
            try store.outgoing.beginAttempt(server: server, id: work.id)
            let result = try await api.sendConversation(owner, message: message)
            // A refused first message leaves no conversation behind, so later messages keep
            // their own start.
            switch result.fate {
            case "refused":
                try store.outgoing.settle(server: server, id: work.id, state: .failed,
                    note: "Not delivered · " + ConversationDelivery.refusalText(result.refusalReason), conversationID: message.conversationId)
            case "suppressed":
                try store.outgoing.settle(server: server, id: work.id, state: .failed,
                    note: "Not delivered · " + (result.reason ?? "no longer applicable"), conversationID: message.conversationId)
            default:
                // Started, injected, queued or recorded by the server: the message is there.
                try store.outgoing.settle(server: server, id: work.id, state: .sent, conversationID: result.conversationId)
            }
        } catch {
            settleFailure(Self.classify(error), id: work.id, server: server,
                restorePhotoDraft: (error as? APIError)?.status == 422 && message.content.contains(where: { $0.piece == "image" }))
        }
    }

    private func processVoice(_ work: PendingWork, api: APIClient, server: String) async {
        guard let file = work.audioFile else { return }
        do {
            let audio = try Data(contentsOf: LocalStore.directory.appendingPathComponent(file))
            try store.outgoing.beginAttempt(server: server, id: work.id)
            let transcript = try await api.transcribeReviewAudio(audio)
            // The local transaction prevents a late duplicate response from appending twice.
            guard self.work(server, id: work.id) != nil else { return }
            try store.outgoing.finishVoice(server: server, id: work.id, transcript: transcript)
            if (try? store.outgoing.audioIsUnowned(file)) == true { try? FileManager.default.removeItem(at: LocalStore.directory.appendingPathComponent(file)) }
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile || error.code == .fileReadCorruptFile {
            try? store.outgoing.settle(server: server, id: work.id, state: .failed, note: "The recording could not be read.")
        } catch { settleFailure(Self.classify(error), id: work.id, server: server) }
    }

    private func processMarkRead(_ work: PendingWork, api: APIClient, server: String) async {
        guard let conversationID = work.readConversationID, let through = work.readThroughSequence else { return }
        do {
            try store.outgoing.beginAttempt(server: server, id: work.id)
            _ = try await api.markConversationRead(conversationID, through: through)
            try store.outgoing.settle(server: server, id: work.id, state: .sent)
        } catch is OutgoingStore.StoreError {
            // A higher read point replaced this one while it was in flight.
        } catch { settleFailure(Self.classify(error), id: work.id, server: server) }
    }

    /// A receipt means the answer landed. After a refusal, no receipt means the ask stopped
    /// waiting: the answer becomes an ordinary message. After a lost response, it is sent again.
    private func processAskAnswer(_ row: OutgoingStore.Summary, work: PendingWork, api: APIClient, server: String) async {
        guard let answer = work.askAnswer else { return }
        do {
            if row.attempted, try await api.askAnswerReceipt(conversationID: answer.conversationID, operationID: work.id) != nil {
                try store.outgoing.settle(server: server, id: work.id, state: .sent)
                return
            }
            try store.outgoing.beginAttempt(server: server, id: work.id)
            let fate = try await api.answerAsk(answer, operationID: work.id)
            guard fate == "refused" else {
                try store.outgoing.settle(server: server, id: work.id, state: .sent)
                return
            }
            if try await api.askAnswerReceipt(conversationID: answer.conversationID, operationID: work.id) != nil {
                try store.outgoing.settle(server: server, id: work.id, state: .sent)
                return
            }
            let messageID = Self.lateAnswerMessageID(operationID: work.id)
            let late = PendingWork(id: messageID, created: now(), kind: .message, owner: work.owner,
                message: ConversationSend(conversationId: answer.conversationID, content: [.init(text: answer.lateMessageText)],
                    senderMessageId: messageID, sentAtUnixMilliseconds: Int64(now().timeIntervalSince1970 * 1000)))
            try store.outgoing.convertLateAnswer(server: server, id: work.id, message: late)
            drainWakes[server]?.yield(.changed)
        } catch is OutgoingStore.StoreError {
        } catch { settleFailure(Self.classify(error), id: work.id, server: server) }
    }

    private func processDecision(_ fact: OutgoingStore.Summary, work: PendingWork, api: APIClient, server: String) async {
        guard let ticketID = fact.ticketID, let field = fact.proposalField, let revision = fact.proposalRevision,
              let body = work.protectedDecisionBody,
              let command = try? APIClient.decoder().decode(DecisionCommand.self, from: body) else {
            try? store.outgoing.settle(server: server, id: fact.id, state: .failed, note: "This saved decision could not be read. Review again.")
            return
        }
        // The receipt comes first, every time. Only an explicit absence permits a submit.
        let identity = DecisionCommand(decision: fact.kind == "approval" ? "approve" : "send_back",
            field: field, expectedProposalRevision: revision)
        do {
            let receipt: DecisionReceipt
            if let existing = try await api.lookupReviewDecision(ticketID: ticketID, operationID: fact.id, command: identity) {
                receipt = existing
            } else {
                try store.outgoing.beginAttempt(server: server, id: fact.id)
                receipt = try await api.submitReviewDecision(ticketID: ticketID, operationID: fact.id, command: command, frozenBody: body)
            }
            try settleDecision(receipt, id: fact.id, work: work, server: server)
        } catch is OutgoingStore.StoreError {
        } catch let error as DecisionResponseError where error == .unsupported {
            try? store.outgoing.settle(server: server, id: fact.id, state: .failed,
                note: "Not sent · the server needs an update before the phone can approve or send back. Use the web for now.")
        } catch let error as APIError where [401, 403].contains(error.status) {
            try? store.outgoing.settle(server: server, id: fact.id, state: .failed,
                note: "Not sent · sign in or check your access to this ticket, then review again.")
        } catch let error as APIError where !error.isUpdateRequired && (400..<500).contains(error.status) && ![408, 429].contains(error.status) {
            try? store.outgoing.settle(server: server, id: fact.id, state: .failed, note: "Not sent · " + error.message)
        } catch { settleFailure(Self.classify(error), id: fact.id, server: server) }
    }
    private func settleDecision(_ receipt: DecisionReceipt, id: String, work: PendingWork, server: String) throws {
        if receipt.outcome == "applied" {
            var draft: (id: String, text: String, updated: Date)?
            if let draftID = work.draftID, let updated = work.decisionDraftUpdatedAt, let body = work.protectedDecisionBody,
               let text = try APIClient.decoder().decode(DecisionCommand.self, from: body).message {
                draft = (id: draftID, text: text, updated: updated)
            }
            try store.outgoing.settle(server: server, id: id, state: .sent, decisionDraft: draft)
        } else {
            try store.outgoing.settle(server: server, id: id, state: .failed,
                note: "Not sent · " + (receipt.error?.message ?? "This decision was refused. Review again."))
        }
    }
}
