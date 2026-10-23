import Foundation
import Observation

@MainActor @Observable final class ConversationModel {
    var conversationID: String?
    var draft = "" { didSet { saveDraft() } }
    private var persistenceAPI: APIClient?
    private var persistenceOwner: ConversationOwner?
    private var restoring = false
    private var customDraftID: String?
    private var draftKey: String { customDraftID ?? "conversation:" + (persistenceOwner?.path ?? "") }
    private func saveDraft() {
        guard !restoring, let api = persistenceAPI else { return }
        LocalStore.shared.saveDraft(draft, server: LocalStore.server(api.baseURL), id: draftKey)
    }
    func restore(owner: ConversationOwner, api: APIClient, draftID: String? = nil) {
        guard persistenceAPI?.baseURL != api.baseURL || persistenceOwner != owner || customDraftID != draftID else { syncOutbox(); return }
        persistenceAPI = api; persistenceOwner = owner; customDraftID = draftID; restoring = true
        defer { restoring = false }
        draft = LocalStore.shared.draft(server: LocalStore.server(api.baseURL), id: draftKey) ?? ""
        if let data = try? LocalStore.shared.record(server: LocalStore.server(api.baseURL), kind: "attachments", id: draftKey)?.payload {
            attachments = (try? JSONDecoder().decode([ConversationSend.Piece].self, from: data)) ?? []
        }
        if let id = conversationID,
           let snapshot: ConversationSnapshot = api.cached("/api/conversation/conversations/\(id)/view", query: [.init(name: "lens", value: lens.rawValue), .init(name: "transcript", value: "true")]) {
            status = snapshot.status; items = snapshot.page.items; older = snapshot.page.older; loadedLens = lens
            rememberRows(items.map(\.id))
        }
        syncOutbox()
    }
    func syncOutbox() {
        guard let api = persistenceAPI, let owner = persistenceOwner else { return }
        let saved = Outbox.shared.entries(LocalStore.server(api.baseURL)).filter { $0.kind == .message && $0.owner == owner && (conversationID == nil || $0.message?.conversationId == conversationID) }
        for work in saved {
            if let previous = work.priorMessageIDs, let first = previous.first {
                supersededIDs.formUnion(previous)
                retryAliases[work.id] = first
            }
        }
        if conversationID == nil { conversationID = saved.last?.message?.conversationId }
        let received = Set(items.compactMap(\.senderMessageId))
        outgoing = saved.filter { !supersededIDs.contains($0.id) && (!received.contains($0.id) || $0.state != .sent) }.map {
            Outgoing(id: $0.id, text: $0.displayText, note: $0.note, retryable: $0.state == .failed)
        }
        rememberRows(saved.map { retryAliases[$0.id] ?? $0.id })
    }
    var lens: ConversationLens = .focus
    var items: [ConversationItem] = []
    var expandedBodies: [String: ConversationBody] = [:]
    var status: ConversationStatus?
    var hasUnread = false
    private var readThroughSequence = 0
    var heldPrompts: [ConversationConfiguration.HeldPrompt] = []
    var older: ConversationCursor?
    var error: String?
    var sending = false
    var loadingOlder = false
    var outgoing: [Outgoing] = []
    struct Outgoing: Identifiable {
        let id: String
        let text: String
        var note: String?
        var retryable = false
    }
    var deliveryMode = "steer"
    var attachments: [ConversationSend.Piece] = [] {
        didSet {
            guard !restoring, let api = persistenceAPI else { return }
            do { try LocalStore.shared.put(server: LocalStore.server(api.baseURL), kind: "attachments", id: draftKey, data: JSONEncoder().encode(attachments)) }
            catch { self.error = "Attachments could not be saved: \(error.localizedDescription)" }
        }
    }
    private var outgoingText: [String: String] = [:]
    private var outgoingAttachments: [String: [ConversationSend.Piece]] = [:]
    var backendKey: String?
    var selectedModel: String?
    var reasoningEffort: String?
    private var pinnedMessageIDs: Set<String> = []
    private var rowOrder: [String] = []
    private var supersededIDs: Set<String> = []
    private var liveRow: ConversationRow?
    private var liveTurnKey: String?
    private var refreshing = false
    private var retryAliases: [String: String] = [:]

