import Foundation
import Observation

@MainActor @Observable final class FeedbackModel {
    private var resource: ResourceHandle<FeedbackResponse>?
    private var owner: ResourceOwner?
    private var standaloneResponse: FeedbackResponse?
    var response: FeedbackResponse? { resource == nil ? standaloneResponse : resource?.value }
    func connect(_ owner: ResourceOwner) {
        self.owner = owner; resource = owner.handle(FeedbackResources.notes)
        standaloneResponse = nil; actionError = nil
    }
    private(set) var loading = false
    private(set) var pendingNoteID: String?
    private var actionError: String?
    var error: String? { actionError ?? resource?.error }
    private var readVersion = 0

    func load(using client: any FeedbackServing) async {
        if let resource { await resource.refresh(); return }
        readVersion += 1
        let version = readVersion
        loading = true
        defer { if version == readVersion { loading = false } }
        do {
            let response = try await client.feedback()
            guard version == readVersion, !Task.isCancelled else { return }
            self.standaloneResponse = response
            actionError = nil
        } catch {
            guard version == readVersion, !Task.isCancelled else { return }
            self.actionError = "Couldn’t refresh feedback. \(error.localizedDescription)"
        }
    }

    @discardableResult
    func update(_ note: FeedbackNote, action: FeedbackAction, using client: any FeedbackServing) async -> Bool {
        guard pendingNoteID == nil else { return false }
        pendingNoteID = note.id
        let generation = owner?.admissionGeneration
        actionError = nil
        defer { pendingNoteID = nil }
        do {
            _ = try await client.updateFeedback(note.id, action: action)
            guard generation == owner?.admissionGeneration else { return true }
            owner?.invalidate([FeedbackResources.notes.key])
            await load(using: client)
            return true
        } catch {
            guard generation == owner?.admissionGeneration else { return false }
            self.actionError = "Couldn’t \(action.rawValue) this note. \(error.localizedDescription)"
            return false
        }
    }
}
