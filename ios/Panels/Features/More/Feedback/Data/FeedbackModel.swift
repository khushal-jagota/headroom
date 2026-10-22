import Foundation
import Observation

@MainActor @Observable final class FeedbackModel {
    private(set) var response: FeedbackResponse?
    private(set) var loading = false
    private(set) var pendingNoteID: String?
    private(set) var error: String?
    private var readVersion = 0

    func load(using client: any FeedbackServing) async {
        readVersion += 1
        let version = readVersion
        loading = true
        defer { if version == readVersion { loading = false } }
        do {
            let response = try await client.feedback()
            guard version == readVersion, !Task.isCancelled else { return }
            self.response = response
            error = nil
        } catch {
            guard version == readVersion, !Task.isCancelled else { return }
            self.error = "Couldn’t refresh feedback. \(error.localizedDescription)"
        }
    }

    @discardableResult
    func update(_ note: FeedbackNote, action: FeedbackAction, using client: any FeedbackServing) async -> Bool {
        guard pendingNoteID == nil else { return false }
        pendingNoteID = note.id
        error = nil
        defer { pendingNoteID = nil }
        do {
            _ = try await client.updateFeedback(note.id, action: action)
            await load(using: client)
            return true
        } catch {
            self.error = "Couldn’t \(action.rawValue) this note. \(error.localizedDescription)"
            return false
        }
    }
}
