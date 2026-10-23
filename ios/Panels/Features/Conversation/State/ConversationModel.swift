import Foundation
import Observation

@MainActor @Observable final class ConversationModel {
    var conversationID: String?
    var draft = ""
    var lens: ConversationLens = .focus
    var items: [ConversationItem] = []
    var expandedBodies: [String: ConversationBody] = [:]
    var status: ConversationStatus?
    var older: ConversationCursor?
    var error: String?
    var sending = false
    var loadingOlder = false
    var outgoing: [Outgoing] = []
    struct Outgoing: Identifiable { let id: String; let text: String; var note: String? }
    private var loadedLens: ConversationLens?

    func refresh(using service: any ConversationServing, transcript: Bool) async {
        guard let id = conversationID else { return }
        let requestedLens = lens
        do {
            let snapshot = try await service.conversationSnapshot(id, lens: requestedLens, transcript: transcript)
            try Task.checkCancellation()
            guard id == conversationID, lens == requestedLens else { return }
            status = snapshot.status
            if transcript {
                if loadedLens != requestedLens { items = []; loadedLens = requestedLens; older = snapshot.page.older }
                // Replace the current window; retain explicitly loaded history before it.
                let keys = Set(snapshot.page.items.map(\.key))
                if let first = snapshot.page.items.first, let at = items.firstIndex(where: { $0.key == first.key }) {
                    items = Array(items.prefix(at)).filter { !keys.contains($0.key) } + snapshot.page.items
                } else { items = snapshot.page.items; older = snapshot.page.older }
                let received = Set(items.compactMap(\.senderMessageId))
                outgoing.removeAll { received.contains($0.id) }
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
            items.insert(contentsOf: page.items.filter { !keys.contains($0.key) }, at: 0)
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
        guard !sending, !text.isEmpty else { return false }
        sending = true
        defer { sending = false }
        let id = UUID().uuidString
        outgoing.append(Outgoing(id: id, text: text, note: nil))
        draft = ""
        let message = ConversationSend(conversationId: conversationID, content: [.init(text: text)], senderMessageId: id, sentAtUnixMilliseconds: Int64(Date().timeIntervalSince1970 * 1000))
        do {
            let result = try await service.sendConversation(owner, message: message)
            if let newID = result.conversationId { conversationID = newID }
            switch result.fate {
            case "started", "injected": return true
            case "queued": setNote(id, "Queued"); return true
            case "suppressed": setNote(id, "Not delivered · \(result.reason ?? "no longer applicable")")
            case "refused": setNote(id, "Not delivered · \(result.refusalReason ?? "refused")")
            default: setNote(id, "Delivery uncertain · do not resend")
            }
        } catch {
            // A lost response does not prove the server rejected the message.
            setNote(id, "Delivery uncertain · do not resend")
        }
        return false
    }
    private func setNote(_ id: String, _ note: String) {
        if let at = outgoing.firstIndex(where: { $0.id == id }) { outgoing[at].note = note }
    }
}