    var rows: [ConversationRow] {
        let received = Set(items.compactMap(\.senderMessageId))
        let remote = items.compactMap { item -> ConversationRow? in
            guard !supersededIDs.contains(item.id) else { return nil }
            guard let body = expandedBodies[item.key] ?? item.body?.value else {
                return item.body?.reference == nil ? nil : ConversationRow(id: item.id, text: "Read message", own: false, note: nil, item: item)
            }
            guard !body.text.isEmpty else { return nil }
            let local = outgoing.first { $0.id == item.senderMessageId }
            let note: String?
            switch body.kind {
            case "prompt": note = body.isOwnerPrompt ? "Sent" : nil
            case "prompt_refused": note = "Not delivered · " + (body.refusalDetail ?? ConversationDelivery.refusalText(body.reason))
            case "prompt_uncertain": note = "Delivery uncertain · do not resend"
            case "prompt_discarded": note = "Not delivered · no longer applicable"
            case "permission_ask", "user_input": note = "Respond on the web"
            default: note = nil
            }
            return ConversationRow(id: retryAliases[item.id] ?? item.id, text: body.text, own: body.isOwnerPrompt, note: note,
                                   senderLabel: body.isPrompt && !body.isOwnerPrompt ? body.senderLabel : nil,
                                   retryID: local?.retryable == true ? local?.id : nil, item: item)
        }
        var combined = remote + outgoing.filter { !received.contains($0.id) }.map {
            ConversationRow(id: retryAliases[$0.id] ?? $0.id, text: $0.text, own: true, note: $0.note, retryID: $0.retryable ? $0.id : nil)
        }
        if let liveRow, !liveRow.text.isEmpty { combined.append(liveRow) }
        let positions = Dictionary(uniqueKeysWithValues: rowOrder.enumerated().map { ($0.element, $0.offset) })
        return combined.sorted { (positions[$0.id] ?? Int.max) < (positions[$1.id] ?? Int.max) }
    }
    var latestMessage: ConversationRow? {
        rows.last(where: { $0.own || $0.senderLabel != nil || ["agent_message", "user_input", "permission_ask"].contains($0.item?.body?.value?.kind ?? "") || $0.id == liveRow?.id })
    }
    var latestLine: String {
        guard let row = latestMessage else { return "Conversation" }
        let delivery = row.own && row.note != "Sent" ? row.note.map { " · " + $0 } ?? "" : ""
        return row.previewText + delivery
    }

    func applyConfiguration(_ configuration: ConversationConfiguration) {
        heldPrompts = configuration.heldPrompts
        readThroughSequence = configuration.ownerReadThroughSequence ?? 0
        hasUnread = (status?.rest.latestOwnerMessageSequence ?? 0) > readThroughSequence
        if backendKey == nil {
            backendKey = configuration.backendKey
            selectedModel = configuration.model
            reasoningEffort = configuration.reasoningEffort
        }
        let received = Set(items.compactMap(\.senderMessageId))
        for held in configuration.heldPrompts {
            let id = held.senderMessageId ?? held.heldPromptId
            if !received.contains(id), !outgoing.contains(where: { $0.id == id }) {
                outgoing.append(.init(id: id, text: held.text, note: "Queued"))
                pinnedMessageIDs.insert(id)
                rememberRows([id])
            }
        }
    }
    var firstUnreadID: String? {
        guard hasUnread else { return nil }
        return items.first { ($0.sourceSequence ?? 0) > readThroughSequence && ["agent_message", "user_input", "permission_ask"].contains($0.body?.value?.kind ?? "") }.map { retryAliases[$0.id] ?? $0.id }
    }
    func prepareOpening(using service: any ConversationServing) async {
        while hasUnread, older != nil, let first = items.compactMap(\.sourceSequence).min(), first > readThroughSequence {
            let cursor = older?.order.sequence
            await loadOlder(using: service)
            if error != nil || cursor == older?.order.sequence || Task.isCancelled { return }
        }
    }
    func applyHeldChange(_ result: ConversationHeldChange, senderID: String) {
        if result.discarded == true { setNote(senderID, "Not delivered · removed from queue") }
        else if let fate = result.fate {
            _ = applyDelivery(ConversationDelivery(conversationId: nil, fate: fate, refusalReason: result.refusalReason, reason: result.reason), id: senderID)
        }
    }
    private var loadedLens: ConversationLens?

    func updateSpeechTranscript(_ text: String, prefix: String) {
        let prefix = prefix.trimmingCharacters(in: .whitespacesAndNewlines)
        let transcript = text.trimmingCharacters(in: .whitespacesAndNewlines)
        draft = [prefix, transcript].filter { !$0.isEmpty }.joined(separator: " ")
    }

