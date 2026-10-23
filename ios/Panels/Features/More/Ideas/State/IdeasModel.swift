import Foundation
import Observation

@MainActor @Observable final class IdeasModel {
    var title = "" { didSet { persist() } }
    var detail = "" { didSet { persist() } }
    var projectID: String? { didSet { persist() } }
    private var draftServer: String?
    private var restoring = false
    private func persist() {
        guard !restoring, let server = draftServer else { return }
        LocalStore.shared.saveDraft(title, server: server, id: "ideas:title")
        LocalStore.shared.saveDraft(detail, server: server, id: "ideas:detail")
        LocalStore.shared.saveDraft(projectID ?? "", server: server, id: "ideas:project")
    }
    private func restore(_ service: any IdeasServing) {
        guard let api = service as? APIClient else { return }
        let server = LocalStore.server(api.baseURL)
        guard draftServer != server else { return }
        restoring = true; draftServer = server
        title = LocalStore.shared.draft(server: server, id: "ideas:title") ?? ""
        detail = LocalStore.shared.draft(server: server, id: "ideas:detail") ?? ""
        let project = LocalStore.shared.draft(server: server, id: "ideas:project") ?? ""
        projectID = project.isEmpty ? nil : project
        restoring = false
    }
    private(set) var ideas: [Idea] = []
    private(set) var projects: [IdeaProject] = []
    private(set) var hasLoaded = false
    private(set) var isCapturing = false
    private(set) var readError: String?
    private(set) var captureError: String?
    private var readGeneration = 0

    var canCapture: Bool { !isCapturing && !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    func refresh(using service: any IdeasServing) async {
        restore(service)
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
