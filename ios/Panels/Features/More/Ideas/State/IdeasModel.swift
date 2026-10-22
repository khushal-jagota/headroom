import Foundation
import Observation

@MainActor @Observable final class IdeasModel {
    var title = ""
    var detail = ""
    var projectID: String?
    private(set) var ideas: [Idea] = []
    private(set) var projects: [IdeaProject] = []
    private(set) var hasLoaded = false
    private(set) var isCapturing = false
    private(set) var readError: String?
    private(set) var captureError: String?
    private var readGeneration = 0

    var canCapture: Bool { !isCapturing && !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    func refresh(using service: any IdeasServing) async {
        readGeneration += 1
        let generation = readGeneration
        do {
            async let ideaRead = service.ideas()
            async let projectRead = service.ideaProjects()
            let (response, catalogue) = try await (ideaRead, projectRead)
            guard !Task.isCancelled, generation == readGeneration else { return }
            ideas = response.ideas
            projects = catalogue.projects
            hasLoaded = true
            readError = nil
        } catch {
            guard !Task.isCancelled, generation == readGeneration else { return }
            readError = error.localizedDescription
        }
    }

    @discardableResult func capture(using service: any IdeasServing) async -> Bool {
        guard canCapture else { return false }
        let capture = IdeaCapture(
            title: title.trimmingCharacters(in: .whitespacesAndNewlines),
            body: detail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : detail,
            projectID: projectID
        )
        isCapturing = true
        captureError = nil
        defer { isCapturing = false }
        do {
            let idea = try await service.captureIdea(capture)
            // An earlier read must not erase the acknowledged capture.
            readGeneration += 1
            ideas.removeAll { $0.id == idea.id }
            ideas.insert(idea, at: 0)
            title = ""; detail = ""; projectID = nil
            hasLoaded = true
            return true
        } catch {
            captureError = error.localizedDescription
            return false
        }
    }
}