    func refresh(using service: any ConversationServing, transcript: Bool) async {
        guard !refreshing, let id = conversationID else { return }
        refreshing = true
        defer { refreshing = false }
        let requestedLens = lens
        do {
            let snapshot = try await service.conversationSnapshot(id, lens: requestedLens, transcript: transcript)
            let output = snapshot.status.liveOutput
            let liveText: String?
            if let output { liveText = try await service.conversationLiveText(id, reference: output.reference) }
            else { liveText = nil }
            let receiptIDs = Array(outgoing.map(\.id).suffix(64))
            let receipts = receiptIDs.isEmpty ? [] : try await service.conversationReceipts(id, messageIDs: receiptIDs)
            try Task.checkCancellation()
            guard id == conversationID, lens == requestedLens else { return }
            status = snapshot.status
            if transcript {
                if loadedLens != requestedLens { items = []; loadedLens = requestedLens; older = snapshot.page.older; liveRow = nil; liveTurnKey = nil }
                if let liveRow, output?.reference.version != liveRow.id.replacingOccurrences(of: "live:", with: "", options: .anchored),
                   let settled = snapshot.page.items.last(where: { incoming in
                       incoming.turnKey == liveTurnKey && incoming.body?.value?.kind == "agent_message" && !items.contains(where: { $0.key == incoming.key })
                   }) {
                    retryAliases[settled.id] = liveRow.id
                }
                // Replace the current window; retain explicitly loaded history before it.
                let keys = Set(snapshot.page.items.map(\.key))
                if let first = snapshot.page.items.first, let at = items.firstIndex(where: { $0.key == first.key }) {
                    items = Array(items.prefix(at)).filter { !keys.contains($0.key) } + snapshot.page.items
                } else { items = snapshot.page.items; older = snapshot.page.older }
                insertRemoteRows(items.map { retryAliases[$0.id] ?? $0.id })
                let received = Set(items.compactMap(\.senderMessageId))
                outgoing.removeAll { message in
                    guard received.contains(message.id) else { return false }
                    // A refusal remains retryable even after its server row arrives.
                    let delivered = items.first(where: { $0.senderMessageId == message.id })?.body?.value?.kind == "prompt"
                    if delivered { outgoingText[message.id] = nil; outgoingAttachments[message.id] = nil }
                    return delivered
                }
                for item in items {
                    guard let senderID = item.senderMessageId else { continue }
                    switch item.body?.value?.kind {
                    case "prompt_refused": setNote(senderID, "Not delivered", retryable: true)
                    case "prompt_discarded": setNote(senderID, "Not delivered · no longer applicable")
                    case "prompt_uncertain": setNote(senderID, "Delivery uncertain · do not resend")
                    default: break
                    }
                }
            }
            if let output, let liveText, !liveText.isEmpty {
                liveTurnKey = output.turnKey
                let identity = "live:" + output.reference.version
                liveRow = ConversationRow(id: identity, text: liveText, own: false, note: nil)
                rememberRows([identity])
            } else { liveRow = nil; liveTurnKey = nil }
            for receipt in receipts {
                switch receipt.fate {
                case "prompt": setNote(receipt.senderMessageId, "Sent")
                case "prompt_refused": setNote(receipt.senderMessageId, "Not delivered", retryable: true)
                case "prompt_uncertain": setNote(receipt.senderMessageId, "Delivery uncertain · do not resend")
                case "prompt_discarded": setNote(receipt.senderMessageId, "Not delivered · no longer applicable")
                default: break
                }
            }
            error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    func loadOlder(using service: any ConversationServing) async {
        guard !loadingOlder, let id = conversationID, let cursor = older else { return }
        loadingOlder = true
        defer { loadingOlder = false }
        do {
            let page = try await service.conversationPage(id, cursor: cursor)
            try Task.checkCancellation()
            guard cursor.lens == lens.rawValue, id == conversationID else { return }
            let keys = Set(items.map(\.key))
            let earlier = page.items.filter { !keys.contains($0.key) }
            items.insert(contentsOf: earlier, at: 0)
            let earlierIDs = earlier.map(\.id).filter { !rowOrder.contains($0) }
            rowOrder.insert(contentsOf: earlierIDs, at: 0)
            older = page.older
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    func loadBody(_ item: ConversationItem, using service: any ConversationServing) async {
        guard let id = conversationID, let reference = item.body?.reference else { return }
        do { expandedBodies[item.key] = try await service.conversationBody(id, reference: reference) }
        catch { self.error = error.localizedDescription }
    }
    @discardableResult func send(to owner: ConversationOwner, using service: any ConversationServing) async -> Bool {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !sending, !text.isEmpty || !attachments.isEmpty else { return false }
        if let api = persistenceAPI {
            let id = UUID().uuidString
            var message = ConversationSend(conversationId: conversationID, content: (text.isEmpty ? [] : [.init(text: text)]) + attachments,
                senderMessageId: id, sentAtUnixMilliseconds: Int64(Date().timeIntervalSince1970 * 1000))
            message.mode = deliveryMode; message.backendKey = backendKey; message.model = selectedModel; message.reasoningEffort = reasoningEffort
            let work = PendingWork(id: id, created: .now, kind: .message, owner: owner, message: message)
            do {
                try LocalStore.shared.put(server: LocalStore.server(api.baseURL), kind: "outbox", id: id,
                    data: JSONEncoder().encode(work), draft: (draftKey, ""))
                draft = ""; attachments = []; syncOutbox()
                Task { await Outbox.shared.drain(api: api); syncOutbox() }
                return true
            } catch { self.error = "Message is still in the composer: \(error.localizedDescription)"; return false }
        }
        let id = UUID().uuidString
        let displayText = ([text] + attachments.compactMap(\.fileName)).filter { !$0.isEmpty }.joined(separator: "\n")
        outgoing.append(Outgoing(id: id, text: displayText, note: "Sending…"))
        pinnedMessageIDs.insert(id)
        rememberRows([id])
        outgoingText[id] = text
        outgoingAttachments[id] = attachments
        attachments = []
        draft = ""
        return await deliver(id: id, text: text, to: owner, using: service)
    }

    func retry(_ id: String, to owner: ConversationOwner, using service: any ConversationServing) async {
        if let api = persistenceAPI { Outbox.shared.retry(id, api: api); syncOutbox(); return }
        guard !sending, let at = outgoing.firstIndex(where: { $0.id == id && $0.retryable }) else { return }
        let old = outgoing[at]
        // An explicitly refused attempt has a terminal server identity. A new attempt
        // keeps the same visual identity but gets its own delivery receipt.
        let next = UUID().uuidString
        retryAliases[next] = retryAliases[id] ?? id
        supersededIDs.insert(id)
        outgoingText[next] = outgoingText.removeValue(forKey: id)
        outgoingAttachments[next] = outgoingAttachments.removeValue(forKey: id)
        items.removeAll { $0.senderMessageId == id }
        outgoing[at] = Outgoing(id: next, text: old.text, note: "Sending…")
        _ = await deliver(id: next, text: outgoingText[next] ?? old.text, to: owner, using: service)
    }

    private func deliver(id: String, text: String, to owner: ConversationOwner, using service: any ConversationServing) async -> Bool {
        sending = true
        defer { sending = false }
        var message = ConversationSend(conversationId: conversationID, content: (text.isEmpty ? [] : [.init(text: text)]) + (outgoingAttachments[id] ?? []), senderMessageId: id, sentAtUnixMilliseconds: Int64(Date().timeIntervalSince1970 * 1000))
        message.mode = deliveryMode
        message.backendKey = backendKey
        message.model = selectedModel
        message.reasoningEffort = reasoningEffort
        do {
            let result = try await service.sendConversation(owner, message: message)
            if let newID = result.conversationId { conversationID = newID }
            return applyDelivery(result, id: id)
        } catch let error as URLError where [.notConnectedToInternet, .cannotConnectToHost, .cannotFindHost].contains(error.code) {
            setNote(id, "Offline · waiting to retry", retryable: true)
        } catch {
            // Timeout or a lost response does not prove the server rejected it.
            setNote(id, "Delivery uncertain · do not resend")
        }
        return false
    }
    private func applyDelivery(_ result: ConversationDelivery, id: String) -> Bool {
            switch result.fate {
            case "started", "injected": setNote(id, "Sent"); return true
            case "queued": setNote(id, "Queued"); return true
            case "suppressed": setNote(id, "Not delivered · \(result.reason ?? "no longer applicable")")
            case "refused": setNote(id, "Not delivered · \(ConversationDelivery.refusalText(result.refusalReason))", retryable: true)
            default: setNote(id, "Delivery uncertain · do not resend")
            }
        return false
    }
    private func insertRemoteRows(_ ids: [String]) {
        for (index, id) in ids.enumerated() where !rowOrder.contains(id) {
            if let following = ids.dropFirst(index + 1).first(where: { rowOrder.contains($0) && !pinnedMessageIDs.contains($0) }), let at = rowOrder.firstIndex(of: following) {
                rowOrder.insert(id, at: at)
            } else { rowOrder.append(id) }
        }
    }
    private func rememberRows(_ ids: [String]) {
        for id in ids where !rowOrder.contains(id) { rowOrder.append(id) }
    }
    private func setNote(_ id: String, _ note: String, retryable: Bool = false) {
        if let at = outgoing.firstIndex(where: { $0.id == id }) {
            outgoing[at].note = note
            outgoing[at].retryable = retryable
        }
    }
}
